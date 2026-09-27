"""
Negative Space detectors
========================
Situations where *expected evidence is absent*.  Absence is easy to fake and hard
to spot, so these detectors always state the comparison basis explicitly: what was
expected, on what basis (sector reference profile, peer cohort, asset inventory,
24x7 staffing assumption), and what was actually observed.

Data-quality gating is deliberate: a negative-space detector must not fire on an
entity that simply reports badly.  When the relevant table's completeness score is
below the configured minimum the detector stays silent and the caller raises a
data-quality finding instead.
"""

from __future__ import annotations

import pathlib
import sys

_DET_DIR = pathlib.Path(__file__).resolve().parent
for _p in (str(_DET_DIR.parent), str(_DET_DIR)):
    if _p not in sys.path:
        sys.path.append(_p)

from common import finalize_finding, safe_div, severity_from_signal  # noqa: E402


def detect_silent_assets(alerts, assets, data_quality_score, thresholds=None) -> list[dict]:
    """NS-001 - critical assets in the inventory that produced zero alerts."""
    thresholds = thresholds or {}
    if assets is None or assets.empty:
        return []
    if float(data_quality_score or 0) < float(thresholds.get("data_quality_minimum", 0.60)):
        return []

    silent = assets[
        assets["criticality_tier"].astype(str).str.lower().isin(["critical", "tier1", "tier 1"])
    ].copy()
    if silent.empty:
        return []
    alerted = set()
    if alerts is not None and not alerts.empty:
        alerted = {str(a) for a in alerts["asset_id"].dropna() if str(a).strip()}
    silent = silent[~silent["asset_id"].astype(str).isin(alerted)]
    if silent.empty:
        return []

    sample = silent.head(5)
    label = ", ".join(f"{r.asset_name or r.asset_id} ({r.asset_class})" for r in sample.itertuples())
    severity, score = severity_from_signal(len(silent), high=3, medium=1)
    rationale = (
        f"{len(silent)} asset(s) marked critical in the submitted inventory appear in no alert record "
        f"for the review period, while {len(alerted)} other assets do. Missing: {label}. "
        f"Inventory completeness score for this entity is {float(data_quality_score):.2f}, so this is "
        f"treated as evidence absence rather than a reporting defect."
    )
    return [finalize_finding(
        rule_id="NS-001",
        title="Critical assets with no telemetry evidence",
        description=(f"{len(silent)} critical asset(s) generated no alerts at all in the review period - "
                     f"a possible monitoring blind spot rather than genuinely low risk."),
        rationale=rationale,
        weakness_type="negative_space",
        capability_tags=["C1", "C5", "C8"],
        severity=severity,
        severity_score=score,
        evidence_ids=silent["asset_id"].tolist(),
        metric_value=len(silent),
        threshold_value=1,
        thresholds=thresholds,
    )]


def detect_missing_categories(alerts, entity_sector, thresholds=None) -> list[dict]:
    """NS-002 - alert categories expected for the sector never observed."""
    thresholds = thresholds or {}
    _ensure_config_path()
    from config import SECTOR_EXPECTED_CATEGORIES

    expected = SECTOR_EXPECTED_CATEGORIES.get(entity_sector, [])
    if not expected:
        return []

    present = set()
    if alerts is not None and not alerts.empty and "alert_category" in alerts.columns:
        present = {str(c).lower().strip() for c in alerts["alert_category"].dropna()}

    expected_lower = {e.lower() for e in expected}
    missing = sorted(expected_lower - present)
    coverage = safe_div(len(expected_lower) - len(missing), len(expected_lower), default=1.0)
    flag = float(thresholds.get("category_coverage_flag", 0.50))
    if coverage >= flag:
        return []

    severity, score = severity_from_signal(1 - coverage, high=0.6, medium=0.3)
    rationale = (
        f"The sector reference profile for '{entity_sector}' expects {len(expected_lower)} alert "
        f"categories; only {len(expected_lower) - len(missing)} were observed "
        f"(coverage {coverage:.0%}, flag level {flag:.0%}). Absent: {', '.join(missing)}. "
        f"Comparable sector entities do report these categories, so the absence is a coverage signal "
        f"rather than proof that the events do not occur."
    )
    return [finalize_finding(
        rule_id="NS-002",
        title="Expected alert categories absent for sector",
        description=(f"{len(missing)} of {len(expected_lower)} alert categories expected for the sector "
                     f"were never seen: {', '.join(missing)}."),
        rationale=rationale,
        weakness_type="negative_space",
        capability_tags=["C1", "C5", "C6"],
        severity=severity,
        severity_score=score,
        evidence_ids=missing,
        metric_value=coverage,
        threshold_value=flag,
        thresholds=thresholds,
    )]


def detect_night_gap(alerts, thresholds=None, cohort_night_share=None) -> list[dict]:
    """NS-003 - no meaningful alert activity outside business hours.

    Two independent tests, because a flat "33% of alerts should be at night"
    assumption is wrong for any SOC with a diurnal load profile (which is every
    real SOC):

    1. an absolute floor - almost *nothing* is logged at night, which is a blind
       spot regardless of what peers look like; and
    2. a cohort-relative gap - night activity materially below the portfolio norm.
    """
    thresholds = thresholds or {}
    if alerts is None or alerts.empty or "is_night" not in alerts.columns:
        return []

    night_share = safe_div(float(alerts["is_night"].sum()), len(alerts))
    floor = float(thresholds.get("night_gap_absolute_floor", 0.05))
    baseline = float(cohort_night_share or thresholds.get("expected_night_share", 8.0 / 24.0))
    flag = float(thresholds.get("night_gap_flag", 40.0))
    gap = max(0.0, (baseline - night_share) / baseline) * 100 if baseline else 0.0
    blind_spot = night_share < floor

    if not blind_spot and gap <= flag:
        return []

    if blind_spot:
        severity, score = "High", 0.9
        basis = (f"less than the {floor:.0%} absolute floor for a 24x7 monitored estate and "
                 f"{gap:.0f}% below the portfolio norm of {baseline:.1%}")
    else:
        severity, score = severity_from_signal(gap, high=90, medium=60)
        basis = f"{gap:.0f}% below the portfolio norm of {baseline:.1%} (flag level {flag:.0f}%)"

    rationale = (
        f"Only {night_share:.1%} of {len(alerts):,} alerts were logged between 22:00 and 06:00 - {basis}. "
        f"{1 - night_share:.1%} of activity falls inside daytime hours, which is consistent with "
        f"business-hours-only monitoring or with night-shift events not being logged at all."
    )
    return [finalize_finding(
        rule_id="NS-003",
        title="Little or no monitoring evidence outside business hours",
        description=(f"Night-time (22:00-06:00) activity is {gap:.0f}% below the level expected for a "
                     f"continuously monitored estate at {baseline:.1%} of the cohort norm."),
        rationale=rationale,
        weakness_type="negative_space",
        capability_tags=["C5", "C7"],
        severity=severity,
        severity_score=score,
        evidence_ids=[],
        metric_value=night_share,
        threshold_value=baseline,
        thresholds=thresholds,
    )]


def detect_low_activity(metrics, sector_median_per_asset, thresholds=None) -> list[dict]:
    """NS-004 - alert volume far below peers once normalised by monitored estate size.

    Normalising by monitored assets avoids the classic false positive where a small
    entity is flagged simply for having fewer systems.
    """
    thresholds = thresholds or {}
    if not sector_median_per_asset or sector_median_per_asset <= 0:
        return []

    total_alerts = float(metrics.get("total_alerts") or 0)
    monitored = float(metrics.get("monitored_assets") or metrics.get("total_assets") or 0)
    if monitored <= 0:
        return []

    per_asset = total_alerts / monitored
    deviation = max(0.0, 1.0 - per_asset / sector_median_per_asset)
    flag = float(thresholds.get("low_activity_deviation_flag", 0.60))
    if deviation <= flag:
        return []

    severity, score = severity_from_signal(deviation, high=0.85, medium=0.7)
    rationale = (
        f"{total_alerts:.0f} alerts across {monitored:.0f} monitored asset(s) is {per_asset:.1f} alerts "
        f"per asset, against a sector median of {sector_median_per_asset:.1f} - a shortfall of "
        f"{deviation:.0%} (flag level {flag:.0%}). Either monitoring coverage is genuinely thin or the "
        f"estate is not submitting complete alert data."
    )
    return [finalize_finding(
        rule_id="NS-004",
        title="Unexpectedly low activity for the monitored estate",
        description=(f"Alert volume is {deviation:.0%} below the sector norm once normalised by the "
                     f"number of monitored assets."),
        rationale=rationale,
        weakness_type="negative_space",
        capability_tags=["C1", "C5"],
        severity=severity,
        severity_score=score,
        evidence_ids=[],
        metric_value=per_asset,
        threshold_value=sector_median_per_asset,
        thresholds=thresholds,
    )]


def detect_missing_case_records(alerts, escalations, thresholds=None) -> list[dict]:
    """NS-005 - critical/high alerts with no investigation or escalation trail.

    Also covers dangling references: escalation rows pointing at cases or alerts
    that do not exist in the submission.
    """
    thresholds = thresholds or {}
    if alerts is None or alerts.empty:
        return []
    crit_high = alerts[alerts["is_crit_high"]]
    if crit_high.empty:
        return []

    flag = float(thresholds.get("missing_case_rate_flag", 0.50))
    missing = crit_high[~crit_high["has_case"]]
    rate = safe_div(len(missing), len(crit_high))

    dangling = 0
    if escalations is not None and not escalations.empty:
        alert_ids = {str(a) for a in alerts["alert_id"]}
        case_ids = {str(a) for a in alerts["case_id"].dropna() if str(a).strip()}
        for col, known in (("alert_id", alert_ids), ("case_id", case_ids)):
            if col in escalations.columns:
                values = {str(v) for v in escalations[col].dropna() if str(v).strip()}
                dangling += len(values - known)

    if rate <= flag and dangling == 0:
        return []

    severity, score = severity_from_signal(max(rate, min(1.0, dangling / 5.0)), high=0.7, medium=0.5)
    rationale = (
        f"{len(missing)} of {len(crit_high)} critical/high alerts ({rate:.0%}) are not linked to any "
        f"case-management record, so no investigation workflow exists for them (flag level {flag:.0%}). "
        + (f"{dangling} escalation record(s) reference a case or alert that does not appear in the "
           f"submission." if dangling else "")
    )
    return [finalize_finding(
        rule_id="NS-005",
        title="Missing investigation / escalation records",
        description=(f"{rate:.0%} of critical and high alerts have no linked investigation record"
                     + (f"; {dangling} escalation reference(s) point at records that do not exist."
                        if dangling else ".")),
        rationale=rationale,
        weakness_type="negative_space",
        capability_tags=["C2", "C3", "C6"],
        severity=severity,
        severity_score=score,
        evidence_ids=missing["alert_id"].tolist(),
        metric_value=rate,
        threshold_value=flag,
        thresholds=thresholds,
    )]


def detect_unmonitored_assets(assets, thresholds=None) -> list[dict]:
    """NS-006 - controls/inventory present but the assets are not being monitored."""
    thresholds = thresholds or {}
    if assets is None or assets.empty:
        return []

    crit = assets[
        assets["criticality_tier"].astype(str).str.lower().isin(["critical", "tier1", "tier 1"])]
    if crit.empty:
        return []
    status = crit["monitoring_status"].astype(str).str.lower()
    unmonitored = crit[~status.isin(["active", "enabled", "true", "1", "yes", ""])]
    minimum = int(thresholds.get("unmonitored_asset_min", 1))
    if len(unmonitored) < minimum:
        return []

    labels = ", ".join(f"{r.asset_name or r.asset_id} [{r.monitoring_status}]"
                       for r in unmonitored.head(5).itertuples())
    severity, score = severity_from_signal(len(unmonitored), high=3, medium=1)
    rationale = (
        f"{len(unmonitored)} critical asset(s) are recorded in the inventory with a monitoring status "
        f"other than active: {labels}. This is the 'controls deployed but not effectively monitored' "
        f"pattern - the control exists on paper but produces no operational evidence."
    )
    return [finalize_finding(
        rule_id="NS-006",
        title="Critical assets deployed but not effectively monitored",
        description=(f"{len(unmonitored)} critical asset(s) carry a non-active monitoring status in the "
                     f"submitted inventory."),
        rationale=rationale,
        weakness_type="negative_space",
        capability_tags=["C5", "C6", "C8"],
        severity=severity,
        severity_score=score,
        evidence_ids=unmonitored["asset_id"].tolist(),
        metric_value=len(unmonitored),
        threshold_value=minimum,
        thresholds=thresholds,
    )]


def detect_weekend_blind_spot(metrics, thresholds=None) -> list[dict]:
    """NS-007 - telemetry that stops at the weekend.

    Distinct from NS-003 (night hours): an estate can report diligently through the
    night and still go completely dark from Friday evening to Monday morning, which is
    the largest unattended window in a working week and the one attackers prefer. The
    measure is a rate ratio rather than a share of volume, so it is not distorted by
    the fact that two of seven days are weekends.
    """
    thresholds = thresholds or {}
    ratio = float(metrics.get("weekend_activity_ratio", 1.0) or 0.0)
    total_alerts = float(metrics.get("total_alerts") or 0)
    weekend_alerts = int(float(metrics.get("weekend_alerts") or 0))
    flag = float(thresholds.get("weekend_activity_ratio_flag", 0.15))
    minimum = int(thresholds.get("weekend_min_alerts", 150))

    if ratio > flag or total_alerts < minimum:
        return []

    severity, score = severity_from_signal(flag - ratio, high=0.15, medium=0.10)
    rationale = (
        f"{total_alerts:,.0f} alerts were submitted, of which only {weekend_alerts:,} fall on a "
        f"Saturday or Sunday. Measured as a rate per calendar day, weekend activity runs at "
        f"{ratio:.2f}x the weekday rate (flag level {flag:.2f}x). A 24x7 monitoring claim cannot "
        f"be reconciled with an estate that reports {(1 - ratio):.0%} less on the two days it is "
        f"least staffed: either collection stops, or processing is deferred to Monday and the "
        f"weekend window is unmonitored. This is measured per entity, so it also survives a "
        f"whole sector sharing the same weekday-only pattern."
    )
    return [finalize_finding(
        rule_id="NS-007",
        title="Monitoring activity absent at weekends",
        description=(f"Weekend alert activity runs at {ratio:.2f}x the weekday rate across "
                     f"{total_alerts:,.0f} submitted alerts."),
        rationale=rationale,
        weakness_type="negative_space",
        capability_tags=["C1", "C5", "C8"],
        severity=severity,
        severity_score=score,
        evidence_ids=[],
        metric_value=ratio,
        threshold_value=flag,
        thresholds=thresholds,
    )]


def detect_data_quality(table, dq_score, row_count, thresholds=None) -> list[dict]:
    """DQ-001 - records too incomplete to support supervisory conclusions."""
    thresholds = thresholds or {}
    minimum = float(thresholds.get("data_quality_minimum", 0.60))
    if float(dq_score or 0) >= minimum:
        return []
    return [finalize_finding(
        rule_id="DQ-001",
        title=f"Incomplete {table} submission",
        description=(f"The submitted {table} records are only {float(dq_score):.0%} complete on required "
                     f"fields, below the {minimum:.0%} minimum. Negative-space conclusions for this table "
                     f"are withheld until the entity re-submits."),
        rationale=(f"Completeness score for '{table}' is {float(dq_score):.2f} across {row_count} record(s) "
                   f"(minimum {minimum:.2f}). Absence of evidence cannot be distinguished from absence of "
                   f"reporting, so this is raised as a data-quality issue rather than a supervisory finding."),
        weakness_type="data_quality",
        capability_tags=["C6"],
        severity="Medium",
        severity_score=0.5,
        evidence_ids=[],
        metric_value=float(dq_score or 0),
        threshold_value=minimum,
        thresholds=thresholds,
    )]


def run_all(alerts, assets, escalations, entity_sector, metrics, sector_median_per_asset,
            thresholds=None, dq_scores=None, cohort_night_share=None) -> list[dict]:
    """Run every negative-space detector for one entity."""
    thresholds = thresholds or {}
    dq_scores = dq_scores or {}
    findings = []
    findings += detect_silent_assets(alerts, assets, dq_scores.get("asset_inventory", 1.0), thresholds)
    findings += detect_missing_categories(alerts, entity_sector, thresholds)
    findings += detect_night_gap(alerts, thresholds, cohort_night_share)
    findings += detect_low_activity(metrics, sector_median_per_asset, thresholds)
    findings += detect_missing_case_records(alerts, escalations, thresholds)
    findings += detect_unmonitored_assets(assets, thresholds)
    findings += detect_weekend_blind_spot(metrics, thresholds)
    return findings


def _ensure_config_path():
    if str(_DET_DIR.parent) not in sys.path:
        sys.path.append(str(_DET_DIR.parent))
