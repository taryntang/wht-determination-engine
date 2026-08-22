"""
Step 3 — FIRPTA analysis (IRC 1445, 897).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from .models import Determination, Payment
from .regime import FIRPTA

GENERAL_RATE = Decimal("15")
DOMESTIC_PARTNERSHIP_FOREIGN_PARTNER_GAIN_RATE = Decimal("21")


def determine(payment: Payment, as_of: date) -> Determination:
    det = Determination(
        payment_id=payment.payment_id,
        regime=FIRPTA,
        withholding_required=True,
        rate=None,
        withholding_amount=None,
        citation="IRC 1445",
        rationale="",
    )
    rp = payment.real_property
    det.add_event("regime", "Routed to FIRPTA — disposition of a U.S. real property interest by a foreign person.", "IRC 1445, 897")

    if rp is None or not rp.is_usrpi:
        det.confidence = "needs_review"
        det.flags.append("MISSING_USRPI_FACTS")
        det.rationale = (
            "Payment was routed to FIRPTA but no RealPropertyFacts (or "
            "is_usrpi=False) were provided. Cannot complete the analysis — "
            "flagged for manual review rather than guessed."
        )
        det.confidence = "needs_review"
        return det

    if rp.amount_realized is None:
        det.confidence = "needs_review"
        det.flags.append("MISSING_AMOUNT_REALIZED")
        det.rationale = "USRPI disposition confirmed but amount realized is missing — cannot compute the withholding amount."
        return det

    # --- Foreign transferor / exception checks ---
    if rp.seller_has_us_affidavit:
        det.withholding_required = False
        det.rate = Decimal("0")
        det.withholding_amount = Decimal("0")
        det.rationale = (
            "Seller provided a valid affidavit of U.S. status. No FIRPTA "
            "withholding required — even if the underlying entity is a "
            "partnership with foreign partners, since the partnership "
            "handles its own partners' withholding separately."
        )
        det.add_event("exception", "Seller U.S.-status affidavit on file.")
        return det

    if rp.corp_not_usrphc_affidavit:
        det.withholding_required = False
        det.rate = Decimal("0")
        det.withholding_amount = Decimal("0")
        det.rationale = (
            "Corporate affidavit on file stating the corporation is not, "
            "and has not been within the 5-year testing period, a U.S. "
            "real property holding corporation."
        )
        det.add_event("exception", "Corporate non-USRPHC affidavit on file.")
        return det

    if rp.is_personal_residence_le_300k:
        det.withholding_required = False
        det.rate = Decimal("0")
        det.withholding_amount = Decimal("0")
        det.rationale = "Personal-residence purchase exception applies — amount realized does not exceed $300,000."
        det.add_event("exception", "Personal residence <= $300,000 exception applies.")
        return det

    if rp.is_qualified_foreign_pension_fund:
        det.withholding_required = False
        det.rate = Decimal("0")
        det.withholding_amount = Decimal("0")
        det.citation = "IRC 897(l); Reg. 1.1445-8(e)"
        det.rationale = "Transferor certifies as a qualified foreign pension fund under Section 897(l)."
        det.add_event("exception", "Qualified foreign pension fund certification applies.", "IRC 897(l)")
        return det

    # --- Rate determination ---
    partnership = payment.partnership
    if partnership is not None and not partnership.partnership_is_foreign:
        det.rate = DOMESTIC_PARTNERSHIP_FOREIGN_PARTNER_GAIN_RATE
        det.citation = "IRC 1445(e)(1) [verify exact subsection before relying — see note]"
        det.rationale = (
            "Domestic partnership disposing of a USRPI, on gain allocable "
            "to foreign partners: 21% of that gain. NOTE: cross-check the "
            "precise IRC 1445(e) subsection cite against the current Code "
            "text before using this in a real work product — the source "
            "material behind this engine flagged some ambiguity here."
        )
        det.flags.append("VERIFY_1445(e)_SUBSECTION_CITE")
    else:
        det.rate = GENERAL_RATE
        det.citation = "IRC 1445(a)"
        det.rationale = "General rule: transferee withholds 15% of the total amount realized."

    det.withholding_amount = (rp.amount_realized * det.rate / Decimal("100")).quantize(Decimal("0.01"))
    det.withholding_required = True
    det.add_event("rate_determination", f"{det.rate}% of amount realized ({rp.amount_realized}).", det.citation)

    if partnership is not None:
        det.flags.append("CHECK_1446_OVERLAP_FOR_CREDIT")
        det.add_event(
            "overlap_check",
            "Partnership involved — if the same gain is also ECI subject to "
            "Section 1446 partnership withholding, 1446 controls and there "
            "is no double withholding; a credit applies for tax already "
            "withheld under 1445(a).",
            "Reg. 1.1446-3(c)(2)",
        )

    return det
