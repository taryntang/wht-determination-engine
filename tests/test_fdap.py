from datetime import date
from decimal import Decimal

from wht_engine.models import (
    Documentation,
    DocForm,
    IntermediaryType,
    Payee,
    PayeeType,
    Payment,
    PaymentType,
)
from wht_engine.fdap import determine

AS_OF = date(2026, 8, 22)


def _payee(**kwargs) -> Payee:
    defaults = dict(payee_id="V1", name="Test Vendor", payee_type=PayeeType.CORPORATION, country="United Kingdom")
    defaults.update(kwargs)
    return Payee(**defaults)


def test_no_documentation_defaults_to_30_percent():
    payment = Payment(
        payment_id="P1",
        payee=_payee(),
        payment_type=PaymentType.ROYALTY_PATENT,
        gross_amount=Decimal("1000"),
    )
    det = determine(payment, AS_OF)
    assert det.withholding_required is True
    assert det.rate == Decimal("30")
    assert det.withholding_amount == Decimal("300.00")
    assert "1441" in det.citation


def test_valid_treaty_claim_applies_table_rate():
    payment = Payment(
        payment_id="P2",
        payee=_payee(
            documentation=Documentation(
                form=DocForm.W8BEN_E,
                treaty_country_claimed="United Kingdom",
                treaty_article="Art. 12",
                expiration_date=date(2027, 1, 1),
            )
        ),
        payment_type=PaymentType.ROYALTY_PATENT,
        gross_amount=Decimal("1000"),
    )
    det = determine(payment, AS_OF)
    assert det.rate == Decimal("0")
    assert det.withholding_required is False
    assert det.rate_table_version == "SAMPLE-DEMO-v1"
    assert "CONFIRM_LOB_TABLE_4" in det.flags


def test_expired_documentation_falls_back_to_statutory_rate():
    payment = Payment(
        payment_id="P3",
        payee=_payee(
            documentation=Documentation(
                form=DocForm.W8BEN_E,
                treaty_country_claimed="United Kingdom",
                treaty_article="Art. 12",
                expiration_date=date(2025, 1, 1),  # expired relative to AS_OF
            )
        ),
        payment_type=PaymentType.ROYALTY_PATENT,
        gross_amount=Decimal("1000"),
    )
    det = determine(payment, AS_OF)
    assert det.rate == Decimal("30")
    assert "DOCUMENTATION_EXPIRED" in det.flags
    assert det.confidence == "needs_review"


def test_portfolio_interest_exempt():
    payment = Payment(
        payment_id="P4",
        payee=_payee(),
        payment_type=PaymentType.INTEREST_PORTFOLIO,
        gross_amount=Decimal("5000"),
    )
    det = determine(payment, AS_OF)
    assert det.withholding_required is False
    assert det.rate == Decimal("0")


def test_qi_with_primary_withholding_responsibility_shifts_obligation():
    payment = Payment(
        payment_id="P5",
        payee=_payee(intermediary=IntermediaryType.QI_PWR),
        payment_type=PaymentType.DIVIDEND,
        gross_amount=Decimal("2000"),
    )
    det = determine(payment, AS_OF)
    assert det.withholding_required is False
    assert "QI_HAS_PRIMARY_WITHHOLDING_RESPONSIBILITY" in det.flags


def test_treaty_rate_not_in_table_defaults_conservatively():
    payment = Payment(
        payment_id="P6",
        payee=_payee(
            country="Narnia",
            documentation=Documentation(
                form=DocForm.W8BEN_E,
                treaty_country_claimed="Narnia",
                treaty_article="Art. 1",
                expiration_date=date(2027, 1, 1),
            ),
        ),
        payment_type=PaymentType.ROYALTY_PATENT,
        gross_amount=Decimal("1000"),
    )
    det = determine(payment, AS_OF)
    assert det.rate == Decimal("30")
    assert "TREATY_RATE_NOT_IN_TABLE" in det.flags
    assert det.confidence == "needs_review"
