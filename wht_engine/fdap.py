"""
Step 2 — FDAP withholding analysis (IRC 1441/1442).

By the time a payment reaches this module, regime.py has already confirmed
it's a foreign payee, U.S.-source-eligible, non-ECI, non-payroll, non-FIRPTA,
non-partnership FDAP-type payment. This module runs the remaining parts of
the six-factor test, the exception list, the documentation-driven rate
determination, and a (simplified) pass at intermediary handling.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from .models import (
    Determination,
    DocForm,
    IntermediaryType,
    Payment,
    PayeeType,
)
from .regime import FDAP
from .treaty_rates import TreatyRateTable, DEFAULT_TABLE

STATUTORY_RATE = Decimal("30")

_EXEMPT_PAYMENT_TYPES = {
    # I.R.C. 871(g) — short-term OID is handled at the payment-type level
    # upstream (treated as OTHER_FDAP with notes) since it depends on term,
    # not just type; portfolio interest and bank deposit interest are
    # modeled explicitly.
}


def determine(payment: Payment, as_of: date, treaty_table: TreatyRateTable = DEFAULT_TABLE) -> Determination:
    det = Determination(
        payment_id=payment.payment_id,
        regime=FDAP,
        withholding_required=True,
        rate=None,
        withholding_amount=None,
        citation="IRC 1441, 1442",
        rationale="",
        rate_table_version=None,
    )
    det.add_event("regime", "Routed to FDAP (Step 1 catch-all for passive U.S.-source income).", "IRC 1441, 1442")

    payee = payment.payee
    doc = payee.documentation

    # --- Factor 2: U.S.-source ---
    if not payment.is_us_source:
        det.withholding_required = False
        det.rate = Decimal("0")
        det.withholding_amount = Decimal("0")
        det.citation = "IRC 1441(a) (sourcing threshold)"
        det.rationale = (
            "Marked as non-U.S.-source. FDAP withholding under 1441/1442 only "
            "reaches U.S.-source income — confirm the sourcing conclusion "
            "before relying on this."
        )
        det.flags.append("CONFIRM_SOURCING")
        det.confidence = "needs_review"
        det.add_event("factor_2_us_source", "Payment marked non-U.S.-source; no 1441/1442 withholding on this basis.")
        return det
    det.add_event("factor_2_us_source", "Payment is U.S.-source. Factor satisfied.")

    # --- Exceptions: portfolio interest, bank deposit interest ---
    if payment.payment_type.value == "interest_portfolio":
        det.withholding_required = False
        det.rate = Decimal("0")
        det.withholding_amount = Decimal("0")
        det.citation = "IRC 871(h)/881(c) — portfolio interest exception"
        det.rationale = "Portfolio interest is exempt from FDAP withholding."
        det.add_event("exception", "Portfolio interest exception applies.", "IRC 871(h)/881(c)")
        return det
    if payment.payment_type.value == "interest_bank_deposit":
        det.withholding_required = False
        det.rate = Decimal("0")
        det.withholding_amount = Decimal("0")
        det.citation = "IRC 871(i) — bank deposit interest exception"
        det.rationale = "Bank deposit interest paid to a foreign person is exempt from FDAP withholding."
        det.add_event("exception", "Bank deposit interest exception applies.", "IRC 871(i)")
        return det

    # --- Intermediary handling (simplified) ---
    if payee.intermediary == IntermediaryType.QI_PWR:
        det.withholding_required = False
        det.rationale = (
            "Payment is to a Qualified Intermediary that has assumed primary "
            "withholding responsibility (PWR). The original withholding "
            "agent does not withhold — the QI does, downstream."
        )
        det.flags.append("QI_HAS_PRIMARY_WITHHOLDING_RESPONSIBILITY")
        det.add_event("intermediary", "QI with PWR — withholding obligation shifts to the QI.")
        return det
    if payee.intermediary == IntermediaryType.WFP:
        det.withholding_required = False
        det.rationale = (
            "Payment is to a Withholding Foreign Partnership with an IRS "
            "agreement to assume withholding responsibility. Treated like a "
            "payment to a domestic partnership at this point — no "
            "withholding here; the WFP withholds later per domestic-"
            "partnership timing rules."
        )
        det.flags.append("WFP_ASSUMES_WITHHOLDING")
        det.add_event("intermediary", "Withholding foreign partnership — obligation shifts to the WFP.")
        return det
    if payee.intermediary == IntermediaryType.US_BRANCH_ELECTION:
        det.withholding_required = False
        det.rationale = (
            "Payee is a U.S. branch of a foreign bank/insurer that may have "
            "elected U.S.-person treatment for withholding purposes. "
            "Confirm the election/agreement is on file before relying on "
            "this — not independently verified by this engine."
        )
        det.flags.append("CONFIRM_US_BRANCH_ELECTION_ON_FILE")
        det.confidence = "needs_review"
        det.add_event("intermediary", "U.S. branch election claimed — flagged for confirmation, not verified.")
        return det
    if payee.intermediary == IntermediaryType.DOMESTIC_PARTNERSHIP:
        det.withholding_required = False
        det.rationale = (
            "Payment is to a domestic partnership. No 1441 withholding on "
            "the payment to the partnership itself, even though some "
            "partners may be foreign — the domestic partnership becomes the "
            "withholding agent for its foreign partners' shares (see the "
            "partnership_eci module for that downstream obligation)."
        )
        det.flags.append("DOMESTIC_PARTNERSHIP_IS_DOWNSTREAM_WITHHOLDING_AGENT")
        det.add_event("intermediary", "Domestic partnership — withholding obligation shifts downstream to the partnership.")
        return det
    if payee.intermediary in (IntermediaryType.FOREIGN_PARTNERSHIP, IntermediaryType.NQI):
        # Per the skill: if partner-level/beneficial-owner information is
        # incomplete, withhold at the highest applicable rate on the
        # unaccounted-for portion rather than guess. This demo engine does
        # not implement full per-partner look-through, so it conservatively
        # withholds at the statutory rate and flags for a real look-through.
        det.rate = STATUTORY_RATE
        det.withholding_amount = (payment.gross_amount * det.rate / Decimal("100")).quantize(Decimal("0.01"))
        det.citation = "IRC 1441(a)/1442(a) (conservative default pending look-through)"
        det.rationale = (
            f"Payee is a {'nonqualified intermediary' if payee.intermediary == IntermediaryType.NQI else 'foreign partnership (not a WFP)'}. "
            "Full look-through to underlying beneficial owners is not "
            "modeled in this demo engine — withheld conservatively at the "
            "full statutory rate on the unaccounted-for portion, per the "
            "same rule a human preparer would apply. Route to manual "
            "look-through before relying on this for a real payment."
        )
        det.flags.append("INTERMEDIARY_LOOK_THROUGH_NOT_MODELED")
        det.confidence = "needs_review"
        det.add_event(
            "intermediary",
            "NQI/foreign-partnership without modeled look-through — conservative statutory rate applied.",
        )
        return det

    # --- Documentation-driven rate determination ---
    if doc.form == DocForm.W8ECI:
        # Should have been routed to ECI_SELF_REPORTED upstream if is_eci
        # was set correctly — this is a consistency check, not a guess.
        det.withholding_required = False
        det.rate = Decimal("0")
        det.withholding_amount = Decimal("0")
        det.citation = "Reg. 1.1441-2(a)"
        det.rationale = (
            "W-8ECI is on file, indicating this income is effectively "
            "connected income the recipient self-reports. NOTE: this "
            "payment reached the FDAP module rather than being routed to "
            "ECI_SELF_REPORTED upstream — the payment's is_eci flag may be "
            "inconsistent with its documentation. Flagged for review."
        )
        det.flags.append("INCONSISTENT_ECI_FLAG_VS_DOCUMENTATION")
        det.confidence = "needs_review"
        det.add_event("documentation", "W-8ECI on file but is_eci flag was False upstream — inconsistency flagged.")
        return det

    if doc.form == DocForm.W8EXP and payee.payee_type == PayeeType.GOVERNMENT:
        det.withholding_required = False
        det.rate = Decimal("0")
        det.withholding_amount = Decimal("0")
        det.citation = "IRC 892"
        det.rationale = (
            "Foreign government/controlled entity with a valid W-8EXP on "
            "file may be exempt under Section 892 for this FDAP income. "
            "Confirm the specific income qualifies (892 has its own "
            "exceptions, e.g. for commercial activity income) before "
            "relying on this."
        )
        det.flags.append("CONFIRM_892_EXEMPTION_SCOPE")
        det.confidence = "needs_review"
        det.add_event("documentation", "W-8EXP on file for a government payee — 892 exemption applied, scope not independently verified.")
        return det

    treaty_claimed = doc.form in (DocForm.W8BEN, DocForm.W8BEN_E) and doc.treaty_country_claimed
    doc_expired = doc.is_expired(as_of)

    if treaty_claimed and not doc_expired:
        result = treaty_table.lookup(doc.treaty_country_claimed, payment.payment_type)
        det.rate_table_version = result.table_version
        if result.found:
            det.rate = result.rate
            det.withholding_amount = (payment.gross_amount * det.rate / Decimal("100")).quantize(Decimal("0.01"))
            det.withholding_required = det.rate > 0
            det.citation = f"Treaty rate — {doc.treaty_country_claimed}, {result.treaty_article or 'article not on file'}"
            det.rationale = (
                f"Valid {doc.form.value} on file claiming treaty benefits for "
                f"{doc.treaty_country_claimed}. Reduced rate of {result.rate}% "
                f"applied per the reference treaty rate table "
                f"(version {result.table_version})."
            )
            det.add_event(
                "treaty_rate_lookup",
                f"Found {result.rate}% for {doc.treaty_country_claimed}/{payment.payment_type.value} "
                f"in rate table {result.table_version}.",
                result.treaty_article,
            )
            if result.footnote:
                det.flags.append(f"TREATY_FOOTNOTE: {result.footnote}")
            det.flags.append("CONFIRM_LOB_TABLE_4")
            if payee.payee_type != PayeeType.INDIVIDUAL:
                det.flags.append("LOB_CONFIRMATION_ESPECIALLY_IMPORTANT_FOR_ENTITY_PAYEES")
        else:
            det.rate = STATUTORY_RATE
            det.withholding_amount = (payment.gross_amount * det.rate / Decimal("100")).quantize(Decimal("0.01"))
            det.withholding_required = True
            det.citation = "IRC 1441(a)/1442(a) (treaty claimed but not found in reference table)"
            det.rationale = (
                f"Treaty benefits claimed for {doc.treaty_country_claimed} / "
                f"{payment.payment_type.value}, but no matching row exists in "
                "the reference rate table. Defaulted to the 30% statutory "
                "rate rather than guess — refresh the rate table from the "
                "live IRS Table 1 and re-run."
            )
            det.flags.append("TREATY_RATE_NOT_IN_TABLE")
            det.confidence = "needs_review"
            det.add_event("treaty_rate_lookup", "No match in reference rate table for claimed country/income type.")
        return det

    if treaty_claimed and doc_expired:
        det.rate = STATUTORY_RATE
        det.withholding_amount = (payment.gross_amount * det.rate / Decimal("100")).quantize(Decimal("0.01"))
        det.withholding_required = True
        det.citation = "IRC 1441(a)/1442(a) (documentation expired)"
        det.rationale = (
            f"{doc.form.value} on file claims a treaty rate but expired on "
            f"{doc.expiration_date}. Treated as no valid documentation — "
            "withheld at the 30% statutory default."
        )
        det.flags.append("DOCUMENTATION_EXPIRED")
        det.confidence = "needs_review"
        det.add_event("documentation", f"Treaty-claiming form expired {doc.expiration_date} — treated as no valid documentation.")
        return det

    # No documentation, or documentation present but not a treaty claim.
    det.rate = STATUTORY_RATE
    det.withholding_amount = (payment.gross_amount * det.rate / Decimal("100")).quantize(Decimal("0.01"))
    det.withholding_required = True
    det.citation = "IRC 1441(a), 1442(a)"
    det.rationale = (
        "No valid treaty-claiming documentation on file. Default statutory "
        "rate of 30% applied to the gross amount."
    )
    det.add_event("documentation", "No valid treaty documentation on file — statutory 30% default applied.")
    return det
