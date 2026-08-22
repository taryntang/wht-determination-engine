"""
Step 4 — partnership ECI withholding (IRC 1446 and 1446(f)).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from .models import Determination, Payment, PayeeType
from .regime import PARTNERSHIP_ECI_1446, PARTNERSHIP_ECI_1446F

CORPORATE_PARTNER_RATE = Decimal("21")
# Top individual rate under IRC 1 — verify against the current rate schedule
# before relying on this; it's the kind of figure that changes with tax
# legislation and should live in reference data in a real deployment, not a
# code constant.
TOP_INDIVIDUAL_RATE = Decimal("37")
INTEREST_SALE_RATE = Decimal("10")


def determine_1446(payment: Payment, as_of: date) -> Determination:
    det = Determination(
        payment_id=payment.payment_id,
        regime=PARTNERSHIP_ECI_1446,
        withholding_required=True,
        rate=None,
        withholding_amount=None,
        citation="IRC 1446",
        rationale="",
    )
    det.add_event(
        "regime",
        "Routed to Section 1446 — foreign partner's distributive share of "
        "ECI (applies whether or not actually distributed).",
        "IRC 1446",
    )

    partnership = payment.partnership
    partner_type = partnership.partner_type if partnership else None

    if partner_type == PayeeType.CORPORATION:
        det.rate = CORPORATE_PARTNER_RATE
        det.add_event("rate_determination", "Corporate partner — 21% rate.", "IRC 1446; IRC 11(b)")
    elif partner_type in (PayeeType.INDIVIDUAL, None):
        det.rate = TOP_INDIVIDUAL_RATE
        det.flags.append("VERIFY_CURRENT_TOP_INDIVIDUAL_RATE")
        det.add_event(
            "rate_determination",
            "Individual (or unspecified) partner — withheld at the highest "
            "applicable rate for that category.",
            "IRC 1446; IRC 1",
        )
        if partner_type is None:
            det.flags.append("PARTNER_TYPE_NOT_SPECIFIED_DEFAULTED_TO_HIGHEST_INDIVIDUAL_RATE")
            det.confidence = "needs_review"
    else:
        det.rate = TOP_INDIVIDUAL_RATE
        det.flags.append("PARTNER_TYPE_NOT_MODELED_DEFAULTED_TO_HIGHEST_RATE")
        det.confidence = "needs_review"
        det.add_event("rate_determination", f"Partner type {partner_type} not specifically modeled — defaulted to highest rate as a conservative placeholder.")

    det.withholding_amount = (payment.gross_amount * det.rate / Decimal("100")).quantize(Decimal("0.01"))
    det.rationale = (
        "Withholding applies to the foreign partner's distributive share of "
        "ECI regardless of whether it's actually distributed. Both domestic "
        "and foreign partnerships must withhold. In tiered structures, the "
        "obligation can be pushed down to the lowest-tier partnership with "
        "proper documentation from the upper-tier partnership (tiering not "
        "modeled in this demo engine)."
    )
    if partnership and partnership.partnership_is_foreign:
        det.flags.append("TIERED_PARTNERSHIP_STRUCTURE_NOT_MODELED_IF_APPLICABLE")

    return det


def determine_1446f(payment: Payment, as_of: date) -> Determination:
    det = Determination(
        payment_id=payment.payment_id,
        regime=PARTNERSHIP_ECI_1446F,
        withholding_required=True,
        rate=None,
        withholding_amount=None,
        citation="IRC 1446(f)",
        rationale="",
    )
    det.add_event(
        "regime",
        "Routed to Section 1446(f) — sale of a partnership interest by a foreign partner; buyer (transferee) withholds.",
        "IRC 1446(f)",
    )

    partnership = payment.partnership
    if partnership is None:
        det.confidence = "needs_review"
        det.flags.append("MISSING_PARTNERSHIP_FACTS")
        det.rationale = "Routed to 1446(f) but no PartnershipFacts provided — cannot complete the analysis."
        return det

    if partnership.seller_certifies_non_foreign:
        det.withholding_required = False
        det.rate = Decimal("0")
        det.withholding_amount = Decimal("0")
        det.rationale = "Seller certifies non-foreign status via affidavit — 1446(f) withholding exception applies."
        det.add_event("exception", "Seller non-foreign-status certification on file.", "Reg. 1.1446(f)-2")
        return det

    if partnership.net_gain_eci_pct_of_total is not None and partnership.net_gain_eci_pct_of_total < Decimal("10"):
        det.withholding_required = False
        det.rate = Decimal("0")
        det.withholding_amount = Decimal("0")
        det.rationale = (
            f"Partnership's net gain that would be ECI is "
            f"{partnership.net_gain_eci_pct_of_total}% of total net gain — "
            "below the 10% de minimis threshold, so the 1446(f) exception applies."
        )
        det.add_event("exception", "Net-gain-ECI de minimis (<10%) exception applies.", "Reg. 1.1446(f)-2")
        return det

    det.rate = INTEREST_SALE_RATE
    det.withholding_amount = (payment.gross_amount * det.rate / Decimal("100")).quantize(Decimal("0.01"))
    det.rationale = (
        "No certification or de minimis exception on file. Buyer withholds "
        "10% of the amount realized on the sale. If the transferee fails to "
        "withhold, the partnership itself must withhold from future "
        "distributions to that transferee (IRC 1446(f)(4))."
    )
    det.add_event("rate_determination", "10% of amount realized.", "IRC 1446(f)")
    return det
