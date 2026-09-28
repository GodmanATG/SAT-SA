"""
Composite scoring (proposal Stage 5) - intentionally NOT a trained model
=======================================================================
There is no labelled ground truth in the real deployment, so nothing here is
fitted or optimised from data.  Both the metric weights and the capability
weights live in an editable JSON config file (Settings page) and every number on
screen can be traced back to a named metric or finding.

Two outputs:

* ``composite_risk_score`` - one 0-100 supervisory risk score per entity, built
  as a weighted sum of normalised *metric gaps*.
* ``capability_scores`` - the 8-dimension scorecard (C1..C8), built by summing
  the severity of the findings tagged to each capability.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pandas as pd

_APP_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(_APP_DIR) not in sys.path:
    sys.path.append(str(_APP_DIR))

from config import (  # noqa: E402
    CAPABILITY_COLUMNS, DEFAULT_CAPABILITY_WEIGHTS, RISK_SCORE_BLEND, RISK_SCORE_WEIGHTS,
)

__all__ = ["CAPABILITY_COLUMNS"]  # re-exported: defined once, in config.py

# A finding contributes its severity score (0-1) to each capability it is tagged
# to.  This scale means a dimension saturates at roughly 2.5 High findings.
CAPABILITY_SCALE = 40.0

SILENT_ASSET_SATURATION = 5.0


def _clip01(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v != v:  # NaN
        return 0.0
    return max(0.0, min(1.0, v))


def metric_gaps(metrics: dict) -> dict:
    """Map raw metrics onto 0-1 'worse is larger' components."""
    return {
        "fast_closure_rate": _clip01(metrics.get("fast_closure_rate")),
        "crit_no_escalation_rate": _clip01(metrics.get("crit_no_escalation_rate")),
        "template_note_rate": _clip01(metrics.get("template_note_rate")),
        "night_coverage_gap_pct": _clip01(float(metrics.get("night_coverage_gap_pct") or 0.0) / 100.0)
        if metrics.get("night_coverage_gap_pct") is not None else 0.0,
        "repeat_alert_rate": _clip01(metrics.get("repeat_alert_rate")),
        "category_coverage_gap": 1.0 - _clip01(metrics.get("expected_category_coverage", 1.0)),
        "root_cause_gap": 1.0 - _clip01(metrics.get("root_cause_rate", 1.0)),
        "missing_case_rate": _clip01(metrics.get("missing_case_rate")),
        "activity_deviation": _clip01(metrics.get("activity_deviation")),
        "silent_asset_gap": min(1.0, _clip01(
            float(metrics.get("silent_critical_assets") or 0) / SILENT_ASSET_SATURATION)),
        # incident-management / governance gaps
        "sla_breach_rate": _clip01(metrics.get("sla_breach_rate")),
        "severity_softening_rate": _clip01(metrics.get("severity_softening_rate")),
        "no_root_cause_gap": _clip01(metrics.get("no_root_cause_gap")),
        "rework_loop_rate": _clip01(metrics.get("rework_loop_rate")),
        "unmonitored_asset_gap": min(1.0, _clip01(
            float(metrics.get("unmonitored_critical_assets") or 0) / SILENT_ASSET_SATURATION)),
        "investigation_gap": _clip01(metrics.get("investigation_gap_rate")),
        # Concentration of critical alerts on one analyst. Only concentration *above*
        # half of all critical/high alerts counts as a gap; a modest lead does not.
        "top_analyst_gap": max(0.0, min(1.0, (_clip01(metrics.get("top_analyst_share")) - 0.5) / 0.5)),
        # Telemetry collapsing at the weekend. The ratio is 1.0 when there is nothing
        # to conclude, so an unmeasurable entity does not carry a gap it cannot earn.
        "weekend_activity_gap": _clip01(
            1.0 - min(1.0, _clip01(metrics.get("weekend_activity_ratio", 1.0)) / 0.5)),
        # Cases whose recorded investigation time is seconds rather than minutes.
        "thin_investigation_gap": _clip01(metrics.get("investigation_time_anomaly_rate")),
    }


def metric_index(metrics: dict, weights: dict | None = None) -> float:
    """Weighted 0-100 index of normalised metric gaps (evidence-driven half)."""
    weights = weights or RISK_SCORE_WEIGHTS
    gaps = metric_gaps(metrics)
    total_w = sum(w for k, w in weights.items() if k in gaps) or 1.0
    score = sum(gaps.get(k, 0.0) * w for k, w in weights.items()) / total_w * 100.0
    return round(min(100.0, max(0.0, score)), 1)


def composite_risk_score(metrics: dict, weights: dict | None = None,
                         capability_row: dict | None = None,
                         blend: dict | None = None) -> float:
    """Overall supervisory risk score (0-100, higher = more supervisory concern).

    Blends the metric index with the average of the 8 capability scores when a
    capability row is supplied (see RISK_SCORE_BLEND). The score is a prioritisation
    index, not a probability, and every component of it is displayed to the user.
    """
    blend = blend or RISK_SCORE_BLEND
    index = metric_index(metrics, weights)
    if not capability_row:
        return index
    cap_values = [float(capability_row.get(col, 0.0) or 0.0)
                  for col in CAPABILITY_COLUMNS.values()]
    capability_average = sum(cap_values) / len(cap_values) if cap_values else 0.0
    total = blend.get("metric_index", 0.6) + blend.get("capability_average", 0.4)
    score = (index * blend.get("metric_index", 0.6)
             + capability_average * blend.get("capability_average", 0.4)) / total
    return round(min(100.0, max(0.0, score)), 1)


def risk_contributions(metrics: dict, weights: dict | None = None) -> pd.DataFrame:
    """Per-component contribution to the risk score (explainability table)."""
    weights = weights or RISK_SCORE_WEIGHTS
    gaps = metric_gaps(metrics)
    total_w = sum(w for k, w in weights.items() if k in gaps) or 1.0
    rows = []
    for key, weight in weights.items():
        gap = gaps.get(key, 0.0)
        contribution = gap * weight / total_w * 100.0
        rows.append(dict(component=key, metric_gap=round(gap, 3),
                         weight=weight, score_contribution=round(contribution, 1)))
    return pd.DataFrame(rows).sort_values("score_contribution", ascending=False)


def parse_capability_tags(tags) -> list[str]:
    """Accept tags as a list, a JSON string or a comma-separated string.

    Findings are persisted with ``capability_tags`` as a JSON string, so scoring must
    parse that form - silently skipping it (as an earlier build did) zeroes every
    capability score and therefore the whole scorecard.
    """
    if tags is None:
        return []
    if isinstance(tags, str):
        text = tags.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                tags = parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                tags = [t.strip() for t in text.strip("[]").split(",")]
        else:
            tags = [t.strip() for t in text.split(",")]
    return [str(t).strip().strip('"').strip("'").upper() for t in tags if str(t).strip()]


def capability_scores(findings: list[dict], weights: dict | None = None) -> dict:
    """C1..C8 capability scores (0-100, higher = weaker capability)."""
    weights = weights or DEFAULT_CAPABILITY_WEIGHTS
    raw = {cap: 0.0 for cap in CAPABILITY_COLUMNS}
    for f in findings or []:
        tags = parse_capability_tags(f.get("capability_tags"))
        severity_score = float(f.get("severity_score", 0.0) or 0.0)
        for tag in tags:
            if tag in raw:
                raw[tag] += severity_score

    out = {}
    for cap, col in CAPABILITY_COLUMNS.items():
        weighted = raw[cap] * float(weights.get(cap, 1.0)) * CAPABILITY_SCALE
        out[col] = round(min(100.0, weighted), 1)
    return out
