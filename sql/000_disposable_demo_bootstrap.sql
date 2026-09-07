-- OPTIONAL: fresh disposable Supabase project ONLY. In the real VendorHub
-- project, its migrations own these two existing tables. Do not run this
-- bootstrap there. This is a minimal synthetic intake schema, not VendorHub.
begin;
create table public.vendor_tax_requests (
 id uuid primary key default gen_random_uuid(), vendor_number text not null,
 legal_name text not null, reg_country text not null, entity_type text not null,
 contact_name text, contact_email text, w8_type text, w8_filename text,
 w8_extraction_status text, w8_extraction_method text,
 w8_treaty_country_claimed text, w8_treaty_article text,
 w8_signed_date date, w8_expiration_date date,
 q1_goods text, q2_services text, q3_rental text, q3b_real_property text,
 q4_server text, q5_royalties text
);
create table public.wht_determinations (
 id uuid primary key default gen_random_uuid(),
 vendor_request_id uuid not null references public.vendor_tax_requests(id),
 category text not null, regime text, withholding_required boolean,
 rate numeric, citation text, rationale text, confidence text,
 flags text, notes text, engine_version text,
 review_status text not null default 'pending', reviewer_name text,
 override_rate numeric, override_reasoning text, reviewed_at timestamptz,
 created_at timestamptz not null default now(),
 unique(vendor_request_id,category)
);
alter table public.vendor_tax_requests enable row level security;
alter table public.wht_determinations enable row level security;
revoke all on public.vendor_tax_requests,public.wht_determinations from anon,authenticated;
grant select,insert,update on public.vendor_tax_requests,public.wht_determinations to service_role;
insert into public.vendor_tax_requests
 (id,vendor_number,legal_name,reg_country,entity_type,w8_type,w8_signed_date,w8_expiration_date,q4_server)
 values ('00000000-0000-0000-0000-000000000001','DEMO-SG','Synthetic Singapore Ltd','Singapore','Corporation','W-8BEN-E','2026-01-01','2029-12-31','A');
commit;
