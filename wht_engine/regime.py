"""
Step 1 — regime classification.

Routes a Payment to the regime that governs it, in the order specified by
the underlying tax analysis: the FIRST matching rule controls. This
ordering matters — e.g. a royalty payment is normally FDAP, but if it's
actually part of a partnership's ECI it's governed by Section 1446 instead,
and that check has to happen before the FDAP catch-all.
"""

from __future__ import annotations

from .models import Payment, PaymentType

# Regime constants — kept as plain strings (not an Enum) so the field stays
# easy to read straight off a Determination without an extra import, and
# matches the values documented on Determination.regime.
FIRPTA = "FIRPTA"
PARTNERSHIP_ECI_1446 = "PARTNERSHIP_ECI_1446"
PARTNERSHIP_ECI_1446F = "PARTNERSHIP_ECI_1446F"
PAYROLL_NOT_FDAP = "PAYROLL_NOT_FDAP"
SCHOLARSHIP_871C = "SCHOLARSHIP_871C"
FDAP = "FDAP"
ECI_SELF_REPORTED = "ECI_SELF_REPORTED"
NOT_FOREIGN_OUT_OF_SCOPE = "NOT_FOREIGN_OUT_OF_SCOPE"

_FDAP_PAYMENT_TYPES = {
    PaymentType.DIVIDEND,
    PaymentType.INTEREST,
    PaymentType.INTEREST_PORTFOLIO,
    PaymentType.INTEREST_BANK_DEPOSIT,
    PaymentType.ROYALTY_PATENT,
    PaymentType.ROYALTY_COPYRIGHT,
    PaymentType.ROYALTY_INDUSTRIAL_EQUIPMENT,
    PaymentType.ROYALTY_KNOW_HOW,
    PaymentType.ROYALTY_FILM_TV,
    PaymentType.RENT,
    PaymentType.OTHER_FDAP,
}


def classify_regime(payment: Payment) -> tuple[str, str, str]:
    """Returns (regime, citation, rationale)."""

    payee = payment.payee

    # Out of scope entirely: this engine only handles foreign payees.
    if not payee.is_foreign:
        return (
            NOT_FOREIGN_OUT_OF_SCOPE,
            "IRC 3406",
            "Payee is not foreign — this engine does not implement backup "
            "withholding (IRC 3406), which is the regime that would apply "
            "instead. Route to a separate U.S.-payee/W-9 check.",
        )

    # Step 1a — FIRPTA: disposition of a USRPI by a foreign person.
    if payment.payment_type == PaymentType.REAL_PROPERTY_SALE or (
        payment.real_property is not None and payment.real_property.is_usrpi
    ):
        return (
            FIRPTA,
            "IRC 1445, 897; Reg. 1.1445-11T(d)",
            "Disposition of a U.S. real property interest by a foreign "
            "person routes to FIRPTA, not FDAP.",
        )

    # Step 1b — partnership ECI: distributive share, distribution of ECI,
    # or sale of the partnership interest itself.
    if payment.payment_type == PaymentType.PARTNERSHIP_DISTRIBUTIVE_SHARE_ECI:
        return (
            PARTNERSHIP_ECI_1446,
            "IRC 1446",
            "Foreign partner's distributive share of ECI is withheld on "
            "under Section 1446 regardless of whether it's actually "
            "distributed.",
        )
    if payment.payment_type == PaymentType.PARTNERSHIP_DISTRIBUTION and payment.is_eci:
        return (
            PARTNERSHIP_ECI_1446,
            "IRC 1446",
            "Distribution of income that is ECI to a foreign partner is "
            "withheld on under Section 1446.",
        )
    if payment.payment_type == PaymentType.PARTNERSHIP_INTEREST_SALE:
        return (
            PARTNERSHIP_ECI_1446F,
            "IRC 1446(f)",
            "Sale of a partnership interest by a foreign partner is "
            "withheld on by the buyer under Section 1446(f).",
        )

    # Step 1c — compensation for personal services: not FDAP, goes to payroll
    # withholding (or Form 8233 for a treaty-exempt individual).
    if payment.payment_type == PaymentType.COMPENSATION_SERVICES:
        return (
            PAYROLL_NOT_FDAP,
            "N/A — see wage withholding rules; Form 8233 for treaty exemption",
            "Compensation for personal services is not subject to Section "
            "1441/1442 FDAP withholding — it's subject to wage withholding "
            "instead (or a Form 8233 treaty exemption for an eligible "
            "nonresident alien individual). Flagging for payroll, not this "
            "engine.",
        )

    # Step 1d — scholarship / fellowship.
    if payment.payment_type == PaymentType.SCHOLARSHIP_FELLOWSHIP:
        return (
            SCHOLARSHIP_871C,
            "IRC 871(c)",
            "Scholarship/fellowship grant to a nonresident alien for study, "
            "training, or research is subject to 14% withholding under "
            "Section 871(c), unless excluded from income under Section 117.",
        )

    # Step 1e — ECI not from a partnership (e.g. a foreign corp's US branch
    # income), with a valid W-8ECI on file: no Section 1441 withholding.
    if payment.is_eci and payment.payment_type not in (
        PaymentType.PARTNERSHIP_DISTRIBUTIVE_SHARE_ECI,
        PaymentType.PARTNERSHIP_DISTRIBUTION,
        PaymentType.PARTNERSHIP_INTEREST_SALE,
    ):
        return (
            ECI_SELF_REPORTED,
            "Reg. 1.1441-2(a)",
            "Income effectively connected with a U.S. trade or business, "
            "not from a partnership. Generally not subject to Section 1441 "
            "withholding if a valid W-8ECI is on file — the recipient "
            "self-reports and pays tax through its own return.",
        )

    # Step 1f — passive/FDAP catch-all.
    if payment.payment_type in _FDAP_PAYMENT_TYPES:
        return (
            FDAP,
            "IRC 1441, 1442",
            "U.S.-source passive/investment-type income (FDAP) paid to a "
            "foreign person, not otherwise carved out above.",
        )

    return (
        "NEEDS_MANUAL_REVIEW",
        "N/A",
        f"Payment type {payment.payment_type.value!r} did not match any "
        "modeled regime — route to a human reviewer rather than guess.",
    )
