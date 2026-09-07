import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from adapters.live_treaty_lookup import StaticThenLiveTreatyTable, fetch_live_treaty_rate
from wht_engine.models import PaymentType
from wht_engine.treaty_rates import TreatyRateResult


def _mock_claude_response(payload: dict):
    body = json.dumps({"content": [{"type": "text", "text": json.dumps(payload)}]}).encode()
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = body
    return cm


def _mock_pdf_bytes():
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = b"%PDF-1.4 fake"
    return cm


def test_fetch_live_treaty_rate_found():
    responses = [
        _mock_pdf_bytes(),  # Table 1
        _mock_pdf_bytes(),  # Table 3
        _mock_claude_response({
            "treaty_in_force": True,
            "found_in_table1": True,
            "rate_pct": 0,
            "treaty_article": "Art. 12",
            "footnote_text": None,
            "notes": "",
        }),
    ]
    with patch("urllib.request.urlopen", side_effect=responses):
        result = fetch_live_treaty_rate("Cyprus", PaymentType.ROYALTY_PATENT, "fake-key")

    assert result.found is True
    assert result.rate == Decimal("0")
    assert result.treaty_article == "Art. 12"
    assert result.table_version.startswith("IRS-LIVE-")


def test_fetch_live_treaty_rate_no_treaty_in_force():
    responses = [
        _mock_pdf_bytes(),
        _mock_pdf_bytes(),
        _mock_claude_response({
            "treaty_in_force": False,
            "found_in_table1": False,
            "rate_pct": None,
            "treaty_article": None,
            "footnote_text": None,
            "notes": "No treaty found for this country.",
        }),
    ]
    with patch("urllib.request.urlopen", side_effect=responses):
        result = fetch_live_treaty_rate("Nowhereland", PaymentType.DIVIDEND, "fake-key")

    assert result.found is False
    assert result.footnote == "No treaty found for this country."


def test_fetch_live_treaty_rate_unparseable_response_is_not_found_not_a_crash():
    # Simulate a response with no JSON in it at all.
    bad_response = MagicMock()
    bad_response.__enter__.return_value.read.return_value = json.dumps(
        {"content": [{"type": "text", "text": "sorry, I could not read this PDF"}]}
    ).encode()
    with patch("urllib.request.urlopen", side_effect=[_mock_pdf_bytes(), _mock_pdf_bytes(), bad_response]):
        result = fetch_live_treaty_rate("Cyprus", PaymentType.INTEREST, "fake-key")

    assert result.found is False


def test_unmapped_income_type_short_circuits_without_network_call():
    with patch("urllib.request.urlopen") as mock_urlopen:
        result = fetch_live_treaty_rate("Cyprus", PaymentType.OTHER_FDAP, "fake-key")
    mock_urlopen.assert_not_called()
    assert result.found is False


def test_static_then_live_uses_static_result_when_found():
    static = MagicMock()
    static.lookup.return_value = TreatyRateResult(
        found=True, rate=Decimal("15"), treaty_article="Art. 10", footnote=None,
        table_version="SAMPLE-DEMO-v1", country="United Kingdom", payment_type="dividend",
    )
    table = StaticThenLiveTreatyTable(static, anthropic_api_key="fake-key")

    with patch("adapters.live_treaty_lookup.fetch_live_treaty_rate") as mock_live:
        result = table.lookup("United Kingdom", PaymentType.DIVIDEND)

    mock_live.assert_not_called()
    assert result.rate == Decimal("15")


def test_static_then_live_falls_back_to_live_on_miss():
    static = MagicMock()
    static.lookup.return_value = TreatyRateResult(
        found=False, rate=None, treaty_article=None, footnote=None,
        table_version="SAMPLE-DEMO-v1", country="Cyprus", payment_type="royalty_patent",
    )
    table = StaticThenLiveTreatyTable(static, anthropic_api_key="fake-key")

    live_result = TreatyRateResult(
        found=True, rate=Decimal("0"), treaty_article="Art. 12", footnote=None,
        table_version="IRS-LIVE-2026-09-07", country="Cyprus", payment_type="royalty_patent",
    )
    with patch("adapters.live_treaty_lookup.fetch_live_treaty_rate", return_value=live_result) as mock_live:
        result = table.lookup("Cyprus", PaymentType.ROYALTY_PATENT)

    mock_live.assert_called_once()
    assert result is live_result


def test_static_then_live_skips_live_lookup_without_api_key():
    static = MagicMock()
    static.lookup.return_value = TreatyRateResult(
        found=False, rate=None, treaty_article=None, footnote=None,
        table_version="SAMPLE-DEMO-v1", country="Cyprus", payment_type="royalty_patent",
    )
    table = StaticThenLiveTreatyTable(static, anthropic_api_key=None)

    with patch("adapters.live_treaty_lookup.fetch_live_treaty_rate") as mock_live:
        result = table.lookup("Cyprus", PaymentType.ROYALTY_PATENT)

    mock_live.assert_not_called()
    assert result.found is False


def test_static_then_live_swallows_live_lookup_errors():
    static = MagicMock()
    static_result = TreatyRateResult(
        found=False, rate=None, treaty_article=None, footnote=None,
        table_version="SAMPLE-DEMO-v1", country="Cyprus", payment_type="royalty_patent",
    )
    static.lookup.return_value = static_result
    table = StaticThenLiveTreatyTable(static, anthropic_api_key="fake-key")

    with patch("adapters.live_treaty_lookup.fetch_live_treaty_rate", side_effect=RuntimeError("network down")):
        result = table.lookup("Cyprus", PaymentType.ROYALTY_PATENT)

    assert result is static_result
