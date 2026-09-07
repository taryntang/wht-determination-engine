# WHT Determination Engine — interview demo

A small Python rules engine with Supabase persistence, human review, audit
history, SQL controls, and optional Claude-assisted W-8BEN-E extraction.
**Synthetic/demo use only.** Sample treaty data and unmodeled tax questions
still require expert review. Oracle integration is future-state only.

## What is implemented

| Component | Implementation | Verification in this update |
|---|---|---|
| Tax rules | Existing deterministic `wht_engine/`, versioned sample treaty CSV, full rule audit | Existing and new offline Python tests |
| Vendor intake | Existing VendorHub `vendor_tax_requests`; identity/contact plus extraction provenance snapshotted with each proposal | Mapping tests; external VendorHub unchanged |
| Persistence | Existing Supabase REST adapter extended with complete proposals; migration adds versioned payment/document snapshots and review events | HTTP boundary tests; SQL integration script supplied, **not run against PostgreSQL here** |
| Human review | Existing Streamlit Approve / Override / Reject; validation, displayed-version concurrency protection, immutable completed decisions | Python control tests; **Streamlit not launched here** |
| Reconciliation | SQL view + control totals for missing cases, amounts, documents, audit, reviews and calculation mismatches | SQL test script supplied; **not executed here** |
| W-8BEN-E extraction | Optional local Claude Messages API adapter, forced tool output, strict validation, source hash, model/schema provenance | Mock HTTP + canned fixture tests; **no live Claude accuracy test** |
| Oracle | No API client, posting, or configuration changes | Future-state only |

The original VendorHub-hosted extractor and authenticated portal are separate
applications. This change does not modify or deploy either one. The local
extractor makes this repo independently demonstrable; it does not replace
VendorHub's uploader or claim to persist original PDF bytes.

## Architecture and the AI boundary

```text
VendorHub intake OR local PDF + caller-supplied payment facts
          |                    |
          |              Claude extracts fields (unverified)
          |                    |
          +----------> adapter validates and maps
                              |
                   deterministic Python tax rules
                              |
             Supabase proposal + exact input/audit snapshots
                              |
                 human Approve / Override / Reject
                              |
           atomic review event + final rate / amount in SQL view
                              |
               reconciliation controls (no ERP posting)
```

`wht_engine/` never imports Claude, HTTP, or database code. The local extractor
records a rate *claimed on a form* as evidence; only Python rules and reference
data propose the tax rate. Entity classification, income type, source, ECI,
amount and payment date are explicit caller inputs, not inferred by Claude.
All extraction output remains unverified and flagged for human review.

The existing optional `StaticThenLiveTreatyTable` also uses Claude to read IRS
reference PDFs on a sample-table miss. Its input data is model-assisted, so
**a run with that adapter is not fully deterministic end to end**. Fixed
payment facts and fixed reference inputs make the Python rules reproducible.
The local interview demo uses the static sample table. The persisted audit,
rate-table label and proposal preserve the output evidence, but live IRS PDF
bytes and the entire live-lookup response are not archived by this update.

## Run without credentials or extra packages

Python 3.10+:

```bash
python3 demo/run_tests.py
python3 demo/run_demo.py
python3 demo/run_w8_review.py --fixture
```

The test runner uses standard-library unittest and also runs the original
function-style tests; `pytest -q` remains supported if pytest is installed.
The fixture demo does **not read a PDF or call Claude**. It supplies clearly
labeled canned fields for Synthetic Singapore Ltd and real payment facts:
$100,000 U.S.-source copyright royalty, 2026-09-07. The engine proposes 30%
($30,000), retaining its audit trail and extraction-review notes. Nothing is
saved remotely unless `--persist` is explicitly supplied.

## Supabase setup and review demo

1. Use a disposable Supabase/PostgreSQL 15+ project for the first test.
   Run `sql/000_disposable_demo_bootstrap.sql`, then
   `sql/001_audit_and_controls.sql` once in the SQL editor. The bootstrap is
   a minimal synthetic intake schema, **not a copy of VendorHub's schema**.
2. For an existing VendorHub project, do **not** run the bootstrap. Review
   migration 001 against that project's current schema, constraints and
   triggers in staging first. Its base tables are owned by VendorHub.
   This update has not been deployed or tested for portal compatibility.
3. Set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` as environment variables.
   Keep the key server-side. The new evidence tables/view are denied to
   `anon` and `authenticated`; only the server role can read them. Existing
   VendorHub policies are left intact. See `.env.example` (the existing
   VendorHub CLI/UI load `.env`; the new extraction CLI reads environment
   variables directly).
4. Save the synthetic payment into the bootstrap's synthetic vendor:

```bash
python3 demo/run_w8_review.py --fixture --persist
pip install -r review_ui/requirements.txt
streamlit run review_ui/app.py --server.address 127.0.0.1
```

Inspect the payment/document snapshots and rule audit. Enter reviewer
attribution, then approve the proposed rate, override with a reason, or
reject with a reason. The standalone UI has **no authentication**: keep it
local. A typed reviewer name is attribution, not verified identity.
VendorHub's separate portal uses its own authentication.

Run `sql/002_control_queries.sql` to inspect control totals and exceptions.
An override retains the original proposal and records the final rate/amount
separately. A rejected or pending case has no final amount. Amounts are USD
only in this demo; there is no currency conversion or invoice feed.

For existing onboarding rows, use `python3 demo/run_from_vendorhub.py` after
staging the migration. Onboarding has no payment amount: its internal zero
placeholder is saved as **NULL**, not as a real zero-dollar payment. Such a
case may receive a rate decision, but cannot produce a final dollar amount.

## Review and audit rules

- Every new proposal starts pending, including high-confidence results.
  `needs_review` describes exceptions; it is separate from approval status.
- Pending re-runs retain proposal versions and their exact payment,
  documentation, vendor/extraction snapshots. Identical proposals do not
  increment the version.
- The database writes snapshots and reviewer events inside the transaction
  that saves the determination; there is no partial multi-request save.
- Reviewed proposals cannot be rewritten or deleted. Changed facts require
  a new VendorHub intake/case; a reopen/re-review UI is not implemented.
  A batch attempting to change a reviewed proposal fails visibly.
- Reviews from this UI match both pending status and the version actually
  shown to the reviewer. If it changes, reload and inspect it before saving.
  The external VendorHub portal must adopt this version check separately.
- Overrides require a finite rate from 0 to 100 and a reason. Rejections
  require a reason. Unknown/unclassified results cannot simply be approved.
- Evidence predating the migration is not reconstructed: SQL reports missing
  audit/review evidence. Snapshot absence is an exception, not proof of completeness.
- SQL rounding matches the engine's Decimal half-even rounding.

## Live local W-8BEN-E extraction

Set `ANTHROPIC_API_KEY` and `WHT_AGENT_MODEL` to a model enabled for your
account. Then provide a PDF and explicit payment facts:

```bash
python3 demo/run_w8_review.py --pdf synthetic-w8bene.pdf \
  --vendor-request-id YOUR_EXISTING_VENDOR_REQUEST_UUID \
  --vendor-number DEMO-SG --amount 100000 --payment-date 2026-09-07 \
  --income-type royalty_copyright --entity-type corporation
```

This sends the PDF to Claude. Add `--foreign-source` or `--eci` only when
supported by the payment facts; the defaults are U.S.-source, non-ECI.
Add `--persist` to save. Missing/invalid fields are flagged or rejected,
never silently coerced from strings to booleans. The PDF's SHA-256 is
retained for identification, but the original file must be retained by the
caller/VendorHub. Hashing alone is not document storage or extraction accuracy.

The implementation follows [Claude tool-use documentation](https://platform.claude.com/docs/claude/docs/tool-use).
Database access follows [Supabase's server-key guidance](https://supabase.com/docs/guides/database/secure-data)
and [RLS guidance](https://supabase.com/docs/guides/database/postgres/row-level-security).

## Verification and interview walkthrough

This update ran 72 Python tests: **71 passed, 1 PostgreSQL integration test
skipped** because PostgreSQL tooling/connection was unavailable. The offline
fixture and existing CSV demo also ran. Supabase migrations, live API calls,
and the Streamlit browser flow were not exercised in this environment.

After applying the schema to a disposable project, run:

```bash
psql -v ON_ERROR_STOP=1 -f sql/test_controls.sql
```

Use standard PostgreSQL connection environment variables. Alternatively set
`WHT_TEST_DATABASE_URL` with `psql` installed and run the Python suite; it
runs that SQL test. The SQL script rolls back its test data and checks
versioning, stale decisions, invalid overrides, immutable approvals,
review evidence, rounding and reconciliation.

Read [the interview walkthrough](docs/interview-guide.md) to trace one
payment through the files and explain what each layer owns.

## Limitations (intentionally not modeled, or simplified)

- **Treaty rate table is sample/demo data, 11 countries only.** A country
  simply absent from it (e.g. Cyprus) means "not in this sample," not
  "confirmed no treaty" — Singapore is the one row that's an actual
  confirmed-no-treaty case. `adapters/live_treaty_lookup.py`'s
  `StaticThenLiveTreatyTable` closes this gap by falling back to a live
  fetch of the real IRS Table 1 + Table 3 PDFs (read by Claude) whenever
  the static table has no row for a country/income-type pair — opt in by
  passing it as `determine_withholding()`'s `treaty_table` argument with
  `ANTHROPIC_API_KEY` set; both `demo/run_from_vendorhub.py` and
  VendorHub's webhook already do this. **No rate limit on this path** —
  each call is a real billable Claude API request, and VendorHub's public
  form has no rate limit tied to treaty-lookup misses specifically, so a
  submission burst using countries outside the static sample can drive
  real, uncapped API spend. An earlier version capped this at 2/hour;
  removed by explicit request after that risk was raised. Table 4 (LOB) still isn't checked by
  either path — see the next bullet.
- **LOB (Table 4) is flagged, not verified.** The engine notes that
  Limitation-on-Benefits eligibility should be confirmed for any treaty
  claim, especially for entity payees, but does not implement that test.
- **Direct-dividend ownership thresholds** (many treaties reduce the
  dividend rate further above a 10%+ ownership stake) are not modeled —
  flagged in the sample rate table's footnote column instead.
- **Full intermediary look-through** (tracing a payment through a
  nonqualified intermediary or non-WFP foreign partnership to each
  underlying beneficial owner) is not implemented — the engine applies the
  same conservative fallback a human preparer would (withhold at the
  statutory rate on the unaccounted-for portion) and flags for manual
  look-through.
- **Tiered partnership structures** are flagged, not walked through.
  **FATCA** (§§1471-1474) is out of scope entirely, per the source
  analysis this engine is built from — flagged when a payee looks like it
  could need a separate FATCA analysis.
- **Backup withholding (§3406)** for U.S. payees is explicitly out of
  scope — the engine flags a non-foreign payee and stops rather than
  attempting that different regime.
- **The top individual rate constant** (`partnership_eci.py`) is a point-in-
  time figure that should live in versioned reference data in a real
  deployment, the same way the treaty table does — flagged as a TODO.

## Future-state

Oracle supplier/payment configuration APIs, AP/GL booking reconciliation,
production reviewer authorization, original-PDF retention in this repo,
full reference-input archival, and reopen/re-review workflow remain future
work. This portfolio demo must not be described as a production tax system
or as having posted withholding to Oracle.
