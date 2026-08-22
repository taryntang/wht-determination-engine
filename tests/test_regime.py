from datetime import date
from decimal import Decimal

from wht_engine.models import (
    Documentation,
    DocForm,
    Payee,
    PayeeType,
    Payment,
    PaymentType,
    RealPropertyFacts,
    PartnershipFacts,
)
from wht_engine.regime import (
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


def _payee(**kwargs) -> Payee:
    defaults = dict(payee_id="V1", name="Test Vendor", payee_type=PayeeType.CORPORATION, country="United Kingdom")
    defaults.update(kwargs)
    return Payee(**defaults)


def test_not_foreign_is_out_of_scope():
    payment = Payment(
        payment_id="P1",
        payee=_payee(is_foreign=False),
        payment_type=PaymentType.DIVIDEND,
        gross_amount=Decimal("1000"),
    )
    regime, _, _ = classify_regime(payment)
    assert regime == NOT_FOREIGN_OUT_OF_SCOPE


def test_real_property_sale_routes_to_firpta():
    payment = Payment(
        payment_id="P2",
        payee=_payee(),
        payment_type=PaymentType.REAL_PROPERTY_SALE,
        gross_amount=Decimal("500000"),
        real_property=RealPropertyFacts(is_usrpi=True, amount_realized=Decimal("500000")),
    )
    regime, _, _ = classify_regime(payment)
    assert regime == FIRPTA


def test_partnership_distributive_share_routes_to_1446():
    payment = Payment(
        payment_id="P3",
        payee=_payee(payee_type=PayeeType.PARTNERSHIP),
        payment_type=PaymentType.PARTNERSHIP_DISTRIBUTIVE_SHARE_ECI,
        gross_amount=Decimal("10000"),
        partnership=PartnershipFacts(partner_type=PayeeType.CORPORATION),
    )
    regime, _, _ = classify_regime(payment)
    assert regime == PARTNERSHIP_ECI_1446


def test_partnership_interest_sale_routes_to_1446f():
    payment = Payment(
        payment_id="P4",
        payee=_payee(payee_type=PayeeType.PARTNERSHIP),
        payment_type=PaymentType.PARTNERSHIP_INTEREST_SALE,
        gross_amount=Decimal("100000"),
        partnership=PartnershipFacts(),
    )
    regime, _, _ = classify_regime(payment)
    assert regime == PARTNERSHIP_ECI_1446F


def test_compensation_routes_to_payroll_not_fdap():
    payment = Payment(
        payment_id="P5",
        payee=_payee(payee_type=PayeeType.INDIVIDUAL),
        payment_type=PaymentType.COMPENSATION_SERVICES,
        gross_amount=Decimal("5000"),
    )
    regime, _, _ = classify_regime(payment)
    assert regime == PAYROLL_NOT_FDAP


def test_scholarship_routes_to_871c():
    payment = Payment(
        payment_id="P6",
        payee=_payee(payee_type=PayeeType.INDIVIDUAL),
        payment_type=PaymentType.SCHOLARSHIP_FELLOWSHIP,
        gross_amount=Decimal("3000"),
    )
    regime, _, _ = classify_regime(payment)
    assert regime == SCHOLARSHIP_871C


def test_eci_without_partnership_self_reported():
    payment = Payment(
        payment_id="P7",
        payee=_payee(documentation=Documentation(form=DocForm.W8ECI)),
        payment_type=PaymentType.ROYALTY_PATENT,
        gross_amount=Decimal("20000"),
        is_eci=True,
    )
    regime, _, _ = classify_regime(payment)
    assert regime == ECI_SELF_REPORTED


def test_dividend_routes_to_fdap():
    payment = Payment(
        payment_id="P8",
        payee=_payee(),
        payment_type=PaymentType.DIVIDEND,
        gross_amount=Decimal("1000"),
    )
    regime, _, _ = classify_regime(payment)
    assert regime == FDAP
