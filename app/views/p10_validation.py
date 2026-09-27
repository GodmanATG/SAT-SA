"""
Page 10: Validation — detector performance against known ground truth
====================================================================
The synthetic submission generator records exactly which weaknesses it injected
into which entity (``submissions/ground_truth.json``).  That makes it possible to
report *real* precision and recall for the rule-based and absolute-benchmark
detectors instead of asserting that the tool works.

What these numbers are, and are not:

* They measure the detectors against **known, deliberately injected** weaknesses.
* They are **not** accuracy against NCIIPC expert manual review.  That can only be
  established by the proposed shadow-run pilot (run SAT-SA alongside a real review
  cycle and measure overlap with examiner findings).
* Peer-relative and trend detectors are population-relative by construction - a
  control entity can legitimately look like a peer outlier.  They are therefore
  excluded from the false-positive count and reported separately.
"""

import json
import pathlib

import pandas as pd
import plotly.express as px
import streamlit as st

from database import get_db

APP_DIR = pathlib.Path(__file__).parent.parent
GROUND_TRUTH = APP_DIR.parent / "submissions" / "ground_truth.json"

# Detectors whose firing on a control entity is a genuine false positive
RULE_DETECTORS = {"EG-001", "EG-002", "EG-003", "EG-004", "EG-005", "EG-006", "EG-007",
                  "NS-001", "NS-002", "NS-003", "NS-004", "NS-005", "NS-006", "NS-007",
                  "BM-001", "BM-002", "BM-003",
                  "IM-001", "IM-002", "IM-003", "IM-004", "IM-005", "IM-006",
                  "IM-007", "IM-008", "IM-009"}
POPULATION_DETECTORS = {"STAT-001", "STAT-002", "STAT-003"}

RULE_MEANING = {
    "EG-001": "Critical/high alerts closed implausibly fast",
    "EG-002": "Critical alerts closed without escalation",
    "EG-003": "Repetitive / template-driven investigation notes",
    "EG-004": "Recurring alerts without root-cause remediation",
    "EG-005": "Anomalous bulk closures",
    "EG-006": "Shallow investigation records",
    "EG-007": "Critical alert workload concentrated on one analyst",
    "NS-001": "Critical assets with no telemetry evidence",
    "NS-002": "Expected alert categories absent for sector",
    "NS-003": "Little or no monitoring evidence at night",
    "NS-004": "Unexpectedly low activity for the monitored estate",
    "NS-005": "Missing investigation / escalation records",
    "NS-006": "Critical assets deployed but not monitored",
    "NS-007": "Monitoring activity absent at weekends",
    "BM-001": "Acknowledgement beyond absolute benchmark",
    "BM-002": "Critical closure beyond absolute benchmark",
    "BM-003": "Escalation rate below absolute benchmark",
    "IM-001": "SLA breach / SLA compliance contradicted by closure evidence",
    "IM-002": "Cases recorded below the severity of their own alerts",
    "IM-003": "Significant closures without root cause or remediation",
    "IM-004": "Investigations repeating completed workflow activities",
    "IM-005": "Closures subsequently reopened",
    "IM-006": "Cases without an investigation trace / steps without evidence",
    "IM-007": "Risk accepted without a recorded authority",
    "IM-008": "Declared KPI contradicted by the entity's own records",
    "IM-009": "Cases investigated in seconds",
    "STAT-001": "Peer deviation (sector cohort z-score)",
    "STAT-002": "Deteriorating trend across the window",
    "STAT-003": "Multivariate operational outlier",
    "DQ-001": "Data-quality gate raised",
}


def _load_findings() -> pd.DataFrame:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT f.finding_id, f.rule_id, f.severity, f.weakness_type, f.detector_group, "
            "       f.metric_value, f.threshold_value, f.evidence_count, f.examiner_verdict, "
            "       e.entity_name "
            "FROM findings f JOIN entities e ON f.entity_id = e.entity_id"
        ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def render():
    st.title("🧪 Validation & Methods")
    st.caption("How well the detectors do against known ground truth - and what that evidence does and "
               "does not prove.")

    findings = _load_findings()
    if findings.empty:
        st.info("No findings yet. Run the detection engine first (CSE File Input → Run Detection).")
        return

    if not GROUND_TRUTH.exists():
        st.warning(
            f"No ground-truth file found at `{GROUND_TRUTH}`. Generate the demo submissions "
            "(CSE File Input → Demo dataset) to enable detector validation. Without a labelled "
            "dataset, precision/recall cannot be reported - which is itself the honest position."
        )
        return

    gt = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    labels = {e["entity_name"]: set(e["expected_rules"]) for e in gt.get("entities", [])}
    controls = {name for name, rules in labels.items() if not rules}
    injected = {name: rules for name, rules in labels.items() if rules}

    st.markdown(f"**Ground-truth dataset:** {gt.get('entities') and len(labels)} CSEs · "
                f"{len(injected)} with deliberately injected weaknesses · {len(controls)} control "
                f"entities · generated {gt.get('generated_at', 'n/a')} (seed {gt.get('seed')})")
    st.caption("Ground truth describes the *synthetic* dataset only. An injected weakness is a "
               "generated pattern, not a real organisation's finding.")

    # ── rule-level confusion matrix ──────────────────────────────────────
    fired = {(row.entity_name, row.rule_id) for row in findings.itertuples()
             if row.rule_id in RULE_DETECTORS}
    tp = fn = fp = unverified = 0
    per_rule = {}
    for entity, rules in injected.items():
        for rule in rules:
            hit = (entity, rule) in fired
            tp += hit
            fn += (not hit)
            stats = per_rule.setdefault(rule, dict(tp=0, fn=0, fp=0))
            stats["tp" if hit else "fn"] += 1
        for rule in {r for (e, r) in fired if e == entity} - rules:
            unverified += 1
            per_rule.setdefault(rule, dict(tp=0, fn=0, fp=0))["fp"] += 0
    for entity in controls:
        for rule in {r for (e, r) in fired if e == entity}:
            fp += 1
            per_rule.setdefault(rule, dict(tp=0, fn=0, fp=0))["fp"] += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Injected weaknesses found", f"{tp}/{tp + fn}")
    c2.metric("Recall", f"{recall:.0%}", help="Share of injected weaknesses the detectors caught")
    c3.metric("Precision", f"{precision:.0%}",
              help="Share of rule findings on control entities + matched injections that are correct")
    c4.metric("F1", f"{f1:.2f}")
    c5.metric("Control false positives", fp,
              help="Findings raised on entities with no injected weakness")

    # ── examiner adjudication: the human half of the measurement ─────────
    if "examiner_verdict" in findings.columns:
        adjudicated = findings[findings["examiner_verdict"].fillna("") != ""]
        if not adjudicated.empty:
            counts = adjudicated["examiner_verdict"].value_counts()
            confirmed = int(counts.get("Confirmed", 0))
            false_positive = int(counts.get("False positive", 0))
            st.markdown("---")
            st.subheader("Examiner adjudication (the human reference)")
            e1, e2, e3, e4 = st.columns(4)
            e1.metric("Findings adjudicated", len(adjudicated))
            e2.metric("Confirmed", confirmed)
            e3.metric("Called false positive", false_positive)
            e4.metric("Examiner-agreed precision",
                      f"{confirmed / len(adjudicated):.0%}" if len(adjudicated) else "—",
                      help="Share of adjudicated findings the examiner confirmed. This is the only "
                           "precision figure measured against real human judgement rather than "
                           "against the generator's own labels.")
            st.dataframe(counts.rename("findings").reset_index().rename(
                columns={"examiner_verdict": "Verdict", "count": "findings"}),
                width="stretch", hide_index=True)
            st.caption("Verdicts are recorded on the Examiner Review page, are stored as an append-only "
                       "history, and remove the finding from scoring once adjudicated false positive "
                       "or expected. As this table fills during the shadow-run pilot it becomes the "
                       "evidence for tuning each rule's threshold.")
        else:
            st.markdown("---")
            st.info("No findings have been adjudicated by an examiner yet. The synthetic numbers "
                    "above measure the detectors against injected patterns; only examiner "
                    "adjudication (Examiner Review page) measures them against human judgement.",
                    icon="🧑‍⚖️")

    st.markdown("---")
    st.subheader("Per-detector performance")
    rows = []
    for rule, stats in sorted(per_rule.items()):
        r_tp, r_fn, r_fp = stats["tp"], stats["fn"], stats["fp"]
        rows.append(dict(
            rule=rule, detector=RULE_MEANING.get(rule, rule),
            injected_cases=r_tp + r_fn, caught=r_tp, missed=r_fn, false_positives=r_fp,
            recall=round(r_tp / (r_tp + r_fn), 2) if (r_tp + r_fn) else None,
        ))
    per_df = pd.DataFrame(rows)
    st.dataframe(per_df, width="stretch", hide_index=True)

    if not per_df.empty and per_df["caught"].sum():
        fig = px.bar(
            per_df.melt(id_vars=["rule", "detector"], value_vars=["caught", "missed", "false_positives"],
                        var_name="outcome", value_name="count"),
            x="rule", y="count", color="outcome", barmode="group",
            labels={"rule": "Rule", "count": "Findings", "outcome": ""},
            height=340,
        )
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, width="stretch")

    if unverified:
        st.info(
            f"{unverified} additional rule finding(s) fired on entities that also carry injected "
            "weaknesses but were not the injected rule. These are reported as *unverified*: in a "
            "synthetic dataset they are usually collateral effects of the injection (for example a "
            "template-note entity also being flagged for shallow records); against real data they "
            "would need examiner adjudication."
        )

    # ── population-relative detectors ────────────────────────────────────
    st.markdown("---")
    st.subheader("Population-relative detectors (reported separately)")
    pop = findings[findings["rule_id"].isin(POPULATION_DETECTORS)]
    if pop.empty:
        st.caption("No peer/trend findings in this run.")
    else:
        counts = pop.groupby("rule_id").size().reset_index(name="findings")
        counts["detector"] = counts["rule_id"].map(RULE_MEANING)
        st.dataframe(counts[["rule_id", "detector", "findings"]], width="stretch", hide_index=True)
        st.caption(
            "Peer-deviation, trend and outlier detectors compare entities with each other, so they can "
            "fire on a control entity without being wrong - a portfolio in which every entity behaves "
            "identically is itself the exception. They are excluded from the precision figure above and "
            "are weaker evidence than a single-metric threshold breach; the finding cards label them as such."
        )

    # ── method + limitations ─────────────────────────────────────────────
    st.markdown("---")
    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("How this validation was run")
        st.markdown(
            """
            1. A synthetic portfolio is generated from a **seeded simulation**: Poisson arrivals on a
               diurnal curve, log-normal acknowledgement/closure times, a case lifecycle with
               investigation workflow events, disposition records with response targets, and an
               asset inventory whose monitoring state can contradict its declared state.
            2. A known subset of entities is then **deliberately overridden** to carry specific
               weaknesses - rubber-stamping, no escalation, template notes, silent critical assets,
               a night blind spot, missing case records, low activity, benchmark breaches, and the
               incident-management family (SLA misreporting, severity softening, no root cause,
               investigation rework loops, repeated reopens, absent investigation records,
               ungoverned risk acceptance, and declared KPIs its own records contradict).
            3. Six entities are deliberately left **clean**, so the false-positive count is measured
               rather than assumed.
            4. The generator records every override in `ground_truth.json`; the detection engine runs
               over the ingested submissions **without seeing** that file.
            5. Findings are matched back to the labels to produce the table above.
            """
        )
    with col_b:
        st.subheader("Limitations - stated plainly")
        st.markdown(
            """
            * These are **synthetic-ground-truth** numbers. They prove the detectors fire on the
              patterns they claim to detect; they do **not** prove agreement with expert review.
            * The simulation is calibrated to published-style SOC medians, not to real NCIIPC
              submissions, so absolute rates will differ on real data.
            * A finding that fires on an entity for a rule that was *not* injected is neither
              scored as a hit nor as a false positive here - it is listed as unverified, because in
              a synthetic dataset it is frequently a real collateral pattern.
            * Thresholds are configurable defaults, not fitted values - a supervisor is expected to
              re-tune them (Settings → Detection Thresholds) and re-run; the per-detector examiner
              agreement table above is the evidence for doing so.
            * **Proposed next step:** a shadow-run pilot in which SAT-SA is run alongside one real
              manual review cycle, and overlap between SAT-SA findings and examiner findings is
              measured entity-by-entity and rule-by-rule.
            """
        )

    st.markdown("---")
    st.subheader("Data-quality gates")
    with get_db() as conn:
        dq_rows = conn.execute(
            "SELECT entity_name, data_quality_alerts, data_quality_cases, data_quality_assets, "
            "data_quality_escalations, data_quality_investigations, data_quality_dispositions "
            "FROM entity_metrics ORDER BY data_quality_alerts").fetchall()
    if dq_rows:
        dq_df = pd.DataFrame([dict(r) for r in dq_rows]).rename(columns={
            "entity_name": "Entity", "data_quality_alerts": "Alerts DQ",
            "data_quality_cases": "Cases DQ", "data_quality_assets": "Inventory DQ",
            "data_quality_escalations": "Escalations DQ",
            "data_quality_investigations": "Workflow DQ",
            "data_quality_dispositions": "Closures DQ"})
        st.dataframe(dq_df, width="stretch", hide_index=True)
        st.caption("Negative-space findings are withheld for tables whose completeness falls below the "
                   "configured minimum, so 'no evidence' is never confused with 'no reporting'. A "
                   "completeness score in this table that is unexpectedly low is usually a column-"
                   "mapping problem in the submission rather than a real gap — check the per-file "
                   "validation report on the Register page.")

    # ── ingestion integrity self-check ───────────────────────────────────
    st.markdown("---")
    st.subheader("Ingestion integrity self-check")
    st.caption("A canonical column that no synonym entry can map is not merely unmapped on one "
               "file — it can never be populated by any submission, so every feature built on it is "
               "permanently zero and invisible in a successful ingest. The check below runs against "
               "the live schema so that class of failure cannot hide.")
    from ingestion.normalizer import (REQUIRED_FOR_ANALYSIS, TABLE_COLUMNS,
                                      audit_column_coverage, DATE_COLUMNS)
    blind = audit_column_coverage()
    if blind:
        st.error(f"Unmappable canonical column(s) detected: {blind}. These columns can never be "
                 f"populated by any submission — add a synonym entry in "
                 f"`ingestion/normalizer.py`.")
    else:
        total = sum(len(v) for v in TABLE_COLUMNS.values())
        st.success(f"Every canonical column across all {len(TABLE_COLUMNS)} tables is reachable by "
                   f"at least one source-column synonym ({total} columns checked; "
                   f"{sum(len(v) for v in DATE_COLUMNS.values())} of them standardised to ISO-8601 "
                   f"on ingest).")
    coverage = pd.DataFrame([{
        "Table": table,
        "Canonical columns": len(cols),
        "Timestamp columns (ISO-8601 on ingest)": len(DATE_COLUMNS.get(table, [])),
        "Required for analysis": len(REQUIRED_FOR_ANALYSIS.get(table, [])),
    } for table, cols in TABLE_COLUMNS.items()])
    st.dataframe(coverage, width="stretch", hide_index=True)
