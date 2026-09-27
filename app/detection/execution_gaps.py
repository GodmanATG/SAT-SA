"""
Execution Gap detectors
=======================
Situations where documented controls, governance or reported metrics suggest
effective operation but the operational evidence says otherwise.

Each detector returns canonical finding records (see ``common.finalize_finding``):
they carry the rule id, the capability tags, a templated rationale string, a
capped evidence sample and the *true* evidence count, so a supervisor can click
from a finding to the exact alert/case rows behind it.

Alerts are passed in already enriched by ``features.enrich_alerts`` (timestamps
parsed, ``fast_flag``/``is_repeat``/escalation linkage attached), which keeps
these functions cheap and deterministic.
"""

from __future__ import annotations

import pathlib
import sys

_DET_DIR = pathlib.Path(__file__).resolve().parent
for _p in (str(_DET_DIR.parent), str(_DET_DIR)):
    if _p not in sys.path:
        sys.path.append(_p)

from common import finalize_finding, fmt_ids, safe_div, severity_from_signal  # noqa: E402


def detect_fast_closures(alerts, cases=None, thresholds=None) -> list[dict]:
    """EG-001 - critical/high alerts acknowledged *and* closed implausibly fast."""
    thresholds = thresholds or {}
    if alerts is None or alerts.empty:
        return []

    limit = float(thresholds.get("fast_closure_minutes", 5))
    flag_rate = float(thresholds.get("fast_closure_rate_flag", 0.50))

    crit_high = alerts[alerts["is_crit_high"]]
    if crit_high.empty:
        return []

    fast = crit_high[crit_high["fast_flag"]]
    rate = safe_div(len(fast), len(crit_high))
    if rate <= flag_rate:
        return []

    severity, score = severity_from_signal(rate, high=0.75, medium=flag_rate + 0.1)
    crit_fast = int(fast["is_critical"].sum())
    rationale = (
        f"{rate:.1%} of critical/high alerts ({len(fast)} of {len(crit_high)}) were closed within "
        f"{limit:.0f} minutes of creation, against a configured flag level of {flag_rate:.0%}. "
        f"{crit_fast} of the fast closures were Critical severity. "
        f"Median closure time for the severity mix observed is {crit_high['close_minutes'].median():.0f} minutes. "
        f"Evidence sample: {fmt_ids(fast['alert_id'].tolist())}."
    )
    return [finalize_finding(
        rule_id="EG-001",
        title="Critical/high alerts closed implausibly fast",
        description=(f"{rate:.1%} of critical and high severity alerts were closed in under "
                     f"{limit:.0f} minutes, which does not allow meaningful triage, correlation or "
                     f"scope assessment."),
        rationale=rationale,
        weakness_type="execution_gap",
        capability_tags=["C1", "C2", "C7"],
        severity=severity,
        severity_score=score,
        evidence_ids=fast["alert_id"].tolist(),
        metric_value=rate,
        threshold_value=flag_rate,
        thresholds=thresholds,
    )]


def detect_missing_escalations(alerts, escalations=None, thresholds=None) -> list[dict]:
    """EG-002 - Critical alerts that never reach an escalation record."""
    thresholds = thresholds or {}
    if alerts is None or alerts.empty:
        return []

    critical = alerts[alerts["is_critical"]]
    if critical.empty:
        return []

    flag = float(thresholds.get("escalation_rate_flag", 0.30))
    escalated = critical[critical["escalated"]]
    rate = safe_div(len(escalated), len(critical))
    if rate >= flag:
        return []

    unescalated = critical[~critical["escalated"]]
    severity, score = severity_from_signal(1.0 - rate, high=0.8, medium=0.6)
    rationale = (
        f"Only {rate:.1%} of {len(critical)} Critical alerts have a linked escalation record "
        f"(configured minimum {flag:.0%}). {len(unescalated)} Critical alerts were dispositioned "
        f"without escalation. Evidence sample: {fmt_ids(unescalated['alert_id'].tolist())}."
    )
    return [finalize_finding(
        rule_id="EG-002",
        title="Critical alerts closed without escalation",
        description=(f"Only {rate:.1%} of critical alerts were escalated to a higher tier, below the "
                     f"{flag:.0%} expected by incident response procedure."),
        rationale=rationale,
        weakness_type="execution_gap",
        capability_tags=["C3", "C4"],
        severity=severity,
        severity_score=score,
        evidence_ids=unescalated["alert_id"].tolist(),
        metric_value=rate,
        threshold_value=flag,
        thresholds=thresholds,
    )]


def detect_template_notes(cases, thresholds=None, similarity=None, duplicate_case_ids=None,
                          sample_size=0, sampled=False) -> list[dict]:
    """EG-003 - repetitive / template-driven investigation notes.

    The similarity rate is computed once in feature engineering (TF-IDF + cosine
    similarity) and passed in, so this detector never recomputes it per entity.
    ``sample_size``/``sampled`` state whether the underlying comparison ran over the
    whole case set or a bounded sample, so the rationale never presents an estimate
    as an exact population figure.
    """
    thresholds = thresholds or {}
    if cases is None or cases.empty:
        return []

    rate = float(similarity if similarity is not None else 0.0)
    flag = float(thresholds.get("template_note_rate_flag", 0.40))
    if rate <= flag:
        return []

    threshold = float(thresholds.get("template_cosine_threshold", 0.85))
    dup_ids = duplicate_case_ids if duplicate_case_ids is not None else cases["case_id"].tolist()
    dup_count = len(dup_ids)
    compared = int(sample_size) if sample_size else len(cases)
    scope = (f"a deterministic random sample of {compared:,} of the {len(cases):,} notes "
             f"(the pairwise comparison is quadratic, so it is capped at scale)"
             if sampled else f"the {compared:,} submitted investigation notes")
    severity, score = severity_from_signal(rate, high=0.75, medium=flag + 0.1)
    rationale = (
        f"{rate:.1%} of the notes in {scope} fall into duplicate clusters at "
        f"cosine similarity >= {threshold:.2f} (configured flag level {flag:.0%}). "
        f"{dup_count} case records share near-identical note text. "
        f"Evidence sample: {fmt_ids([str(i) for i in dup_ids])}."
    )
    return [finalize_finding(
        rule_id="EG-003",
        title="Repetitive / template-driven investigation notes",
        description=(f"{rate:.1%} of investigation notes are duplicates of each other, which is "
                     f"consistent with superficially complete rather than substantive investigation."),
        rationale=rationale,
        weakness_type="execution_gap",
        capability_tags=["C2", "C7"],
        severity=severity,
        severity_score=score,
        evidence_ids=[str(i) for i in dup_ids],
        metric_value=rate,
        threshold_value=flag,
        thresholds=thresholds,
    )]


def detect_repeat_alerts(alerts, cases=None, thresholds=None, monitored_assets=0) -> list[dict]:
    """EG-004 - recurring alerts on the same assets with no root-cause remediation.

    The underlying measure (``features._mark_chronic_repeats``) is concentration
    based: a small set of (asset, category) pairs absorb a disproportionate share of
    a category while documenting almost no root cause.  A plain "N alerts in 30 days"
    rule would mostly punish whichever entity reports the most, which is the opposite
    of the supervisory concern.
    """
    thresholds = thresholds or {}
    if alerts is None or alerts.empty or "is_repeat" not in alerts.columns:
        return []

    repeats = alerts[alerts["is_repeat"]]
    if repeats.empty:
        return []

    rate = safe_div(len(repeats), len(alerts))
    flag = float(thresholds.get("repeat_alert_rate_flag", 0.20))
    if rate <= flag:
        return []

    min_count = int(thresholds.get("repeat_asset_min_count", 10))
    concentration = float(thresholds.get("repeat_pair_concentration", 4.0))
    repeated_assets = sorted({str(a) for a in repeats["asset_id"] if str(a).strip()})
    repeated_categories = sorted({str(c) for c in repeats["alert_category"] if str(c).strip()})
    monitored = int(monitored_assets or alerts["asset_id"].nunique())
    affected_text = \
        f"{len(repeated_assets)} of {monitored} monitored assets" if monitored else \
        f"{len(repeated_assets)} assets"
    severity, score = severity_from_signal(rate, high=0.45, medium=flag + 0.05)
    rationale = (
        f"{rate:.1%} of all alerts ({len(repeats)} of {len(alerts)}) are repeat traffic on just "
        f"{affected_text} and {len(repeated_categories)} alert categories. These (asset, category) "
        f"pairs each carry at least {min_count} alerts - {concentration:.1f}x this entity's median "
        f"pair volume - and document root cause on no more than "
        f"{float(thresholds.get('repeat_pair_max_root_cause_rate', 0.2)):.0%} of them, so the same "
        f"condition is being re-detected rather than resolved. "
        f"Evidence sample: {fmt_ids(repeats['alert_id'].tolist())}."
    )
    return [finalize_finding(
        rule_id="EG-004",
        title="Recurring alerts without root-cause remediation",
        description=(f"{rate:.1%} of alerts repeat on a small set of assets and categories that "
                     f"document almost no root cause - the same conditions keep re-firing."),
        rationale=rationale,
        weakness_type="execution_gap",
        capability_tags=["C4", "C8"],
        severity=severity,
        severity_score=score,
        evidence_ids=repeats["alert_id"].tolist(),
        metric_value=rate,
        threshold_value=flag,
        thresholds=thresholds,
    )]


def detect_bulk_closures(alerts, thresholds=None) -> list[dict]:
    """EG-005 - many alerts closed inside the same hour (batch/rubber-stamp pattern)."""
    thresholds = thresholds or {}
    if alerts is None or alerts.empty or "close_dt" not in alerts.columns:
        return []

    closed = alerts.dropna(subset=["close_dt"])
    if len(closed) < 20:
        return []

    hourly = closed.groupby(closed["close_dt"].dt.floor("h")).size()
    if len(hourly) < 3:
        return []
    mean, std = float(hourly.mean()), float(hourly.std())
    sigma = float(thresholds.get("bulk_closure_sigma", 3.0))
    if std < 1:
        return []

    bulk_hours = hourly[hourly > mean + sigma * std]
    if bulk_hours.empty:
        return []

    bulk_alerts = closed[closed["close_dt"].dt.floor("h").isin(bulk_hours.index)]
    peak = int(bulk_hours.max())
    ratio = safe_div(peak, mean)
    severity, score = severity_from_signal(ratio, high=6.0, medium=4.0)
    worst_hour = bulk_hours.idxmax()
    rationale = (
        f"{len(bulk_hours)} closure hour(s) exceed mean + {sigma:.1f} sigma of hourly closure volume. "
        f"The busiest hour ({worst_hour:%Y-%m-%d %H:00}) contains {peak} closures against an hourly mean "
        f"of {mean:.1f} ({ratio:.1f}x). {len(bulk_alerts)} alerts were closed in those hours, which can "
        f"indicate batch closure rather than individual review."
    )
    return [finalize_finding(
        rule_id="EG-005",
        title="Anomalous bulk closures",
        description=(f"{len(bulk_alerts)} alerts were closed in {len(bulk_hours)} abnormal hour(s) "
                     f"(up to {ratio:.1f}x the mean hourly closure volume)."),
        rationale=rationale,
        weakness_type="execution_gap",
        capability_tags=["C2", "C7"],
        severity=severity,
        severity_score=score,
        evidence_ids=bulk_alerts["alert_id"].tolist(),
        metric_value=ratio,
        threshold_value=float(thresholds.get("bulk_closure_sigma", 3.0)),
        thresholds=thresholds,
    )]


def detect_shallow_investigations(cases, thresholds=None, avg_depth=None) -> list[dict]:
    """EG-006 - investigation depth score below the configured floor."""
    thresholds = thresholds or {}
    if cases is None or cases.empty:
        return []

    if avg_depth is None:
        avg_depth = 0.5
    avg_depth = float(avg_depth)
    flag = float(thresholds.get("investigation_depth_flag", 0.40))
    if avg_depth >= flag:
        return []

    case_ids = cases["case_id"].tolist()
    no_notes = int(cases["investigation_note_text"].fillna("").astype(str).str.strip().eq("").sum())
    no_root = int((1 - cases["root_cause_documented"].fillna(0).astype(int)).sum())
    severity, score = severity_from_signal(flag - avg_depth, high=0.25, medium=0.05)
    rationale = (
        f"Mean investigation depth score is {avg_depth:.2f} against a floor of {flag:.2f}, where depth "
        f"combines notes (0.3), documented root cause (0.3), documented remediation (0.2) and escalation "
        f"(0.2). {no_notes} case(s) carry no investigation note at all and {no_root} case(s) document no "
        f"root cause."
    )
    return [finalize_finding(
        rule_id="EG-006",
        title="Shallow investigation records",
        description=(f"Investigation records average a depth score of {avg_depth:.2f}, below the "
                     f"{flag:.2f} floor, indicating recording rather than investigation."),
        rationale=rationale,
        weakness_type="execution_gap",
        capability_tags=["C2", "C4", "C6"],
        severity=severity,
        severity_score=score,
        evidence_ids=case_ids,
        metric_value=avg_depth,
        threshold_value=flag,
        thresholds=thresholds,
    )]


def detect_analyst_concentration(metrics, thresholds=None, alerts=None) -> list[dict]:
    """EG-007 - one analyst carries almost every critical/high alert.

    Severity of the incident, not volume of routine work, is what a SOC is sized for.
    When a single named analyst owns the overwhelming majority of severe alerts, the
    entity has a single point of failure in exactly the function that has to hold up
    at 03:00 on a Saturday - and no amount of reported headcount changes that. The
    detector is deliberately scoped to critical/high traffic and requires a minimum
    headcount, so a genuinely one-person operation is not flagged for being what it is.
    """
    thresholds = thresholds or {}
    share = float(metrics.get("top_analyst_share") or 0.0)
    assigned = int(float(metrics.get("critical_alerts_assigned") or 0))
    active = int(float(metrics.get("critical_analysts_active") or 0))
    flag = float(thresholds.get("analyst_concentration_flag", 0.80))
    min_analysts = int(thresholds.get("analyst_concentration_min_analysts", 3))
    min_alerts = int(thresholds.get("analyst_concentration_min_alerts", 20))

    if share <= flag or assigned < min_alerts or active < min_analysts:
        return []

    top_analyst, evidence = "", []
    if alerts is not None and not alerts.empty and "assigned_analyst_id" in alerts.columns:
        sig = alerts[alerts["is_crit_high"]]
        sig = sig[sig["assigned_analyst_id"].fillna("").astype(str).str.strip() != ""]
        if not sig.empty:
            counts = sig["assigned_analyst_id"].astype(str).value_counts()
            if not counts.empty:
                top_analyst = str(counts.index[0])
                evidence = sig.loc[sig["assigned_analyst_id"].astype(str) == top_analyst,
                                   "alert_id"].tolist()
    label = f"analyst {top_analyst}" if top_analyst else "a single analyst"

    severity, score = severity_from_signal(share, high=0.95, medium=0.90)
    rationale = (
        f"{share:.0%} of the {assigned} critical/high alerts that carry an owner are "
        f"assigned to {label}, out of {active} analyst(s) who touched critical/high work "
        f"(flag level {flag:.0%}). Workload concentration at this level is a continuity "
        f"risk rather than a resourcing preference: leave, shift patterns or attrition for "
        f"one person would materially change how this entity handles its most severe "
        f"alerts, and the concentration also makes the depth of those investigations "
        f"dependent on a single individual's judgement."
    )
    return [finalize_finding(
        rule_id="EG-007",
        title="Critical alert workload concentrated on one analyst",
        description=(f"{share:.0%} of critical/high alerts are owned by {label} "
                     f"({active} analysts active on critical/high work in total)."),
        rationale=rationale,
        weakness_type="execution_gap",
        capability_tags=["C5", "C7", "C8"],
        severity=severity,
        severity_score=score,
        evidence_ids=evidence,
        metric_value=share,
        threshold_value=flag,
        thresholds=thresholds,
    )]


def run_all(alerts, cases, thresholds, *, similarity=0.0, duplicate_case_ids=None,
            avg_depth=None, monitored_assets=0, metrics=None, note_sample_size=0,
            note_sampled=False) -> list[dict]:
    """Run every execution-gap detector for one entity."""
    findings = []
    findings += detect_fast_closures(alerts, cases, thresholds)
    findings += detect_missing_escalations(alerts, None, thresholds)
    findings += detect_template_notes(cases, thresholds, similarity, duplicate_case_ids,
                                      sample_size=note_sample_size, sampled=note_sampled)
    findings += detect_repeat_alerts(alerts, cases, thresholds, monitored_assets)
    findings += detect_bulk_closures(alerts, thresholds)
    findings += detect_shallow_investigations(cases, thresholds, avg_depth)
    findings += detect_analyst_concentration(metrics or {}, thresholds, alerts)
    return findings
