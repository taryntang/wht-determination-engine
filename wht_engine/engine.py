"""
The orchestrator: determine_withholding() ties Steps 1-6 together into one
entrypoint and produces a Determination carrying a full audit trail.

This is the "single source of truth" a review UI or an ERP push would call.
It is deliberately NOT where any LLM/agent call happens — that layer (W-8
document extraction, plain-English rationale generation for a reviewer,
flagging ambiguous cases) sits on top of this, calling this engine as the
deterministic core it explains and defends. See the architecture notes in
the project doc for why that split matters.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from . import firpta, fdap, partnership_eci
from .documentation import documentation_required_for, validate_on_file
from .models import Determination, Payment
from .regime import (
    ECI_SELF_REPORTED,
    FDAP,
    FIRPTA,
    NOT_FOREIGN_OUT_OF_SCOPE,
    PARTNERSHIP_ECI_1446,
    PARTNERSHIP_ECI_1446F,
    PAYROLL_NOT_FDAP,
    SCHOLARSHIP_871C,
    classify_regime,
)
from .treaty_rates import DEFAULT_TABLE, TreatyRateTable

ENGINE_VERSION = "wht-engine-demo-0.1.0"

SCHOLARSHIP_RATE = Decimal("14")


def determine_withholding(
    payment: Payment,
    as_of: date | None = None,
    treaty_table: TreatyRateTable = DEFAULT_TABLE,
) -> Determination:
    as_of = as_of or payment.payment_date

    regime, citation, rationale = classify_regime(payment)

    if regime == NOT_FOREIGN_OUT_OF_SCOPE:
        det = Determination(
            payment_id=payment.payment_id,
            regime=regime,
            withholding_required=False,
            rate=None,
            withholding_amount=None,
            citation=citation,
            rationale=rationale,
            flags=["OUT_OF_SCOPE_BACKUP_WITHHOLDING_NOT_MODELED"],
            confidence="needs_review",
        )
        det.add_event("regime", rationale, citation)
    elif regime == PAYROLL_NOT_FDAP:
        det = Determination(
            payment_id=payment.payment_id,
            regime=regime,
            withholding_required=False,
            rate=None,
            withholding_amount=None,
            citation=citation,
            rationale=rationale,
            flags=["ROUTE_TO_PAYROLL_WITHHOLDING"],
        )
        det.add_event("regime", rationale, citation)
    elif regime == SCHOLARSHIP_871C:
        amount = (payment.gross_amount * SCHOLARSHIP_RATE / Decimal("100")).quantize(Decimal("0.01"))
        det = Determination(
            payment_id=payment.payment_id,
            regime=regime,
            withholding_required=True,
            rate=SCHOLARSHIP_RATE,
            withholding_amount=amount,
            citation=citation,
            rationale=rationale + " (Unless excluded from income under Section 117 — not independently verified here.)",
            flags=["CONFIRM_SECTION_117_EXCLUSION_NOT_APPLICABLE"],
        )
        det.add_event("regime", rationale, citation)
    elif regime == ECI_SELF_REPORTED:
        det = Determination(
            payment_id=payment.payment_id,
            regime=regime,
            withholding_required=False,
            rate=Decimal("0"),
            withholding_amount=Decimal("0"),
            citation=citation,
            rationale=rationale,
        )
        det.add_event("regime", rationale, citation)
    elif regime == FIRPTA:
        det = firpta.determine(payment, as_of)
    elif regime == FDAP:
        det = fdap.determine(payment, as_of, treaty_table)
    elif regime == PARTNERSHIP_ECI_1446:
        det = partnership_eci.determine_1446(payment, as_of)
    elif regime == PARTNERSHIP_ECI_1446F:
        det = partnership_eci.determine_1446f(payment, as_of)
    else:  # NEEDS_MANUAL_REVIEW or anything unmodeled
        det = Determination(
            payment_id=payment.payment_id,
            regime="NEEDS_MANUAL_REVIEW",
            withholding_required=True,  # conservative default — don't assume no withholding
            rate=None,
            withholding_amount=None,
            citation=citation,
            rationale=rationale,
            flags=["UNMODELED_PAYMENT_TYPE"],
            confidence="needs_review",
        )
        det.add_event("regime", rationale, citation)

    # Documentation validation + requirements, applied uniformly regardless
    # of which regime handled the rate math.
    doc_warnings = validate_on_file(payment.payee, as_of)
    for w in doc_warnings:
        det.flags.append(f"DOC_WARNING: {w}")
        det.add_event("documentation_validation", w)
    det.documentation_required = documentation_required_for(det.regime, payment.payment_type, payment.payee)

    det.add_event("engine", f"Determination completed by {ENGINE_VERSION}.")
    return det
