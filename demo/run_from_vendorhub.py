#!/usr/bin/env python3
"""
Runs real VendorHub intake submissions through the WHT determination engine,
in place of the synthetic CSV that demo/run_demo.py uses.

This is the "propose" half only, same as run_demo.py — no human-review
queue, no ERP push. See adapters/vendorhub.py for the mapping this relies
on and its documented gaps (no payment amounts, no treaty details, some
categories/entity types skipped rather than guessed).

Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (see .env.example).
The service_role key is a real secret — set it as an environment variable
or in a local, gitignored .env file. Never commit it or pass it as a
command-line argument.

Run: python3 demo/run_from_vendorhub.py
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.vendorhub import fetch_vendor_tax_requests, map_row_to_candidates
from wht_engine import determine_withholding

AS_OF = date.today()


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader so this stays dependency-free — does not override
    variables already set in the real environment."""
    import os

    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main():
    _load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    out_path = Path(__file__).resolve().parent / "output_vendorhub_determinations.csv"

    rows = fetch_vendor_tax_requests()
    if not rows:
        print("No vendor_tax_requests rows found.")
        return

    fieldnames = [
        "vendor_id",
        "vendor_name",
        "category",
        "regime",
        "withholding_required",
        "rate_pct",
        "citation",
        "confidence",
        "flags",
        "notes",
    ]
    out_rows = []
    n_determined = 0
    n_skipped = 0
    n_needs_review = 0

    for row in rows:
        for candidate in map_row_to_candidates(row):
            print(f"[{candidate.vendor_id}] {candidate.vendor_name} — {candidate.category_label}")

            if candidate.skip_reason:
                n_skipped += 1
                print(f"    ** SKIPPED (not run through engine) ** {candidate.skip_reason}")
                out_rows.append(
                    {
                        "vendor_id": candidate.vendor_id,
                        "vendor_name": candidate.vendor_name,
                        "category": candidate.category_label,
                        "regime": "",
                        "withholding_required": "",
                        "rate_pct": "",
                        "citation": "",
                        "confidence": "skipped",
                        "flags": "",
                        "notes": candidate.skip_reason,
                    }
                )
                print()
                continue

            det = determine_withholding(candidate.payment, as_of=AS_OF)
            n_determined += 1
            if det.confidence == "needs_review":
                n_needs_review += 1

            print(f"    regime={det.regime}  withhold={det.withholding_required}  rate={det.rate}%")
            print(f"    citation: {det.citation}")
            if det.confidence == "needs_review":
                print("    ** NEEDS REVIEW **")
            if det.flags:
                print(f"    flags: {', '.join(det.flags)}")
            for note in candidate.notes:
                print(f"    note: {note}")
            print()

            out_rows.append(
                {
                    "vendor_id": candidate.vendor_id,
                    "vendor_name": candidate.vendor_name,
                    "category": candidate.category_label,
                    "regime": det.regime,
                    "withholding_required": det.withholding_required,
                    "rate_pct": det.rate if det.rate is not None else "",
                    "citation": det.citation,
                    "confidence": det.confidence,
                    "flags": "; ".join(det.flags),
                    "notes": " | ".join(candidate.notes),
                }
            )

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    print("=" * 60)
    print(f"{len(rows)} vendor submissions processed.")
    print(f"{n_determined} candidate payments run through the engine ({n_needs_review} flagged for review).")
    print(f"{n_skipped} candidates skipped (unmapped category/entity type — see notes column).")
    print(f"Full results written to {out_path}")


if __name__ == "__main__":
    main()
