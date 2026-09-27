"""
Absolute-benchmark detectors (proposal Stage 4b)
===============================================
Peer-relative comparison answers "is this entity different from its peers?".  It
cannot answer "is this entity adequate?" - if a whole sector under-performs, every
member looks normal.  These detectors therefore compare the entity against fixed
reference values held in the editable config (``BENCHMARK_REFERENCES``), which the
supervisor can point at whatever published SOC benchmark table the submission
cites.

Reference values shipped here are *illustrative* survey-style medians, clearly
labelled as such, and every finding repeats the reference number it used so the
comparison is auditable.
"""

from __future__ import annotations

import pathlib
import sys

_DET_DIR = pathlib.Path(__file__).resolve().parent
for _p in (str(_DET_DIR.parent), str(_DET_DIR)):
    if _p not in sys.path:
        sys.path.append(_p)

from common import finalize_finding, safe_div, severity_from_signal  # noqa: E402


def detect_ack_benchmark(metrics: dict, benchmarks: dict, thresholds: dict | None = None) -> list[dict]:
    """BM-001 - acknowledgement of critical/high alerts outside the reference median.

    Deliberately measured on critical/high alerts only: comparing a whole-population
    acknowledgement median (dominated by low-severity noise) against a critical-alert
    MTTA benchmark would flag every entity in the portfolio.
    """
    thresholds = thresholds or {}
    reference = float(benchmarks.get("median_time_to_ack_minutes_critical",
                                     benchmarks.get("median_time_to_ack_minutes", 15.0)))
    observed = float(metrics.get("time_to_ack_median_critical")
                     or metrics.get("time_to_ack_median") or 0.0)
    limit = float(thresholds.get("benchmark_ack_minutes", 45.0))
    if observed <= limit or observed <= reference:
        return []

    ratio = safe_div(observed, reference)
    severity, score = severity_from_signal(ratio, high=4.0, medium=2.5)
    rationale = (
        f"Median time-to-acknowledge for critical/high alerts is {observed:.0f} minutes against the "
        f"configured absolute reference median of {reference:.0f} minutes ({ratio:.1f}x) and the flag "
        f"level of {limit:.0f} minutes. Reference basis: {benchmarks.get('reference_label', 'configured reference values')}. "
        f"Threat Detection and Incident Response timelines are both affected."
    )
    return [finalize_finding(
        rule_id="BM-001",
        title="Acknowledgement time beyond absolute benchmark",
        description=(f"Median acknowledgement takes {observed:.0f} minutes, {ratio:.1f}x the "
                     f"{reference:.0f}-minute reference median."),
        rationale=rationale,
        weakness_type="execution_gap",
        capability_tags=["C1", "C4"],
        severity=severity,
        severity_score=score,
        evidence_ids=[],
        detector_group="benchmark",
        metric_value=observed,
        threshold_value=limit,
        thresholds=thresholds,
    )]


def detect_close_benchmark(metrics: dict, benchmarks: dict, thresholds: dict | None = None) -> list[dict]:
    """BM-002 - median closure time for Critical alerts outside the reference median."""
    thresholds = thresholds or {}
    reference = float(benchmarks.get("median_time_to_close_minutes_critical", 240.0))
    observed = float(metrics.get("time_to_close_median_critical") or 0.0)
    limit = float(thresholds.get("benchmark_close_minutes_critical", reference))
    if observed <= limit or observed <= reference:
        return []

    ratio = safe_div(observed, reference)
    severity, score = severity_from_signal(ratio, high=3.0, medium=2.0)
    rationale = (
        f"Median closure time for Critical alerts is {observed:.0f} minutes against the configured "
        f"reference median of {reference:.0f} minutes ({ratio:.1f}x), above the flag level of "
        f"{limit:.0f} minutes. Slow containment of critical incidents extends exposure of the "
        f"critical service."
    )
    return [finalize_finding(
        rule_id="BM-002",
        title="Critical incident closure beyond absolute benchmark",
        description=(f"Critical alerts take a median of {observed:.0f} minutes to close, {ratio:.1f}x the "
                     f"{reference:.0f}-minute reference median."),
        rationale=rationale,
        weakness_type="execution_gap",
        capability_tags=["C4", "C8"],
        severity=severity,
        severity_score=score,
        evidence_ids=[],
        detector_group="benchmark",
        metric_value=observed,
        threshold_value=limit,
        thresholds=thresholds,
    )]


def detect_escalation_benchmark(metrics: dict, benchmarks: dict, thresholds: dict | None = None) -> list[dict]:
    """BM-003 - escalation behaviour below the absolute reference rate.

    Fires independently of EG-002 (which uses a lower, policy-style threshold), so
    an entity that clears the internal policy bar but still sits far below the
    reference norm is surfaced rather than excused.
    """
    thresholds = thresholds or {}
    reference = float(benchmarks.get("escalation_rate_critical", 0.60))
    observed = float(metrics.get("escalation_rate_critical") or 0.0)
    if observed >= reference:
        return []

    shortfall = reference - observed
    severity, score = severity_from_signal(shortfall, high=0.5, medium=0.3)
    rationale = (
        f"Only {observed:.0%} of Critical alerts carry an escalation record against a reference "
        f"expectation of {reference:.0%}. Shortfall of {shortfall:.0%} on the absolute reference basis "
        f"({benchmarks.get('reference_label', 'configured reference values')}), independent of how peers "
        f"behave."
    )
    return [finalize_finding(
        rule_id="BM-003",
        title="Escalation rate below absolute benchmark",
        description=(f"{observed:.0%} of critical alerts escalated against a reference expectation of "
                     f"{reference:.0%}."),
        rationale=rationale,
        weakness_type="execution_gap",
        capability_tags=["C3"],
        severity=severity,
        severity_score=score,
        evidence_ids=[],
        detector_group="benchmark",
        metric_value=observed,
        threshold_value=reference,
        thresholds=thresholds,
    )]


def run_all(metrics, benchmarks, thresholds=None) -> list[dict]:
    findings = []
    findings += detect_ack_benchmark(metrics, benchmarks, thresholds)
    findings += detect_close_benchmark(metrics, benchmarks, thresholds)
    findings += detect_escalation_benchmark(metrics, benchmarks, thresholds)
    return findings
