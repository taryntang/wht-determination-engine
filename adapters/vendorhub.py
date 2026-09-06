"""
Adapter: VendorHub intake submissions -> engine Payment/Payee candidates.

VendorHub (see https://github.com/taryntang/vendorhub) collects vendor-level
onboarding facts, not per-payment invoice data. That's a real gap against
what determine_withholding() needs:

  - No payment amount or a specific payment date is ever captured — this is
    an onboarding form, not an AP invoice feed. Every candidate Payment
    built here carries gross_amount=Decimal("0") and a flag saying so; rate
    and regime are still meaningful for review, the dollar amount is not.
  - The six sourcing questions (q1_goods..q5_royalties) describe categories
    of income the vendor *might* generate, not one specific payment. This
    adapter turns each category the vendor marked as U.S.-source ("A") or
    both ("C") into its own candidate Payment, so a vendor can produce zero,
    one, or several candidates.
  - No treaty country/article/expiration is captured (the form only records
    W-8 type + filename/size), so every candidate's Documentation carries a
    form but never a treaty claim — the engine will correctly (but
    misleadingly, absent this note) default to the 30% statutory rate
    rather than a treaty rate. Flagged explicitly below.
  - Some VendorHub categories/entity types don't correspond to any modeled
    PaymentType/PayeeType (goods sales, an "International organization" or
    "Other" entity type, a services fee for a non-individual payee, an
    unspecified royalty subtype). Rather than force a guess into the
    engine, those are returned as skipped candidates with a reason, for a
    human reviewer to classify.

This module only reads (via the Supabase service_role key — never the
public anon key, which cannot SELECT). It performs no writes.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from wht_engine.models import (
    Documentation,
    DocForm,
    Payee,
    PayeeType,
    Payment,
    PaymentType,
)

VENDOR_TAX_REQUESTS_TABLE = "vendor_tax_requests"

# entity_type (VendorHub) -> (engine PayeeType or None, note-if-uncertain-or-unmapped)
PAYEE_TYPE_MAP: dict[str, tuple[Optional[PayeeType], Optional[str]]] = {
    "Individual": (PayeeType.INDIVIDUAL, None),
    "Corporation": (PayeeType.CORPORATION, None),
    "Partnership": (PayeeType.PARTNERSHIP, None),
    "Trust or Estate": (PayeeType.TRUST, None),
    "Government": (PayeeType.GOVERNMENT, None),
    "LLC": (
        PayeeType.DISREGARDED_ENTITY,
        "LLC's check-the-box election (disregarded/partnership/corporation) "
        "is not captured by the intake form. Defaulted to disregarded-"
        "entity as the common single-member case — confirm the actual "
        "election before relying on this.",
    ),
    "LLP": (
        PayeeType.PARTNERSHIP,
        "Treated as a partnership for withholding purposes — confirm the "
        "entity's actual U.S. tax classification.",
    ),
    "International organization": (
        None,
        "No engine PayeeType models an international organization (a "
        "potential IRC 892 exemption candidate) — route to manual review.",
    ),
    "Other": (
        None,
        "Entity type recorded as 'Other' (see entity_type_other) — not "
        "modeled by the engine. Route to manual review.",
    ),
}

# VendorHub w8_type values line up with DocForm's string values directly for
# the four forms the intake form offers.
_W8_TYPE_TO_DOC_FORM = {
    "W-8BEN": DocForm.W8BEN,
    "W-8BEN-E": DocForm.W8BEN_E,
    "W-8ECI": DocForm.W8ECI,
    "W-8IMY": DocForm.W8IMY,
}

# Each VendorHub sourcing question that has a modeled engine PaymentType
# equivalent. q1_goods (sale of goods) has no entry: sale of goods is not
# FDAP income and isn't modeled by this engine at all.
_CATEGORY_QUESTIONS: list[tuple[str, str, Optional[PaymentType]]] = [
    ("q2_services", "services (Q2)", PaymentType.COMPENSATION_SERVICES),
    ("q3_rental", "movable-property rental (Q3)", PaymentType.RENT),
    ("q3b_real_property", "real-property rental (Q3b)", PaymentType.RENT),
    ("q4_server", "licensed-software fees (Q4)", PaymentType.ROYALTY_COPYRIGHT),
    ("q5_royalties", "patent/copyright royalties (Q5)", None),  # subtype ambiguous, see below
]

_US_SOURCE_ANSWERS = {"A", "C"}  # A = within U.S., C = both


@dataclass
class Candidate:
    """One candidate Payment derived from a single vendor + income category."""

    vendor_id: str
    vendor_name: str
    category_label: str
    payment: Optional[Payment] = None
    notes: list[str] = field(default_factory=list)
    skip_reason: Optional[str] = None


def fetch_vendor_tax_requests(
    supabase_url: Optional[str] = None,
    service_role_key: Optional[str] = None,
) -> list[dict]:
    """Reads every row of vendor_tax_requests via the Supabase REST API.

    Requires the service_role key: the public anon key embedded in
    VendorHub's HTML is insert-only and cannot SELECT (see VendorHub's
    SECURITY.md). Never hardcode the service_role key — pass it via
    environment variables (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY).
    """
    supabase_url = supabase_url or os.environ.get("SUPABASE_URL")
    service_role_key = service_role_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_role_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must both be set "
            "(env vars, or a local .env — see .env.example). The service_role "
            "key is a real secret: never commit it or pass it on the command line."
        )

    url = f"{supabase_url.rstrip('/')}/rest/v1/{VENDOR_TAX_REQUESTS_TABLE}?select=*"
    req = urllib.request.Request(
        url,
        headers={
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def _build_documentation(row: dict) -> tuple[Documentation, list[str]]:
    notes = []
    w8_type = row.get("w8_type")
    form = _W8_TYPE_TO_DOC_FORM.get(w8_type, DocForm.NONE)
    if form is not DocForm.NONE:
        notes.append(
            f"{form.value} on file per intake form, but VendorHub does not "
            "capture a treaty country/article or expiration date, so no "
            "treaty claim can be evaluated here even if one exists — the "
            "engine will fall back to the 30% statutory rate on that basis "
            "alone. Confirm the actual W-8 document for a treaty claim "
            "before relying on the statutory-rate result."
        )
    return Documentation(form=form), notes


def map_row_to_candidates(row: dict) -> list[Candidate]:
    """Vendor tax_requests row -> list of candidate Payments, one per
    U.S.-source-or-both income category the vendor flagged, plus any
    skipped (unmapped) candidates with a reason for a human reviewer.
    """
    vendor_id = row["vendor_number"]
    vendor_name = row["legal_name"]
    candidates: list[Candidate] = []

    payee_type, payee_note = PAYEE_TYPE_MAP.get(
        row["entity_type"], (None, f"Unrecognized entity_type {row['entity_type']!r}.")
    )
    is_foreign = row["reg_country"] != "United States"
    documentation, doc_notes = _build_documentation(row)

    if payee_type is None:
        candidates.append(
            Candidate(
                vendor_id=vendor_id,
                vendor_name=vendor_name,
                category_label="(all categories)",
                skip_reason=payee_note,
            )
        )
        return candidates

    payee = Payee(
        payee_id=vendor_id,
        name=vendor_name,
        payee_type=payee_type,
        country=row["reg_country"],
        is_foreign=is_foreign,
        documentation=documentation,
    )

    for column, label, payment_type in _CATEGORY_QUESTIONS:
        answer = row.get(column)
        if answer not in _US_SOURCE_ANSWERS:
            continue  # "B" (outside U.S.) or "D" (N/A) — no U.S.-source income in this category

        notes = list(doc_notes)
        if payee_note:
            notes.append(payee_note)

        # Q5 (patent/copyright royalties): the intake form doesn't ask which
        # of the two, and the engine's treaty table has different rates for
        # ROYALTY_PATENT vs ROYALTY_COPYRIGHT — guessing one would silently
        # hand a reviewer a specific-looking but unverified rate, so skip.
        if column == "q5_royalties":
            candidates.append(
                Candidate(
                    vendor_id=vendor_id,
                    vendor_name=vendor_name,
                    category_label=label,
                    skip_reason=(
                        "Royalty subtype (patent vs. copyright vs. know-how, "
                        "etc.) is not captured by the intake form and the "
                        "engine's treaty rates differ by subtype — classify "
                        "manually before running through the engine."
                    ),
                )
            )
            continue

        # Q2 (services): the engine only models personal-services
        # compensation, which routes to payroll/Form 8233, not FDAP. That's
        # a sound mapping for an individual payee but not for a business
        # entity performing services (generally an ECI/business-income
        # question the engine doesn't model) — skip rather than mislabel.
        if column == "q2_services" and payee_type != PayeeType.INDIVIDUAL:
            candidates.append(
                Candidate(
                    vendor_id=vendor_id,
                    vendor_name=vendor_name,
                    category_label=label,
                    skip_reason=(
                        "Services income for a non-individual payee isn't "
                        "modeled by this engine (compensation-services only "
                        "maps to personal-services/payroll routing) — likely "
                        "an ECI/business-income question instead. Route to "
                        "manual review."
                    ),
                )
            )
            continue

        assert payment_type is not None
        payment = Payment(
            payment_id=f"{vendor_id}:{column}",
            payee=payee,
            payment_type=payment_type,
            gross_amount=Decimal("0"),
            notes=(
                "gross_amount is a placeholder — VendorHub is an onboarding "
                "form and never captures an invoice/payment amount. Rate and "
                "regime are meaningful; withholding_amount is not, until a "
                "real payment amount is supplied."
            ),
        )
        notes.append(
            "AMOUNT_NOT_CAPTURED_BY_INTAKE_FORM: gross_amount defaulted to 0; "
            "withholding_amount below is not meaningful on its own."
        )
        candidates.append(
            Candidate(
                vendor_id=vendor_id,
                vendor_name=vendor_name,
                category_label=label,
                payment=payment,
                notes=notes,
            )
        )

    if not candidates:
        candidates.append(
            Candidate(
                vendor_id=vendor_id,
                vendor_name=vendor_name,
                category_label="(none)",
                skip_reason="No category was marked U.S.-source or both — nothing to determine yet.",
            )
        )

    return candidates
