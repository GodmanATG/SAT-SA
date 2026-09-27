"""
Shared helpers for the detection engine.

Every detector returns the *same* record shape, and every record carries:

    rule_id, title, description, rationale, weakness_type, capability_tags,
    severity, severity_score, detector_group, evidence_ids, evidence_count,
    metric_value, threshold_value

``rationale`` is the templated, human-readable "why was this flagged" string
required for supervisory explainability; ``evidence_ids`` are capped to a sample
(config ``evidence_sample_cap``) purely to keep the database small - the true
population count is preserved in ``evidence_count`` so nothing is hidden.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MAX_EVIDENCE_DEFAULT = 25


def pct(value, digits=1) -> float:
    """Fraction (0-1) -> percentage for display/rationale text."""
    try:
        return round(float(value) * 100, digits)
    except (TypeError, ValueError):
        return 0.0


def safe_div(a, b, default=0.0) -> float:
    try:
        return float(a) / float(b) if b else default
    except (TypeError, ValueError, ZeroDivisionError):
        return default


def as_datetime(series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def minutes_between(start, end) -> pd.Series:
    return (as_datetime(end) - as_datetime(start)).dt.total_seconds() / 60.0


def compute_data_quality(df: pd.DataFrame, required_fields: list[str]) -> float:
    """Fraction of non-null values across the required fields of a table.

    Used to separate real negative space from "this entity just reports data
    badly" - detectors must not fire on an empty export.
    """
    if df is None or len(df) == 0:
        return 0.0
    cols = [c for c in required_fields if c in df.columns]
    if not cols:
        return 0.0
    return float(df[cols].notna().sum().sum() / (len(df) * len(cols)))


def cap_evidence(evidence_ids, thresholds=None) -> tuple[list, int]:
    """Return (capped sample of ids, true count)."""
    cap = MAX_EVIDENCE_DEFAULT
    if thresholds:
        cap = int(thresholds.get("evidence_sample_cap", MAX_EVIDENCE_DEFAULT))
    ids = [str(i) for i in (evidence_ids or []) if i is not None and str(i) != "nan"]
    return ids[:cap], len(ids)


def finalize_finding(*, rule_id: str, title: str, description: str, rationale: str,
                     weakness_type: str, capability_tags: list[str], severity: str,
                     severity_score: float, evidence_ids=None, detector_group: str = "rule",
                     metric_value: float = 0.0, threshold_value: float = 0.0,
                     thresholds: dict | None = None) -> dict:
    """Build one canonical finding record (evidence already capped)."""
    sample, count = cap_evidence(evidence_ids, thresholds)
    return dict(
        rule_id=rule_id,
        title=title,
        description=description,
        rationale=rationale,
        weakness_type=weakness_type,
        capability_tags=list(capability_tags),
        severity=severity,
        severity_score=round(float(severity_score), 3),
        detector_group=detector_group,
        evidence_ids=sample,
        evidence_count=count,
        metric_value=round(float(metric_value), 4),
        threshold_value=round(float(threshold_value), 4),
    )


def severity_from_signal(signal: float, high: float, medium: float) -> tuple[str, float]:
    """Map how far past a threshold a signal is onto (label, score 0-1)."""
    if signal >= high:
        return "High", 0.9
    if signal >= medium:
        return "Medium", 0.6
    return "Low", 0.3


def flag(value, threshold, higher_is_worse=True) -> bool:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return False
    return bool(value >= threshold) if higher_is_worse else bool(value <= threshold)


def fmt_ids(ids, limit=5) -> str:
    sample = [str(i) for i in (ids or [])[:limit]]
    return ", ".join(sample) if sample else "n/a"
