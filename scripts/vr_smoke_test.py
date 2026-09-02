"""Live connectivity + contract test against Value Research. Read-only.

**Run this on the whitelisted backend (13.234.33.230), not on a laptop.** VR
gates access by source IP at Cloudflare, so from anywhere else every request
returns a Cloudflare challenge page and the results say nothing about our key
or our contract.

Use the app's interpreter, not the system one: PM2 runs ``venv/bin/uvicorn``
(``ecosystem.config.cjs``), and the box's ``python3`` has none of the
dependencies. ``VR_API_KEY`` is read from the backend ``.env``, so it does not
need repeating on the command line once it is set there.

    cd ~/Prozpr_Backend
    venv/bin/python -m scripts.vr_smoke_test --describe        # what our key covers
    venv/bin/python -m scripts.vr_smoke_test --tier core       # the eight requested
    venv/bin/python -m scripts.vr_smoke_test --sample nav      # one page, to see shapes

What it does **not** do: write to the database, fetch more than one page, or
issue a single bulk request. Each table costs one ``output=count`` call — the
cheapest question VR answers — so a full run over 30 tables spends 30 of the
500/hour budget and returns no rows at all unless ``--sample`` is passed.

Read the exit summary for three things:

1. **Cloudflare-blocked** — the key or this IP is wrong. Nothing else in the
   report is meaningful until that is fixed.
2. **Refused by VR** — that table is outside our contract. This is the list to
   take to the vendor, and the answer to "do we get the master tables too".
3. **Unknown fields** (``--sample`` only) — VR returned a field absent from
   ``catalog.json``, so the mirror would silently drop it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    from app.domains.vr_data.client import (  # noqa: E402
        VrAccessError,
        VrClient,
        VrError,
    )
    from app.domains.vr_data.specs import all_specs  # noqa: E402
except ModuleNotFoundError as _exc:  # pragma: no cover - interpreter guard
    # On the deploy box `python3` is the system interpreter with none of the
    # app's dependencies; PM2 runs `venv/bin/uvicorn`. Say so.
    for _candidate in ("venv/bin/python", ".venv/bin/python", ".venv/Scripts/python.exe"):
        if (BACKEND_ROOT / _candidate).exists():
            sys.exit(
                f"{_exc.name!r} is not installed for {sys.executable}.\n"
                "This is the app's interpreter, not the system one - re-run as:\n\n"
                f"    {_candidate} -m scripts.vr_smoke_test {' '.join(sys.argv[1:])}\n"
            )
    raise

OK = "ok"
REFUSED = "refused-by-vr"
BLOCKED = "blocked-by-cloudflare"
FAILED = "failed"


async def probe_table(
    client: VrClient, name: str, *, sample: bool
) -> dict[str, Any]:
    spec = all_specs()[name]
    row: dict[str, Any] = {"table": name, "tier": spec.tier}
    try:
        row["rows_48h"] = await client.count(name)
        row["status"] = OK
    except VrAccessError as exc:
        row["status"] = REFUSED if exc.reached_vr else BLOCKED
        row["detail"] = str(exc)
        return row
    except VrError as exc:
        row["status"] = FAILED
        row["detail"] = str(exc)
        return row

    if sample:
        try:
            page = await client.fetch_page(name)
            row["sample_rows"] = len(page.rows)
            row["paged"] = bool(page.next_url)
            if page.rows:
                returned = set(page.rows[0].keys())
                declared = set(spec.columns)
                row["unknown_fields"] = sorted(returned - declared)
                row["missing_fields"] = sorted(declared - returned)
                row["sample"] = {
                    k: page.rows[0][k] for k in list(page.rows[0])[:8]
                }
        except VrError as exc:
            row["sample_error"] = str(exc)
    return row


async def describe_only() -> int:
    """Ask VR which tables this key is entitled to, and diff against the registry.

    One request. This is the empirical answer to "do we get the master tables
    too" — take its output to the vendor instead of negotiating table by table.
    """
    async with VrClient() as client:
        if not client.configured:
            print("VR_API_KEY is not set - nothing to ask.")
            return 2
        try:
            payload = await client.describe()
        except VrAccessError as exc:
            print(
                "blocked before reaching VR (Cloudflare)."
                if not exc.reached_vr
                else f"VR refused /describe: {exc}"
            )
            return 3
        except VrError as exc:
            print(f"failed: {exc}")
            return 1

    text = json.dumps(payload)
    declared = set(all_specs())
    entitled = {t for t in declared if f'"{t}"' in text}
    missing = sorted(declared - entitled)

    out = BACKEND_ROOT / "vr_describe.json"
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"/describe returned {len(text)} bytes; saved to {out}\n")
    print(f"declared in our registry : {len(declared)}")
    print(f"named in /describe       : {len(entitled)}")
    if missing:
        print("\nDeclared but NOT named by /describe (raise these with VR):")
        for name in missing:
            print(f"  - {name}  [{all_specs()[name].tier}]")
    else:
        print("\nEvery table we declare appears in /describe.")
    print(
        "\nNote: this matches on table names appearing anywhere in the response, "
        "so read vr_describe.json before quoting it."
    )
    return 0


async def run(tiers: tuple[str, ...], sample: bool, only: list[str]) -> int:
    specs = all_specs()
    names = [
        n
        for n, s in specs.items()
        if (not only or n in only) and (not tiers or s.tier in tiers)
    ]
    if not names:
        print("no tables matched")
        return 1

    async with VrClient() as client:
        if not client.configured:
            print("VR_API_KEY is not set — nothing to test.")
            return 2
        print(f"probing {len(names)} table(s) against {client.base_url}\n")
        results = []
        for name in sorted(names):
            result = await probe_table(client, name, sample=sample)
            results.append(result)
            mark = {OK: "  ok", REFUSED: " REF", BLOCKED: " CF!", FAILED: "FAIL"}[
                result["status"]
            ]
            extra = ""
            if result["status"] == OK:
                extra = f"rows(48h)={result.get('rows_48h')}"
                if result.get("unknown_fields"):
                    extra += f"  UNKNOWN FIELDS: {result['unknown_fields']}"
            else:
                extra = str(result.get("detail", ""))[:110]
            print(f"[{mark}] {result['tier']:<10} {name:<42} {extra}")

    blocked = [r for r in results if r["status"] == BLOCKED]
    refused = [r for r in results if r["status"] == REFUSED]
    failed = [r for r in results if r["status"] == FAILED]
    unknown = {
        r["table"]: r["unknown_fields"] for r in results if r.get("unknown_fields")
    }

    print("\n" + "=" * 72)
    print(f"reachable          : {sum(1 for r in results if r['status'] == OK)}")
    print(f"refused by VR      : {len(refused)}  {[r['table'] for r in refused]}")
    print(f"blocked (Cloudflare): {len(blocked)}")
    print(f"other failures     : {len(failed)}  {[r['table'] for r in failed]}")
    if blocked:
        print(
            "\nCloudflare blocked every one of those — the request never reached VR.\n"
            "Check VR_API_KEY, and that this host's public IP is the whitelisted\n"
            "one (13.234.33.230). Run `curl -s ifconfig.me` to confirm."
        )
    if refused:
        print(
            "\nVR itself refused those tables for this key. That is a contract\n"
            "question: it is the list to send the vendor when asking which\n"
            "non-plan_id master tables are included."
        )
    if unknown:
        print(f"\nfields present at VR but absent from catalog.json: {unknown}")
    print(f"\nrequest budget left this hour: {VrClient().budget_remaining}")

    out = BACKEND_ROOT / "vr_smoke_report.json"
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"full report: {out}")
    return 0 if not blocked else 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tier",
        action="append",
        default=[],
        choices=["core", "additional", "optional", "support", "candidate"],
    )
    parser.add_argument(
        "--sample",
        nargs="?",
        const="__all__",
        help="also pull one page (per table, or for the named table) to inspect "
        "field shapes. Costs a data request each.",
    )
    parser.add_argument("--table", action="append", default=[])
    parser.add_argument(
        "--describe",
        action="store_true",
        help="ask VR which tables this key is entitled to (one request) and "
        "diff against our registry, instead of probing table by table",
    )
    args = parser.parse_args()

    if args.describe:
        return asyncio.run(describe_only())

    only = list(args.table)
    sample = bool(args.sample)
    if args.sample and args.sample != "__all__":
        only = [args.sample]
    return asyncio.run(run(tuple(args.tier), sample, only))


if __name__ == "__main__":
    raise SystemExit(main())
