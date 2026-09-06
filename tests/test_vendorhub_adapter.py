from decimal import Decimal

from adapters.vendorhub import map_row_to_candidates
from wht_engine import determine_withholding
from wht_engine.models import PayeeType, PaymentType


def _row(**overrides) -> dict:
    row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "vendor_number": "123456789",
        "legal_name": "Thames Analytics Ltd",
        "reg_country": "United Kingdom",
        "entity_type": "Corporation",
        "q1_goods": "D",
        "q2_services": "D",
        "q3_rental": "D",
        "q3b_real_property": "D",
        "q4_server": "D",
        "q5_royalties": "D",
        "w8_type": None,
    }
    row.update(overrides)
    return row


def test_category_marked_outside_us_produces_no_candidate():
    row = _row(q3_rental="B")
    candidates = map_row_to_candidates(row)
    assert len(candidates) == 1
    assert candidates[0].skip_reason is not None
    assert candidates[0].payment is None


def test_us_source_rental_produces_a_runnable_candidate():
    row = _row(q3_rental="A")
    candidates = map_row_to_candidates(row)
    runnable = [c for c in candidates if c.payment is not None]
    assert len(runnable) == 1
    payment = runnable[0].payment
    assert payment.payment_type == PaymentType.RENT
    assert payment.gross_amount == Decimal("0")

    det = determine_withholding(payment)
    assert det.regime == "FDAP"
    assert det.rate == Decimal("30")  # no treaty details captured -> statutory default


def test_both_answer_also_produces_a_candidate():
    row = _row(q4_server="C")
    candidates = map_row_to_candidates(row)
    runnable = [c for c in candidates if c.payment is not None]
    assert len(runnable) == 1
    assert runnable[0].payment.payment_type == PaymentType.ROYALTY_COPYRIGHT


def test_goods_question_never_produces_a_candidate():
    row = _row(q1_goods="A")
    candidates = map_row_to_candidates(row)
    assert all(c.payment is None for c in candidates)


def test_royalty_subtype_is_skipped_not_guessed():
    row = _row(q5_royalties="A")
    candidates = map_row_to_candidates(row)
    assert len(candidates) == 1
    assert candidates[0].payment is None
    assert "subtype" in candidates[0].skip_reason


def test_services_for_individual_routes_to_payroll():
    row = _row(entity_type="Individual", q2_services="A")
    candidates = map_row_to_candidates(row)
    runnable = [c for c in candidates if c.payment is not None]
    assert len(runnable) == 1
    det = determine_withholding(runnable[0].payment)
    assert det.regime == "PAYROLL_NOT_FDAP"


def test_services_for_corporation_is_skipped_not_guessed():
    row = _row(entity_type="Corporation", q2_services="A")
    candidates = map_row_to_candidates(row)
    assert len(candidates) == 1
    assert candidates[0].payment is None
    assert candidates[0].skip_reason is not None


def test_unmodeled_entity_type_is_skipped_entirely():
    row = _row(entity_type="International organization", q3_rental="A")
    candidates = map_row_to_candidates(row)
    assert len(candidates) == 1
    assert candidates[0].payment is None
    assert candidates[0].category_label == "(all categories)"


def test_w8_on_file_without_treaty_details_still_defaults_to_statutory_rate():
    row = _row(q3_rental="A", w8_type="W-8BEN-E")
    candidates = map_row_to_candidates(row)
    runnable = [c for c in candidates if c.payment is not None]
    det = determine_withholding(runnable[0].payment)
    assert det.rate == Decimal("30")
    assert any("treaty" in note.lower() for note in runnable[0].notes)


def test_no_us_source_category_yields_single_none_candidate():
    row = _row()  # every question defaults to "D"
    candidates = map_row_to_candidates(row)
    assert len(candidates) == 1
    assert candidates[0].category_label == "(none)"
