-- Source-to-determination completeness: count vendors DISTINCTLY to avoid
-- inflating control totals when one vendor has several income categories.
select count(distinct vendor_request_id) as vendors,
 count(distinct vendor_request_id) filter(where missing_determination) as vendors_without_determination,
 count(determination_id) as candidates,
 count(*) filter(where review_status='pending') as pending,
 count(*) filter(where unclassified) as unclassified,
 count(*) filter(where missing_payment_amount) as amounts_unknown,
 count(*) filter(where missing_document or expired_document) as document_exceptions,
 count(*) filter(where missing_audit or missing_review_evidence) as evidence_exceptions,
 count(*) filter(where amount_mismatch) as amount_mismatches,
 sum(final_amount) as approved_candidate_withholding
from public.wht_reconciliation;

select * from public.wht_reconciliation
where missing_determination or missing_payment_amount or missing_document
 or expired_document or missing_audit or missing_review_evidence or amount_mismatch
 or unclassified or review_status='pending';

-- These are candidate-level controls, not an AP/GL reconciliation.
-- No invoice feed, booking feed, currency conversion, or Oracle integration exists.
