"""
Review-queue prototype — the "not built yet" UI named in the engine's
README: vendor, proposed determination, rationale, confidence,
approve/edit/reject.

This is a viewer over a determinations CSV (produced by
demo/run_from_vendorhub.py or demo/run_demo.py), not a new data source.
Approve/reject decisions are held in Streamlit's session state only —
there is no datastore behind them yet (see README's "What isn't built"
list), so a page refresh resets them. That's a deliberate scope line, not
an oversight: persisting review decisions belongs with the real datastore
and audit-trail work called out there.

Run: streamlit run review_ui/app.py
"""

from __future__ import annotations

import csv
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV_CANDIDATES = [
    REPO_ROOT / "demo" / "output_vendorhub_determinations.csv",
    REPO_ROOT / "demo" / "output_determinations.csv",
]

st.set_page_config(page_title="WHT Review Queue", layout="wide")


def load_rows(csv_path: Path) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def default_csv_path() -> Path | None:
    for path in DEFAULT_CSV_CANDIDATES:
        if path.exists():
            return path
    return None


def row_key(row: dict, idx: int) -> str:
    return f"{row.get('vendor_id', '')}:{row.get('category', '')}:{idx}"


st.title("WHT Determination Review Queue")
st.caption(
    "Prototype reviewer view over the engine's proposed determinations. "
    "Approve/Reject is session-only — nothing here is persisted yet."
)

csv_path_input = st.sidebar.text_input(
    "Determinations CSV",
    value=str(default_csv_path() or DEFAULT_CSV_CANDIDATES[0]),
    help="Produced by demo/run_from_vendorhub.py (live VendorHub data) or demo/run_demo.py (synthetic).",
)
csv_path = Path(csv_path_input)

if not csv_path.exists():
    st.warning(
        f"No file found at `{csv_path}`. Run `python3 demo/run_from_vendorhub.py` "
        "(or `demo/run_demo.py` for synthetic data) first, then reload."
    )
    st.stop()

rows = load_rows(csv_path)
if not rows:
    st.info("The CSV is empty — nothing to review.")
    st.stop()

if "decisions" not in st.session_state:
    st.session_state.decisions = {}

confidence_options = sorted({r.get("confidence", "") for r in rows})
regime_options = sorted({r.get("regime", "") for r in rows if r.get("regime")})

with st.sidebar:
    st.subheader("Filters")
    selected_confidence = st.multiselect("Confidence", confidence_options, default=confidence_options)
    selected_regime = st.multiselect("Regime", regime_options, default=regime_options)
    vendor_search = st.text_input("Vendor name contains")

filtered = []
for idx, row in enumerate(rows):
    if row.get("confidence", "") not in selected_confidence:
        continue
    if row.get("regime") and row.get("regime") not in selected_regime:
        continue
    if vendor_search and vendor_search.lower() not in row.get("vendor_name", "").lower():
        continue
    filtered.append((idx, row))

total = len(rows)
needs_review = sum(1 for r in rows if r.get("confidence") == "needs_review")
skipped = sum(1 for r in rows if r.get("confidence") == "skipped")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total candidates", total)
c2.metric("Needs review", needs_review)
c3.metric("Skipped (unmapped)", skipped)
c4.metric("Showing", len(filtered))

st.divider()

CONFIDENCE_BADGE = {
    "high": "🟢",
    "needs_review": "🟡",
    "skipped": "⚪",
}

for idx, row in filtered:
    key = row_key(row, idx)
    decision = st.session_state.decisions.get(key, "pending")
    badge = CONFIDENCE_BADGE.get(row.get("confidence", ""), "")

    header = f"{badge} **{row.get('vendor_name', '(unknown)')}** — {row.get('category', '')}"
    if row.get("regime"):
        header += f" · {row['regime']}"
    if decision != "pending":
        header += f" · _{decision}_"

    with st.expander(header):
        left, right = st.columns([3, 1])
        with left:
            if row.get("confidence") == "skipped":
                st.write(f"**Skipped:** {row.get('notes', '')}")
            else:
                st.write(f"**Regime:** {row.get('regime', '')}")
                st.write(f"**Withholding required:** {row.get('withholding_required', '')}")
                st.write(f"**Rate:** {row.get('rate_pct', '')}%")
                st.write(f"**Citation:** {row.get('citation', '')}")
                st.write(f"**Rationale:** {row.get('rationale', '')}")
                if row.get("flags"):
                    st.write(f"**Flags:** {row['flags']}")
                if row.get("notes"):
                    st.write(f"**Notes:** {row['notes']}")
        with right:
            st.write(f"Vendor ID: `{row.get('vendor_id', '')}`")
            b1, b2, b3 = st.columns(3)
            if b1.button("Approve", key=f"approve-{key}"):
                st.session_state.decisions[key] = "approved"
                st.rerun()
            if b2.button("Edit", key=f"edit-{key}"):
                st.session_state.decisions[key] = "needs edit"
                st.rerun()
            if b3.button("Reject", key=f"reject-{key}"):
                st.session_state.decisions[key] = "rejected"
                st.rerun()
