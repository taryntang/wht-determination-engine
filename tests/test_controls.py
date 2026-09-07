from datetime import date
from decimal import Decimal
import json
import unittest
from unittest.mock import patch, MagicMock

from adapters.vendorhub import Candidate, upsert_determination, set_review_decision
from wht_engine import determine_withholding
from wht_engine.models import Payment, Payee, PayeeType, PaymentType, Documentation, DocForm


def payment():
    return Payment('P1', Payee('V1', 'Synthetic Ltd', PayeeType.CORPORATION, 'Singapore',
        documentation=Documentation(form=DocForm.W8BEN_E, signed_date=date(2025,1,1), expiration_date=date(2028,12,31))),
        PaymentType.ROYALTY_COPYRIGHT, Decimal('100000'), payment_date=date(2026,9,7))

class ControlsTests(unittest.TestCase):
    def test_document_warning_requires_review(self):
        p = payment(); p.payee.documentation.form = DocForm.W8BEN
        self.assertEqual(determine_withholding(p).confidence, 'needs_review')

    def test_nonfinite_and_negative_money_rejected(self):
        for value in ('NaN', 'Infinity', '-1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                p=payment(); p.gross_amount=Decimal(value); determine_withholding(p)

    def test_proposal_preserves_evidence_and_unknown_amount(self):
        p=payment(); det=determine_withholding(p)
        c=Candidate('request-1','V1','Synthetic Ltd','copyright', payment=p)
        with patch('adapters.vendorhub._supabase_request', return_value=[{'id':'D1'}]) as req:
            upsert_determination(c, det)
        row=req.call_args.kwargs['body']
        self.assertEqual(row['audit_trail'][-1]['rule'], 'engine')
        self.assertEqual(row['payment_snapshot']['gross_amount'], '100000')
        self.assertIsNone(row['gross_amount'])
        self.assertIsNone(row['withholding_amount'])
        self.assertTrue(row['needs_review'])
        self.assertEqual(row['document_snapshot']['form'], 'W-8BEN-E')

    def test_review_validation_happens_before_network(self):
        cases=[('approved',' ',None,None),('overridden','Jane',101,'reason'),
               ('overridden','Jane',float('nan'),'reason'),('overridden','Jane',10,' '),
               ('rejected','Jane',None,None),('approved','Jane',10,'reason')]
        with patch('adapters.vendorhub._supabase_request') as req:
            for status,name,rate,reason in cases:
                with self.subTest(status=status,rate=rate), self.assertRaises(ValueError):
                    set_review_decision('D1',status,name,rate,reason)
            req.assert_not_called()

    def test_stale_review_is_conflict_not_success(self):
        with patch('adapters.vendorhub._supabase_request', return_value=[] ) as req:
            with self.assertRaisesRegex(ValueError,'changed|reviewed'):
                set_review_decision('D1','approved','Jane', expected_version=3)
        self.assertIn('proposal_version=eq.3',req.call_args.args[1])
        self.assertIn('review_status=eq.pending',req.call_args.args[1])

    def test_known_amount_is_saved_exactly(self):
        p=payment(); c=Candidate('request-1','V1','Synthetic Ltd','copyright',payment=p,amount_known=True)
        with patch('adapters.vendorhub._supabase_request',return_value=[{}]) as req:
            upsert_determination(c,determine_withholding(p))
        self.assertEqual(req.call_args.kwargs['body']['withholding_amount'],'30000.00')

    def test_skipped_payload_clears_old_result(self):
        c=Candidate('request-1','V1','Synthetic Ltd','copyright',skip_reason='unknown subtype')
        with patch('adapters.vendorhub._supabase_request',return_value=[{}]) as req:
            upsert_determination(c,None)
        body=req.call_args.kwargs['body']
        self.assertIsNone(body['rate']);self.assertIsNone(body['rationale'])
        self.assertEqual(body['audit_trail'],[])

    def test_live_treaty_malformed_rates_are_not_applied(self):
        from adapters.live_treaty_lookup import fetch_live_treaty_rate
        from wht_engine.models import PaymentType
        for rate in ('NaN',-1,101,True):
            cm=MagicMock();cm.__enter__.return_value.read.return_value=json.dumps({'content':[{'type':'text','text':json.dumps({
                'treaty_in_force':True,'found_in_table1':True,'rate_pct':rate})}]}).encode()
            with patch('adapters.live_treaty_lookup._download',return_value=b'pdf'),patch('urllib.request.urlopen',return_value=cm):
                self.assertFalse(fetch_live_treaty_rate('Test',PaymentType.DIVIDEND,'key').found)
