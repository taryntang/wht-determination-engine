"""
Review-queue prototype — the human-review step named in the engine's
README: vendor, proposed determination, rationale, confidence, and a
reviewer's Approve / Override (rate + reasoning) / Reject decision.

Reads and writes wht_determinations directly (via adapters.vendorhub,
using the Supabase service_role key — from a local .env for `streamlit
run`, or from Streamlit Community Cloud's Secrets when deployed there;
never the public anon key). Unlike the earlier CSV-viewer prototype,
decisions here are
persisted: an Approve/Override/Reject click writes review_status,
override_rate, override_reasoning, reviewer_name, and reviewed_at back to
the row, and the database's own CHECK constraints (see the migration)
reject an override with no rate/reasoning or a rejection with no
reasoning — the UI mirrors those rules so a reviewer sees why a save
failed rather than a raw Postgres error.

Run: streamlit run review_ui/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.vendorhub import fetch_determinations, set_review_decision
from review_ui.state import displayed_version


def _load_dotenv(path: Path) -> None:
    import os

    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _load_cloud_secrets() -> None:
    """Streamlit Community Cloud injects secrets via st.secrets, not OS env
    vars. adapters.vendorhub reads os.environ, so bridge the two here
    rather than changing that module's credential-reading for one hosting
    target. No-op locally, where st.secrets is empty."""
    import os

    try:
        for key in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
            if key in st.secrets:
                os.environ.setdefault(key, st.secrets[key])
    except Exception:
        pass  # no secrets.toml and nothing configured -- fine for local runs


_load_dotenv(Path(__file__).resolve().parent.parent / ".env")
_load_cloud_secrets()

st.set_page_config(page_title="WHT Review Queue", layout="wide")

st.title("WHT Determination Review Queue")
st.caption(
    "Live view of wht_determinations. Approve / Override / Reject decisions "
    "are persisted immediately — this is the real reviewer step, not a demo file viewer."
)

reviewer_name = st.sidebar.text_input(
    "Your name (recorded as reviewer_name)",
    value=st.session_state.get("reviewer_name", ""),
    help="No real authentication yet — this is a free-text attribution field, not a login.",
)
st.session_state["reviewer_name"] = reviewer_name

if st.sidebar.button("Reload proposals"):
    for key in list(st.session_state):
        if key.startswith("displayed-version:"):
            del st.session_state[key]
    st.rerun()

try:
    rows = fetch_determinations()
except RuntimeError as e:
    st.error(str(e))
    st.stop()
except Exception as e:
    st.error(f"Could not reach Supabase: {e}")
    st.stop()

if not rows:
    st.info(
        "No determinations yet. Run `python3 demo/run_from_vendorhub.py` "
        "against a VendorHub submission first, then reload this page."
    )
    st.stop()

for row in rows:
    vendor = row.get("vendor_tax_requests") or {}
    row["_vendor_name"] = vendor.get("legal_name", "(unknown vendor)")
    row["_vendor_number"] = vendor.get("vendor_number", "")

confidence_options = sorted({r.get("confidence") or "" for r in rows})
regime_options = sorted({r["regime"] for r in rows if r.get("regime")})
status_options = sorted({r["review_status"] for r in rows if r.get("review_status")})

with st.sidebar:
    st.subheader("Filters")
    selected_confidence = st.multiselect("Confidence", confidence_options, default=confidence_options)
    selected_status = st.multiselect("Review status", status_options, default=status_options)
    vendor_search = st.text_input("Vendor name contains")

filtered = [
    r
    for r in rows
    if (r.get("confidence") or "") in selected_confidence
    and r.get("review_status") in selected_status
    and (not vendor_search or vendor_search.lower() in r["_vendor_name"].lower())
]

total = len(rows)
pending = sum(1 for r in rows if r["review_status"] == "pending")
needs_review = sum(1 for r in rows if r.get("needs_review", True))
skipped = sum(1 for r in rows if r.get("confidence") == "skipped")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total", total)
c2.metric("Pending", pending)
c3.metric("Needs review", needs_review)
c4.metric("Skipped (unmapped)", skipped)
c5.metric("Showing", len(filtered))

st.divider()

STATUS_BADGE = {
    "pending": "⏳",
    "approved": "✅",
    "overridden": "✏️",
    "rejected": "❌",
}
CONFIDENCE_ICON = {"high": "🟢", "needs_review": "🟡", "skipped": "⚪"}


def _refresh():
    st.cache_data.clear()
    st.rerun()


def _save_decision(*args, **kwargs):
    try:
        return set_review_decision(*args, **kwargs)
    except Exception:
        st.error("Could not save the decision. It may have changed or already been reviewed. Reload and inspect the latest proposal.")
        st.stop()


for row in filtered:
    det_id = row["id"]
    seen_version = displayed_version(st.session_state, det_id, row.get("proposal_version", 1))
    status_badge = STATUS_BADGE.get(row["review_status"], "")
    conf_icon = CONFIDENCE_ICON.get(row.get("confidence") or "", "")

    header = f"{status_badge} {conf_icon} **{row['_vendor_name']}** — {row.get('category', '')}"
    if row.get("regime"):
        header += f" · {row['regime']}"
    header += f" · _{row['review_status']}_"

    with st.expander(header):
        left, right = st.columns([3, 2])

        with left:
            if row.get("confidence") == "skipped":
                st.write(f"**Skipped by the adapter:** {row.get('notes', '')}")
            else:
                st.write(f"**Vendor ID:** `{row['_vendor_number']}`")
                st.write(f"**Regime:** {row.get('regime', '')}")
                st.write(f"**Withholding required:** {row.get('withholding_required', '')}")
                st.write(f"**Engine-proposed rate:** {row.get('rate', '')}%")
                st.write(f"**Citation:** {row.get('citation', '')}")
                st.write(f"**Rationale:** {row.get('rationale', '')}")
                if row.get("flags"):
                    st.write(f"**Flags:** {row['flags']}")
                if row.get("notes"):
                    st.write(f"**Notes:** {row['notes']}")

            st.write(f"**Proposal version:** {row.get('proposal_version', 1)}")
            st.write(f"**Payment amount:** {row.get('gross_amount')}" if row.get("amount_known") else "**Payment amount:** unknown — rate review only")
            st.write(f"**Rate table:** {row.get('rate_table_version') or 'Not applicable / not captured'}")
            st.json(row.get("audit_trail", []))
            st.json({"payment": row.get("payment_snapshot", {}), "document": row.get("document_snapshot", {}), "vendor / extraction": row.get("vendor_snapshot", {})})

            if row["review_status"] != "pending":
                st.divider()
                st.write(f"**Reviewer:** {row.get('reviewer_name', '')}")
                st.write(f"**Reviewed at:** {row.get('reviewed_at', '')}")
                if row["review_status"] == "overridden":
                    st.write(f"**Override rate:** {row.get('override_rate', '')}%")
                    st.write(f"**Override reasoning:** {row.get('override_reasoning', '')}")
                elif row["review_status"] == "rejected":
                    st.write(f"**Rejection reasoning:** {row.get('override_reasoning', '')}")

        with right:
            if seen_version != row.get("proposal_version", 1):
                st.warning("This proposal changed since you viewed it. Use Reload proposals, then review the updated facts.")
                continue
            if row["review_status"] != "pending":
                st.caption("Decision saved. Reviewed proposals are retained unchanged; submit a new case for corrected facts.")
                continue
            if row.get("confidence") == "skipped":
                st.caption("Skipped candidates aren't reviewable here — classify manually.")
                continue

            if not reviewer_name.strip():
                st.info("Enter your name in the sidebar to record decisions.")

            if st.button("Approve", key=f"approve-{det_id}", disabled=not reviewer_name.strip() or row.get("rate") is None):
                _save_decision(det_id, "approved", reviewer_name.strip(), expected_version=seen_version)
                _refresh()

            st.write("**Override**")
            override_rate = st.number_input(
                "New rate (%)",
                min_value=0.0,
                max_value=100.0,
                value=float(row.get("override_rate") or row.get("rate") or 0),
                step=0.5,
                key=f"rate-{det_id}",
            )
            override_reasoning = st.text_area(
                "Reasoning (required to save an override or a rejection)",
                value=row.get("override_reasoning") or "",
                key=f"reason-{det_id}",
            )
            oc1, oc2 = st.columns(2)
            if oc1.button("Save override", key=f"override-{det_id}", disabled=not reviewer_name.strip()):
                if not override_reasoning.strip():
                    st.error("Reasoning is required to save an override.")
                else:
                    _save_decision(
                        det_id,
                        "overridden",
                        reviewer_name.strip(),
                        expected_version=seen_version,
                        override_rate=override_rate,
                        override_reasoning=override_reasoning.strip(),
                    )
                    _refresh()
            if oc2.button("Reject", key=f"reject-{det_id}", disabled=not reviewer_name.strip()):
                if not override_reasoning.strip():
                    st.error("Reasoning is required to reject a determination.")
                else:
                    _save_decision(
                        det_id,
                        "rejected",
                        reviewer_name.strip(),
                        expected_version=seen_version,
                        override_reasoning=override_reasoning.strip(),
                    )
                    _refresh()

