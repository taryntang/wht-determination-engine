# Explain one payment, file by file

Start with the synthetic demo: `python3 demo/run_w8_review.py --fixture`.
Its extracted fields are canned. The Python calculation is real.

1. `demo/run_w8_review.py` supplies the vendor key, entity type, income type,
   source, amount and date. The CLI deliberately separates payment facts
   from the document's statements.
2. `adapters/w8_extract.py::extract_w8` either calls Claude with a forced
   extraction tool or accepts an explicitly labeled fixture. It validates
   every field and records a source hash, model, schema version and method.
   `candidate_from_extraction` maps fields into existing Python dataclasses;
   the claimed rate is retained but never assigned to the engine's rate.
3. `wht_engine/models.py` defines `Payee`, `Documentation`, `Payment` and
   `Determination`. Decimal represents money. Unknown money in onboarding
   remains distinguishable from an actual zero-dollar amount in storage.
4. `wht_engine/engine.py::determine_withholding` classifies the regime,
   delegates to its rate module, checks documentation and appends rule
   events. `fdap.py` consults a versioned reference table. None imports AI.
5. `adapters/vendorhub.py::upsert_determination` serializes exact input
   dataclasses and output events. One Supabase REST write saves a pending
   proposal. `amount_known=False` converts the internal onboarding placeholder
   to SQL NULL. It never sends reviewer fields when recalculating a proposal.
6. `sql/001_audit_and_controls.sql` makes that write transactional. Before
   saving, a trigger checks the transition and protects reviewed records.
   After saving, another trigger stores proposal/payment/document snapshots
   and, on a final decision, a separate review event.
7. `review_ui/app.py` displays the audit and proposed facts. `state.py`
   remembers the version actually displayed across Streamlit reruns.
   `set_review_decision` validates the decision and uses a conditional update
   for the displayed pending version; zero updated rows means a conflict.
8. `wht_reconciliation` retains the proposed rate and derives the final rate
   from approval/override. Pending and rejected cases never become final
   amounts. It starts with vendors and left-joins results, so missing results
   appear as exceptions rather than disappearing.

## Useful interview questions

**What did AI decide?** It extracted unverified document fields. The ordinary
engine path uses Python rules and sample reference data. An optional existing
live treaty-table adapter also reads IRS references with Claude; that path's
inputs are not deterministic and should be described honestly.

**What survives a failure?** Supabase keeps the committed proposal or review.
Snapshot creation and the summary write are one database transaction. A failed
transaction doesn't leave a half-saved review. API failures surface to the caller.

**What if two reviewers click?** The first pending/version-matched update wins.
The other gets no row and must reload. The database also prohibits rewriting
completed decisions. The UI submits the version shown before its button rerun.

**Can someone approve an exception?** Yes, human judgment is the point. Exception
flags stay in the proposal. Approval doesn't erase them. A proposal without a
rate or classification cannot simply be approved; overrides need reasoning.

**Is this SOX compliant?** It demonstrates controls and evidence, not certification.
The local UI has no authenticated identity, and production access, separation
of duties and retention need further design. Do not claim company implementation
work that this synthetic portfolio project doesn't demonstrate.

**Is SQL reconciling to Oracle?** No. These controls reconcile vendor intake to
candidate determinations and evidence. There is no invoice, GL or ERP feed.

**What was verified?** Offline Python logic and HTTP boundaries, plus the fixture
and CSV demo. The PostgreSQL integration script is supplied but was not run here.
Live extraction accuracy, deployed Supabase behavior and Streamlit UI require
an environment with those dependencies before claiming them as verified.
