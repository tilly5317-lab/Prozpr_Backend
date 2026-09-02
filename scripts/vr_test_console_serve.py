"""Serve the VR test console on http://localhost:8080 and proxy its calls.

    python -m scripts.vr_test_console_serve                      # -> local backend :8000
    python -m scripts.vr_test_console_serve --backend https://prozpr.in
    python -m scripts.vr_test_console_serve --direct             # also expose raw VR
    python -m scripts.vr_test_console_serve --direct --socks 127.0.0.1:1080

Everything the page calls is same-origin on :8080, so there is no CORS to
configure and no backend change needed to test from a laptop.

Two proxy paths, and the difference matters:

``/api/*`` -> our backend's ``/api/v1/vr/*`` ops endpoints
    The default, and the one to use. The backend holds the VR key and is the
    whitelisted origin, so this tests the integration we actually ship — the
    mirror, the crosswalk, the sync — rather than just the vendor.

``/vr/*`` -> ``valueresearchapi.in`` directly (only with ``--direct``)
    Raw contract probing. The key is read from ``VR_API_KEY`` in **this
    process's** environment and attached server-side; it is never sent to the
    browser and never written anywhere. Useful for answering "which tables does
    our key cover" before the mirror exists at all.

Value Research allowlists our backend's IP (13.234.33.230), not a laptop's, so
``--direct`` from a developer machine gets a Cloudflare 403 unless the request
egresses from an allowlisted host. Two ways to arrange that:

  * run this script **on** the backend, or
  * open a SOCKS tunnel through it and pass ``--socks``:

        ssh -N -D 1080 ubuntu@13.234.33.230
        VR_API_KEY=... python -m scripts.vr_test_console_serve --direct --socks 127.0.0.1:1080

The console is a **read-mostly** tool. It deliberately exposes no bulk-request
route: VR caps extract generation at two per table per calendar day and does
not refund, which is not something to leave behind a button in a browser.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

PORT = 8080
VR_UPSTREAM = "https://valueresearchapi.in"
CONSOLE = Path(__file__).with_name("vr_test_console.html")

_opener: urllib.request.OpenerDirector | None = None
_args: argparse.Namespace | None = None


def _build_opener(socks: str | None) -> urllib.request.OpenerDirector:
    """Plain opener, or one that egresses through a SOCKS5 tunnel."""
    if not socks:
        return urllib.request.build_opener()
    try:
        import socket

        import socks as pysocks  # type: ignore[import-not-found]
    except ImportError:
        sys.exit(
            "--socks needs PySocks:  pip install PySocks\n"
            "(or run this script on the whitelisted backend and drop --socks)"
        )
    host, _, port = socks.partition(":")
    pysocks.set_default_proxy(pysocks.SOCKS5, host, int(port or 1080))
    socket.socket = pysocks.socksocket  # type: ignore[assignment]
    return urllib.request.build_opener()


def _table_catalog() -> dict:
    """The declared table registry, so the page cannot drift from the code."""
    from app.domains.vr_data.specs import all_specs

    return {
        "tables": [
            {
                "name": s.name,
                "tier": s.tier,
                "fields": len(s.columns),
                "primary_key": list(s.primary_key),
                "sync_mode": s.sync_mode,
                "update_frequency": s.update_frequency,
                "rationale": s.rationale,
            }
            for s in sorted(
                all_specs().values(), key=lambda s: (s.tier, s.name)
            )
        ]
    }


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A002 - quieter console
        sys.stderr.write("  %s\n" % (fmt % args))

    # -- helpers -------------------------------------------------------

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _relay(self, url: str, headers: dict[str, str], method: str) -> None:
        """Forward one request upstream and stream the answer back verbatim.

        Nothing is cached, rewritten or stored. A non-2xx is relayed with its
        real status and body, because the body is the diagnosis: a 403 with an
        HTML body is Cloudflare (wrong key or non-whitelisted IP), a 403 with
        JSON is Value Research refusing that table for this key.
        """
        request = urllib.request.Request(url, method=method)
        for key, value in headers.items():
            request.add_header(key, value)
        assert _opener is not None
        try:
            with _opener.open(request, timeout=120) as response:
                body = response.read()
                status, ctype = response.status, response.headers.get(
                    "Content-Type", "application/json"
                )
        except urllib.error.HTTPError as exc:
            body = exc.read()
            status = exc.code
            ctype = exc.headers.get("Content-Type", "text/plain")
        except Exception as exc:  # noqa: BLE001 - surfaced in the console
            self._send_json(
                502, {"error": f"{type(exc).__name__}: {exc}", "upstream": url}
            )
            return

        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy_backend(self, method: str) -> None:
        assert _args is not None
        path = self.path[len("/api") :]
        url = f"{_args.backend.rstrip('/')}/api/v1{path}"
        headers = {"Accept": "application/json"}
        # The browser holds the operator's own JWT; we forward it untouched and
        # never persist it. The VR key is not involved on this path at all.
        auth = self.headers.get("Authorization")
        if auth:
            headers["Authorization"] = auth
        member = self.headers.get("X-Family-Member-Id")
        if member:
            headers["X-Family-Member-Id"] = member
        self._relay(url, headers, method)

    def _proxy_vr(self, method: str) -> None:
        assert _args is not None
        if not _args.direct:
            self._send_json(
                404,
                {
                    "error": "Direct VR proxying is off. Restart with --direct "
                    "(and --socks, unless this host is whitelisted)."
                },
            )
            return
        key = os.environ.get("VR_API_KEY", "").strip()
        if not key:
            self._send_json(
                503,
                {
                    "error": "VR_API_KEY is not set in this process's environment. "
                    "The key is attached server-side and never sent to the browser."
                },
            )
            return
        url = VR_UPSTREAM + self.path[len("/vr") :]
        self._relay(url, {"API_KEY": key, "Accept": "application/json"}, method)

    # -- routes --------------------------------------------------------

    def do_GET(self):  # noqa: N802
        if self.path in ("/", "/index.html"):
            self.path = "/" + CONSOLE.name
            return super().do_GET()
        if self.path == "/tables.json":
            try:
                return self._send_json(200, _table_catalog())
            except Exception as exc:  # noqa: BLE001
                return self._send_json(500, {"error": str(exc)})
        if self.path == "/config.json":
            assert _args is not None
            return self._send_json(
                200,
                {
                    "backend": _args.backend,
                    "direct_enabled": _args.direct,
                    "socks": _args.socks,
                    "vr_key_present": bool(os.environ.get("VR_API_KEY", "").strip()),
                },
            )
        if self.path.startswith("/api/"):
            return self._proxy_backend("GET")
        if self.path.startswith("/vr/"):
            return self._proxy_vr("GET")
        return super().do_GET()

    def do_POST(self):  # noqa: N802
        if self.path.startswith("/api/"):
            return self._proxy_backend("POST")
        if self.path.startswith("/vr/"):
            return self._proxy_vr("POST")
        return self._send_json(404, {"error": f"no route for POST {self.path}"})


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    # NOT allow_reuse_address on Windows. There SO_REUSEADDR behaves like
    # SO_REUSEPORT: a second server binds :8080 successfully and silently, while
    # the first one keeps answering — so you restart with new flags, see the old
    # process's behaviour, and conclude the flags do not work. On POSIX it keeps
    # its usual meaning (rebind through TIME_WAIT), which is worth having.
    allow_reuse_address = os.name != "nt"


def _port_is_taken(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    global _opener, _args
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        default="http://127.0.0.1:8000",
        help="Backend base URL to proxy /api to (default: local dev server)",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="also expose /vr/* proxied straight to Value Research, using "
        "VR_API_KEY from this process's environment",
    )
    parser.add_argument(
        "--socks",
        default=None,
        metavar="HOST:PORT",
        help="egress upstream calls through a SOCKS5 tunnel, e.g. an ssh -D "
        "tunnel opened on the whitelisted backend",
    )
    parser.add_argument("--port", type=int, default=PORT)
    _args = parser.parse_args()
    _opener = _build_opener(_args.socks)

    if not CONSOLE.exists():
        sys.exit(f"missing console page: {CONSOLE}")

    if _port_is_taken(_args.port):
        sys.exit(
            f"port {_args.port} is already serving. Stop that process first — "
            "on Windows a second bind can appear to succeed while the old "
            "server keeps answering, which makes new flags look ignored.\n"
            f"  netstat -ano | findstr :{_args.port}     then  taskkill /PID <pid> /F"
        )

    os.chdir(CONSOLE.parent)
    print(f"VR test console   http://localhost:{_args.port}")
    print(f"  /api/*  ->  {_args.backend}/api/v1/*")
    if _args.direct:
        key_state = (
            "VR_API_KEY set" if os.environ.get("VR_API_KEY") else "VR_API_KEY MISSING"
        )
        print(f"  /vr/*   ->  {VR_UPSTREAM}  ({key_state})")
    else:
        print("  /vr/*   ->  off (pass --direct to enable raw VR probing)")
    if _args.socks:
        print(f"  egress  ->  SOCKS5 {_args.socks}")
    else:
        print(
            "  egress  ->  this machine. Value Research allowlists 13.234.33.230;\n"
            "              from a laptop, direct calls get a Cloudflare 403."
        )
    print("\nCtrl-C to stop.\n")

    with Server(("127.0.0.1", _args.port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
