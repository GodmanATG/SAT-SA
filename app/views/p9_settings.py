"""
Page 9: Settings — Editable detection thresholds and capability weights
"""

import streamlit as st
from config import (
    load_config, save_config, load_capability_weights, load_benchmarks,
    DEFAULT_THRESHOLDS, CAPABILITY_NAMES, CONFIG_PATH,
)
from database import db_stats, vacuum_db


def render():
    st.title("️ Settings")
    st.caption(
        "All detection thresholds, reference benchmarks and scoring weights live in an editable "
        "JSON config (`app/detection_config.json`) — never hardcoded, and never trained from data. "
        "Changes take effect on the next detection run."
    )

    tab_thresh, tab_bench, tab_weights, tab_maint, tab_about = st.tabs([
        "Detection Thresholds", "Reference Benchmarks", "Capability Weights", "Maintenance",
        "About"
    ])

    # ── Tab 1: Thresholds ────────────────────────────────────────────────
    with tab_thresh:
        st.markdown("### Detection Thresholds")
        st.caption("These control when each algorithm flags a finding. "
                    "Lower thresholds = more sensitive (more findings). "
                    "Higher = less sensitive (fewer findings).")

        current = load_config()

        st.markdown("---")
        st.markdown("#### Execution Gap Detectors")

        c1, c2 = st.columns(2)
        with c1:
            current["fast_closure_minutes"] = st.number_input(
                "Fast closure threshold (minutes)",
                value=float(current.get("fast_closure_minutes", 5)),
                min_value=1.0, max_value=60.0, step=1.0,
                help="Alerts closed faster than this are counted as 'fast closures'",
            )
            current["fast_closure_rate_flag"] = st.slider(
                "Fast closure rate flag (%)",
                0, 100, int(current.get("fast_closure_rate_flag", 0.5) * 100),
                help="Flag if more than this % of critical alerts are fast-closed",
            ) / 100

            current["template_cosine_threshold"] = st.slider(
                "Template similarity threshold",
                0.50, 1.00, float(current.get("template_cosine_threshold", 0.85)),
                step=0.05,
                help="Notes with cosine similarity >= this are 'duplicates'",
            )

        with c2:
            current["escalation_rate_flag"] = st.slider(
                "Escalation rate flag (%)",
                0, 100, int(current.get("escalation_rate_flag", 0.3) * 100),
                help="Flag if fewer than this % of critical alerts are escalated",
            ) / 100

            current["repeat_alert_rate_flag"] = st.slider(
                "Repeat alert rate flag (%)",
                0, 100, int(current.get("repeat_alert_rate_flag", 0.2) * 100),
                help="Flag if more than this % of alerts are repeats",
            ) / 100

            current["bulk_closure_sigma"] = st.number_input(
                "Bulk closure sigma",
                value=float(current.get("bulk_closure_sigma", 3.0)),
                min_value=1.0, max_value=5.0, step=0.5,
                help="Flag hours with closures > mean + this × std_dev",
            )

        st.markdown("---")
        st.markdown("#### Negative Space Detectors")

        c3, c4 = st.columns(2)
        with c3:
            current["category_coverage_flag"] = st.slider(
                "Category coverage flag (%)",
                0, 100, int(current.get("category_coverage_flag", 0.5) * 100),
                help="Flag if coverage is below this %",
            ) / 100
        with c4:
            current["night_gap_flag"] = st.slider(
                "Night gap flag (%)",
                0, 100, int(current.get("night_gap_flag", 30)),
                help="Flag if night-time gap exceeds this %",
            )

        st.markdown("---")
        st.markdown("#### Incident Management & Governance (IM-xxx)")
        st.caption("These thresholds drive the detectors that read the case, investigation-workflow "
                   "and disposition/closure records. They are the ones an examiner is most likely to "
                   "re-tune after the first shadow-run cycle, guided by the per-detector examiner "
                   "agreement table on the Validation page.")

        i1, i2, i3 = st.columns(3)
        with i1:
            current["sla_breach_rate_flag"] = st.slider(
                "IM-001 · SLA breach rate flag", 0.01, 0.60,
                float(current.get("sla_breach_rate_flag", 0.10)), step=0.01,
                help="Share of closures exceeding the response target recorded against them")
            current["sla_misreport_threshold"] = st.slider(
                "IM-001 · SLA misreporting tolerance", 0.01, 0.60,
                float(current.get("sla_misreport_threshold", 0.15)), step=0.01,
                help="Closures flagged SLA-compliant whose own time-to-close contradicts it")
            current["severity_softening_flag"] = st.slider(
                "IM-002 · Severity softening flag", 0.05, 0.80,
                float(current.get("severity_softening_flag", 0.25)), step=0.05,
                help="Cases logged below the severity of the alerts they came from")
        with i2:
            current["no_root_cause_flag"] = st.slider(
                "IM-003 · Missing root cause flag", 0.05, 0.90,
                float(current.get("no_root_cause_flag", 0.35)), step=0.05,
                help="Significant closures with an empty root-cause field")
            current["no_remediation_flag"] = st.slider(
                "IM-003 · Missing remediation flag", 0.05, 0.90,
                float(current.get("no_remediation_flag", 0.40)), step=0.05,
                help="Significant closures with no remediation status or reference")
            current["rework_loop_flag"] = st.slider(
                "IM-004 · Investigation rework flag", 0.05, 0.90,
                float(current.get("rework_loop_flag", 0.30)), step=0.05,
                help="Investigations returning to an already-completed activity")
        with i3:
            current["reopen_rate_flag"] = st.slider(
                "IM-005 · Reopen rate flag", 0.01, 0.40,
                float(current.get("reopen_rate_flag", 0.05)), step=0.01,
                help="Share of closures subsequently reopened")
            current["investigation_gap_flag"] = st.slider(
                "IM-006 · Investigation trace gap flag", 0.05, 0.90,
                float(current.get("investigation_gap_flag", 0.30)), step=0.05,
                help="Cases with fewer than the expected workflow events")
            current["evidence_missing_flag"] = st.slider(
                "IM-006 · Workflow evidence gap flag", 0.05, 0.90,
                float(current.get("evidence_missing_flag", 0.40)), step=0.05,
                help="Workflow events recording neither evidence nor a result")
        current["template_note_sample_cap"] = st.number_input(
            "EG-003 · Max notes compared per entity (scale guard)", min_value=500,
            max_value=50000, value=int(current.get("template_note_sample_cap", 5000)), step=500,
            help="The note-similarity comparison is quadratic, so it runs on a deterministic "
                 "random sample of at most this many notes per entity. Raising it increases "
                 "precision at the cost of run time.")

        i4, i5 = st.columns(2)
        with i4:
            current["risk_accept_no_authority_flag"] = st.slider(
                "IM-007 · Risk accepted without authority", 0.10, 1.00,
                float(current.get("risk_accept_no_authority_flag", 0.5)), step=0.05,
                help="Accepted risks with no named accepting authority")
        with i5:
            current["investigation_time_min_minutes"] = st.number_input(
                "IM-009 · Minimum credible investigation time (minutes)",
                min_value=0.5, max_value=60.0,
                value=float(current.get("investigation_time_min_minutes", 2.0)), step=0.5,
                help="A case whose recorded workflow time is below this cannot credibly have "
                     "been investigated, however complete its record looks.")
            current["investigation_time_anomaly_flag"] = st.slider(
                "IM-009 · Thin-investigation flag", 0.05, 0.90,
                float(current.get("investigation_time_anomaly_flag", 0.30)), step=0.05,
                help="Share of cases below that floor before it is raised as a finding")

        st.markdown("---")
        st.markdown("#### Workforce & coverage (EG-007, NS-007)")
        e1, e2 = st.columns(2)
        with e1:
            current["analyst_concentration_flag"] = st.slider(
                "EG-007 · Analyst concentration flag", 0.50, 1.00,
                float(current.get("analyst_concentration_flag", 0.80)), step=0.05,
                help="Share of critical/high alerts one analyst may own before it is raised")
            current["analyst_concentration_min_analysts"] = st.number_input(
                "EG-007 · Minimum analysts active on critical/high work",
                min_value=1, max_value=50,
                value=int(current.get("analyst_concentration_min_analysts", 3)), step=1,
                help="Prevents flagging a genuinely single-analyst operation for being one")
        with e2:
            current["weekend_activity_ratio_flag"] = st.number_input(
                "NS-007 · Weekend/weekday activity ratio flag", min_value=0.01, max_value=1.0,
                value=float(current.get("weekend_activity_ratio_flag", 0.15)), step=0.05,
                help="Weekend activity at or below this multiple of the weekday rate is a blind "
                     "spot")
            current["weekend_min_alerts"] = st.number_input(
                "NS-007 · Minimum alerts to judge", min_value=20, max_value=5000,
                value=int(current.get("weekend_min_alerts", 150)), step=10,
                help="Below this the ratio is noise rather than a finding")

        st.markdown("---")
        st.markdown("#### IM-008 · Declared vs evidence tolerances")
        st.caption(
            "Rule IM-008 compares each performance metric an entity **declares** in its cover "
            "sheet against the same measure recomputed from its own records. These tolerances "
            "decide how much divergence is accepted as consistent before the declaration is "
            "called contradicted. They are the first thing to re-tune after a shadow-run cycle: "
            "if examiners keep calling IM-008 findings immaterial, widen the tolerance rather "
            "than switching the rule off."
        )
        tolerances = dict(current.get("declared_kpi_tolerances") or {})
        t1, t2, t3 = st.columns(3)
        with t1:
            tolerances["sla_compliance_pct"] = st.number_input(
                "SLA compliance · tolerance (percentage points)", min_value=0.0, max_value=60.0,
                value=float(tolerances.get("sla_compliance_pct", 8.0)), step=1.0)
            tolerances["escalation_compliance_pct"] = st.number_input(
                "Escalation compliance · tolerance (percentage points)", min_value=0.0,
                max_value=60.0, value=float(tolerances.get("escalation_compliance_pct", 10.0)),
                step=1.0)
        with t2:
            tolerances["investigation_records_completeness_pct"] = st.number_input(
                "Investigation completeness · tolerance (percentage points)", min_value=0.0,
                max_value=60.0,
                value=float(tolerances.get("investigation_records_completeness_pct", 10.0)),
                step=1.0)
            tolerances["monitoring_coverage_production_pct"] = st.number_input(
                "Monitoring coverage · tolerance (percentage points)", min_value=0.0,
                max_value=60.0,
                value=float(tolerances.get("monitoring_coverage_production_pct", 5.0)), step=1.0)
        with t3:
            tolerances["critical_alert_ack_minutes_median"] = st.number_input(
                "Critical acknowledgement · tolerance factor (measured/declared)", min_value=1.0,
                max_value=10.0,
                value=float(tolerances.get("critical_alert_ack_minutes_median", 1.5)), step=0.1)
            tolerances["critical_incident_containment_minutes_median"] = st.number_input(
                "Critical containment · tolerance factor (measured/declared)", min_value=1.0,
                max_value=10.0,
                value=float(tolerances.get("critical_incident_containment_minutes_median", 1.5)),
                step=0.1)
        current["declared_kpi_tolerances"] = tolerances
        current["declared_kpi_min_contradictions"] = st.number_input(
            "Contradicted declarations required to raise IM-008", min_value=1, max_value=6,
            value=int(current.get("declared_kpi_min_contradictions", 1)), step=1,
            help="1 raises a finding on any unsupported declaration; raise it to see only "
                 "mutually reinforcing patterns.")

        st.markdown("---")
        st.markdown("#### Statistical Detectors")

        c5, c6 = st.columns(2)
        with c5:
            current["zscore_threshold"] = st.number_input(
                "Z-score threshold",
                value=float(current.get("zscore_threshold", 2.0)),
                min_value=1.0, max_value=4.0, step=0.5,
                help="Flag entities with |z-score| > this",
            )
            current["min_cohort_size"] = st.number_input(
                "Minimum cohort size for peer comparison",
                value=int(current.get("min_cohort_size", 5)),
                min_value=2, max_value=20, step=1,
                help="Fall back to absolute benchmarking if sector has fewer entities",
            )
        with c6:
            current["isolation_forest_contamination"] = st.slider(
                "Isolation Forest contamination",
                0.01, 0.30, float(current.get("isolation_forest_contamination", 0.1)),
                step=0.01,
                help="Expected fraction of outliers",
            )
            current["trend_pvalue_threshold"] = st.slider(
                "Trend p-value threshold",
                0.01, 0.10, float(current.get("trend_pvalue_threshold", 0.05)),
                step=0.01,
                help="Flag trends with p-value below this (stricter = lower)",
            )

        if st.button(" Save and re-run analytics now", key="thresh_apply_run", type="primary"):
            # Save first, then run: the point of re-running from here is to see the effect of
            # the values on screen, so they must be the values the engine actually reads.
            save_config(current)
            from views.components import run_detection_with_progress
            summary = run_detection_with_progress("Re-running analytics with the saved thresholds")
            st.success(f" Thresholds saved and applied — {summary['entities']} entities · "
                       f"{summary['findings']} findings · {summary['cycle_label']} · "
                       f"{summary['duration_s']}s. A new cycle snapshot was recorded.")

        st.markdown("---")
        col_save, col_reset = st.columns(2)
        with col_save:
            if st.button(" Save Thresholds", type="primary"):
                save_config(current)
                st.success(" Thresholds saved! Run detection again to apply.")
        with col_reset:
            if st.button(" Reset to Defaults"):
                save_config(DEFAULT_THRESHOLDS)
                st.success("Thresholds reset to defaults.")
                st.rerun()

    # ── Tab 2: Absolute reference benchmarks ─────────────────────────────
    with tab_bench:
        st.markdown("### Absolute Reference Benchmarks")
        st.caption(
            "Used by the peer-independent detectors (BM-001/002/003). Peer comparison can only say "
            "whether an entity differs from its peers — if a whole sector under-performs, every "
            "member looks normal. These are the fixed reference values that catch that case. "
            "Replace them with the benchmark table your submission cites."
        )
        benchmarks = load_benchmarks()
        b1, b2 = st.columns(2)
        with b1:
            benchmarks["median_time_to_ack_minutes_critical"] = st.number_input(
                "Reference median time-to-acknowledge, critical/high (minutes)",
                min_value=1.0, max_value=600.0,
                value=float(benchmarks.get("median_time_to_ack_minutes_critical", 15.0)), step=1.0)
            benchmarks["median_time_to_close_minutes_critical"] = st.number_input(
                "Reference median time-to-close, critical (minutes)",
                min_value=5.0, max_value=5000.0,
                value=float(benchmarks.get("median_time_to_close_minutes_critical", 240.0)), step=10.0)
        with b2:
            benchmarks["escalation_rate_critical"] = st.slider(
                "Reference escalation rate for critical alerts (%)",
                0, 100, int(benchmarks.get("escalation_rate_critical", 0.6) * 100)) / 100
            benchmarks["reference_label"] = st.text_input(
                "Reference basis (appears in every benchmark finding rationale)",
                value=str(benchmarks.get("reference_label", "Illustrative SOC survey medians")))
        if st.button(" Save benchmarks", type="primary"):
            save_config(load_config(), {"benchmarks": benchmarks})
            st.success(" Benchmarks saved — re-run detection to apply.")

    # ── Tab 3: Capability Weights ────────────────────────────────────────
    with tab_weights:
        st.markdown("### Capability Dimension Weights")
        st.caption(
            "These weights determine how much each capability dimension contributes "
            "to the overall risk score. Default is equal weighting."
        )

        weights = load_capability_weights()

        for cap_id, cap_name in CAPABILITY_NAMES.items():
            weights[cap_id] = st.slider(
                f"{cap_id}: {cap_name}",
                0.0, 3.0, float(weights.get(cap_id, 1.0)),
                step=0.1,
                key=f"weight_{cap_id}",
            )

        if st.button(" Save Weights", type="primary"):
            # save_config merges sections, so saving weights never clobbers thresholds
            save_config(load_config(), {"capability_weights": weights})
            st.success(" Capability weights saved!")
        st.caption("A capability's score is the weighted sum of the severity of findings tagged to it. "
                   "Raising a weight makes that capability dominate the scorecard average inside the "
                   "composite risk score.")

    # ── Tab 4: Maintenance ───────────────────────────────────────────────
    with tab_maint:
        st.markdown("### Database & Storage")
        stats = db_stats()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Database size", f"{stats['size_mb']} MB")
        m2.metric("Alerts", f"{stats['counts'].get('alerts', 0):,}")
        m3.metric("Cases", f"{stats['counts'].get('cases', 0):,}")
        m4.metric("Findings", f"{stats['counts'].get('findings', 0):,}")
        st.caption("The database is a single portable SQLite file (`app/satsa.db`) — copy it to any "
                   "air-gapped machine and the tool works with no installation of a database server.")

        st.markdown("---")
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button(" Compact database (VACUUM)"):
                result = vacuum_db()
                st.success(f"Reclaimed {result['saved'] / 1024 / 1024:.1f} MB — now {result['size_mb']} MB.")
                st.rerun()
        with col_b:
            st.markdown("**Config file**")
            st.code(str(CONFIG_PATH), language=None)
            if CONFIG_PATH.exists():
                st.download_button("⬇️ Download current config (JSON)",
                                   CONFIG_PATH.read_bytes(), "detection_config.json",
                                   "application/json")

        st.markdown("---")
        st.markdown("### Audit trail (last 25 actions)")
        from database import get_db
        with get_db() as conn:
            logs = [dict(r) for r in conn.execute(
                "SELECT timestamp, action, entity_id, details FROM audit_log "
                "ORDER BY log_id DESC LIMIT 25").fetchall()]
        if logs:
            import pandas as pd
            st.dataframe(pd.DataFrame(logs), width="stretch", hide_index=True)
        else:
            st.info("No audit entries yet.")

    # ── Tab 5: About ─────────────────────────────────────────────────────
    with tab_about:
        st.markdown("### About SAT-SA")
        st.markdown("""
        **SAT-SA** (Supervisory Analytics Tool for SOC Assessment) is a batch analytics
        tool built for NCIIPC supervisors to review Security Operations Center performance
        across Critical Sector Entities.

        **Key Design Principles:**
        -  **Fully offline** — no cloud, no internet, no external APIs
        -  **Explainable** — every finding links to evidence records
        - ️ **Configurable** — all thresholds and weights are editable
        - ️ **Modular** — new detection rules can be added without rebuilding

        **Detection Engine (22 detectors + a data-quality gate, all explainable):**
        - **7 Execution Gap** rules — fast closures (EG-001), missing escalation (EG-002),
          template notes (EG-003), chronic repeat alerts (EG-004), bulk closures (EG-005),
          shallow investigation records (EG-006), critical workload concentrated on one analyst
          (EG-007)
        - **7 Negative Space** rules — silent critical assets (NS-001), missing sector categories
          (NS-002), night blind spot (NS-003), low activity vs peers (NS-004), missing case /
          escalation records (NS-005), deployed-but-unmonitored assets (NS-006), weekend blind
          spot (NS-007)
        - **9 Incident-management & governance** rules — SLA integrity and SLA misreporting
          (IM-001), severity softening (IM-002), root-cause/remediation records (IM-003),
          investigation rework loops (IM-004), reopen rate (IM-005), investigation trace and
          evidence gaps (IM-006), accepted risk without authority (IM-007), declared vs measured
          performance (IM-008), cases investigated in seconds (IM-009)
        - **3 Absolute Benchmark** detectors — acknowledgement (BM-001), critical closure (BM-002),
          escalation rate (BM-003) against fixed reference values
        - **3 Population detectors** — peer z-scores (STAT-001), trend drift (STAT-002),
          Isolation Forest outlier (STAT-003)
        - **Data-quality gate (DQ-001)** — withholds negative-space conclusions when a table is
          too incomplete to distinguish "no evidence" from "no reporting"

        **Scoring:** 8-capability scorecard (C1–C8) + composite prioritisation index.
        Weights, thresholds and benchmarks are all editable above — nothing is trained from data.

        **Human in the loop:** every finding can be adjudicated (Examiner Review page). Verdicts
        are append-only, survive re-analysis, and remove a finding from scoring when it is judged
        a false positive or explained. **Cycle snapshots** are retained on every run, so movement
        between review periods is measurable rather than assumed.

        **Tech stack:** Python · SQLite · Streamlit · Plotly · pandas · scikit-learn · fpdf2.
        No cloud service, no SaaS, no external AI API; runs fully offline.

        **Team Sirius** — SIH 2026
        """)
