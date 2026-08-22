from datetime import date
from decimal import Decimal

from wht_engine import determine_withholding
from wht_engine.models import Documentation, DocForm, Payee, PayeeType, Payment, PaymentType

AS_OF = date(2026, 8, 22)


def _payee(**kwargs) -> Payee:
    defaults = dict(payee_id="V1", name="Test Vendor", payee_type=PayeeType.CORPORATION, country="United Kingdom")
    defaults.update(kwargs)
    return Payee(**defaults)


def test_end_to_end_fdap_default_rate_has_audit_trail():
    payment = Payment(
        payment_id="E1",
        payee=_payee(),
        payment_type=PaymentType.ROYALTY_PATENT,
        gross_amount=Decimal("1000"),
        payment_date=AS_OF,
    )
    det = determine_withholding(payment)
    assert det.regime == "FDAP"
    assert det.rate == Decimal("30")
    assert len(det.audit_trail) >= 2
    assert DocForm.W8BEN_E in det.documentation_required


def test_end_to_end_payroll_not_fdap():
    payment = Payment(
        payment_id="E2",
        payee=_payee(payee_type=PayeeType.INDIVIDUAL),
        payment_type=PaymentType.COMPENSATION_SERVICES,
        gross_amount=Decimal("5000"),
        payment_date=AS_OF,
    )
    det = determine_withholding(payment)
    assert det.regime == "PAYROLL_NOT_FDAP"
    assert det.withholding_required is False
    assert "ROUTE_TO_PAYROLL_WITHHOLDING" in det.flags


def test_end_to_end_not_foreign_out_of_scope():
    payment = Payment(
        payment_id="E3",
        payee=_payee(is_foreign=False),
        payment_type=PaymentType.DIVIDEND,
        gross_amount=Decimal("1000"),
        payment_date=AS_OF,
    )
    det = determine_withholding(payment)
    assert det.regime == "NOT_FOREIGN_OUT_OF_SCOPE"
    assert det.confidence == "needs_review"


def test_end_to_end_documentation_mismatch_flagged():
    # A W-8ECI on file for a payment that isn't marked as ECI — the FDAP
    # module should flag the inconsistency rather than silently zero out
    # withholding.
    payment = Payment(
        payment_id="E4",
        payee=_payee(documentation=Documentation(form=DocForm.W8ECI)),
        payment_type=PaymentType.DIVIDEND,
        gross_amount=Decimal("1000"),
        payment_date=AS_OF,
        is_eci=False,
    )
    det = determine_withholding(payment)
    assert "INCONSISTENT_ECI_FLAG_VS_DOCUMENTATION" in det.flags
    assert det.confidence == "needs_review"


def test_end_to_end_scholarship_rate():
    payment = Payment(
        payment_id="E5",
        payee=_payee(payee_type=PayeeType.INDIVIDUAL),
        payment_type=PaymentType.SCHOLARSHIP_FELLOWSHIP,
        gross_amount=Decimal("2000"),
        payment_date=AS_OF,
    )
    det = determine_withholding(payment)
    assert det.regime == "SCHOLARSHIP_871C"
    assert det.rate == Decimal("14")
    assert det.withholding_amount == Decimal("280.00")
