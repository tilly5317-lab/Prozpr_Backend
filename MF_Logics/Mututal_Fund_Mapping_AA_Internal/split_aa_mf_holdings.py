#!/usr/bin/env python3
"""
Split Account Aggregator mutual-fund holdings JSON into separate CSV files.

Expected shape (typical AA / CAMS-style payload)::

    {
      "pan": "...",
      "fromDate": "...",
      "toDate": "...",
      "data": {
        "dtTransaction": [ { ... }, ... ],
        "dtSummary": [ { ... }, ... ]
      }
    }

Writes (by default next to the input file, or under --out-dir):

  • ``<stem>_meta.csv``      — one row: pan, fromDate, toDate
  • ``<stem>_transactions.csv`` — all transaction rows
  • ``<stem>_summary.csv``   — all holding-summary rows

With ``--all-in-one``, writes a **single** ``<stem>_all.csv`` instead: each row has
``segment`` = ``meta`` | ``transaction`` | ``summary``, plus shared ``pan`` /
``fromDate`` / ``toDate`` on every row.

Nested dict/list cell values are JSON-encoded in CSV cells.

Usage
-----
  cd project/backend/MF_Logics/Mututal_Fund_Mapping_AA_Internal

  python split_aa_mf_holdings.py path/to/aa_holdings.json
  python split_aa_mf_holdings.py path/to/aa_holdings.json --all-in-one --out-dir ./out
  python split_aa_mf_holdings.py -  < aa_holdings.json   # stdin

  # Batch: process all JSON files in a directory, appending to shared CSVs
  python split_aa_mf_holdings.py --input-dir Testing_data/ --stem combined --out-dir ./out
  python split_aa_mf_holdings.py file1.json file2.json --stem combined --out-dir ./out
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


def _load_json(path: Path | None) -> Any:
    if path is None:
        return json.load(sys.stdin)
    text = path.read_text(encoding="utf-8")
    return json.loads(text)


def _as_row_list(value: Any) -> list[dict[str, Any]]:
    """Normalize payload fragment to a list of flat dict rows."""
    if value is None:
        return []
    if isinstance(value, list):
        out: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                out.append(item)
            elif isinstance(item, list):
                out.extend(_as_row_list(item))
        return out
    if isinstance(value, dict):
        # Single nested object that might hold arrays
        rows: list[dict[str, Any]] = []
        for v in value.values():
            rows.extend(_as_row_list(v))
        return rows
    return []


def _collect_headers(rows: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for row in rows:
        for k in row:
            seen.setdefault(str(k), None)
    return sorted(seen.keys())


def _fieldnames_ordered(
    rows: list[dict[str, Any]],
    priority: tuple[str, ...],
) -> list[str]:
    all_sorted = _collect_headers(rows)
    have = set(all_sorted)
    out = [p for p in priority if p in have]
    out.extend(k for k in all_sorted if k not in out)
    return out


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    priority_columns: tuple[str, ...] | None = None,
    append: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = (
        _fieldnames_ordered(rows, priority_columns)
        if priority_columns
        else _collect_headers(rows)
    )
    if append and path.exists() and path.stat().st_size > 0:
        existing_headers: list[str] = []
        with path.open("r", encoding="utf-8", newline="") as rf:
            reader = csv.reader(rf)
            existing_headers = next(reader, [])
        new_cols = [h for h in headers if h not in existing_headers]
        merged_headers = existing_headers + new_cols
        with path.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=merged_headers, extrasaction="ignore")
            for row in rows:
                w.writerow({h: _cell(row.get(h)) for h in merged_headers})
    else:
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow({h: _cell(row.get(h)) for h in headers})


def _flatten_investor_details(inv: dict[str, Any] | None) -> dict[str, Any]:
    """Flatten investorDetails (including nested address) into a single-level dict."""
    if not inv or not isinstance(inv, dict):
        return {}
    flat: dict[str, Any] = {}
    for k, v in inv.items():
        if isinstance(v, dict):
            for sk, sv in v.items():
                flat[f"{k}_{sk}"] = sv if sv is not None else ""
        else:
            flat[k] = v if v is not None else ""
    return flat


def _normalize_data_block(raw_data: Any) -> dict[str, Any]:
    """Accept ``data`` as either a dict or a list of dicts and return a
    unified dict with ``dtTransaction`` and ``dtSummary`` lists."""
    if isinstance(raw_data, dict):
        return raw_data
    if isinstance(raw_data, list):
        merged: dict[str, list[Any]] = {"dtTransaction": [], "dtSummary": []}
        for item in raw_data:
            if not isinstance(item, dict):
                continue
            merged["dtTransaction"].extend(item.get("dtTransaction") or [])
            merged["dtSummary"].extend(item.get("dtSummary") or [])
        return merged
    return {}


def _extract_holdings(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    meta_keys = ("pan", "pekrn", "email", "fromDate", "toDate", "reqId")
    meta = {k: payload.get(k, "") for k in meta_keys}
    data = _normalize_data_block(payload.get("data", payload))
    txn_rows = _as_row_list(data.get("dtTransaction"))
    sum_rows = _as_row_list(data.get("dtSummary"))
    investor = _flatten_investor_details(payload.get("investorDetails"))
    return meta, txn_rows, sum_rows, investor


def combined_rows_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """One list: meta row, then investor row, then each transaction, then each summary."""
    meta, txn_rows, sum_rows, investor = _extract_holdings(payload)
    rows: list[dict[str, Any]] = [{"segment": "meta", **meta}]
    if investor:
        rows.append({"segment": "investor", **meta, **investor})
    for t in txn_rows:
        rows.append({"segment": "transaction", **meta, **t})
    for s in sum_rows:
        rows.append({"segment": "summary", **meta, **s})
    return rows


def split_payload(
    payload: Any,
    stem: str,
    out_dir: Path,
    *,
    append: bool = False,
) -> tuple[Path, Path, Path, Path, int, int]:
    if not isinstance(payload, dict):
        raise ValueError("Root JSON must be an object")

    meta, txn_rows, sum_rows, investor = _extract_holdings(payload)

    meta_path = out_dir / f"{stem}_meta.csv"
    inv_path = out_dir / f"{stem}_investor.csv"
    txn_path = out_dir / f"{stem}_transactions.csv"
    sum_path = out_dir / f"{stem}_summary.csv"

    _write_csv(meta_path, [{**meta, **investor}], append=append)
    if investor:
        _write_csv(inv_path, [investor], append=append)
    _write_csv(txn_path, txn_rows, append=append)
    _write_csv(sum_path, sum_rows, append=append)

    return meta_path, txn_path, sum_path, inv_path, len(txn_rows), len(sum_rows)


def _resolve_input_files(args: argparse.Namespace) -> list[Path | None]:
    """Return a list of Path objects (or None for stdin) from CLI args."""
    files: list[Path | None] = []
    if args.input_dir:
        d = Path(args.input_dir).expanduser().resolve()
        if not d.is_dir():
            print(f"Not a directory: {d}", file=sys.stderr)
            return []
        files = sorted(d.glob("*.json"))
    elif args.input_json:
        for f in args.input_json:
            if f == "-":
                files.append(None)
            else:
                files.append(Path(f).expanduser().resolve())
    return files


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Split AA MF holdings JSON into meta CSV + transactions + summary CSV",
    )
    ap.add_argument(
        "input_json",
        nargs="*",
        help="Path(s) to JSON file(s), or '-' for stdin",
    )
    ap.add_argument(
        "--input-dir",
        type=str,
        default=None,
        help="Directory containing JSON files to process (all *.json files)",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: same folder as input file, or cwd for stdin)",
    )
    ap.add_argument(
        "--stem",
        default=None,
        help="Base name for output files (default: input filename without .json)",
    )
    ap.add_argument(
        "--all-in-one",
        action="store_true",
        help="Write a single <stem>_all.csv (segment: meta | transaction | summary)",
    )
    args = ap.parse_args()

    input_files = _resolve_input_files(args)
    if not input_files:
        ap.error("No input files specified. Provide file paths or --input-dir.")

    batch_mode = len(input_files) > 1
    total_txn = 0
    total_sum = 0

    for idx, in_path in enumerate(input_files):
        append = batch_mode and idx > 0

        if in_path is None:
            payload = _load_json(None)
            stem = args.stem or "aa_mf_holdings"
            out_dir = (args.out_dir or Path.cwd()).resolve()
        else:
            payload = _load_json(in_path)
            stem = args.stem or (input_files[0].stem if batch_mode else in_path.stem)
            out_dir = (args.out_dir or in_path.parent).resolve()

        if not isinstance(payload, dict):
            print(f"Skipping {in_path}: root JSON is not an object", file=sys.stderr)
            continue

        source = in_path.name if in_path else "<stdin>"

        if args.all_in_one:
            rows = combined_rows_from_payload(payload)
            all_path = out_dir / f"{stem}_all.csv"
            _write_csv(
                all_path,
                rows,
                priority_columns=("segment", "pan", "fromDate", "toDate"),
                append=append,
            )
            print(f"[{source}] Appended {len(rows)} rows -> {all_path}") if append else print(
                f"[{source}] Wrote {all_path}  ({len(rows)} rows incl. 1 meta)")
        else:
            meta_path, txn_path, summ_path, inv_path, n_txn, n_sum = split_payload(
                payload, stem, out_dir, append=append,
            )
            total_txn += n_txn
            total_sum += n_sum
            action = "Appended" if append else "Wrote"
            print(f"[{source}] {action}: {n_txn} transactions, {n_sum} summary rows")

    if not args.all_in_one and batch_mode:
        print(f"\nDone. Totals: {total_txn} transactions, {total_sum} summary rows across {len(input_files)} files.")
        first_stem = args.stem or input_files[0].stem
        final_dir = (args.out_dir or input_files[0].parent).resolve()
        print(f"Output files in {final_dir}/:")
        for suffix in ("_meta.csv", "_investor.csv", "_transactions.csv", "_summary.csv"):
            p = final_dir / f"{first_stem}{suffix}"
            if p.exists():
                print(f"  {p.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
