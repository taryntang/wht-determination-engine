-- Run with psql -v ON_ERROR_STOP=1 after bootstrap + migration in a disposable
-- project. Rolls back test data; do not point tests at a production project.
begin;
do $$
declare vendor uuid := gen_random_uuid(); det uuid; n integer; ver integer;
begin
 insert into public.vendor_tax_requests(id,vendor_number,legal_name,reg_country,entity_type)
 values(vendor,'TEST-CONTROLS','Synthetic Control Test','Singapore','Corporation');
 if not exists(select 1 from public.wht_reconciliation where vendor_request_id=vendor and missing_determination) then
  raise exception 'Completeness control failed';
 end if;
 insert into public.wht_determinations(vendor_request_id,category,regime,rate,confidence,
  amount_known,gross_amount,withholding_amount,audit_trail,payment_snapshot,document_snapshot)
 values(vendor,'royalty_copyright','FDAP',30,'needs_review',true,100000,30000,
  '[{"rule":"engine","detail":"test"}]','{"payment_id":"TEST"}',
  '{"form":"W-8BEN-E","expiration_date":"2029-12-31"}') returning id into det;
 update public.wht_determinations set rationale='Revised pending proposal' where id=det;
 select proposal_version into ver from public.wht_determinations where id=det;
 if ver<>2 then raise exception 'Proposal version did not increment'; end if;
 update public.wht_determinations set review_status='approved',reviewer_name='Tester'
 where id=det and review_status='pending' and proposal_version=1;
 get diagnostics n=row_count;
 if n<>0 then raise exception 'Stale review accepted'; end if;
 begin
  update public.wht_determinations set review_status='overridden',reviewer_name='Tester',override_rate=10 where id=det;
  raise exception 'Unreasoned override accepted' using errcode='XX000';
 exception when raise_exception then null;
 end;
 update public.wht_determinations set review_status='overridden',reviewer_name='Tester',override_rate=10,
  override_reasoning='Synthetic manual evidence' where id=det and proposal_version=2;
 if not exists(select 1 from public.wht_review_events where determination_id=det and final_amount=10000 and final_rate=10) then
  raise exception 'Review evidence missing or wrong';
 end if;
 if (select count(*) from public.wht_proposal_versions where determination_id=det)<>2 then
  raise exception 'Proposal history lost';
 end if;
 if not exists(select 1 from public.wht_reconciliation where determination_id=det and final_amount=10000 and not amount_mismatch and not missing_review_evidence) then
  raise exception 'Reconciliation incorrect';
 end if;
 begin
  update public.wht_determinations set rate=0 where id=det;
  raise exception 'Reviewed proposal changed' using errcode='XX000';
 exception when raise_exception then null;
 end;
 if public.wht_round_money(0.005)<>0 or public.wht_round_money(0.015)<>0.02 then
  raise exception 'Money rounding differs from Python Decimal';
 end if;
 -- Unknown onboarding amounts must never appear as real zero-dollar payments.
 insert into public.wht_determinations(vendor_request_id,category,confidence)
 values(vendor,'unclassified','skipped');
 if not exists(select 1 from public.wht_reconciliation where vendor_request_id=vendor and category='unclassified' and missing_payment_amount and final_amount is null) then
  raise exception 'Unknown amount disappeared';
 end if;
end $$;
rollback;
