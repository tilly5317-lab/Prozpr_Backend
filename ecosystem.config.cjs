/**
 * PM2 process definition for production (used by .github/workflows/deploy.yml).
 * Run from repo root: pm2 start ecosystem.config.cjs --only prozpr_backend
 */
module.exports = {
  apps: [
    {
      name: "prozpr_backend",
      cwd: __dirname,
      script: "venv/bin/uvicorn",
      args: "main:app --host 0.0.0.0 --port 8000",
      interpreter: "none",
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      watch: false,
      max_restarts: 15,
      min_uptime: "10s",
      restart_delay: 3000,
      max_memory_restart: "1G",
      merge_logs: true,
      time: true,
    },
  ],
};
