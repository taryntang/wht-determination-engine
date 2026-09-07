# WHT Determination Engine (demo)

A deterministic, testable rules engine that determines whether a payment to
a foreign payee requires U.S. withholding tax — and if so, under which
regime, at what rate, and with what documentation — built as a portfolio
piece demonstrating the "vendor hub → automated determination → human
review → push to AP/ERP" architecture described in the accompanying
project notes.

**This is a demo, not a production system.** It runs on synthetic vendor
data, its treaty rate table is illustrative sample data (not the live IRS
tables), and several areas are explicitly flagged rather than fully modeled
(see Limitations below). Every one of those gaps is called out in-code and
in the engine's own output — that's deliberate, not an oversight: a tool
like this should say "I don't know, ask a human" rather than quietly guess.

## Why it's built this way

The core design choice: **the actual tax determination is a deterministic,
versioned rules engine — not an LLM call.** An LLM layer sits *around* this
engine — VendorHub's `w8-extract` function uses Claude to read an uploaded
W-8 PDF and pull out treaty-claim data — but the determination itself stays
plain, testable Python with no model in the loop, so every rate/regime
result is reproducible and defensible to an auditor even though the
document-reading step that feeds it isn't. See `wht_engine/engine.py`'s
docstring, and VendorHub's `w8-extract/README.md` for how that boundary is
kept: extraction output is written to its own columns, flagged as
unverified, and never presented as more certain than it is.

This mirrors a real control gap: at a company where vendor onboarding
doesn't flag withholding-relevant vendors to AP, payments go out without
tax being considered until someone catches it after the fact. This engine
is the automated-determination piece of fixing that — proposing a result at
onboarding time, before AP ever cuts a check, with a human tax reviewer
still required to approve before anything downstream happens.

## Architecture

```
vendor hub data (CSV here; a real system's vendor master + payment feed)
        |
        v
   regime.py          <- Step 1: which regime governs (FDAP / FIRPTA / 1446 / 1446(f) / payroll / scholarship / self-reported ECI)
        |
        v
 fdap.py / firpta.py / partnership_eci.py   <- Step 2-4: rate determination for that regime
        |
        v
  documentation.py    <- Step 5: validate what's on file, say what SHOULD be on file
        |
        v
   engine.py           <- orchestrates all of the above into one Determination,
                           with a full audit trail (which rule fired, which
                           citation, which rate-table version)
        |
        v
  [not built here] human review queue -> approval -> push to AP / Oracle Party Tax Profile API
```

The treaty rate table (`wht_engine/data/treaty_rates_table1_sample.csv`) is
deliberately external, versioned reference data rather than rate literals
buried in the code — the same principle a real deployment would use to keep
"which rate applied to this vendor eight months ago" answerable.

## Running it

```bash
# tests
pip install pytest --break-system-packages   # or your usual venv workflow
pytest -q

# demo: run a synthetic vendor feed through the engine
python3 demo/run_demo.py
```

`demo/run_demo.py` reads `demo/synthetic_vendors.csv` (12 synthetic
vendors covering every regime the engine models), runs each through
`determine_withholding()`, prints a reviewer-style summary, and writes
`demo/output_determinations.csv` — the shape of what a review queue or an
AP notification feed would consume.

## What's modeled

- Full regime classification (Step 1): FIRPTA, partnership ECI (§1446 and
  §1446(f)), payroll/Form 8233 routing, §871(c) scholarship withholding,
  self-reported ECI, and the FDAP catch-all.
- FDAP six-factor test, treaty-rate lookup against a versioned reference
  table, statutory 30% default, portfolio-interest and bank-deposit-interest
  exceptions, and simplified intermediary handling (QI/WFP/US-branch-election/
  domestic-partnership shift the obligation; NQI/foreign-partnership without
  full look-through default conservatively to the statutory rate and flag
  for manual look-through).
- FIRPTA: USRPI test, seller/corporate/residence/QFPF exemptions, 15%
  general rate, 21% domestic-partnership-disposition rate (with a citation
  caveat carried over from the source analysis — flagged for verification).
- §1446 distributive-share withholding (21% corporate partner / top
  individual rate) and §1446(f) 10% buyer withholding with the
  certification and de-minimis exceptions.
- Documentation validation: form-vs-payee-type mismatches, expiration
  checks, and a documentation-required checklist per regime.
- A full audit trail on every determination (`Determination.audit_trail`),
  a rate-table version stamp, and a `confidence` flag (`needs_review`)
  whenever the engine hits a gap in its own modeling rather than guessing.

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

## Connecting to VendorHub (real intake data)

[VendorHub](https://github.com/taryntang/vendorhub) is the vendor-facing
intake form this engine's "vendor hub" input is modeled on. `adapters/vendorhub.py`
reads real `vendor_tax_requests` rows over VendorHub's Supabase REST API and
maps them into candidate `Payment`/`Payee` objects; `demo/run_from_vendorhub.py`
runs those through `determine_withholding()` the same way `demo/run_demo.py`
runs the synthetic CSV, then **persists each result to a `wht_determinations`
table** in the same Supabase project (see VendorHub's
`supabase/migrations/20260906141541_add_wht_determinations.sql`) — keyed on
`(vendor_request_id, category)`, so re-running updates existing rows rather
than duplicating them, and never touches a row's review fields once a human
has reviewed it.

```bash
cp .env.example .env   # fill in SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
python3 demo/run_from_vendorhub.py
```

Reading and writing requires VendorHub's Supabase `service_role` key
(Project Settings -> API in the Supabase dashboard) — the public anon key
embedded in VendorHub's HTML is insert-only on two other tables and has no
access to `wht_determinations` at all (see VendorHub's `SECURITY.md`). The
service_role key is a real secret: it lives only in a local, gitignored
`.env` (see `.env.example`), never in code or on the command line.

**This mapping is intentionally partial, not a shortcut taken carelessly.**
VendorHub is an onboarding form, not an invoice feed, so several things the
engine wants are structurally absent from it:

- **No payment amount.** Every candidate's `gross_amount` is a `Decimal("0")`
  placeholder — `regime`, `rate`, and `citation` are meaningful, `withholding_amount`
  is not, until a real payment is known.
- **Treaty claim details, when available, come from an async extraction step,
  not the intake form itself.** VendorHub's `vendorhub/w8-extract` function
  reads the actual uploaded W-8 PDF (form fields first, Claude as a fallback
  for flattened/scanned copies) and writes `w8_treaty_country_claimed` /
  `w8_treaty_article` / `w8_signed_date` / `w8_expiration_date` back onto the
  row once it completes — `_build_documentation()` here picks those up when
  present. Until extraction finishes (or if it finds no claim), the engine
  still correctly falls back to the 30% statutory rate, flagged on every
  affected row either way — extraction results are never independently
  verified, so a treaty rate applied this way still needs a human to confirm
  it against the physical document.
- **Some categories/entity types aren't modeled and are skipped rather than
  guessed**: sale of goods (not FDAP at all), an ambiguous patent-vs-copyright
  royalty subtype, and entity types of `Other` or `International organization`.
  Each skip carries a reason in the output rather than silently disappearing.
- **Services performed in the U.S. by a non-individual payee** (corporation,
  partnership, etc.) are mapped to the FDAP catch-all with `is_eci` set only
  if a W-8ECI is actually on file — not to personal-services/payroll routing,
  which only applies to individuals. Absent a W-8ECI, this conservatively
  withholds at the statutory rate pending one, rather than assuming a treaty
  permanent-establishment exception applies (which this engine doesn't verify).

## Review queue UI

**The real reviewer experience now lives in VendorHub itself**:
`public/portal.html` in the [VendorHub repo](https://github.com/taryntang/vendorhub),
gated behind real Supabase Auth (not a free-text name field) — its Tax
Review tab does the same Approve/Override/Reject job described below,
plus a fourth outcome (Ask Vendor) and a Reasoning Results tab, all
reading/writing `wht_determinations` directly from the browser via RLS.
See that repo's README and `SECURITY.md` for how staff access is scoped.

This repo also ships a lighter, standalone alternative that reads the
same table — a Streamlit app, still functional, useful for a quick local
look without deploying anything:

- vendor, proposed regime/rate/citation, rationale, confidence, flags —
  with Approve / Override / Reject actions. **Decisions are persisted
  immediately**, not session-only: Approve writes `review_status='approved'`;
  Override requires both a new rate and a reasoning text and writes
  `review_status='overridden'` with `override_rate`/`override_reasoning`;
  Reject requires a reasoning and writes `review_status='rejected'`. All
  three stamp `reviewer_name` (a free-text attribution field here — this
  standalone app has no login, unlike the portal above) and `reviewed_at`.
  The database's own CHECK constraints back this up independently of the
  UI (see the migration) — an unreasoned override or rejection is
  rejected at the database layer even if the UI's own check were ever
  bypassed.

```bash
pip install -r review_ui/requirements.txt
python3 demo/run_from_vendorhub.py   # populate wht_determinations first
streamlit run review_ui/app.py
```

## What isn't built (next steps toward the real architecture)

- Distinct staff roles/permissions in the VendorHub portal — any
  authenticated account currently has the same access as any other.
- Plain-English, LLM-generated reviewer rationale on top of this engine's
  structured output (distinct from W-8 *document extraction*, which
  VendorHub's `w8-extract` function now does via Claude).
- The push integration to Oracle's Party Tax Profile REST API (POST/PATCH)
  once IT confirms the tenant has that service enabled, gated on reviewer
  approval.
- Persisting the engine's full step-by-step `audit_trail` (not just the
  summary fields) to `wht_determinations`, and the SQL-side
  reconciliation/control-total reporting layer.
