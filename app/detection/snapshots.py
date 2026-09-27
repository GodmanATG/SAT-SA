"""
Cycle-over-cycle snapshots
==========================
The live ``entity_metrics`` table is overwritten on every analytics run, so on its
own it can only ever answer "how does this entity look today?". Continuous
supervision needs the other question — **"is this entity getting better or worse
since the last submission cycle?"** — and that requires keeping the previous run.

Each run therefore appends one immutable row per entity to ``metric_snapshots``.
Nothing is overwritten: run 7 is still readable after run 8, so a supervisor can
see a trend rather than a snapshot, and can prove *why* their view of an entity
changed between review periods.

``cycle_diff`` produces the presentation-ready comparison: previous value, latest
value, delta, and — crucially — the **direction**, because for some measures a rise
is the finding (fast closures) and for others a fall is (escalations, root causes).
Direction is declared once, here, instead of being re-guessed in each view.
"""

from __future__ import annotations

import pathlib
import sys
import uuid

import pandas as pd

_APP_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(_APP_DIR) not in sys.path:
    sys.path.append(str(_APP_DIR))

# Columns stored in a snapshot row. This is the audit surface of a run: if a
# supervisor asks "what did the tool see last time?", it is answerable from here
# without re-running anything.
SNAPSHOT_COLUMNS = [
    "entity_id", "entity_name", "sector", "criticality_tier", "risk_score", "risk_tier",
    "risk_rank", "capability_average", "metric_index", "findings_count", "findings_high",
    "fast_closure_rate", "crit_no_escalation_rate", "template_note_rate", "repeat_alert_rate",
    "missing_case_rate", "root_cause_rate", "night_coverage_gap_pct",
    "expected_category_coverage", "activity_deviation", "silent_critical_assets",
    "unmonitored_critical_assets", "sla_breach_rate", "sla_misreport_rate",
    "severity_softening_rate", "no_root_cause_gap", "no_remediation_gap", "rework_loop_rate",
    "reopen_rate", "investigation_gap_rate", "evidence_gap_rate",
    "risk_accept_no_authority_rate", "telemetry_silence_pct",
    "critical_monitoring_coverage_pct", "top_analyst_share", "weekend_activity_ratio",
    "investigation_time_anomaly_rate", "declared_kpi_contradictions", "total_alerts",
    "total_cases", "total_escalations", "total_dispositions", "c1_threat_detection",
    "c2_investigation", "c3_escalation", "c4_incident_response", "c5_security_operations",
    "c6_governance", "c7_operational_discipline", "c8_cyber_resilience",
    "examiner_suppressed",
]

# metric -> (label, "high" = a rise is worse | "low" = a fall is worse | None = context)
DIFF_METRICS: dict[str, tuple[str, str | None]] = {
    "risk_score": ("Supervisory risk score", "high"),
    "capability_average": ("Capability weakness average", "high"),
    "metric_index": ("Weighted metric index", "high"),
    "findings_count": ("Findings raised", "high"),
    "findings_high": ("High-severity findings", "high"),
    "fast_closure_rate": ("Critical/high closed within minutes", "high"),
    "crit_no_escalation_rate": ("Critical alerts without escalation", "high"),
    "template_note_rate": ("Near-duplicate investigation notes", "high"),
    "repeat_alert_rate": ("Chronic repeat alert traffic", "high"),
    "missing_case_rate": ("Critical/high alerts with no case record", "high"),
    "root_cause_rate": ("Alerts with a root cause on file", "low"),
    "night_coverage_gap_pct": ("Night-time coverage gap", "high"),
    "expected_category_coverage": ("Expected category coverage", "low"),
    "activity_deviation": ("Activity shortfall vs sector peers", "high"),
    "silent_critical_assets": ("Critical assets generating no alerts", "high"),
    "unmonitored_critical_assets": ("Critical assets not effectively monitored", "high"),
    "sla_breach_rate": ("Closures exceeding their target", "high"),
    "sla_misreport_rate": ("SLA compliance the records contradict", "high"),
    "severity_softening_rate": ("Cases logged below their alert severity", "high"),
    "no_root_cause_gap": ("Significant closures with no root cause", "high"),
    "no_remediation_gap": ("Significant closures with no remediation", "high"),
    "rework_loop_rate": ("Investigations repeating completed steps", "high"),
    "reopen_rate": ("Closures later reopened", "high"),
    "investigation_gap_rate": ("Cases with no adequate investigation trace", "high"),
    "evidence_gap_rate": ("Workflow steps with no evidence or result", "high"),
    "risk_accept_no_authority_rate": ("Accepted risks with no authority", "high"),
    "telemetry_silence_pct": ("Monitored assets gone silent", "high"),
    "critical_monitoring_coverage_pct": ("Critical estate monitoring coverage", "low"),
    "top_analyst_share": ("Critical alerts held by one analyst", "high"),
    "weekend_activity_ratio": ("Weekend activity vs weekday rate", "low"),
    "investigation_time_anomaly_rate": ("Cases \"investigated\" in under two minutes", "high"),
    "declared_kpi_contradictions": ("Declared KPIs the records contradict", "high"),
    "examiner_suppressed": ("Findings removed by examiner judgement", "low"),
    "total_alerts": ("Alerts submitted", None),
    "total_cases": ("Case records submitted", None),
    "total_escalations": ("Escalation records submitted", None),
    "total_dispositions": ("Closure records submitted", None),
}

PCT_METRICS = {"fast_closure_rate", "crit_no_escalation_rate", "template_note_rate",
               "repeat_alert_rate", "missing_case_rate", "root_cause_rate",
               "expected_category_coverage", "sla_breach_rate", "sla_misreport_rate",
               "severity_softening_rate", "no_root_cause_gap", "no_remediation_gap",
               "rework_loop_rate", "reopen_rate", "investigation_gap_rate",
               "evidence_gap_rate", "risk_accept_no_authority_rate",
               "top_analyst_share", "investigation_time_anomaly_rate"}

# A change smaller than this (in the metric's own units) is not called a movement -
# it prevents noise in a 1-decimal percentage from rendering as a "deterioration".
NOISE_FLOOR = {
    "risk_score": 0.5, "capability_average": 0.5, "metric_index": 0.5,
    "night_coverage_gap_pct": 1.0, "activity_deviation": 0.01,
    "weekend_activity_ratio": 0.02, "critical_monitoring_coverage_pct": 1.0,
    "telemetry_silence_pct": 1.0,
}


def new_run_id() -> str:
    return f"RUN-{uuid.uuid4().hex[:10].upper()}"


def next_cycle_label(conn) -> str:
    """Human label for the run about to be recorded ("Cycle 3")."""
    try:
        row = conn.execute("SELECT COUNT(DISTINCT run_id) AS c FROM metric_snapshots").fetchone()
        return f"Cycle {int(row['c']) + 1}"
    except Exception:
        return "Cycle 1"


def save_snapshot(conn, metrics_frame: pd.DataFrame, findings_frame: pd.DataFrame,
                  run_id: str, cycle_label: str,
                  tiers: dict | None = None) -> int:
    """Append one immutable snapshot row per entity for this run. Returns row count."""
    if metrics_frame is None or metrics_frame.empty:
        return 0
    frame = metrics_frame.copy()
    tiers = tiers or {}
    frame["criticality_tier"] = frame["entity_id"].map(tiers).fillna("")

    if findings_frame is not None and not findings_frame.empty:
        grouped = findings_frame.groupby("entity_id").agg(
            findings_count=("finding_id", "size"),
            findings_high=("severity", lambda s: int((s == "High").sum())))
        frame = frame.merge(grouped, on="entity_id", how="left")
    for col in ("findings_count", "findings_high"):
        if col not in frame.columns:
            frame[col] = 0
    frame[["findings_count", "findings_high"]] = (
        frame[["findings_count", "findings_high"]].fillna(0).astype(int))

    frame["run_id"] = run_id
    frame["cycle_label"] = cycle_label
    cols = [c for c in ["run_id", "cycle_label", *SNAPSHOT_COLUMNS] if c in frame.columns]
    frame[cols].to_sql("metric_snapshots", conn, if_exists="append", index=False)
    return len(frame)


def list_cycles(conn, entity_id: str | None = None,
                limit: int = 40) -> list[dict]:
    """All recorded cycles, newest first (optionally for one entity)."""
    sql = ("SELECT run_id, cycle_label, MIN(taken_at) AS taken_at, COUNT(*) AS entities "
           "FROM metric_snapshots")
    params: tuple = ()
    if entity_id:
        sql += " WHERE entity_id = ?"
        params = (entity_id,)
    sql += " GROUP BY run_id, cycle_label ORDER BY MIN(snapshot_id) DESC LIMIT ?"
    try:
        rows = conn.execute(sql, (*params, int(limit))).fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows]


def cycle_diff(conn, entity_id: str, latest_run: str | None = None,
               previous_run: str | None = None) -> dict:
    """Compare one entity's latest snapshot with the one before it.

    Returns ``{latest, previous, rows, metrics: {metric: change_count}}`` where each
    row is presentation-ready: label, previous value, latest value, delta, and a
    ``direction`` of ``better`` / ``worse`` / ``flat``.
    """
    try:
        rows = conn.execute(
            "SELECT * FROM metric_snapshots WHERE entity_id = ? "
            "ORDER BY snapshot_id DESC LIMIT 2", (entity_id,)).fetchall()
    except Exception:
        rows = []
    if latest_run:
        picked = [r for r in conn.execute(
            "SELECT * FROM metric_snapshots WHERE entity_id = ? AND run_id IN (?, ?) "
            "ORDER BY snapshot_id DESC", (entity_id, latest_run, previous_run or "")
        ).fetchall()]
        if picked:
            rows = picked
    if not rows:
        return dict(latest=None, previous=None, changes=[], summary={})
    if len(rows) == 1:
        return dict(latest=dict(rows[0]), previous=None, changes=[], summary={})

    latest, previous = dict(rows[0]), dict(rows[1])
    changes = []
    for metric, (label, direction) in DIFF_METRICS.items():
        if metric not in latest or metric not in previous:
            continue
        try:
            new = float(latest.get(metric) or 0.0)
            old = float(previous.get(metric) or 0.0)
        except (TypeError, ValueError):
            continue
        delta = round(new - old, 4)
        floor = NOISE_FLOOR.get(metric, 0.004 if metric in PCT_METRICS else 0.0)
        if abs(delta) <= floor:
            verdict = "flat"
        elif direction is None:
            verdict = "up" if delta > 0 else "down"
        else:
            worse = (delta > 0) if direction == "high" else (delta < 0)
            verdict = "worse" if worse else "better"
        changes.append(dict(
            metric=metric, label=label, previous=old, latest=new, delta=delta,
            direction=direction, verdict=verdict,
            is_pct=metric in PCT_METRICS,
            previous_display=_display(old, metric), latest_display=_display(new, metric),
            delta_display=_delta_display(delta, metric),
        ))

    summary = dict(
        better=sum(1 for c in changes if c["verdict"] == "better"),
        worse=sum(1 for c in changes if c["verdict"] == "worse"),
        flat=sum(1 for c in changes if c["verdict"] in ("flat", "up", "down")),
    )
    return dict(latest=latest, previous=previous, changes=changes, summary=summary)


def _display(value: float, metric: str) -> str:
    if metric in PCT_METRICS:
        return f"{value * 100:.1f}%"
    if metric == "weekend_activity_ratio":
        return f"{value:.2f}x"
    if metric in ("risk_score", "capability_average", "metric_index"):
        return f"{value:.1f}"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:.2f}"


def _delta_display(delta: float, metric: str) -> str:
    if metric in PCT_METRICS:
        return f"{delta * 100:+.1f} pts"
    if metric == "weekend_activity_ratio":
        return f"{delta:+.2f}x"
    if metric in ("risk_score", "capability_average", "metric_index"):
        return f"{delta:+.1f}"
    if float(delta).is_integer():
        return f"{int(delta):+,}"
    return f"{delta:+.2f}"


def portfolio_changes(conn, run_id: str | None = None) -> pd.DataFrame:
    """Every entity's biggest movement since the previous cycle, for the overview page."""
    entity_rows = conn.execute(
        "SELECT DISTINCT entity_id, entity_name FROM metric_snapshots").fetchall()
    out = []
    for row in entity_rows:
        diff = cycle_diff(conn, row["entity_id"], latest_run=run_id)
        if not diff["changes"]:
            continue
        worse = [c for c in diff["changes"] if c["verdict"] == "worse"]
        better = [c for c in diff["changes"] if c["verdict"] == "better"]
        latest = diff["latest"] or {}
        previous = diff["previous"] or {}
        out.append(dict(
            entity=row["entity_name"],
            latest_cycle=latest.get("cycle_label", ""),
            previous_cycle=previous.get("cycle_label", ""),
            risk_now=latest.get("risk_score", 0), risk_before=previous.get("risk_score", 0),
            risk_delta=round(float(latest.get("risk_score", 0) or 0)
                             - float(previous.get("risk_score", 0) or 0), 1),
            tier=latest.get("risk_tier", ""), worse=len(worse), better=len(better),
            biggest_movement=worse[0]["label"] if worse else (
                better[0]["label"] if better else ""),
        ))
    frame = pd.DataFrame(out)
    return frame.sort_values(["worse", "risk_delta"], ascending=False) if not frame.empty \
        else frame
