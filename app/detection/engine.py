"""
Detection engine
================
Orchestrates the whole supervisory analytics pipeline for every registered entity:

    read canonical tables
      -> feature engineering (entity + entity x month)
      -> execution-gap / negative-space / absolute-benchmark detectors
      -> peer-relative deviation + multivariate outlier detection
      -> trend drift on real monthly features
      -> composite risk score + 8-capability scorecard
      -> persistence (entity_metrics, findings, monthly_metrics) + audit log

Every finding is explainable by construction: rule id, capability tags, templated
rationale, the metric value and threshold that triggered it, and a capped sample of
the exact evidence records behind it.

Callable head-lessly (``python detection/engine.py``) so a supervisor can run the
same analytics without the UI and get a text summary - useful for batch
submission cycles and for the validation harness.
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
import uuid
from datetime import datetime

import pandas as pd

_DET_DIR = pathlib.Path(__file__).resolve().parent
_APP_DIR = _DET_DIR.parent
for _p in (str(_APP_DIR), str(_DET_DIR)):
    if _p not in sys.path:
        sys.path.append(_p)

from config import (  # noqa: E402
    RISK_SCORE_WEIGHTS, SECTOR_EXPECTED_CATEGORIES, config_snapshot, is_non_counting,
    load_benchmarks, load_capability_weights, load_config, risk_tier_from_score,
)
from database import get_db, log_action  # noqa: E402

from common import compute_data_quality, finalize_finding  # noqa: E402
from benchmarks import run_all as run_benchmarks  # noqa: E402
from execution_gaps import run_all as run_execution_gaps  # noqa: E402
from features import (  # noqa: E402
    entity_features, monthly_case_features, monthly_features,
)
from incident_mgmt import declared_vs_measured  # noqa: E402
from incident_mgmt import run_all as run_incident_mgmt  # noqa: E402
from snapshots import new_run_id, next_cycle_label, save_snapshot  # noqa: E402
from negative_space import detect_data_quality, run_all as run_negative_space  # noqa: E402
from scoring import (  # noqa: E402
    CAPABILITY_COLUMNS, capability_scores, composite_risk_score, metric_index,
)
from statistical import (  # noqa: E402
    detect_isolation_forest_outliers, detect_peer_anomalies, detect_trend_drift,
    leave_one_out_medians, sector_medians,
)

DQ_REQUIRED = {
    "alerts": ["alert_category", "severity", "created_ts", "closed_ts"],
    "cases": ["investigation_note_text", "root_cause_documented"],
    "escalations": ["escalated_ts", "escalated_to_role"],
    "asset_inventory": ["asset_class", "criticality_tier"],
    "investigations": ["activity_type", "ts"],
    "dispositions": ["disposition", "closure_ts"],
}

# Metric columns produced by the feature layer that entity_metrics persists.
EXTRA_METRIC_COLUMNS = [
    "total_dispositions", "sla_breach_rate", "sla_misreport_rate", "sla_misreport_count",
    "close_time_over_target_median", "true_positive_rate", "risk_accept_rate",
    "risk_accept_no_authority_rate", "no_root_cause_gap", "no_remediation_gap",
    "significant_closures", "investigation_events", "cases_with_events",
    "cases_without_investigation", "avg_events_per_case", "investigation_gap_rate",
    "evidence_gap_rate", "rework_loop_rate", "rework_cases", "significant_cases",
    "cases_from_significant_alerts", "severity_softening_rate", "severity_softening_count",
    "escalation_downgrade_rate", "monitoring_coverage_pct", "critical_monitoring_coverage_pct",
    "telemetry_silence_pct", "declared_kpi_contradictions", "examiner_suppressed",
    # declared-vs-measured comparison table (rendered on the Entity Profile page and
    # in the PDF report). Without it here the column stays at its schema default and
    # the comparison silently renders as "no measured comparison available".
    "declared_kpi_details",
    # workforce concentration (EG-007)
    "top_analyst_share", "critical_analysts_active", "critical_alerts_assigned",
    # weekend coverage (NS-007)
    "weekend_alerts", "weekend_activity_ratio",
    # investigation time (IM-009)
    "cases_with_thin_investigation", "investigation_time_anomaly_rate",
    "investigation_minutes_median",
    # text-similarity sampling provenance (EG-003)
    "note_similarity_sample_size", "note_similarity_sampled",
]

ENTITY_METRIC_COLUMNS = [
    "entity_id", "entity_name", "sector", "period", "total_alerts", "total_cases",
    "total_escalations", "total_assets", "monitored_assets", "critical_assets",
    "time_to_ack_median", "time_to_ack_median_critical", "time_to_close_median",
    "time_to_close_median_critical", "pct_critical_closed_under_5min", "escalation_rate_critical",
    "repeat_alert_rate", "fast_closure_rate", "crit_no_escalation_rate",
    "investigation_note_similarity",
    "reopen_rate", "avg_investigation_depth", "template_note_rate",
    "expected_category_coverage", "silent_critical_assets",
    "unmonitored_critical_assets", "night_coverage_gap_pct", "activity_deviation",
    "alerts_per_monitored_asset", "root_cause_rate", "missing_case_rate",
    *EXTRA_METRIC_COLUMNS,
    "risk_score", "risk_tier", "risk_rank", "metric_index", "capability_average",
    "peer_outlier_score",
    "c1_threat_detection", "c2_investigation", "c3_escalation", "c4_incident_response",
    "c5_security_operations", "c6_governance", "c7_operational_discipline",
    "c8_cyber_resilience", "data_quality_alerts", "data_quality_cases",
    "data_quality_assets", "data_quality_escalations", "data_quality_investigations",
    "data_quality_dispositions", "review_period_start", "review_period_end", "computed_at",
]

FINDING_COLUMNS = [
    "finding_id", "entity_id", "period", "capability_tags", "weakness_type", "rule_id",
    "title", "description", "rationale", "detector_group", "metric_value",
    "threshold_value", "severity_score", "severity", "evidence_ids", "evidence_count",
    "examiner_verdict",
]

MONTHLY_COLUMNS = [
    "entity_id", "entity_name", "month", "total_alerts", "fast_closure_rate",
    "crit_no_escalation_rate", "template_note_rate", "repeat_alert_rate",
    "night_coverage_gap_pct", "root_cause_rate", "escalation_rate_critical",
    "missing_case_rate", "risk_score", "total_cases", "total_dispositions",
    "sla_breach_rate", "rework_cases", "investigation_events",
    "weekend_alerts", "weekend_activity_ratio",
]


def _read_tables(conn) -> dict:
    def q(sql):
        return pd.read_sql_query(sql, conn)
    return dict(
        entities=q("SELECT * FROM entities"),
        alerts=q("SELECT * FROM alerts"),
        cases=q("SELECT * FROM cases"),
        escalations=q("SELECT * FROM escalations"),
        assets=q("SELECT * FROM asset_inventory"),
        investigations=q("SELECT * FROM investigations"),
        dispositions=q("SELECT * FROM dispositions"),
    )


def _dq_scores(alerts, cases, escalations, assets, investigations, dispositions) -> dict:
    return {
        "alerts": compute_data_quality(alerts, DQ_REQUIRED["alerts"]),
        "cases": compute_data_quality(cases, DQ_REQUIRED["cases"]),
        "escalations": compute_data_quality(escalations, DQ_REQUIRED["escalations"]),
        "asset_inventory": compute_data_quality(assets, DQ_REQUIRED["asset_inventory"]),
        "investigations": compute_data_quality(investigations, DQ_REQUIRED["investigations"]),
        "dispositions": compute_data_quality(dispositions, DQ_REQUIRED["dispositions"]),
    }


def finding_id_for(entity_id: str, rule_id: str, title: str) -> str:
    """Deterministic finding id.

    A finding's identity is (entity, rule, concept) - *not* the randomness of the
    run. That is what lets an examiner's adjudication survive the next detection
    cycle: re-running the pipeline regenerates the same id, so the verdict recorded
    against a finding still applies instead of silently detaching.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL,
                          f"sat-sa:finding:{entity_id}:{rule_id}:{title}"))


def _record(finding: dict, entity_id: str, entity_name: str, period: str) -> dict:
    return {
        "finding_id": finding_id_for(entity_id, finding["rule_id"], finding["title"]),
        "entity_id": entity_id,
        "entity_name": entity_name,
        "period": period,
        "capability_tags": json.dumps(finding["capability_tags"]),
        "weakness_type": finding["weakness_type"],
        "rule_id": finding["rule_id"],
        "title": finding["title"],
        "description": finding["description"],
        "rationale": finding["rationale"],
        "detector_group": finding.get("detector_group", "rule"),
        "metric_value": finding.get("metric_value", 0.0),
        "threshold_value": finding.get("threshold_value", 0.0),
        "severity": finding["severity"],
        "severity_score": finding["severity_score"],
        "evidence_ids": json.dumps(finding["evidence_ids"]),
        "evidence_count": finding["evidence_count"],
    }


def run_detection(db_path=None, progress=None) -> dict:
    """Run the full pipeline. Returns a summary dict."""
    started = time.time()
    if progress:
        progress("Loading canonical tables")

    thresholds = load_config()
    benchmarks = load_benchmarks()
    cap_weights = load_capability_weights()
    snapshot = config_snapshot()

    with get_db(db_path) as conn:
        tables = _read_tables(conn)
        # Latest examiner verdict per finding, loaded once: a finding adjudicated as a
        # false positive or explained cannot keep inflating a capability score.
        latest_verdicts = {}
        try:
            from database import latest_adjudications
            latest_verdicts = {fid: rec["verdict"] for fid, rec in
                               latest_adjudications(conn).items()}
        except Exception:
            latest_verdicts = {}
    non_counting_ids = {fid for fid, verdict in latest_verdicts.items()
                        if is_non_counting(verdict)}

    entities = tables["entities"]
    if entities.empty:
        return dict(entities=0, findings=0, message="No entities registered - ingest a submission first.")

    alerts, cases = tables["alerts"], tables["cases"]
    escalations, assets = tables["escalations"], tables["assets"]
    investigations, dispositions = tables["investigations"], tables["dispositions"]

    if progress:
        progress(f"Engineering features across {len(alerts):,} alert records")
    feats, enriched = entity_features(alerts, cases, escalations, assets, thresholds,
                                      investigations=investigations, dispositions=dispositions)
    if feats.empty:
        return dict(entities=0, findings=0, message="No alert records found for the registered entities.")

    # period label + attach registry metadata
    all_dates = pd.to_datetime(enriched["created_ts"], errors="coerce").dropna()
    period = (f"{all_dates.min():%Y-%m-%d}..{all_dates.max():%Y-%m-%d}"
              if not all_dates.empty else "unknown")
    meta = entities.set_index("entity_id")
    feats = feats.set_index("entity_id")
    dup_case_ids = feats.pop("_duplicate_case_ids") if "_duplicate_case_ids" in feats.columns else {}
    feats = feats.reindex(sorted(feats.index))
    feats["total_escalations"] = 0.0
    feats["total_cases"] = 0.0

    # fill any entity that has an inventory but no alerts, so it is still assessed
    for entity_id in meta.index:
        if entity_id not in feats.index:
            feats.loc[entity_id] = 0.0

    alerts_by_entity = {k: v for k, v in enriched.groupby("entity_id")}
    cases_by_entity = {k: v for k, v in cases.groupby("entity_id")} if not cases.empty else {}
    esc_by_entity = {k: v for k, v in escalations.groupby("entity_id")} if not escalations.empty else {}
    assets_by_entity = {k: v for k, v in assets.groupby("entity_id")} if not assets.empty else {}
    inv_by_entity = {k: v for k, v in investigations.groupby("entity_id")} \
        if not investigations.empty else {}
    disp_by_entity = {k: v for k, v in dispositions.groupby("entity_id")} \
        if not dispositions.empty else {}

    # sector medians for the low-activity detector (needs raw counts + estate size)
    preview = feats.reset_index()
    preview["total_alerts"] = pd.to_numeric(preview["total_alerts"], errors="coerce").fillna(0)
    preview["sector"] = preview["entity_id"].map(meta["sector"])
    medians = sector_medians(preview)
    loo_medians = leave_one_out_medians(preview)

    # Cohort night-activity norm: the baseline NS-003 compares against. A flat
    # "33% of alerts should be at night" is wrong for any SOC with a diurnal load
    # profile, so the norm is measured from the cohort and stated in the rationale.
    night_shares = enriched.groupby("entity_id")["is_night"].mean()
    cohort_night_share = float(night_shares.median()) if not night_shares.empty else 8.0 / 24.0
    sector_night_share = (enriched.assign(sector=enriched["entity_id"].map(meta["sector"]))
                          .groupby("sector")["is_night"].mean().to_dict())

    if progress:
        progress("Running detectors")
    findings: list[dict] = []
    metrics_rows: list[dict] = []

    for entity_id in feats.index:
        row = feats.loc[entity_id]
        entity_name = str(meta.at[entity_id, "entity_name"]) if entity_id in meta.index else entity_id
        sector = str(meta.at[entity_id, "sector"]) if entity_id in meta.index else ""
        ent_alerts = alerts_by_entity.get(entity_id, pd.DataFrame())
        ent_cases = cases_by_entity.get(entity_id, pd.DataFrame())
        ent_escalations = esc_by_entity.get(entity_id, pd.DataFrame())
        ent_assets = assets_by_entity.get(entity_id, pd.DataFrame())
        ent_investigations = inv_by_entity.get(entity_id, pd.DataFrame())
        ent_dispositions = disp_by_entity.get(entity_id, pd.DataFrame())
        declared_kpis = {}
        if entity_id in meta.index and "declared_kpis" in meta.columns:
            try:
                declared_kpis = json.loads(meta.at[entity_id, "declared_kpis"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                declared_kpis = {}

        dq = _dq_scores(ent_alerts, ent_cases, ent_escalations, ent_assets,
                        ent_investigations, ent_dispositions)

        metrics = {k: float(v) if not isinstance(v, (list, dict)) else 0.0 for k, v in row.items()}
        metrics.update(dict(entity_id=entity_id, entity_name=entity_name, sector=sector,
                            total_escalations=float(len(ent_escalations)),
                            total_cases=float(len(ent_cases))))
        monitored = metrics.get("monitored_assets") or metrics.get("total_assets") or 0
        metrics["alerts_per_monitored_asset"] = round(
            metrics.get("total_alerts", 0) / monitored, 3) if monitored else 0.0
        # peer baseline excludes the entity itself (see leave_one_out_medians)
        median_per_asset = loo_medians.get(entity_id) or medians.get(sector) or medians.get("__all__") or 0
        metrics["activity_deviation"] = (
            round(max(0.0, 1.0 - metrics["alerts_per_monitored_asset"] / median_per_asset), 4)
            if median_per_asset else 0.0)

        # Category coverage against the sector reference profile (a metric, not just
        # a detector input - it carries 8% of the composite risk score).
        expected_cats = {c.lower() for c in SECTOR_EXPECTED_CATEGORIES.get(sector, [])}
        cat_series = ent_alerts.get("alert_category", pd.Series(dtype=str)).dropna().str.lower().str.strip()
        cat_counts = cat_series.value_counts()
        present_cats = set(cat_counts[cat_counts >= 3].index)
        metrics["expected_category_coverage"] = (
            round(len(expected_cats & present_cats) / len(expected_cats), 4) if expected_cats else 1.0)

        # ── measured values for the declared-vs-evidence comparison (IM-008) ──
        # These are the same numbers the entity reports to its board, recomputed
        # from its own submission so the two can be set side by side.
        metrics["measured_sla_compliance_pct"] = round(
            max(0.0, 1.0 - metrics.get("sla_breach_rate", 0.0)) * 100, 2)
        metrics["measured_escalation_compliance_pct"] = round(
            metrics.get("escalation_rate_critical", 0.0) * 100, 2)
        metrics["measured_investigation_completeness_pct"] = round(
            max(0.0, 1.0 - metrics.get("investigation_gap_rate", 0.0)) * 100, 2)

        ent_findings: list[dict] = []
        ent_findings += run_execution_gaps(
            ent_alerts, ent_cases, thresholds,
            similarity=metrics.get("investigation_note_similarity", 0.0),
            duplicate_case_ids=dup_case_ids.get(entity_id),
            avg_depth=metrics.get("avg_investigation_depth", 0.0),
            monitored_assets=metrics.get("monitored_assets", 0.0),
            metrics=metrics,
            note_sample_size=metrics.get("note_similarity_sample_size", 0),
            note_sampled=bool(metrics.get("note_similarity_sampled", 0)),
        )
        ent_findings += run_negative_space(
            ent_alerts, ent_assets, ent_escalations, sector, metrics, median_per_asset,
            thresholds, dq_scores=dq,
            cohort_night_share=sector_night_share.get(sector, cohort_night_share),
        )
        ent_findings += run_benchmarks(metrics, benchmarks, thresholds)
        ent_findings += run_incident_mgmt(
            metrics, thresholds, cases=ent_cases, dispositions=ent_dispositions,
            investigations=ent_investigations, declared_kpis=declared_kpis)

        # data-quality findings (keep absence-of-evidence separate from bad reporting)
        for table, score in dq.items():
            count = len({"alerts": ent_alerts, "cases": ent_cases, "escalations": ent_escalations,
                         "asset_inventory": ent_assets, "investigations": ent_investigations,
                         "dispositions": ent_dispositions}[table])
            if count:
                ent_findings += detect_data_quality(table, score, count, thresholds)

        # declared-vs-measured table is persisted so the report can show the comparison
        # even for the KPIs the detector did not treat as contradictions
        comparison = declared_vs_measured(metrics, declared_kpis, thresholds)
        metrics["declared_kpi_contradictions"] = sum(1 for r in comparison if r["contradicted"])
        metrics["declared_kpi_details"] = json.dumps(comparison)

        findings += [_record(f, entity_id, entity_name, period) for f in ent_findings]
        metrics_rows.append(dict(
            metrics, data_quality_alerts=dq["alerts"], data_quality_cases=dq["cases"],
            data_quality_assets=dq["asset_inventory"],
            data_quality_escalations=dq["escalations"],
            data_quality_investigations=dq["investigations"],
            data_quality_dispositions=dq["dispositions"], period=period))

    metrics_df = pd.DataFrame(metrics_rows)

    if progress:
        progress("Comparing entities against peers and benchmarks")
    # ── peer-relative deviation (sector cohort, with documented fallback) ──
    peer_df = metrics_df.copy()
    peer_df["critical_alerts"] = peer_df["critical"] if "critical" in peer_df.columns else 0
    for entity_id in peer_df["entity_id"]:
        peer_findings = detect_peer_anomalies(peer_df, entity_id, thresholds)
        ent_meta = meta.loc[entity_id] if entity_id in meta.index else None
        findings += [_record(f, entity_id,
                             str(ent_meta["entity_name"]) if ent_meta is not None else entity_id,
                             period) for f in peer_findings]

    outliers = detect_isolation_forest_outliers(peer_df, thresholds)
    for entity_id, score in outliers.items():
        ent_meta = meta.loc[entity_id] if entity_id in meta.index else None
        finding = finalize_finding(
            rule_id="STAT-003",
            title="Multivariate operational outlier",
            description=("This entity is an outlier across the combined set of operational discipline "
                         "indicators, even where no single metric crosses its own threshold."),
            rationale=(f"Isolation Forest anomaly score {score:.3f} (contamination "
                       f"{thresholds.get('isolation_forest_contamination', 0.1):.0%}) across "
                       f"closure, escalation, investigation, coverage and activity features. "
                       f"Multivariate flags are weaker evidence than a single-metric breach and are "
                       f"labelled as such."),
            weakness_type="peer_anomaly",
            capability_tags=["C5", "C8"],
            severity="Medium",
            severity_score=0.5,
            evidence_ids=[],
            detector_group="peer",
            metric_value=score,
            thresholds=thresholds,
        )
        findings.append(_record(finding, entity_id,
                                str(ent_meta["entity_name"]) if ent_meta is not None else entity_id,
                                period))

    if progress:
        progress("Computing monthly trends")
    monthly = monthly_features(enriched)
    case_monthly = monthly_case_features(cases, dispositions, investigations,
                                         ["entity_id", "month"])
    if not case_monthly.empty:
        monthly = case_monthly if monthly.empty else monthly.merge(
            case_monthly, on=["entity_id", "month"], how="outer")
    monthly_rows = []
    if not monthly.empty:
        monthly = monthly.fillna(0)
        monthly = monthly.sort_values(["entity_id", "month"])
        for entity_id, group in monthly.groupby("entity_id"):
            ent_meta = meta.loc[entity_id] if entity_id in meta.index else None
            entity_name = str(ent_meta["entity_name"]) if ent_meta is not None else entity_id
            for f in detect_trend_drift(group, thresholds):
                findings.append(_record(f, entity_id, entity_name, period))
            for _, grow in group.iterrows():
                monthly_rows.append(dict(
                    entity_id=entity_id, entity_name=entity_name, month=grow["month"],
                    total_alerts=int(grow.get("total_alerts", 0)),
                    fast_closure_rate=round(float(grow.get("fast_closure_rate", 0)), 4),
                    crit_no_escalation_rate=round(float(grow.get("crit_no_escalation_rate", 0)), 4),
                    template_note_rate=round(float(grow.get("template_note_rate", 0)), 4),
                    repeat_alert_rate=round(float(grow.get("repeat_alert_rate", 0)), 4),
                    night_coverage_gap_pct=round(float(grow.get("night_coverage_gap_pct", 0)), 2),
                    root_cause_rate=round(float(grow.get("root_cause_rate", 0)), 4),
                    escalation_rate_critical=round(float(grow.get("escalation_rate_critical", 0)), 4),
                    missing_case_rate=round(float(grow.get("missing_case_rate", 0)), 4),
                    total_cases=int(grow.get("total_cases", 0) or 0),
                    total_dispositions=int(grow.get("closed_records", 0) or 0),
                    sla_breach_rate=round(float(grow.get("sla_breach_rate", 0) or 0), 4),
                    rework_cases=int(grow.get("reopened_cases", 0) or 0),
                    investigation_events=int(grow.get("investigation_events", 0) or 0),
                    weekend_alerts=int(grow.get("weekend_alerts", 0) or 0),
                    weekend_activity_ratio=round(
                        float(grow.get("weekend_activity_ratio", 1.0) or 0) or 1.0, 4),
                    risk_score=metric_index(grow.to_dict(), RISK_SCORE_WEIGHTS),
                ))

    if progress:
        progress("Scoring capabilities")
    findings_df = pd.DataFrame(findings)
    by_entity = {eid: g.to_dict("records") for eid, g in findings_df.groupby("entity_id")} \
        if not findings_df.empty else {}

    for row in metrics_rows:
        entity_id = row["entity_id"]
        entity_findings = by_entity.get(entity_id, [])
        # Scoring respects the examiner: findings adjudicated 'False positive' or
        # 'Expected' stay visible and auditable but stop contributing to the score.
        counted = [f for f in entity_findings if f.get("finding_id") not in non_counting_ids]
        row["examiner_suppressed"] = len(entity_findings) - len(counted)
        caps = capability_scores(counted, cap_weights)
        row.update({col: caps.get(col, 0.0) for col in CAPABILITY_COLUMNS.values()})
        row["peer_outlier_score"] = outliers.get(entity_id, 0.0)
        row["metric_index"] = metric_index(row, RISK_SCORE_WEIGHTS)
        row["capability_average"] = round(
            sum(caps.get(col, 0.0) for col in CAPABILITY_COLUMNS.values())
            / len(CAPABILITY_COLUMNS), 1)
        row["risk_score"] = composite_risk_score(row, RISK_SCORE_WEIGHTS, caps)
        row["risk_tier"] = risk_tier_from_score(row["risk_score"])
        row["pct_critical_closed_under_5min"] = round(
            float(row.get("fast_closure_rate", 0.0)) * 100, 2)
        alert_times = pd.to_datetime(enriched.loc[enriched.entity_id == entity_id, "created_ts"],
                                     errors="coerce").dropna()
        row["review_period_start"] = f"{alert_times.min():%Y-%m-%d}" if not alert_times.empty else ""
        row["review_period_end"] = f"{alert_times.max():%Y-%m-%d}" if not alert_times.empty else ""
        row["computed_at"] = datetime.now().isoformat(timespec="seconds")

    metrics_df = pd.DataFrame(metrics_rows).sort_values("risk_score", ascending=False)
    metrics_df["risk_rank"] = range(1, len(metrics_df) + 1)

    if progress:
        progress("Persisting results")
    with get_db(db_path) as conn:
        # Findings are regenerated on every run, so the whole table is rewritten. Examiner
        # adjudications are an append-only record that must outlive that rebuild, which is
        # why they carry no foreign key to findings - FK enforcement is relaxed here so the
        # rewrite can never cascade into an examiner's decision history.
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM entity_metrics")
        conn.execute("DELETE FROM findings")
        conn.execute("DELETE FROM monthly_metrics")

        persist = metrics_df[[c for c in ENTITY_METRIC_COLUMNS if c in metrics_df.columns]]
        persist.to_sql("entity_metrics", conn, if_exists="append", index=False)

        if not findings_df.empty:
            findings_df["examiner_verdict"] = findings_df["finding_id"].map(latest_verdicts)
            findings_df["examiner_verdict"] = findings_df["examiner_verdict"].fillna("")
            cols = [c for c in FINDING_COLUMNS if c in findings_df.columns]
            findings_df[cols].to_sql("findings", conn, if_exists="append", index=False)
        if monthly_rows:
            pd.DataFrame(monthly_rows)[MONTHLY_COLUMNS].to_sql(
                "monthly_metrics", conn, if_exists="append", index=False)

        # ── cycle-over-cycle snapshot ──
        # entity_metrics is overwritten every run, so "is this entity improving?" can only
        # be answered by keeping the previous run. One immutable row per entity per run.
        run_id = new_run_id()
        cycle_label = next_cycle_label(conn)
        tier_map = ({str(eid): str(meta.at[eid, "tier"] or "") for eid in meta.index}
                    if "tier" in meta.columns else {})
        snapshotted = save_snapshot(conn, metrics_df, findings_df, run_id, cycle_label, tier_map)

        log_action(conn, "DETECTION_RUN", "",
                   f"{cycle_label} ({run_id}): analysed {len(metrics_df)} entities, "
                   f"{len(enriched):,} alerts, {len(findings_df)} findings over {period}; "
                   f"{snapshotted} metric snapshot(s) retained; "
                   f"thresholds={json.dumps(snapshot['thresholds'], sort_keys=True)}")

    summary = dict(
        entities=len(metrics_df),
        alerts=int(len(enriched)),
        findings=int(len(findings_df)),
        findings_by_group=findings_df["detector_group"].value_counts().to_dict() if not findings_df.empty else {},
        findings_by_type=findings_df["weakness_type"].value_counts().to_dict() if not findings_df.empty else {},
        critical_entities=int((metrics_df["risk_tier"] == "Critical Attention").sum()),
        cases=int(len(cases)),
        investigations=int(len(investigations)),
        dispositions=int(len(dispositions)),
        period=period,
        run_id=run_id,
        cycle_label=cycle_label,
        snapshots=snapshotted,
        duration_s=round(time.time() - started, 2),
    )
    if progress:
        progress("Done")
    return summary


if __name__ == "__main__":
    result = run_detection(progress=lambda msg: print(f"  · {msg}"))
    print(json.dumps(result, indent=2, default=str))
