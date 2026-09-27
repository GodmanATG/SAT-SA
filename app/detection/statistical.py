"""
Absolute-benchmark and peer-relative detectors
=============================================
Two independent comparison bases, deliberately kept separate:

1. **Absolute benchmarks** (``benchmarks.py``) - the entity is compared against
   fixed reference values of the kind published in annual SOC surveys.  This is
   what stops a systemic, sector-wide problem from hiding behind peer comparison:
   if every entity in a sector rubber-stamps, "normal relative to peers" must not
   mean "fine".

2. **Peer-relative statistics** (this module) - z-scores inside the entity's
   sector cohort, plus an Isolation Forest for multivariate outliers.

Both the cohort basis and the fallback are reported in the finding rationale.
When a sector cohort is smaller than the configured minimum the comparison falls
back to the whole population and says so, rather than silently returning nothing
(which is what made the earlier build's peer detector effectively dead code).
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd

_DET_DIR = pathlib.Path(__file__).resolve().parent
for _p in (str(_DET_DIR.parent), str(_DET_DIR)):
    if _p not in sys.path:
        sys.path.append(_p)

from common import finalize_finding, severity_from_signal  # noqa: E402

# Metrics compared across peers, with the direction that indicates concern
PEER_METRICS = {
    "fast_closure_rate": "higher worse",
    "crit_no_escalation_rate": "higher worse",
    "template_note_rate": "higher worse",
    "time_to_ack_median": "higher worse",
    "time_to_close_median": "higher worse",
    "repeat_alert_rate": "higher worse",
    "missing_case_rate": "higher worse",
    "night_coverage_gap_pct": "higher worse",
    "avg_investigation_depth": "lower worse",
    "expected_category_coverage": "lower worse",
    "critical_alerts": "lower worse",
}

ISOLATION_FEATURES = ["fast_closure_rate", "crit_no_escalation_rate", "template_note_rate",
                      "time_to_ack_median", "time_to_close_median", "repeat_alert_rate",
                      "missing_case_rate", "night_coverage_gap_pct", "avg_investigation_depth",
                      "expected_category_coverage"]


def effective_cohort(metrics_df: pd.DataFrame, entity_id: str, thresholds: dict) -> tuple[pd.DataFrame, str]:
    """Return (cohort frame, basis label) for one entity."""
    row = metrics_df[metrics_df["entity_id"] == entity_id]
    if row.empty:
        return pd.DataFrame(), "none"
    sector = row.iloc[0].get("sector", "")
    minimum = int(thresholds.get("min_cohort_size", 3))
    sector_peers = metrics_df[metrics_df["sector"] == sector]
    if len(sector_peers) >= minimum:
        return sector_peers, f"sector cohort '{sector}' (n={len(sector_peers)})"
    return metrics_df, (f"whole portfolio (n={len(metrics_df)}) because sector cohort '{sector}' "
                        f"has only {len(sector_peers)} entities, below the minimum of {minimum}")


def peer_deviations(metrics_df: pd.DataFrame, thresholds: dict | None = None) -> pd.DataFrame:
    """Z-scores of each entity's metrics against its effective cohort.

    Degenerate cohorts (zero standard deviation, too few values) are skipped
    instead of producing meaningless z-scores.
    """
    thresholds = thresholds or {}
    rows = []
    if metrics_df is None or metrics_df.empty:
        return pd.DataFrame()

    for entity_id in metrics_df["entity_id"]:
        cohort, basis = effective_cohort(metrics_df, entity_id, thresholds)
        if cohort.empty:
            continue
        row = metrics_df[metrics_df["entity_id"] == entity_id].iloc[0]
        for metric, direction in PEER_METRICS.items():
            if metric not in cohort.columns:
                continue
            values = pd.to_numeric(cohort[metric], errors="coerce").dropna()
            if len(values) < 3:
                continue
            std = float(values.std(ddof=0))
            if std < 1e-9:
                continue
            value = float(pd.to_numeric(row[metric], errors="coerce") or 0.0)
            z = (value - float(values.mean())) / std
            rows.append(dict(entity_id=entity_id, entity_name=row.get("entity_name", ""),
                             sector=row.get("sector", ""), metric=metric, direction=direction,
                             value=value, cohort_mean=round(float(values.mean()), 4),
                             cohort_std=round(std, 4), z_score=round(z, 3),
                             cohort_basis=basis, cohort_size=len(values)))
    return pd.DataFrame(rows)


def detect_peer_anomalies(metrics_df: pd.DataFrame, entity_id: str,
                          thresholds: dict | None = None) -> list[dict]:
    """STAT-001 - significant deviation from peers on a comparable metric."""
    thresholds = thresholds or {}
    z_threshold = float(thresholds.get("zscore_threshold", 2.0))
    deviations = peer_deviations(metrics_df, thresholds)
    if deviations.empty:
        return []
    mine = deviations[deviations["entity_id"] == entity_id]

    findings = []
    for row in mine.itertuples():
        concerning = (row.z_score >= z_threshold) if row.direction == "higher worse" \
            else (row.z_score <= -z_threshold)
        if not concerning:
            continue
        severity, score = severity_from_signal(abs(row.z_score), high=3.0, medium=z_threshold)
        direction_text = "above" if row.z_score > 0 else "below"
        rationale = (
            f"{row.metric.replace('_', ' ')} is {abs(row.z_score):.2f} standard deviations "
            f"{direction_text} the comparison basis ({row.cohort_basis}); entity value {row.value:.3f} "
            f"against cohort mean {row.cohort_mean:.3f} (sd {row.cohort_std:.3f}). "
            f"Threshold: |z| > {z_threshold:.1f}."
        )
        findings.append(finalize_finding(
            rule_id="STAT-001",
            title=f"Peer deviation: {row.metric.replace('_', ' ')}",
            description=(f"{row.metric.replace('_', ' ')} deviates {row.z_score:+.2f} sigma from "
                         f"{row.cohort_basis}."),
            rationale=rationale,
            weakness_type="peer_anomaly",
            capability_tags=["C5", "C8"],
            severity=severity,
            severity_score=score,
            evidence_ids=[],
            detector_group="peer",
            metric_value=row.z_score,
            threshold_value=z_threshold,
            thresholds=thresholds,
        ))
    return findings


def detect_isolation_forest_outliers(metrics_df: pd.DataFrame, thresholds: dict | None = None) -> dict:
    """STAT-003 - multivariate outlier scoring (guarded against tiny/degenerate input).

    Returns ``{entity_id: score}`` for outliers only.  A constant feature matrix or
    a portfolio smaller than the configured minimum returns an empty dict instead
    of flagging an arbitrary entity, which is what an unguarded IsolationForest
    does on degenerate input.
    """
    thresholds = thresholds or {}
    if metrics_df is None or len(metrics_df) < int(thresholds.get("isolation_forest_min_entities", 12)):
        return {}

    features = [c for c in ISOLATION_FEATURES if c in metrics_df.columns]
    if len(features) < 3:
        return {}
    X = metrics_df[features].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    varying = [c for c in features if float(X[c].std(ddof=0)) > 1e-9]
    if len(varying) < 3:
        return {}
    X = X[varying]

    contamination = float(thresholds.get("isolation_forest_contamination", 0.10))
    contamination = min(max(contamination, 0.01), 0.5)
    try:
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import StandardScaler

        model = IsolationForest(contamination=contamination, random_state=42, n_estimators=200)
        scaled = StandardScaler().fit_transform(X)
        labels = model.fit_predict(scaled)
        scores = -model.score_samples(scaled)  # higher = more anomalous
        return {metrics_df.iloc[i]["entity_id"]: round(float(scores[i]), 4)
                for i in np.where(labels == -1)[0]}
    except Exception:
        return {}


def detect_trend_drift(monthly_df: pd.DataFrame, thresholds: dict | None = None) -> list[dict]:
    """STAT-002 - a worsening, statistically significant trend inside the window.

    ``monthly_df`` must contain per-month metrics (not entity-level constants).
    """
    thresholds = thresholds or {}
    if monthly_df is None or monthly_df.empty or len(monthly_df) < 4:
        return []

    df = monthly_df.sort_values("month")
    x = np.arange(len(df))
    p_threshold = float(thresholds.get("trend_pvalue_threshold", 0.05))
    metrics = [("fast_closure_rate", True), ("crit_no_escalation_rate", True),
               ("template_note_rate", True), ("night_coverage_gap_pct", True),
               ("repeat_alert_rate", True)]

    findings = []
    for metric, higher_worse in metrics:
        if metric not in df.columns:
            continue
        y = pd.to_numeric(df[metric], errors="coerce").fillna(0.0).to_numpy(dtype=float)
        if len(y) < 4 or float(np.std(y)) < 1e-9:
            continue
        try:
            from scipy import stats
            slope, _, _, p_value, _ = stats.linregress(x, y)
        except Exception:
            slope, p_value = 0.0, 1.0
        worsening = slope > 0 if higher_worse else slope < 0
        if not (worsening and p_value < p_threshold):
            continue

        values = ", ".join(f"{m}:{v:.3f}" for m, v in zip(df["month"], y))
        severity, score = severity_from_signal(abs(slope) * 100, high=5.0, medium=1.5)
        rationale = (
            f"{metric.replace('_', ' ')} is trending worse across {len(df)} months (slope "
            f"{slope:+.4f} per month, p={p_value:.3f} < {p_threshold:.2f}). Monthly values - {values}. "
            f"A single-snapshot review would not surface this drift."
        )
        findings.append(finalize_finding(
            rule_id="STAT-002",
            title=f"Deteriorating trend: {metric.replace('_', ' ')}",
            description=(f"{metric.replace('_', ' ')} has worsened consistently over the review window "
                         f"(p={p_value:.3f})."),
            rationale=rationale,
            weakness_type="peer_anomaly",
            capability_tags=["C6", "C7"],
            severity=severity,
            severity_score=score,
            evidence_ids=[],
            detector_group="trend",
            metric_value=slope,
            threshold_value=p_threshold,
            thresholds=thresholds,
        ))
    return findings


def _per_asset(df: pd.DataFrame) -> pd.Series:
    monitored = pd.to_numeric(df.get("monitored_assets", 0), errors="coerce").fillna(0)
    total = pd.to_numeric(df.get("total_alerts", 0), errors="coerce").fillna(0)
    return pd.Series(np.where(monitored > 0, total / monitored.replace(0, np.nan), np.nan),
                     index=df.index)


def sector_medians(metrics_df: pd.DataFrame) -> dict:
    """Median alerts per monitored asset inside each sector (used by NS-004)."""
    if metrics_df is None or metrics_df.empty:
        return {}
    df = metrics_df.copy()
    df["per_asset"] = _per_asset(df)
    overall = float(df["per_asset"].median(skipna=True) or 0.0)
    medians = {"__all__": overall}
    for sector, group in df.groupby("sector"):
        value = float(group["per_asset"].median(skipna=True) or 0.0)
        medians[sector] = value or overall
    return medians


def leave_one_out_medians(metrics_df: pd.DataFrame) -> dict:
    """entity_id -> peer median alerts-per-monitored-asset, excluding the entity itself.

    Leave-one-out matters in small cohorts: with only two entities in a sector, a
    plain sector median sits halfway between the entity and its single peer, which
    caps the measurable shortfall at 50% and hides exactly the under-reporting the
    detector is looking for.
    """
    if metrics_df is None or metrics_df.empty:
        return {}
    df = metrics_df.copy()
    df["per_asset"] = _per_asset(df)
    overall_median = float(df["per_asset"].median(skipna=True) or 0.0)
    out = {}
    for _, row in df.iterrows():
        peers = df[df["sector"] == row.get("sector")]
        peers = peers[peers["entity_id"] != row["entity_id"]]
        if len(peers) >= 1 and peers["per_asset"].notna().any():
            value = float(peers["per_asset"].median(skipna=True))
        else:
            others = df[df["entity_id"] != row["entity_id"]]["per_asset"]
            value = float(others.median(skipna=True) or overall_median)
        out[row["entity_id"]] = value or overall_median
    return out
