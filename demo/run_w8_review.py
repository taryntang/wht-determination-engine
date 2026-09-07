"""Offline fixture or live PDF -> extraction -> engine -> optional Supabase save."""
import argparse
from dataclasses import asdict
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from adapters.w8_extract import extract_w8, candidate_from_extraction
from adapters.vendorhub import upsert_determination
from wht_engine import determine_withholding
from wht_engine.models import PayeeType, PaymentType


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--fixture',action='store_true',help='Canned extraction, no PDF reading or API call')
    source.add_argument('--pdf',type=Path,help='Send this PDF to Claude for live extraction')
    parser.add_argument('--persist',action='store_true',help='Save to configured Supabase project')
    parser.add_argument('--vendor-request-id',default='00000000-0000-0000-0000-000000000001')
    parser.add_argument('--vendor-number',default='DEMO-SG')
    parser.add_argument('--amount',type=Decimal,default=Decimal('100000'))
    parser.add_argument('--payment-date',type=date.fromisoformat,default=date(2026,9,7))
    parser.add_argument('--income-type',choices=[p.value for p in PaymentType],default='royalty_copyright')
    parser.add_argument('--entity-type',choices=[p.value for p in PayeeType],default='corporation')
    parser.add_argument('--foreign-source',action='store_true')
    parser.add_argument('--eci',action='store_true')
    args=parser.parse_args()
    fixture=None
    if args.fixture:
        fixture={'form':'W-8BEN-E','legal_name':'Synthetic Singapore Ltd','country':'Singapore',
                 'signed_date':'2026-01-01','treaty_country':None,'treaty_article':None,
                 'claimed_rate':None,'flags':['CANNED_FIXTURE: no PDF was read']}
    pdf=b'%PDF synthetic fixture marker; not an actual PDF' if args.fixture else args.pdf.read_bytes()
    extraction=extract_w8(pdf,response=fixture)
    candidate=candidate_from_extraction(extraction,vendor_request_id=args.vendor_request_id,
        vendor_number=args.vendor_number,payee_type=PayeeType(args.entity_type),
        payment_type=PaymentType(args.income_type),amount=args.amount,
        is_us_source=not args.foreign_source,is_eci=args.eci,payment_date=args.payment_date)
    determination=determine_withholding(candidate.payment)
    print(json.dumps({'extraction':extraction,'determination':asdict(determination),
                      'review_notes':candidate.notes,'review_status':'pending'},default=str,indent=2))
    if args.persist:
        row=upsert_determination(candidate,determination)
        print('Saved proposal:',row['id'],'version',row['proposal_version'])

if __name__=='__main__':
    main()
