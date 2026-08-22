"""
Step 5 — documentation validation and requirements mapping.

Two separate jobs live here:
  1. Sanity-check what's actually on file for a payee (right form for the
     payee type, not expired) — this catches data-entry problems before
     they silently produce a wrong rate.
  2. Say what documentation *should* be requested for a given regime, so a
     reviewer (or the vendor-onboarding workflow upstream) knows what to
     chase down.
"""

from __future__ import annotations

from datetime import date

from .models import DocForm, Payee, PayeeType, PaymentType, VALID_FORMS_BY_PAYEE_TYPE
from .regime import (
    ECI_SELF_REPORTED,
    FDAP,
    FIRPTA,
    PARTNERSHIP_ECI_1446,
    PARTNERSHIP_ECI_1446F,
    PAYROLL_NOT_FDAP,
    SCHOLARSHIP_871C,
)


def validate_on_file(payee: Payee, as_of: date) -> list[str]:
    """Returns a list of human-readable warnings, empty if nothing's wrong."""

    warnings: list[str] = []
    doc = payee.documentation

    if doc.is_present():
        valid_forms = VALID_FORMS_BY_PAYEE_TYPE.get(payee.payee_type, set())
        if doc.form not in valid_forms:
            warnings.append(
                f"{doc.form.value} is not a typical form for payee type "
                f"{payee.payee_type.value} — double-check this wasn't a "
                "data-entry mismatch before relying on it."
            )
        if doc.is_expired(as_of):
            warnings.append(f"Documentation on file ({doc.form.value}) expired {doc.expiration_date}.")
        if doc.form in (DocForm.W8BEN, DocForm.W8BEN_E) and doc.treaty_country_claimed and not doc.treaty_article:
            warnings.append("Treaty benefits claimed but no treaty article recorded on file.")

    return warnings


def documentation_required_for(regime: str, payment_type: PaymentType, payee: Payee) -> list[DocForm]:
    """What should be on file to support this determination, for the reviewer's checklist."""

    if regime == FDAP:
        if payee.payee_type == PayeeType.GOVERNMENT:
            return [DocForm.W8EXP]
        if payee.payee_type == PayeeType.INDIVIDUAL:
            return [DocForm.W8BEN]
        return [DocForm.W8BEN_E]
    if regime == ECI_SELF_REPORTED:
        return [DocForm.W8ECI]
    if regime == PAYROLL_NOT_FDAP:
        return [DocForm.FORM_8233]
    if regime == SCHOLARSHIP_871C:
        return [DocForm.W8BEN]
    if regime == FIRPTA:
        return []  # affidavits/certifications, not W-8 forms — handled via RealPropertyFacts
    if regime in (PARTNERSHIP_ECI_1446, PARTNERSHIP_ECI_1446F):
        return [DocForm.W8IMY, DocForm.W8BEN_E]
    return []
