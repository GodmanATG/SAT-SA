"""
Page 3: Finding Cards — explainable findings + prioritised review samples
=======================================================================
Two of the problem statement's requirements live here:

* *Explainability* — every card states why the entity was flagged, in templated
  prose that repeats the measured value, the threshold and the comparison basis.
* *Prioritisation of alert samples* (Requirement 10) — the bottom section turns the
  highest-severity findings into a concrete list of records for an examiner to pull
  and read by hand, which is the point of the whole tool.
"""

import json
import pathlib
import sys

import pandas as pd
import streamlit as st

APP_DIR = pathlib.Path(__file__).parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config import (  # noqa: E402
    ADJUDICATION_VERDICTS, CAPABILITY_NAMES, SEVERITY_ICON, is_non_counting,
)
from config import canonical_verdict_label  # noqa: E402
from database import get_db, latest_adjudications, log_action, record_adjudication  # noqa: E402
from views.components import full_evidence_export, recalculate_button  # noqa: E402

TYPE_ICON = {
    "execution_gap": "⚙️ Execution gap",
    "negative_space": "🕳️ Negative space",
    "peer_anomaly": "📊 Peer / trend",
    "data_quality": "📋 Data quality",
}
GROUP_LABEL = {
    "rule": "Rule-based detector (deterministic threshold)",
    "benchmark": "Absolute benchmark (fixed reference value)",
    "peer": "Peer-relative statistic (cohort comparison)",
    "trend": "Trend detector (month-over-month drift)",
}


def render(filtered_metrics: pd.DataFrame, filtered_entity_ids: set):
    st.subheader("Supervisory Finding Cards")
    st.caption("Each card states the rule, the measured value against its threshold, why it matters, "
               "and the exact evidence records behind it.")

    if not filtered_entity_ids:
        st.info("No entities match current filters.")
        return

    with get_db() as conn:
        rows = conn.execute(
            "SELECT f.*, e.entity_name FROM findings f "
            "JOIN entities e ON f.entity_id = e.entity_id "
            "ORDER BY f.severity_score DESC"
        ).fetchall()
        # Live verdicts, not the mirrored column: the mirror is only refreshed by an
        # analytics run, and a card must show the verdict the examiner just recorded.
        # latest_adjudications resolves the newest verdict per finding; a raw SELECT
        # over the append-only table would depend on row order for that.
        adjudications = latest_adjudications(conn)
    findings = [dict(r) for r in rows if r["entity_id"] in filtered_entity_ids]
    for finding in findings:
        record = adjudications.get(finding["finding_id"]) or {}
        finding["examiner_verdict"] = record.get("verdict", "")
        finding["verdict_label"] = canonical_verdict_label(record.get("verdict"))
    if not findings:
        st.info("No findings for the selected entities. Ingest submissions and run the detection engine.")
        return

    fdf = pd.DataFrame(findings)

    # The recalculate control lives once, at the top of the page, instead of on every card:
    # a portfolio view can hold hundreds of findings, and a re-run button repeated on each
    # of them is hundreds of identical controls for one portfolio-wide action.
    recalculate_button(
        "finding_cards_page",
        label="🔄 Recalculate scores now",
        caption="Applies every verdict recorded so far — findings judged *False positive* or "
                "*Expected* stop counting towards their entity's capability scores and risk "
                "score. This also refreshes every other entity and records a new cycle snapshot.",
        columns=(1, 2))

    # ── Filters ──────────────────────────────────────────────────────────
    fc1, fc2, fc3, fc4 = st.columns(4)
    with fc1:
        f_entity = st.selectbox("Entity", ["All"] + sorted(fdf["entity_name"].unique().tolist()))
    with fc2:
        f_type = st.selectbox("Finding type", ["All"] + sorted(fdf["weakness_type"].unique().tolist()))
    with fc3:
        f_sev = st.selectbox("Severity", ["All", "High", "Medium", "Low"])
    with fc4:
        verdict_options = ["All", "Adjudicated", "Outstanding"] + ADJUDICATION_VERDICTS
        f_verdict = st.selectbox("Examiner verdict", verdict_options,
                                 help="Verdicts are recorded on the card itself; findings judged "
                                      "false positive or expected stop counting towards the score.")
    f_cap = st.selectbox("Capability", ["All"] + [f"{k}: {v}" for k, v in CAPABILITY_NAMES.items()])

    view = fdf.copy()
    if f_verdict == "Adjudicated":
        view = view[view["examiner_verdict"] != ""]
    elif f_verdict == "Outstanding":
        view = view[view["examiner_verdict"] == ""]
    elif f_verdict != "All":
        # Matched on the resolved label, so a verdict qualified with a note still appears
        # under the verdict it is.
        view = view[view["verdict_label"] == f_verdict]
    if f_entity != "All":
        view = view[view["entity_name"] == f_entity]
    if f_type != "All":
        view = view[view["weakness_type"] == f_type]
    if f_sev != "All":
        view = view[view["severity"] == f_sev]
    if f_cap != "All":
        code = f_cap.split(":")[0]
        view = view[view["capability_tags"].str.contains(f'"{code}"', na=False)]

    st.caption(f"{len(view)} of {len(fdf)} finding(s) shown")

    # ── Cards ────────────────────────────────────────────────────────────
    for f in view.to_dict("records"):
        with st.container(border=True):
            verdict = f.get("examiner_verdict", "")
            verdict_tag = (f" &nbsp;|&nbsp; 🧑‍⚖️ **{verdict}**"
                           + (" *(not counted)*" if is_non_counting(verdict) else "")
                           ) if verdict else ""
            st.markdown(
                f"**{TYPE_ICON.get(f['weakness_type'], f['weakness_type'])} — {f['title']}**  \n"
                f"{SEVERITY_ICON.get(f['severity'], '')} {f['severity']} severity &nbsp;|&nbsp; "
                f"**{f['entity_name']}** &nbsp;|&nbsp; `{f['rule_id']}` &nbsp;|&nbsp; "
                f"{GROUP_LABEL.get(f['detector_group'], f['detector_group'])}"
                f"{verdict_tag}"
            )

            st.markdown(f"**Why flagged.** {f.get('rationale') or f.get('description', '')}")
            if f.get("rationale") and f.get("description"):
                st.caption(f["description"])

            m1, m2, m3 = st.columns(3)
            m1.metric("Observed", f"{f.get('metric_value', 0):.3f}")
            m2.metric("Threshold", f"{f.get('threshold_value', 0):.3f}")
            m3.metric("Evidence records", f"{f.get('evidence_count', 0):,}")

            try:
                caps = json.loads(f.get("capability_tags") or "[]")
            except (json.JSONDecodeError, TypeError):
                caps = []
            if caps:
                st.caption("🏷️ Capabilities affected: " +
                           ", ".join(f"{c} ({CAPABILITY_NAMES.get(c, c)})" for c in caps))

            count = int(f.get("evidence_count") or 0)
            try:
                sample = json.loads(f.get("evidence_ids") or "[]")
            except (json.JSONDecodeError, TypeError):
                sample = []
            sample_len = len(sample)
            if count:
                shown = ", ".join(str(s) for s in sample[:5])
                st.caption(f"📎 **{count:,}** total flagged record(s) *(inline preview sample of {min(count, sample_len or 25)} IDs: `{shown}`"
                           + (" …" if count > 5 else "")
                           + ")*")
            else:
                st.caption("📎 Derived from the absence of expected records (negative space) — "
                           "verify via the inventory / coverage views.")

            # Prominent Download Full CSV button for examiners needing all records
            full_evidence_export(
                f, key=f"card_{f['finding_id']}",
                label=f"⬇️ Download Full CSV ({count:,} records)" if count else "⬇️ Download Full CSV (Evidence Basis)"
            )

            # ── inline adjudication: the examiner's judgement, recorded where it is formed ──
            with st.expander("🧑‍⚖️ Examiner verdict" + (f" — currently **{verdict}**" if verdict else "")):
                if is_non_counting(verdict):
                    st.caption("This finding is excluded from the entity's score. It stays visible "
                               "here for audit, and recording a new verdict changes that.")
                with st.form(key=f"card_adj_{f['finding_id']}", clear_on_submit=False):
                    v = st.radio("Verdict", ADJUDICATION_VERDICTS,
                                 index=ADJUDICATION_VERDICTS.index(verdict)
                                 if verdict in ADJUDICATION_VERDICTS else 0,
                                 horizontal=True, key=f"v_{f['finding_id']}")
                    why = st.text_area("Examiner rationale", height=80,
                                       key=f"r_{f['finding_id']}",
                                       placeholder="What was checked, what was found, why the finding "
                                                   "does or does not stand.")
                    who = st.text_input("Examiner", value="supervisor", key=f"w_{f['finding_id']}")
                    if st.form_submit_button("Record verdict"):
                        if v != "Confirmed" and not why.strip():
                            st.error("A rationale is required unless the finding is confirmed.")
                        else:
                            with get_db() as conn:
                                record_adjudication(conn, f["finding_id"], f["entity_id"], v,
                                                    why.strip(), who.strip() or "supervisor")
                                log_action(conn, "FINDING_ADJUDICATED", f["entity_id"],
                                           f"{f['rule_id']} → {v}")
                            st.success(f"Recorded **{v}**. Press **Recalculate scores** at the top "
                                       f"of this page to apply it to the supervisory score.")
                            st.rerun()

    # ── Prioritised review sample (Requirement 10) ────────────────────────
    st.markdown("---")
    st.subheader("Prioritised alert & case samples for manual review")
    st.caption(
        "The examiner-facing output: rather than reviewing a random sample, pull the specific "
        "records that sit behind the highest-severity findings. Ranked by finding severity, then by "
        "how many records the finding covers."
    )

    with_case = fdf[(fdf["evidence_count"] > 0)
                    & (~fdf["examiner_verdict"].map(is_non_counting))].copy()
    if with_case.empty:
        st.info("No findings with attributable evidence records in the current view "
                "(findings adjudicated false positive or expected are excluded from the review queue).")
        return

    with_case["capabilities"] = with_case["capability_tags"].apply(
        lambda t: ", ".join(json.loads(t) if isinstance(t, str) else (t or [])))
    samples = with_case.sort_values(["severity_score", "evidence_count"], ascending=False)
    sample_size = st.slider("Findings to list", min_value=5, max_value=100, value=25, step=5,
                            key="review_sample_size",
                            help="The review sample is the examiner-facing worklist: how many of "
                                 "the top-ranked findings to pull records for in this session.")
    top = samples.head(int(sample_size))[[
        "entity_name", "rule_id", "title", "severity", "weakness_type", "detector_group",
        "capabilities", "metric_value", "threshold_value", "evidence_count", "evidence_ids",
    ]].rename(columns={
        "entity_name": "Entity", "rule_id": "Rule", "title": "Finding",
        "severity": "Severity", "weakness_type": "Type", "detector_group": "Detector",
        "capabilities": "Capabilities", "metric_value": "Observed",
        "threshold_value": "Threshold", "evidence_count": "Records", "evidence_ids": "Sample IDs",
    })
    st.dataframe(top, width="stretch", hide_index=True)
    st.caption("The stored sample IDs column is capped; use the full evidence export on any card (or "
               "the Evidence Drill-Down page) to get every record for a finding you are examining by "
               "hand.")

    st.caption(
        "Suggested review order: (1) the Critical Attention / Elevated entities on Risk Ranking, "
        "(2) high-severity findings for those entities, (3) the evidence samples listed here. "
        "Every step is traceable in the audit log."
    )

    col_a, col_b = st.columns(2)
    with col_a:
        st.download_button(
            "⬇️ Export review sample list (CSV)",
            top.to_csv(index=False).encode("utf-8"),
            "sat_sa_review_sample.csv", "text/csv",
        )
    with col_b:
        st.download_button(
            "⬇️ Export full findings register (JSON, audit trail)",
            json.dumps(findings, indent=2, default=str).encode("utf-8"),
            "sat_sa_findings_register.json", "application/json",
        )

    # ── Summary ──────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Finding summary")
    sc1, sc2, sc3, sc4 = st.columns(4)
    sc1.metric("Findings in view", len(view))
    sc2.metric("High severity", int((view["severity"] == "High").sum()))
    sc3.metric("Execution gaps", int((view["weakness_type"] == "execution_gap").sum()))
    sc4.metric("Negative space", int((view["weakness_type"] == "negative_space").sum()))
    by_type = view.groupby(["weakness_type", "severity"]).size().unstack(fill_value=0)
    st.dataframe(by_type, width="stretch")
