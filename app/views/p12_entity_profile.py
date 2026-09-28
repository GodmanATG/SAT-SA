"""
Page 12: Entity Profile & Declarations
======================================
Everything the register holds about one entity, in one place — and, most
importantly, the reconciliation between what the entity *says* about itself and
what its own records show.

This is the page an examiner keeps open while reading a submission: profile and SOC
arrangements, the declared controls and performance metrics, the declared-vs-measured
comparison behind rule IM-008, which of the six submission field groups actually
arrived, the measured feature set with each value named, the entity's findings with
their adjudication state, and the audit trail of everything done to this entity.
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
    CAPABILITY_COLUMNS, CAPABILITY_NAMES, NON_COUNTING_VERDICTS, SEVERITY_ICON,
)
from database import get_db, latest_adjudications  # noqa: E402
from views.components import (  # noqa: E402
    capability_radar, capability_source_note, cycle_panel, verdict_display,
)

# metric key -> (label, how to render, which direction is worse)
METRIC_VIEW = [
    ("total_alerts", "Alerts submitted", "int", None),
    ("total_cases", "Case records", "int", None),
    ("investigation_events", "Investigation workflow events", "int", None),
    ("total_escalations", "Escalation records", "int", None),
    ("total_dispositions", "Disposition / closure records", "int", None),
    ("total_assets", "Assets in inventory", "int", None),
    ("monitored_assets", "Assets actively monitored", "int", None),
    ("critical_assets", "Critical assets", "int", None),
    ("alerts_per_monitored_asset", "Alerts per monitored asset", "2f", None),
    ("time_to_ack_median_critical", "Median acknowledgement, critical/high (min)", "1f", "high"),
    ("time_to_close_median_critical", "Median closure, critical (min)", "1f", "high"),
    ("fast_closure_rate", "Critical/high closed within minutes", "pct", "high"),
    ("crit_no_escalation_rate", "Critical alerts without escalation", "pct", "high"),
    ("missing_case_rate", "Critical/high alerts with no case record", "pct", "high"),
    ("template_note_rate", "Investigation notes that are near-duplicates", "pct", "high"),
    ("root_cause_rate", "Alerts with a root cause on the linked case", "pct_rate", None),
    ("repeat_alert_rate", "Alerts that are chronic repeat traffic", "pct", "high"),
    ("night_coverage_gap_pct", "Night-time activity gap vs expected", "pct_direct", "high"),
    ("expected_category_coverage", "Expected alert-category coverage", "pct_rate", None),
    ("silent_critical_assets", "Critical assets generating no alerts", "int", "high"),
    ("unmonitored_critical_assets", "Critical assets not effectively monitored", "int", "high"),
    ("telemetry_silence_pct", "Monitored assets with no recent telemetry", "pct_direct", "high"),
    ("sla_breach_rate", "Closures exceeding their response target", "pct", "high"),
    ("sla_misreport_rate", "SLA-compliant closures its own timeline contradicts", "pct", "high"),
    ("severity_softening_rate", "Cases logged below their alert's severity", "pct", "high"),
    ("no_root_cause_gap", "Significant closures without a root cause", "pct", "high"),
    ("no_remediation_gap", "Significant closures without remediation evidence", "pct", "high"),
    ("rework_loop_rate", "Investigations repeating completed activities", "pct", "high"),
    ("investigation_gap_rate", "Cases with no adequate investigation record", "pct", "high"),
    ("evidence_gap_rate", "Workflow steps with no evidence or result", "pct", "high"),
    ("reopen_rate", "Closures subsequently reopened", "pct", "high"),
    ("risk_accept_no_authority_rate", "Accepted risks with no named authority", "pct", "high"),
    ("avg_investigation_depth", "Average investigation depth score", "2f", "low"),
    ("top_analyst_share", "Critical/high alerts owned by the most loaded analyst", "pct", "high"),
    ("critical_analysts_active", "Analysts handling critical/high alerts", "int", None),
    ("weekend_activity_ratio", "Weekend activity vs weekday rate", "ratio", "low"),
    ("weekend_alerts", "Alerts falling on a Saturday or Sunday", "int", None),
    ("investigation_time_anomaly_rate",
     "Cases with under two minutes of recorded investigation time", "pct", "high"),
    ("investigation_minutes_median", "Median recorded investigation minutes per case", "2f", "low"),
    ("data_quality_alerts", "Alert data completeness", "pct_rate", None),
    ("note_similarity_sample_size", "Cases compared in the note-similarity pass", "int", None),
]

def _fmt(value, kind: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if kind == "int":
        return f"{int(number):,}"
    if kind == "pct":
        return f"{number * 100:.1f}%"
    if kind == "pct_rate":
        return f"{number * 100:.1f}%"
    if kind == "pct_direct":
        return f"{number:.1f}%"
    if kind == "ratio":
        return f"{number:.2f}x"
    if kind == "1f":
        return f"{number:.1f}"
    return f"{number:.2f}"


def render(entity_ids=None):
    st.info("💡 **How to read this profile:** This page provides a deep dive into a single entity. It compares what the entity *claimed* on paper against what the engine *measured* in their raw logs. Scroll down to review and adjudicate specific finding cards.")

    st.title(" Entity Profile & Declarations")
    st.caption("What the entity submitted, what it declares about itself, and whether its own "
               "records support those declarations.")

    with get_db() as conn:
        entities = [dict(r) for r in conn.execute(
            "SELECT * FROM entities ORDER BY entity_name").fetchall()]
        metrics = {r["entity_id"]: dict(r) for r in conn.execute("SELECT * FROM entity_metrics")}
        adjudications = latest_adjudications(conn)
        counts = {}
        for table in ("alerts", "cases", "investigations", "escalations", "dispositions",
                      "asset_inventory"):
            for rec in conn.execute(f"SELECT entity_id, COUNT(*) AS c FROM {table} GROUP BY entity_id"):
                counts.setdefault(rec["entity_id"], {})[table] = rec["c"]
        finding_counts = {r["entity_id"]: r["c"] for r in conn.execute(
            "SELECT entity_id, COUNT(*) AS c FROM findings GROUP BY entity_id")}

    if entity_ids:
        entities = [e for e in entities if e["entity_id"] in entity_ids]
    if not entities:
        st.info("No entities in the register yet. Register one from the **Register & Submissions** "
                "page.")
        return

    options = {f"{e['entity_name']}  ·  {e['tier']}": e for e in entities}
    choice = st.selectbox("Entity", list(options.keys()), key="profile_choice")
    entity = options[choice]
    entity_id = entity["entity_id"]
    row = metrics.get(entity_id, {})

    # ── identity ─────────────────────────────────────────────────────────
    st.markdown("---")
    a1, a2, a3, a4, a5 = st.columns(5)
    a1.metric("Risk tier", row.get("risk_tier", "not analysed"))
    a2.metric("Supervisory risk score", row.get("risk_score", "—"),
              help="Composite of the weighted metric gaps (60%) and the capability scorecard (40%). "
                   "Findings adjudicated false positive or expected are excluded.")
    a3.metric("Rank", f"#{row.get('risk_rank', '—')}" if row.get("risk_rank") else "—")
    a4.metric("Findings", finding_counts.get(entity_id, 0),
              help="Findings raised by the last analytics run. Those adjudicated false positive or "
                   "expected are excluded from the score but remain visible below.")
    a5.metric("Declared-KPI contradictions", row.get("declared_kpi_contradictions", "—"),
              help="Declarations the entity's own records do not support (rule IM-008).")

    b1, b2, b3, b4 = st.columns(4)
    b1.markdown(f"**Sector**  \n{entity['sector']}"
                + (f" / {entity['sub_sector']}" if entity.get("sub_sector") else ""))
    b2.markdown(f"**Registered office**  \n{entity.get('hq') or '—'}"
                + (f", {entity.get('state')}" if entity.get("state") else ""))
    b3.markdown(f"**NCIIPC / CIN**  \n{entity.get('nciipc_id') or '—'} / {entity.get('cin') or '—'}")
    b4.markdown(f"**Review period**  \n"
                f"{entity.get('review_period_start') or '—'} → {entity.get('review_period_end') or '—'}")

    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**SOC**  \n{entity.get('soc_name') or '—'}  \n"
                f"{entity.get('soc_model') or 'model not stated'}")
    c2.markdown(f"**Monitoring window**  \n{entity.get('soc_coverage') or 'not stated'}  \n"
                f"{entity.get('analyst_count') or 0} analysts")
    c3.markdown(f"**Contact**  \n{entity.get('contact') or '—'}  \n"
                f"{entity.get('contact_email') or ''} {entity.get('contact_phone') or ''}")
    if entity.get("notes"):
        st.caption(f"Notes: {entity['notes']}")

    # ── submission coverage ──────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Submission coverage")
    groups = [
        ("alerts", "Alert metadata"), ("cases", "Case management"),
        ("investigations", "Investigation workflow"), ("escalations", "Escalation records"),
        ("dispositions", "Disposition & closure"), ("asset_inventory", "Asset inventory"),
    ]
    cols = st.columns(6)
    for (key, label), col in zip(groups, cols):
        value = counts.get(entity_id, {}).get(key, 0)
        col.metric(label, f"{value:,}" if value else "not submitted",
                   delta=None if value else "absent", delta_color="off")
    st.caption("An absent field group is supervisory-relevant in itself: analysis of escalation "
               "discipline is limited if escalation records were never submitted, and that limitation "
               "is recorded rather than silently ignored.")

    # ── declarations vs evidence ─────────────────────────────────────────
    st.markdown("---")
    st.subheader("What the entity declares, and what its records show")
    declared_kpis = {}
    if entity.get("declared_kpis"):
        try:
            declared_kpis = json.loads(entity["declared_kpis"]) or {}
        except (TypeError, ValueError, json.JSONDecodeError):
            declared_kpis = {}
    controls = []
    if entity.get("declared_controls"):
        try:
            controls = json.loads(entity["declared_controls"]) or []
        except (TypeError, ValueError, json.JSONDecodeError):
            controls = []

    d1, d2 = st.columns([3, 2])
    with d1:
        if declared_kpis:
            detail = row.get("declared_kpi_details")
            try:
                comparison = json.loads(detail) if detail else []
            except (TypeError, ValueError, json.JSONDecodeError):
                comparison = []
            if comparison:
                frame = pd.DataFrame([{
                    "Declared KPI": c.get("label"),
                    "Declared": c.get("declared"),
                    "Measured from records": c.get("measured"),
                    "Divergence": c.get("detail"),
                    "Supported by evidence": "— no" if c.get("contradicted") else "yes",
                } for c in comparison])
                st.dataframe(frame, width="stretch", hide_index=True)
            else:
                st.info("Declared KPIs are recorded but no measured comparison is available yet — "
                        "run the analytics.")
        else:
            st.info("This entity did not declare any performance metrics in its cover sheet. "
                    "Declarations are what makes the declared-vs-evidence comparison possible, and "
                    "their absence limits the assessment — recorded here rather than ignored.")
    with d2:
        st.markdown("**Declared controls**")
        if controls:
            for control in controls:
                st.markdown(f"- {control}")
            st.caption("Documented controls are treated as claims. The detectors test whether the "
                       "operational records are consistent with them; where they are not, that is "
                       "the execution gap the problem statement describes.")
        else:
            st.caption("No declared controls recorded for this entity.")

    # ── measured features ────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("View Raw Feature Measurements & Technical Evidence", expanded=False):
        if not row:
            st.info("No metrics computed yet for this entity - run the analytics.")
        else:
            rows = []
            for key, label, kind, worse in METRIC_VIEW:
                if key not in row:
                    continue
                rows.append(dict(
                    Feature=label,
                    Value=_fmt(row.get(key), kind),
                    Direction={"high": "higher is worse", "low": "lower is worse",
                               None: "context"}[worse],
                    Source=("key: " + key),
                ))
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            st.caption("Every detector reads from this same named feature set, so a finding can always be "
                       "traced back to the value that produced it. Columns show the raw metric key used "
                       "in threshold configuration.")

    # ── cycle over cycle ─────────────────────────────────────────────────
    st.markdown("---")
    cycle_panel(entity_id)

    # ── capability scorecard ─────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Capability scorecard")
    caps = {CAPABILITY_NAMES[c]: row.get(col, 0) for c, col in CAPABILITY_COLUMNS.items() if row}
    if caps:
        st.caption("Stored as weakness (0 = strong, 100 = weakest). The radar is inverted to "
                   "capability strength so that a larger shape always means a better entity.")
        radar_col, table_col = st.columns([3, 2])
        with radar_col:
            capability_radar(row, name=entity.get('entity_name', 'entity'), key="profile_radar")
        with table_col:
            scorecard = pd.DataFrame([{"Capability": k, "Weakness score": v}
                                      for k, v in caps.items()])
            st.dataframe(scorecard.sort_values("Weakness score", ascending=False),
                         width="stretch", hide_index=True)
            weakest = max(caps.items(), key=lambda kv: kv[1]) if caps else None
            strongest = min(caps.items(), key=lambda kv: kv[1]) if caps else None
            if weakest and strongest:
                st.caption(f"Weakest dimension: **{weakest[0]}** ({weakest[1]}). "
                           f"Strongest: **{strongest[0]}** ({strongest[1]}).")
        capability_source_note()

    # ── findings + adjudication state ────────────────────────────────────
    st.markdown("---")
    st.subheader("Findings for this entity")
    with get_db() as conn:
        finding_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM findings WHERE entity_id = ? ORDER BY severity_score DESC",
            (entity_id,)).fetchall()]
    if not finding_rows:
        st.success("No findings raised for this entity.")
    else:
        def _verdict_cell(finding: dict) -> str:
            """Verdict with its indicator and, where one exists, when it was decided."""
            decision = adjudications.get(finding["finding_id"]) or {}
            return verdict_display(finding.get("examiner_verdict"),
                                   when=decision.get("decided_at") or "",
                                   outstanding="not yet adjudicated")

        frame = pd.DataFrame([{
            "Severity": f"{SEVERITY_ICON.get(f['severity'], '')} {f['severity']}",
            "Rule": f["rule_id"],
            "Type": f["weakness_type"].replace("_", " "),
            "Finding": f["title"],
            "Examiner verdict": _verdict_cell(f),
        } for f in finding_rows])
        st.dataframe(frame, width="stretch", hide_index=True)
        st.caption("A verdict of **False positive** or **Expected** excludes the finding from this "
                   "entity's score and capability scorecard; every other verdict leaves it "
                   "counting.")
        suppressed = [f for f in finding_rows
                      if (f.get("examiner_verdict") or "") in NON_COUNTING_VERDICTS]
        if suppressed:
            st.warning(f"{len(suppressed)} of these findings were adjudicated by the examiner as "
                       f"false positive or expected, and are excluded from the score. They remain "
                       f"here for audit.")

    # ── audit trail ──────────────────────────────────────────────────────
    with st.expander("Audit trail for this entity"):
        with get_db() as conn:
            logs = [dict(r) for r in conn.execute(
                "SELECT timestamp, action, details FROM audit_log WHERE entity_id = ? "
                "ORDER BY log_id DESC LIMIT 60", (entity_id,)).fetchall()]
        if logs:
            st.dataframe(pd.DataFrame(logs), width="stretch", hide_index=True)
        else:
            st.caption("No audit entries recorded for this entity.")

    st.download_button(
        "Export this entity's record (JSON)",
        json.dumps(dict(entity={k: v for k, v in entity.items()}, metrics=row),
                   indent=2, default=str).encode("utf-8"),
        file_name=f"sat-sa-{(entity.get('entity_name') or 'entity').replace(' ', '_')}.json",
        mime="application/json")
