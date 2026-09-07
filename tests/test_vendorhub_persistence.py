"""
Tests for the wht_determinations read/write functions in
adapters/vendorhub.py, mocking the HTTP layer — no live Supabase project
needed. These check that the right method/URL/headers/body get built,
mirroring what execute_sql-based manual testing already confirmed works
against the real database (upsert-on-conflict dedup, and that the CHECK
constraints reject an unreasoned override/rejection).
"""

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import unittest
from unittest.mock import patch

from adapters.vendorhub import Candidate, set_review_decision, upsert_determination
from wht_engine.models import Determination


def _mock_urlopen(response_body: bytes):
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = response_body
    return cm


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict("os.environ", {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-key"})
        env.start()
        self.addCleanup(env.stop)

    def test_upsert_determination_skip_reason_only(self):
        candidate = Candidate(
            vendor_request_id="vr-1",
            vendor_id="V1",
            vendor_name="Test Vendor",
            category_label="(none)",
            skip_reason="nothing to determine",
        )
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"[{}]")) as mock_open:
            upsert_determination(candidate, None)

        request = mock_open.call_args[0][0]
        assert request.method == "POST"
        assert "on_conflict=vendor_request_id,category" in request.full_url
        assert request.headers["Prefer"] == "resolution=merge-duplicates,return=representation"
        assert request.headers["Apikey"] == "test-service-role-key"
        body = json.loads(request.data)
        assert body["vendor_request_id"] == "vr-1"
        assert body["confidence"] == "skipped"
        assert body["notes"] == "nothing to determine"
        assert "review_status" not in body  # never touch review fields on re-run


    def test_upsert_determination_with_result(self):
        candidate = Candidate(
            vendor_request_id="vr-2",
            vendor_id="V2",
            vendor_name="Test Vendor 2",
            category_label="rent (Q3)",
            notes=["some note"],
        )
        det = Determination(
            payment_id="vr-2:q3_rental",
            regime="FDAP",
            withholding_required=True,
            rate=Decimal("30"),
            withholding_amount=Decimal("0"),
            citation="IRC 1441, 1442",
            rationale="statutory default",
            flags=["SOME_FLAG"],
        )
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"[{}]")) as mock_open:
            upsert_determination(candidate, det)

        body = json.loads(mock_open.call_args[0][0].data)
        assert body["regime"] == "FDAP"
        assert body["rate"] == "30"
        assert body["flags"] == "SOME_FLAG"
        assert body["notes"] == "some note"
        assert "review_status" not in body


    def test_set_review_decision_rejects_bad_status(self):
        with self.assertRaises(ValueError):
            set_review_decision("det-1", "not_a_real_status", "Reviewer")


    def test_set_review_decision_approved_builds_patch(self):
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"[{}]")) as mock_open:
            set_review_decision("det-1", "approved", "Jane Reviewer")

        request = mock_open.call_args[0][0]
        assert request.method == "PATCH"
        assert "wht_determinations?id=eq.det-1" in request.full_url
        body = json.loads(request.data)
        assert body["review_status"] == "approved"
        assert body["reviewer_name"] == "Jane Reviewer"
        assert "reviewed_at" in body


    def test_set_review_decision_overridden_includes_rate_and_reasoning(self):
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"[{}]")) as mock_open:
            set_review_decision(
                "det-1",
                "overridden",
                "Jane Reviewer",
                override_rate=10.0,
                override_reasoning="Treaty confirmed manually.",
            )

        body = json.loads(mock_open.call_args[0][0].data)
        assert body["override_rate"] == 10.0
        assert body["override_reasoning"] == "Treaty confirmed manually."


    def test_missing_credentials_raises_before_any_request(self):
        import os

        del os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        with self.assertRaises(RuntimeError):
            set_review_decision("det-1", "approved", "Jane Reviewer")

