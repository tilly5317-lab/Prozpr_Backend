"""Send early-access ticket mails, by hand, in a batch you choose.

The signup endpoint deliberately mails nobody (see the early_access router), so
this script is the ONLY thing that sends an applicant a ticket. That is the
point: a form being tested, spammed or replayed cannot reach a real inbox, and
mail goes out when the team decides it goes out.

**It will not send anything unless you pass --send.** Without that flag it
prints what it would do and exits, so the natural thing to type is also the
safe one.

Two ways to name recipients:

    # One person
    python scripts/send_early_access_mails.py --to a@example.com \\
        --name "Ananya Rao" --seat 3

    # A batch, from the Sheet: File > Download > CSV, then
    python scripts/send_early_access_mails.py --csv entries.csv

The CSV is the register's own export, so the columns it reads are the ones the
Apps Script writes: Name, Email, Seat. Rows without an email are skipped and
counted, never guessed at.

Preview before you send — this writes the exact HTML that would go out:

    python scripts/send_early_access_mails.py --to a@example.com \\
        --name "Ananya Rao" --seat 3 --preview ticket.html

Seats past the cap are sent the standby ticket automatically; pass --waitlist
to force it for a seat inside the cap.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import pathlib
import sys

# The script sits in scripts/, so the package root is its parent.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.domains.early_access.services.early_access_email_service import (  # noqa: E402
    _render,
    send_early_access_confirmation,
)


class Recipient:
    """One person to write to. `seat` decides which ticket they get."""

    def __init__(self, email: str, name: str, seat: int, waitlisted: bool = False):
        self.email = email
        self.name = name
        self.seat = seat
        self.waitlisted = waitlisted


def _read_csv(path: pathlib.Path, seats_total: int, force_waitlist: bool):
    """Read the Sheet's own CSV export. Tolerates the column names the Apps
    Script writes, in any order and any case."""
    recipients: list[Recipient] = []
    skipped = 0
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            lower = {
                (k or "").strip().lower(): (v or "").strip() for k, v in row.items()
            }
            email = lower.get("email", "")
            if not email or "@" not in email:
                skipped += 1
                continue
            try:
                seat = int(lower.get("seat") or 0)
            except ValueError:
                seat = 0
            recipients.append(
                Recipient(
                    email=email,
                    name=lower.get("name", ""),
                    seat=seat,
                    waitlisted=force_waitlist or (seat > seats_total > 0),
                )
            )
    return recipients, skipped


async def _run(args: argparse.Namespace) -> int:
    seats_total = args.seats_total or get_settings().get_early_access_seats()

    if args.csv:
        recipients, skipped = _read_csv(
            pathlib.Path(args.csv), seats_total, args.waitlist
        )
        if skipped:
            print(f"  skipped {skipped} row(s) with no usable email")
    else:
        recipients = [
            Recipient(
                email=args.to,
                name=args.name or "",
                seat=args.seat,
                waitlisted=args.waitlist or (args.seat > seats_total > 0),
            )
        ]

    if not recipients:
        print("Nothing to send.")
        return 1

    if args.preview:
        r = recipients[0]
        _, _, html_body = _render(
            full_name=r.name,
            seat=r.seat,
            seats_total=seats_total,
            waitlisted=r.waitlisted,
        )
        out = pathlib.Path(args.preview)
        out.write_text(html_body, encoding="utf-8")
        print(f"Preview written to {out.resolve()}")
        return 0

    kind = "STANDBY" if any(r.waitlisted for r in recipients) else "seat"
    print(
        f"{len(recipients)} recipient(s), {seats_total} seats, "
        f"{'DRY RUN - nothing will be sent' if not args.send else 'SENDING'} ({kind})"
    )

    sent = failed = 0
    for r in recipients:
        label = f"  seat {r.seat:>3}  {r.email}" + (
            "  [standby]" if r.waitlisted else ""
        )
        if not args.send:
            print(f"{label}   (dry run)")
            continue
        ok = await send_early_access_confirmation(
            to_email=r.email,
            full_name=r.name,
            seat=r.seat,
            seats_total=seats_total,
            waitlisted=r.waitlisted,
        )
        # One failure must not abandon the rest of the list.
        sent, failed = (sent + 1, failed) if ok else (sent, failed + 1)
        print(f"{label}   {'sent' if ok else 'FAILED - see the log above'}")

    if not args.send:
        print("\nNothing was sent. Re-run with --send to actually mail these people.")
        return 0
    print(f"\nSent {sent}, failed {failed}.")
    return 1 if failed else 0


def main() -> int:
    # The service logs the reason a send failed (a rejected status, an
    # unverified sending domain) through `logging`. Without this the script
    # would print "FAILED" and swallow the one line that says why.
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    p = argparse.ArgumentParser(
        description="Send early-access ticket mails. Dry run unless --send.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--to", help="a single recipient's email address")
    src.add_argument("--csv", help="the Sheet's CSV export (Name, Email, Seat)")
    p.add_argument("--name", help="holder name printed on the ticket (with --to)")
    p.add_argument("--seat", type=int, default=1, help="seat number (with --to)")
    p.add_argument(
        "--seats-total",
        type=int,
        default=0,
        help="override the cap; defaults to EARLY_ACCESS_SEATS",
    )
    p.add_argument(
        "--waitlist",
        action="store_true",
        help="force the standby ticket even for a seat inside the cap",
    )
    p.add_argument("--preview", help="write the HTML to this file and send nothing")
    p.add_argument(
        "--send",
        action="store_true",
        help="actually send. Without this the script only prints what it would do.",
    )
    return asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
