from datetime import date
from decimal import Decimal

from wht_engine.models import (
    Payee,
    PayeeType,
    Payment,
    PaymentType,
    PartnershipFacts,
    RealPropertyFacts,
)
from wht_engine.firpta import determine

AS_OF = date(2026, 8, 22)


def _payee(**kwargs) -> Payee:
    defaults = dict(payee_id="S1", name="Foreign Seller", payee_type=PayeeType.INDIVIDUAL, country="Canada")
    defaults.update(kwargs)
    return Payee(**defaults)


def test_general_rate_15_percent():
    payment = Payment(
        payment_id="F1",
        payee=_payee(),
        payment_type=PaymentType.REAL_PROPERTY_SALE,
        gross_amount=Decimal("500000"),
        real_property=RealPropertyFacts(is_usrpi=True, amount_realized=Decimal("500000")),
    )
    det = determine(payment, AS_OF)
    assert det.rate == Decimal("15")
    assert det.withholding_amount == Decimal("75000.00")
    assert det.withholding_required is True


def test_us_status_affidavit_exempts():
    payment = Payment(
        payment_id="F2",
        payee=_payee(),
        payment_type=PaymentType.REAL_PROPERTY_SALE,
        gross_amount=Decimal("400000"),
        real_property=RealPropertyFacts(
            is_usrpi=True, amount_realized=Decimal("400000"), seller_has_us_affidavit=True
        ),
    )
    det = determine(payment, AS_OF)
    assert det.withholding_required is False
    assert det.rate == Decimal("0")


def test_personal_residence_le_300k_exempts():
    payment = Payment(
        payment_id="F3",
        payee=_payee(),
        payment_type=PaymentType.REAL_PROPERTY_SALE,
        gross_amount=Decimal("290000"),
        real_property=RealPropertyFacts(
            is_usrpi=True, amount_realized=Decimal("290000"), is_personal_residence_le_300k=True
        ),
    )
    det = determine(payment, AS_OF)
    assert det.withholding_required is False


def test_domestic_partnership_disposition_uses_21_percent():
    payment = Payment(
        payment_id="F4",
        payee=_payee(payee_type=PayeeType.PARTNERSHIP),
        payment_type=PaymentType.REAL_PROPERTY_SALE,
        gross_amount=Decimal("100000"),
        real_property=RealPropertyFacts(is_usrpi=True, amount_realized=Decimal("100000")),
        partnership=PartnershipFacts(partnership_is_foreign=False),
    )
    det = determine(payment, AS_OF)
    assert det.rate == Decimal("21")
    assert "VERIFY_1445(e)_SUBSECTION_CITE" in det.flags


def test_missing_usrpi_facts_flagged_not_guessed():
    payment = Payment(
        payment_id="F5",
        payee=_payee(),
        payment_type=PaymentType.REAL_PROPERTY_SALE,
        gross_amount=Decimal("100000"),
        real_property=None,
    )
    det = determine(payment, AS_OF)
    assert det.confidence == "needs_review"
    assert "MISSING_USRPI_FACTS" in det.flags
