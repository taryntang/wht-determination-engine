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

This module always uses the Supabase service_role key — never VendorHub's
public anon key, which can only INSERT into vendor_tax_requests/vendor_accounts
and cannot SELECT at all, let alone read or write wht_determinations. It
reads vendor_tax_requests, and both reads and writes wht_determinations
(the engine's persisted output plus the tax reviewer's decision on it).
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from wht_engine import ENGINE_VERSION
from wht_engine.models import (
    Determination,
    Documentation,
    DocForm,
    Payee,
    PayeeType,
    Payment,
    PaymentType,
)

VENDOR_TAX_REQUESTS_TABLE = "vendor_tax_requests"
DETERMINATIONS_TABLE = "wht_determinations"


def _credentials(supabase_url: Optional[str], service_role_key: Optional[str]) -> tuple[str, str]:
    supabase_url = supabase_url or os.environ.get("SUPABASE_URL")
    service_role_key = service_role_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_role_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must both be set "
            "(env vars, or a local .env — see .env.example). The service_role "
            "key is a real secret: never commit it or pass it on the command line."
        )
    return supabase_url.rstrip("/"), service_role_key


def _supabase_request(
    method: str,
    path_and_query: str,
    supabase_url: Optional[str] = None,
    service_role_key: Optional[str] = None,
    body: Optional[Any] = None,
    prefer: Optional[str] = None,
) -> Any:
    """Shared PostgREST request helper. Always uses the service_role key —
    this module never touches VendorHub's public anon key, which cannot
    SELECT/UPDATE/INSERT outside its narrow public-facing insert policies.
    """
    base_url, service_role_key = _credentials(supabase_url, service_role_key)
    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{base_url}/rest/v1/{path_and_query}",
        method=method,
        headers=headers,
        data=data,
    )
    with urllib.request.urlopen(req) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None

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

    vendor_request_id: str  # vendor_tax_requests.id (uuid) -- the real FK target
    vendor_id: str  # vendor_number, for display
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
    return _supabase_request(
        "GET",
        f"{VENDOR_TAX_REQUESTS_TABLE}?select=*",
        supabase_url,
        service_role_key,
    )


def upsert_determination(
    candidate: "Candidate",
    determination: Optional[Determination],
    supabase_url: Optional[str] = None,
    service_role_key: Optional[str] = None,
) -> dict:
    """Persists one candidate's engine result (or skip reason) to
    wht_determinations, keyed on (vendor_request_id, category) so re-running
    the adapter updates the existing row instead of duplicating it.

    Re-running never touches a row once a human has reviewed it beyond
    resetting its engine-derived columns — review_status/override fields
    are only ever written by set_review_decision, never here.
    """
    if determination is None:
        row = {
            "vendor_request_id": candidate.vendor_request_id,
            "category": candidate.category_label,
            "confidence": "skipped",
            "notes": candidate.skip_reason,
        }
    else:
        row = {
            "vendor_request_id": candidate.vendor_request_id,
            "category": candidate.category_label,
            "regime": determination.regime,
            "withholding_required": determination.withholding_required,
            "rate": float(determination.rate) if determination.rate is not None else None,
            "citation": determination.citation,
            "rationale": determination.rationale,
            "confidence": determination.confidence,
            "flags": "; ".join(determination.flags) if determination.flags else None,
            "notes": " | ".join(candidate.notes) if candidate.notes else None,
            "engine_version": ENGINE_VERSION,
        }

    result = _supabase_request(
        "POST",
        f"{DETERMINATIONS_TABLE}?on_conflict=vendor_request_id,category",
        supabase_url,
        service_role_key,
        body=row,
        prefer="resolution=merge-duplicates,return=representation",
    )
    return result[0] if isinstance(result, list) else result


def fetch_determinations(
    supabase_url: Optional[str] = None,
    service_role_key: Optional[str] = None,
) -> list[dict]:
    """All determinations, joined with the originating vendor's name/number
    for display, newest first."""
    return _supabase_request(
        "GET",
        f"{DETERMINATIONS_TABLE}"
        "?select=*,vendor_tax_requests(legal_name,vendor_number)"
        "&order=created_at.desc",
        supabase_url,
        service_role_key,
    )


def set_review_decision(
    determination_id: str,
    review_status: str,
    reviewer_name: str,
    override_rate: Optional[float] = None,
    override_reasoning: Optional[str] = None,
    supabase_url: Optional[str] = None,
    service_role_key: Optional[str] = None,
) -> dict:
    """Records a tax reviewer's decision on one determination. review_status
    must be 'approved', 'overridden', or 'rejected' — the database itself
    enforces that 'overridden' carries both an override_rate and
    override_reasoning, and that 'rejected' carries a reasoning, via CHECK
    constraints (see the migration), so a bad call here fails loudly rather
    than silently persisting an unreasoned override.
    """
    if review_status not in ("approved", "overridden", "rejected"):
        raise ValueError(f"Invalid review_status: {review_status!r}")

    body = {
        "review_status": review_status,
        "reviewer_name": reviewer_name,
        "override_rate": override_rate,
        "override_reasoning": override_reasoning,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    result = _supabase_request(
        "PATCH",
        f"{DETERMINATIONS_TABLE}?id=eq.{determination_id}",
        supabase_url,
        service_role_key,
        body=body,
        prefer="return=representation",
    )
    return result[0] if isinstance(result, list) else result


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
    vendor_request_id = row["id"]
    vendor_id = row["vendor_number"]
    vendor_name = row["legal_name"]
    candidates: list[Candidate] = []

    def _candidate(**kwargs) -> Candidate:
        return Candidate(
            vendor_request_id=vendor_request_id,
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            **kwargs,
        )

    payee_type, payee_note = PAYEE_TYPE_MAP.get(
        row["entity_type"], (None, f"Unrecognized entity_type {row['entity_type']!r}.")
    )
    is_foreign = row["reg_country"] != "United States"
    documentation, doc_notes = _build_documentation(row)

    if payee_type is None:
        candidates.append(_candidate(category_label="(all categories)", skip_reason=payee_note))
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
                _candidate(
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
                _candidate(
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
        candidates.append(_candidate(category_label=label, payment=payment, notes=notes))

    if not candidates:
        candidates.append(
            _candidate(
                category_label="(none)",
                skip_reason="No category was marked U.S.-source or both — nothing to determine yet.",
            )
        )

    return candidates
