# Interview workflow update — 2026-09-07

Baseline: GitHub main 365ed30621e7aa38156a5a8b4d9f8b5ddd3b0356.
Existing VendorHub intake, Supabase REST adapter and Streamlit review UI remain the integration path.

1. Preserve original engine rules; raise confidence to needs_review on document warnings and reject non-finite/negative money at the engine boundary. Regression tests first.
2. Extend the existing determination payload with full audit trail, table version, explicit amount-known state, input/document snapshots and extraction provenance. Keep unknown onboarding amounts NULL in persistence.
3. Add a transactional PostgreSQL migration: proposal versions, payment/document snapshots, append-only review events, immutable reviewed proposals and reconciliation views. Existing VendorHub vendor_tax_requests is the vendor master prerequisite; provide an optional disposable-project bootstrap.
4. Validate review input in Python; use pending status and expected proposal version for optimistic concurrency. Show audit details and final values in Streamlit. No Oracle calls.
5. Add a dependency-free Claude W-8BEN-E extraction adapter with strict response validation, missing-field flags and source hash. Keep the actual API call injectable and never allow extracted rate to determine tax. Provide explicit fixture mode and a CLI that produces candidate inputs without modifying the external VendorHub project.
6. Run every existing and new Python test through unittest-compatible discovery; preserve pytest compatibility. Add opt-in PostgreSQL integration tests and disclose if PostgreSQL/live Supabase/Claude/UI cannot run here.

The existing live IRS/Claude treaty lookup is preserved as an optional unverified reference-data source. Its outputs must be marked for review; deterministic rules are reproducible only when reference inputs are fixed. No additional live calls are made during tests.
