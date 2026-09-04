"""DEV-ONLY — run the MF Central mock as a SEPARATE process.

You almost certainly do not need this. The mock is mounted inside the app at
``/mfc-mock`` whenever no real MFC credentials are configured, so
``uvicorn main:app --reload`` already serves it and the flow works with nothing
else running.

This exists for the two cases where mounting is not enough:

* pointing a DIFFERENT backend (a colleague's, a staging box) at a mock you
  control, by setting their ``MFC_API_BASE_URL`` / ``MFC_REDIRECT_BASE_URL``
  at this process;
* reproducing a failure with the mock isolated from our own app, so a
  traceback cannot be blamed on our middleware.

    python scripts/mfc_mock_server.py                # serves on :9110
    python scripts/mfc_mock_server.py --print-env    # the .env block to point at it

The protocol, sample payloads and consent page all live in
``app/domains/ingestion/dev/mfc_mock.py`` — this is only a runner, so the
mounted and standalone modes can never drift.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI  # noqa: E402

from app.domains.ingestion.dev import mfc_mock  # noqa: E402


def build_app() -> FastAPI:
    """The mock alone, with its routes at the root rather than under /mfc-mock."""
    app = FastAPI(title="MF Central (mock)", docs_url="/docs")
    app.include_router(mfc_mock.router)
    return app


def print_env(host: str, port: int) -> None:
    """The .env block that points a backend at THIS process.

    Both keys are printed because the mock stands in for both sides of MFC's
    exchange: it verifies our signature with the public half and signs its
    responses with the private half.
    """
    private_key, public_key = mfc_mock.keys()
    base = f"http://{host}:{port}"
    print(f"""# --- MF Central: standalone mock ({Path(__file__).name}) ---
MFC_API_BASE_URL={base}
MFC_REDIRECT_BASE_URL={base}
MFC_ORIGIN={base}
MFC_REDIRECT_URL=http://localhost:8080/mfc-cas/callback
MFC_CLIENT_ID={mfc_mock.MOCK_CLIENT_ID}
MFC_CLIENT_SECRET={mfc_mock.MOCK_CLIENT_SECRET}
MFC_USERNAME={mfc_mock.MOCK_USERNAME}
MFC_PASSWORD={mfc_mock.MOCK_PASSWORD}
MFC_ENCRYPTION_KEY={mfc_mock.MOCK_ENCRYPTION_KEY}
MFC_IV={mfc_mock.MOCK_IV}
MFC_URL_ENCRYPTION_KEY={mfc_mock.MOCK_URL_KEY}
MFC_PRIVATE_KEY={private_key.replace(chr(10), "\\n")}
MFC_PUBLIC_KEY={public_key.replace(chr(10), "\\n")}
MFC_VERIFY_RESPONSE_SIGNATURE=true
""")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9110)
    parser.add_argument(
        "--print-env",
        action="store_true",
        help="Print the .env block pointing a backend at this process, then exit.",
    )
    args = parser.parse_args()

    if args.print_env:
        print_env(args.host, args.port)
        return

    import uvicorn

    print(f"MF Central mock on http://{args.host}:{args.port} — docs at /docs")
    print(f"RSA pair: {mfc_mock.KEY_FILE}")
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
