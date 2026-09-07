import unittest
from unittest.mock import patch
from decimal import Decimal
from datetime import date
from wht_engine import determine_withholding
from wht_engine.models import PayeeType, PaymentType

class ExtractionTests(unittest.TestCase):
    def payload(self):
        return {'form':'W-8BEN-E','legal_name':'Synthetic Ltd','country':'Singapore',
                'signed_date':'2026-01-01','treaty_country':None,'treaty_article':None,
                'claimed_rate':0,'flags':[]}

    def test_extracted_rate_cannot_override_engine(self):
        from adapters.w8_extract import extract_w8, candidate_from_extraction
        result=extract_w8(b'%PDF synthetic', response=self.payload())
        c=candidate_from_extraction(result, vendor_request_id='R1',vendor_number='V1',
            payee_type=PayeeType.CORPORATION,payment_type=PaymentType.ROYALTY_COPYRIGHT,
            amount=Decimal('100000'),is_us_source=True,is_eci=False, payment_date=date(2026,9,7))
        self.assertEqual(determine_withholding(c.payment).rate,Decimal('30'))
        self.assertTrue(c.amount_known)
        self.assertEqual(result['method'],'fixture')
        self.assertEqual(len(result['sha256']),64)
        self.assertTrue(c.notes)

    def test_missing_fields_remain_unknown_and_flagged(self):
        from adapters.w8_extract import extract_w8
        data=self.payload();data['signed_date']=None;data['country']=None
        r=extract_w8(b'%PDF synthetic',response=data)
        self.assertIsNone(r['fields']['signed_date'])
        self.assertTrue(any('signed_date' in f for f in r['fields']['flags']))

    def test_malformed_response_rejected(self):
        from adapters.w8_extract import extract_w8
        for key,value in [('claimed_rate',True),('claimed_rate',float('nan')),
                          ('signed_date','bad-date'),('flags','none'),('country',10),('form','W-9')]:
            data=self.payload();data[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                extract_w8(b'%PDF synthetic',response=data)

    def test_live_call_uses_forced_tool_and_checks_truncation(self):
        from adapters.w8_extract import extract_w8
        import json
        from unittest.mock import MagicMock
        cm=MagicMock(); cm.__enter__.return_value.read.return_value=json.dumps({
          'stop_reason':'max_tokens','content':[{'type':'tool_use','name':'record_w8','input':self.payload()}]}).encode()
        with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test','WHT_AGENT_MODEL':'test-model'}),patch('urllib.request.urlopen',return_value=cm) as request:
            with self.assertRaisesRegex(ValueError,'incomplete'):
                extract_w8(b'%PDF synthetic')
        body=json.loads(request.call_args.args[0].data)
        self.assertEqual(body['tool_choice'],{'type':'tool','name':'record_w8'})
