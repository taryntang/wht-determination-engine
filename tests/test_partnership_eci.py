from datetime import date
from decimal import Decimal

from wht_engine.models import Payee, PayeeType, Payment, PaymentType, PartnershipFacts
from wht_engine.partnership_eci import determine_1446, determine_1446f

AS_OF = date(2026, 8, 22)


def _payee(**kwargs) -> Payee:
    defaults = dict(payee_id="PP1", name="Foreign Partner", payee_type=PayeeType.PARTNERSHIP, country="Germany")
    defaults.update(kwargs)
    return Payee(**defaults)


def test_1446_corporate_partner_21_percent():
    payment = Payment(
        payment_id="N1",
        payee=_payee(),
        payment_type=PaymentType.PARTNERSHIP_DISTRIBUTIVE_SHARE_ECI,
        gross_amount=Decimal("10000"),
        partnership=PartnershipFacts(partner_type=PayeeType.CORPORATION),
    )
    det = determine_1446(payment, AS_OF)
    assert det.rate == Decimal("21")
    assert det.withholding_amount == Decimal("2100.00")


def test_1446_individual_partner_top_rate_and_flag():
    payment = Payment(
        payment_id="N2",
        payee=_payee(),
        payment_type=PaymentType.PARTNERSHIP_DISTRIBUTIVE_SHARE_ECI,
        gross_amount=Decimal("10000"),
        partnership=PartnershipFacts(partner_type=PayeeType.INDIVIDUAL),
    )
    det = determine_1446(payment, AS_OF)
    assert det.rate == Decimal("37")
    assert "VERIFY_CURRENT_TOP_INDIVIDUAL_RATE" in det.flags


def test_1446f_default_10_percent():
    payment = Payment(
        payment_id="N3",
        payee=_payee(),
        payment_type=PaymentType.PARTNERSHIP_INTEREST_SALE,
        gross_amount=Decimal("50000"),
        partnership=PartnershipFacts(),
    )
    det = determine_1446f(payment, AS_OF)
    assert det.rate == Decimal("10")
    assert det.withholding_amount == Decimal("5000.00")


def test_1446f_non_foreign_certification_exempts():
    payment = Payment(
        payment_id="N4",
        payee=_payee(),
        payment_type=PaymentType.PARTNERSHIP_INTEREST_SALE,
        gross_amount=Decimal("50000"),
        partnership=PartnershipFacts(seller_certifies_non_foreign=True),
    )
    det = determine_1446f(payment, AS_OF)
    assert det.withholding_required is False


def test_1446f_de_minimis_eci_exempts():
    payment = Payment(
        payment_id="N5",
        payee=_payee(),
        payment_type=PaymentType.PARTNERSHIP_INTEREST_SALE,
        gross_amount=Decimal("50000"),
        partnership=PartnershipFacts(net_gain_eci_pct_of_total=Decimal("5")),
    )
    det = determine_1446f(payment, AS_OF)
    assert det.withholding_required is False
