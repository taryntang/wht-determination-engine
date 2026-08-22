#!/usr/bin/env python3
"""
Demo runner: simulates a "vendor hub" feed (synthetic_vendors.csv) flowing
into the WHT determination engine, the way the real architecture envisions
it — vendor hub already holds the tax data, the engine proposes a
determination, and (in a real deployment) a tax reviewer approves before
anything is pushed to AP/Oracle.

This script is the "propose" half only. It does not implement the
human-review queue or the Oracle push — see the README for where those
would plug in.

Run: python3 demo/run_demo.py
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wht_engine import determine_withholding
from wht_engine.models import (
    Documentation,
    DocForm,
    IntermediaryType,
    Payee,
    PayeeType,
    Payment,
    PaymentType,
    PartnershipFacts,
    RealPropertyFacts,
)

AS_OF = date(2026, 8, 22)

_TRUE = {"true", "1", "yes"}


def _parse_date(s: str):
    return date.fromisoformat(s) if s else None


def load_payments(csv_path: Path) -> list[Payment]:
    payments = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            doc = Documentation(
                form=DocForm(row["doc_form"]) if row["doc_form"] else DocForm.NONE,
                treaty_country_claimed=row["treaty_country_claimed"] or None,
                treaty_article=row["treaty_article"] or None,
                expiration_date=_parse_date(row["doc_expiration"]),
            )
            payee = Payee(
                payee_id=row["vendor_id"],
                name=row["vendor_name"],
                payee_type=PayeeType(row["payee_type"]),
                country=row["country"],
                is_foreign=row["is_foreign"].strip().lower() in _TRUE,
                documentation=doc,
                intermediary=IntermediaryType(row["intermediary_type"]),
            )

            payment_type = PaymentType(row["payment_type"])
            gross_amount = Decimal(row["gross_amount"])

            # These transaction-specific facts (USRPI status, partner type,
            # certifications) live with the specific deal, not the vendor
            # master record — a real vendor-hub integration would pull them
            # from the AP invoice / transaction context, not this file. For
            # the demo we derive reasonable illustrative values so every
            # regime in the engine gets exercised end to end.
            real_property = None
            partnership = None
            if payment_type == PaymentType.REAL_PROPERTY_SALE:
                real_property = RealPropertyFacts(is_usrpi=True, amount_realized=gross_amount)
            if payment_type in (
                PaymentType.PARTNERSHIP_DISTRIBUTIVE_SHARE_ECI,
                PaymentType.PARTNERSHIP_DISTRIBUTION,
            ):
                partnership = PartnershipFacts(partner_type=PayeeType.CORPORATION)
            if payment_type == PaymentType.PARTNERSHIP_INTEREST_SALE:
                partnership = PartnershipFacts()

            payments.append(
                Payment(
                    payment_id=row["vendor_id"],
                    payee=payee,
                    payment_type=payment_type,
                    gross_amount=gross_amount,
                    is_eci=row["is_eci"].strip().lower() in _TRUE,
                    payment_date=AS_OF,
                    real_property=real_property,
                    partnership=partnership,
                )
            )
    return payments


def main():
    csv_path = Path(__file__).resolve().parent / "synthetic_vendors.csv"
    out_path = Path(__file__).resolve().parent / "output_determinations.csv"

    payments = load_payments(csv_path)

    fieldnames = [
        "vendor_id",
        "vendor_name",
        "regime",
        "withholding_required",
        "rate_pct",
        "withholding_amount",
        "gross_amount",
        "citation",
        "confidence",
        "flags",
        "documentation_required",
    ]

    rows = []
    for payment in payments:
        det = determine_withholding(payment, as_of=AS_OF)
        rows.append(
            {
                "vendor_id": payment.payee.payee_id,
                "vendor_name": payment.payee.name,
                "regime": det.regime,
                "withholding_required": det.withholding_required,
                "rate_pct": det.rate if det.rate is not None else "",
                "withholding_amount": det.withholding_amount if det.withholding_amount is not None else "",
                "gross_amount": payment.gross_amount,
                "citation": det.citation,
                "confidence": det.confidence,
                "flags": "; ".join(det.flags),
                "documentation_required": "; ".join(f.value for f in det.documentation_required),
            }
        )

        # Console summary — this is roughly what a reviewer queue row would show.
        print(f"[{payment.payee.payee_id}] {payment.payee.name}")
        print(f"    regime={det.regime}  withhold={det.withholding_required}  rate={det.rate}%  amount={det.withholding_amount}")
        print(f"    citation: {det.citation}")
        if det.confidence == "needs_review":
            print("    ** NEEDS REVIEW **")
        if det.flags:
            print(f"    flags: {', '.join(det.flags)}")
        print()

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    needs_review = sum(1 for r in rows if r["confidence"] == "needs_review")
    total_withheld = sum(Decimal(str(r["withholding_amount"])) for r in rows if r["withholding_amount"] != "")

    print("=" * 60)
    print(f"{len(rows)} payments processed, {needs_review} flagged for review.")
    print(f"Total proposed withholding across all payments: {total_withheld}")
    print(f"Full results written to {out_path}")


if __name__ == "__main__":
    main()
