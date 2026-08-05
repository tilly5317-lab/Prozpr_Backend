/**
 * PM2 process definition for production (used by .github/workflows/deploy.yml).
 * Run from repo root: pm2 start ecosystem.config.cjs --only prozpr_backend
 *
 * EC2 prerequisite — enable swap (run once on the host):
 *   sudo fallocate -l 1G /swapfile
 *   sudo chmod 600 /swapfile
 *   sudo mkswap /swapfile
 *   sudo swapon /swapfile
 *   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
 */
// Short SHA of the checkout, or "unknown". Wrapped because this file is
// EVALUATED by pm2: an uncaught throw here (git missing from PATH, dir not a
// repo) would stop the app from starting. A missing version label must never
// cost you the process.
function gitCommit() {
  try {
    return require("child_process")
      .execSync("git rev-parse HEAD", { cwd: __dirname, stdio: ["ignore", "pipe", "ignore"] })
      .toString()
      .trim();
  } catch {
    return "unknown";
  }
}

module.exports = {
  apps: [
    {
      name: "prozpr_backend",
      cwd: __dirname,
      script: "venv/bin/uvicorn",
      // No --limit-max-requests: on this single-instance setup, recycling the worker
      // every ~1000 requests caused a ~11s 502 on every recycle (no sibling worker to
      // cover the restart). The May-2026 memory leak it was added to mask was fixed in
      // code; max_memory_restart (below) is the memory backstop now — it restarts only
      // if memory actually climbs to 800M, instead of on a blind request counter.
      // --timeout-graceful-shutdown bounds uvicorn's wait for in-flight requests.
      // It defaults to unbounded, so without it the lifespan shutdown (and the
      // telemetry flush in it) may never start before PM2 gives up.
      args: "main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 5",
      interpreter: "none",
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      watch: false,
      max_restarts: 15,
      min_uptime: "10s",
      restart_delay: 3000,
      max_memory_restart: "800M",
      merge_logs: true,
      time: true,
      // Telemetry flush budget. PM2 SIGKILLs 1600ms after SIGINT by default, which
      // truncates the PostHog and OTLP batch flushes in lifespan _shutdown().
      // Sized above uvicorn's 5s graceful wait + the 5s OTLP export timeout ceiling.
      kill_timeout: 8000,
      // Telemetry identity. Set HERE, not in .env on the host: config.py calls
      // load_dotenv() without override, so the process environment wins, and this
      // file is the one place that is by definition production. Without these,
      // every prod event was stamped environment="development" (the config.py
      // default) and service_version="unknown", making prod indistinguishable
      // from a laptop in PostHog. Applied on `pm2 reload --update-env`.
      env: {
        DEPLOY_ENV: "production",
        // Deploys are the most common explanation for a change in any metric, so
        // every event and span carries the build that produced it. Read at config
        // load, which survives a manual `pm2 restart` and a reboot resurrect —
        // exporting it in deploy.yml would be frozen stale by `pm2 save`.
        GIT_COMMIT: gitCommit(),
      },
    },
  ],
};
