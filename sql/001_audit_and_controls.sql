-- Apply after the existing VendorHub migrations (or the disposable bootstrap).
-- Additive: existing portal summary columns remain. No Oracle integration.
begin;
alter table public.wht_determinations
 add column if not exists audit_trail jsonb not null default '[]',
 add column if not exists rate_table_version text,
 add column if not exists needs_review boolean not null default true,
 add column if not exists amount_known boolean not null default false,
 add column if not exists gross_amount numeric,
 add column if not exists withholding_amount numeric,
 add column if not exists payment_snapshot jsonb not null default '{}',
 add column if not exists document_snapshot jsonb not null default '{}',
 add column if not exists vendor_snapshot jsonb not null default '{}',
 add column if not exists proposal_version integer not null default 1;

-- Versioned snapshots keep the exact facts and proposed result used at review.
create table public.wht_proposal_versions (
 determination_id uuid not null references public.wht_determinations(id),
 proposal_version integer not null,
 proposal jsonb not null,
 recorded_at timestamptz not null default now(),
 primary key(determination_id,proposal_version)
);
create table public.wht_payment_snapshots (
 determination_id uuid not null,
 proposal_version integer not null,
 amount_known boolean not null,
 gross_amount numeric,
 facts jsonb not null,
 primary key(determination_id,proposal_version),
 foreign key(determination_id,proposal_version) references public.wht_proposal_versions
);
create table public.wht_document_snapshots (
 determination_id uuid not null,
 proposal_version integer not null,
 facts jsonb not null, extraction jsonb,
 primary key(determination_id,proposal_version),
 foreign key(determination_id,proposal_version) references public.wht_proposal_versions
);
create table public.wht_review_events (
 id uuid primary key default gen_random_uuid(),
 determination_id uuid not null,
 proposal_version integer not null,
 decision text not null, reviewer_name text not null, reasoning text,
 final_rate numeric, final_amount numeric,
 reviewed_at timestamptz not null default now(),
 foreign key(determination_id,proposal_version) references public.wht_proposal_versions
);

-- This local demo uses the server-held service key. New evidence tables have
-- no anonymous/browser policies. Existing VendorHub portal policies are not changed.
alter table public.wht_proposal_versions enable row level security;
alter table public.wht_payment_snapshots enable row level security;
alter table public.wht_document_snapshots enable row level security;
alter table public.wht_review_events enable row level security;
revoke all on public.wht_proposal_versions,public.wht_payment_snapshots,
 public.wht_document_snapshots,public.wht_review_events from public,anon,authenticated,service_role;
grant select on public.wht_proposal_versions,public.wht_payment_snapshots,
 public.wht_document_snapshots,public.wht_review_events to service_role;

create function public.wht_proposal_body(record jsonb) returns jsonb
language sql immutable set search_path = '' as $$
 select record - array['review_status','reviewer_name','override_rate',
  'override_reasoning','reviewed_at','proposal_version','updated_at'];
$$;

create function public.wht_guard_change() returns trigger
language plpgsql set search_path = '' as $$
declare changed boolean;
begin
 if tg_op = 'DELETE' then
  raise exception 'Determinations are retained for audit; deletion is disabled';
 end if;
 if tg_op = 'UPDATE' then
  changed := public.wht_proposal_body(to_jsonb(new)) is distinct from public.wht_proposal_body(to_jsonb(old));
  if old.review_status in ('approved','overridden','rejected') then
   if to_jsonb(new) - 'updated_at' is distinct from to_jsonb(old) - 'updated_at' then
    raise exception 'Reviewed proposal is immutable; submit a new case for changed facts';
   end if;
   return new;
  end if;
  if changed and new.review_status in ('approved','overridden','rejected') then
   raise exception 'Save new facts before reviewing the new proposal';
  end if;
  new.proposal_version := old.proposal_version + case when changed then 1 else 0 end;
 else
  new.proposal_version := 1;
  if new.review_status <> 'pending' then
   raise exception 'New proposals must start pending';
  end if;
 end if;
 if new.amount_known then
  if new.gross_amount is null or not (new.gross_amount >= 0 and new.gross_amount < 'Infinity'::numeric) then
   raise exception 'Known amount must be finite and non-negative';
  end if;
 elsif new.gross_amount is not null or new.withholding_amount is not null then
  raise exception 'Unknown amounts must remain NULL';
 end if;
 if jsonb_typeof(new.audit_trail) <> 'array' then raise exception 'Audit trail must be an array'; end if;
 if new.rate is not null and not (new.rate >= 0 and new.rate <= 100) then
  raise exception 'Proposal rate must be between 0 and 100';
 end if;
 -- Other existing VendorHub statuses (e.g. Ask Vendor) remain governed by
 -- VendorHub's own constraints. Only final decisions are validated here.
 if new.review_status in ('approved','overridden','rejected') then
  if nullif(btrim(new.reviewer_name),'') is null then raise exception 'Reviewer required'; end if;
  if new.review_status in ('approved','overridden') and
    (new.confidence = 'skipped' or new.regime is null) then
   raise exception 'Unclassified proposals cannot be approved';
  end if;
  if new.review_status = 'approved' and new.rate is null then raise exception 'Cannot approve an unknown rate'; end if;
  if new.review_status in ('overridden','rejected') and nullif(btrim(new.override_reasoning),'') is null then
   raise exception 'Reasoning required';
  end if;
  if new.review_status = 'overridden' then
   if new.override_rate is null or not (new.override_rate >= 0 and new.override_rate <= 100) then
    raise exception 'Override rate must be between 0 and 100';
   end if;
  elsif new.override_rate is not null then raise exception 'Only overrides may carry a rate';
  end if;
  new.reviewed_at := clock_timestamp();
 end if;
 return new;
end;
$$;
create trigger wht_guard_change before insert or update or delete on public.wht_determinations
 for each row execute function public.wht_guard_change();

-- A trigger makes the summary write, snapshots and review event one transaction.
-- SECURITY DEFINER is limited to this trigger; callers cannot invoke it as an RPC.
create function public.wht_record_evidence() returns trigger
language plpgsql security definer set search_path = '' as $$
declare effective_rate numeric;
begin
 insert into public.wht_proposal_versions(determination_id,proposal_version,proposal)
 values(new.id,new.proposal_version,public.wht_proposal_body(to_jsonb(new))) on conflict do nothing;
 insert into public.wht_payment_snapshots values
 (new.id,new.proposal_version,new.amount_known,new.gross_amount,new.payment_snapshot) on conflict do nothing;
 insert into public.wht_document_snapshots values
 (new.id,new.proposal_version,new.document_snapshot,new.vendor_snapshot->'extraction') on conflict do nothing;
 if tg_op = 'UPDATE' and old.review_status not in ('approved','overridden','rejected') and new.review_status in ('approved','overridden','rejected') then
  effective_rate := case new.review_status when 'approved' then new.rate when 'overridden' then new.override_rate end;
  insert into public.wht_review_events(determination_id,proposal_version,decision,reviewer_name,reasoning,final_rate,final_amount,reviewed_at)
  values(new.id,new.proposal_version,new.review_status,new.reviewer_name,new.override_reasoning,effective_rate,
   case when new.amount_known then public.wht_round_money(new.gross_amount * effective_rate / 100) end,new.reviewed_at);
 end if;
 return new;
end;
$$;
revoke all on function public.wht_record_evidence() from public,anon,authenticated,service_role;
create trigger wht_record_evidence after insert or update on public.wht_determinations
 for each row execute function public.wht_record_evidence();

-- Match the engine's Decimal quantize default: ties round to an even cent.
create function public.wht_round_money(amount numeric) returns numeric
language sql immutable set search_path = '' as $$
 select case when abs(amount*100-trunc(amount*100))=0.5
  and mod(abs(trunc(amount*100)),2)=0 then trunc(amount*100)/100
  else round(amount,2) end;
$$;

create view public.wht_reconciliation with (security_invoker=true) as
select v.id as vendor_request_id,v.vendor_number,v.legal_name,d.id as determination_id,
 d.category,d.proposal_version,d.review_status,d.needs_review,d.amount_known,d.gross_amount,
 d.rate as proposed_rate,d.withholding_amount as proposed_amount,
 case d.review_status when 'approved' then d.rate when 'overridden' then d.override_rate end as final_rate,
 case when d.amount_known and d.review_status in ('approved','overridden') then
  public.wht_round_money(d.gross_amount * case d.review_status when 'approved' then d.rate else d.override_rate end / 100)
 end as final_amount,
 d.id is null as missing_determination,
 d.id is not null and not d.amount_known as missing_payment_amount,
 d.id is not null and (d.document_snapshot = '{}' or coalesce(d.document_snapshot->>'form','none_on_file')='none_on_file') as missing_document,
 case when d.document_snapshot->>'expiration_date' is not null then
  (d.document_snapshot->>'expiration_date')::date < current_date else false end as expired_document,
 d.id is not null and jsonb_array_length(d.audit_trail)=0 as missing_audit,
 d.confidence='skipped' as unclassified,
 case when d.amount_known and d.rate is not null then
  d.withholding_amount is distinct from public.wht_round_money(d.gross_amount*d.rate/100) else false end as amount_mismatch,
 d.review_status in ('approved','overridden','rejected') and not exists (
  select 1 from public.wht_review_events r where r.determination_id=d.id and r.proposal_version=d.proposal_version
 ) as missing_review_evidence
from public.vendor_tax_requests v left join public.wht_determinations d on d.vendor_request_id=v.id;
revoke all on public.wht_reconciliation from public,anon,authenticated;
grant select on public.wht_reconciliation to service_role;
commit;
