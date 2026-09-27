"""
Incident-management, governance and accountability detectors (IM-xxx)
=====================================================================
The problem statement's first category of supervisory weakness is the *execution
gap*: "documented controls, governance arrangements, policies, procedures,
metrics or reported capabilities suggest effective operation, but operational
evidence indicates otherwise".

The detectors in this module target exactly that class of weakness, using the
parts of a submission that a metrics dashboard would not look at:

======================  ==================================================================
IM-001  SLA integrity   closure records claiming compliance that the recorded time
                        contradicts (metric gaming), plus measured breach rate
IM-002  Severity         cases and escalations logged below the severity of the alerts
        softening       they were opened from - the incident is re-labelled on the way in
IM-003  Root cause       significant incidents closed with no root cause or remediation
                        reference on record
IM-004  Investigation    investigations cycling through the same workflow activities
        rework          (process mining over the investigation event log)
IM-005  Reopen rate      a material share of closures are later reopened
IM-006  Investigation    cases with no investigation workflow record at all, and workflow
        trace           events that record neither evidence nor a result
IM-007  Accepted risk    risks accepted without a named accepting authority
IM-008  Declared vs      the entity's own declared KPIs are contradicted by its own
        evidence        operational records
======================  ==================================================================

Every detector is a pure function of the feature row (plus, for evidence, the
underlying frames), so each can be unit-tested and each can be re-run from the
Settings page after a threshold change.
"""

from __future__ import annotations

import pathlib
import sys

_DET_DIR = pathlib.Path(__file__).resolve().parent
for _p in (str(_DET_DIR.parent), str(_DET_DIR)):
    if _p not in sys.path:
        sys.path.append(_p)

import pandas as pd  # noqa: E402

from common import finalize_finding, pct, safe_div, severity_from_signal  # noqa: E402


def _evidence(frame, mask, id_col, cap_frame_limit: int = 200):
    """Return (ids, count) for the rows matching ``mask`` (capped later by finalize)."""
    if frame is None or getattr(frame, "empty", True) or id_col not in frame.columns:
        return [], 0
    try:
        rows = frame.loc[mask, id_col]
    except (KeyError, IndexError):
        return [], 0
    ids = [str(v) for v in rows.tolist() if str(v).strip()]
    return ids[:cap_frame_limit], len(ids)


def _metric(metrics: dict, key: str, default: float = 0.0) -> float:
    value = metrics.get(key, default)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return 0.0 if value != value else value  # NaN -> 0


# ---------------------------------------------------------------------------
# IM-001  SLA integrity and SLA misreporting
# ---------------------------------------------------------------------------

def detect_sla_integrity(metrics: dict, thresholds: dict, dispositions=None) -> list[dict]:
    """IM-001 - closures that miss the target, and closures that claim they did not.

    Two separate signals, because they mean different things:

    * ``sla_breach_rate``   - the control is not working;
    * ``sla_misreport_rate`` - the *record* says the control worked. This is the
      metric-gaming pattern and is scored more severely, since it also attacks the
      reliability of every other metric the entity reports.
    """
    findings = []
    breach_limit = float(thresholds.get("sla_breach_rate_flag", 0.10))
    misreport_limit = float(thresholds.get("sla_misreport_threshold", 0.15))
    min_cases = int(thresholds.get("sla_breach_min_cases", 5))

    breach = _metric(metrics, "sla_breach_rate")
    measured_rows = int(_metric(metrics, "total_dispositions"))
    if measured_rows < min_cases:
        return findings

    if breach > breach_limit:
        over = _metric(metrics, "close_time_over_target_median")
        severity, score = severity_from_signal(breach, high=0.4, medium=0.25)
        rationale = (
            f"{pct(breach)}% of the {measured_rows} closure records exceed the response target "
            f"recorded against them (flag level {pct(breach_limit)}%). Median overrun on breached "
            f"closures is {over:.2f}x the target. Measured from each disposition record's own "
            f"time-to-close and target columns, not from reported compliance."
        )
        ids, count = _evidence(
            dispositions,
            (dispositions.get("sla_breach") == 1) if dispositions is not None
            and "sla_breach" in getattr(dispositions, "columns", []) else None,
            "disposition_id")
        findings.append(finalize_finding(
            rule_id="IM-001", title="Closures exceed the recorded response target",
            description=(f"{pct(breach)}% of closures breach their SLA target "
                         f"({int(breach * measured_rows)} of {measured_rows})."),
            rationale=rationale, weakness_type="execution_gap",
            capability_tags=["C4", "C7"], severity=severity, severity_score=score,
            evidence_ids=ids, detector_group="incident_management",
            metric_value=breach, threshold_value=breach_limit, thresholds=thresholds))

    misreport = _metric(metrics, "sla_misreport_rate")
    if misreport > misreport_limit:
        count = int(_metric(metrics, "sla_misreport_count"))
        severity, score = severity_from_signal(misreport, high=0.5, medium=0.3)
        rationale = (
            f"{count} closure records are marked as having met the SLA while the recorded "
            f"time-to-close is greater than the recorded target - {pct(misreport)}% of all closures "
            f"marked compliant (flag level {pct(misreport_limit)}%). The reported compliance figure "
            f"cannot be reconciled with the entity's own closure records, which calls into question "
            f"the reliability of the wider metric set, not just this indicator."
        )
        ids, ev_count = _evidence(
            dispositions,
            (dispositions.get("sla_misreport") == 1) if dispositions is not None
            and "sla_misreport" in getattr(dispositions, "columns", []) else None,
            "disposition_id")
        findings.append(finalize_finding(
            rule_id="IM-001", title="SLA compliance reported contrary to closure evidence",
            description=(f"{count} closures are flagged SLA-compliant although they exceed the "
                         f"target."),
            rationale=rationale, weakness_type="execution_gap",
            capability_tags=["C6", "C7"], severity=severity, severity_score=score,
            evidence_ids=ids, detector_group="incident_management",
            metric_value=misreport, threshold_value=misreport_limit, thresholds=thresholds))
    return findings


# ---------------------------------------------------------------------------
# IM-002  Severity softening
# ---------------------------------------------------------------------------

def detect_severity_softening(metrics: dict, thresholds: dict, cases=None) -> list[dict]:
    """IM-002 - cases/escalations recorded below the severity of their own alerts."""
    limit = float(thresholds.get("severity_softening_flag", 0.25))
    rate = _metric(metrics, "severity_softening_rate")
    significant = int(_metric(metrics, "cases_from_significant_alerts"))
    if rate <= limit or significant < 3:
        return []

    count = int(_metric(metrics, "severity_softening_count"))
    downgrade = _metric(metrics, "escalation_downgrade_rate")
    severity, score = severity_from_signal(rate, high=0.6, medium=0.4)
    rationale = (
        f"{count} of {significant} cases opened from a Critical or High alert are recorded at a "
        f"lower severity than the alert itself ({pct(rate)}%, flag level {pct(limit)}%). "
        + (f"{pct(downgrade)}% of escalation records additionally carry a downgrade or "
           f"close-at-source decision. " if downgrade else "")
        + "Because closure time, escalation rate and priority-based reporting are all keyed off "
          "the case severity, a downgrade at intake makes every downstream indicator look "
          "healthier without reducing the underlying risk."
    )
    ids, count_ev = _evidence(
        cases,
        (cases.get("softened") == 1) if cases is not None
        and "softened" in getattr(cases, "columns", []) else None,
        "case_id")
    return [finalize_finding(
        rule_id="IM-002", title="Cases recorded below the severity of their alerts",
        description=(f"{pct(rate)}% of cases from Critical/High alerts are logged at a lower "
                     f"severity."),
        rationale=rationale, weakness_type="execution_gap",
        capability_tags=["C3", "C6", "C7"], severity=severity, severity_score=score,
        evidence_ids=ids, detector_group="incident_management",
        metric_value=rate, threshold_value=limit, thresholds=thresholds)]


# ---------------------------------------------------------------------------
# IM-003  Root cause / remediation records
# ---------------------------------------------------------------------------

def detect_root_cause_records(metrics: dict, thresholds: dict, dispositions=None) -> list[dict]:
    """IM-003 - significant closures with no root cause or remediation reference."""
    findings = []
    rc_limit = float(thresholds.get("no_root_cause_flag", 0.35))
    rem_limit = float(thresholds.get("no_remediation_flag", 0.40))
    significant = int(_metric(metrics, "significant_closures"))
    if significant < 3:
        return findings

    rc_gap = _metric(metrics, "no_root_cause_gap")
    if rc_gap > rc_limit:
        severity, score = severity_from_signal(rc_gap, high=0.7, medium=0.5)
        ids, count = _evidence(
            dispositions,
            (dispositions.get("no_root_cause") == 1) if dispositions is not None
            and "no_root_cause" in getattr(dispositions, "columns", []) else None,
            "disposition_id")
        findings.append(finalize_finding(
            rule_id="IM-003", title="Significant incidents closed without a root cause",
            description=(f"{pct(rc_gap)}% of significant closures record no root cause "
                         f"({count} of {significant})."),
            rationale=(
                f"{count} of {significant} closures that resolved a Critical/High alert or a "
                f"confirmed true positive carry an empty root-cause field ({pct(rc_gap)}%, flag level "
                f"{pct(rc_limit)}%). Without a recorded root cause there is no basis on which the "
                f"entity can demonstrate that the underlying weakness - rather than the alert - was "
                f"addressed, and repeat activity on the same assets becomes unidentifiable."),
            weakness_type="execution_gap", capability_tags=["C2", "C4", "C6"],
            severity=severity, severity_score=score, evidence_ids=ids,
            detector_group="incident_management", metric_value=rc_gap,
            threshold_value=rc_limit, thresholds=thresholds))

    rem_gap = _metric(metrics, "no_remediation_gap")
    if rem_gap > rem_limit:
        severity, score = severity_from_signal(rem_gap, high=0.7, medium=0.55)
        findings.append(finalize_finding(
            rule_id="IM-003", title="Significant incidents closed without remediation evidence",
            description=f"{pct(rem_gap)}% of significant closures have no remediation action or reference.",
            rationale=(
                f"{pct(rem_gap)}% of significant closures show neither a completed/in-progress "
                f"remediation status nor a remediation reference (flag level {pct(rem_limit)}%). "
                f"Closure without recorded remediation is consistent with the alert being "
                f"suppressed rather than the condition being fixed, which is how repeat alerts on "
                f"the same asset become chronic."),
            weakness_type="execution_gap", capability_tags=["C4", "C8"],
            severity=severity, severity_score=score, evidence_ids=[], detector_group="incident_management",
            metric_value=rem_gap, threshold_value=rem_limit, thresholds=thresholds))
    return findings


# ---------------------------------------------------------------------------
# IM-004  Investigation rework loops (process mining)
# ---------------------------------------------------------------------------

def detect_investigation_rework(metrics: dict, thresholds: dict, investigations=None) -> list[dict]:
    """IM-004 - investigations cycling through the same workflow activities."""
    limit = float(thresholds.get("rework_loop_flag", 0.30))
    rate = _metric(metrics, "rework_loop_rate")
    cases_with_events = int(_metric(metrics, "cases_with_events"))
    if rate <= limit or cases_with_events < 5:
        return []

    loops = int(_metric(metrics, "rework_cases"))
    avg_events = _metric(metrics, "avg_events_per_case")
    severity, score = severity_from_signal(rate, high=0.6, medium=0.4)
    rationale = (
        f"{loops} of {cases_with_events} investigations return to an activity they had already "
        f"completed, with at least one other activity in between ({pct(rate)}% of cases with a "
        f"workflow record, flag level {pct(limit)}%), against an average of {avg_events:.1f} "
        f"workflow events per case. Repeating the same steps indicates work being re-done or "
        f"handed back - the pattern of a triage that never reached a conclusion - rather than "
        f"progressive investigation. Derived from the submitted investigation event log "
        f"(activity sequence), not from case status."
    )
    ids, count = _evidence(
        investigations,
        (investigations.get("activity_type").astype(str).str.len() > 0)
        if investigations is not None else None,
        "investigation_id")
    return [finalize_finding(
        rule_id="IM-004", title="Investigations repeat completed workflow activities",
        description=f"{pct(rate)}% of investigations show rework loops across their own activity log.",
        rationale=rationale, weakness_type="execution_gap",
        capability_tags=["C2", "C5", "C7"], severity=severity, severity_score=score,
        evidence_ids=ids, detector_group="incident_management",
        metric_value=rate, threshold_value=limit, thresholds=thresholds)]


# ---------------------------------------------------------------------------
# IM-005  Reopen rate
# ---------------------------------------------------------------------------

def detect_reopen_rate(metrics: dict, thresholds: dict, dispositions=None) -> list[dict]:
    """IM-005 - closures that were subsequently reopened."""
    limit = float(thresholds.get("reopen_rate_flag", 0.05))
    rate = _metric(metrics, "reopen_rate")
    total = int(_metric(metrics, "total_dispositions"))
    if rate <= limit or total < 10:
        return []

    count = int(_metric(metrics, "reopened_closures"))
    severity, score = severity_from_signal(rate, high=0.20, medium=0.10)
    return [finalize_finding(
        rule_id="IM-005", title="Closures subsequently reopened",
        description=f"{pct(rate)}% of closures ({count} of {total}) were reopened after closure.",
        rationale=(
            f"{count} of {total} closure records carry a reopen count greater than zero "
            f"({pct(rate)}%, flag level {pct(limit)}%). A reopened incident means the initial "
            f"closure did not actually resolve the condition; at this rate the entity's first-time-"
            f"closure measure overstates the effectiveness of its incident response."),
        weakness_type="execution_gap", capability_tags=["C2", "C4"],
        severity=severity, severity_score=score,
        evidence_ids=[], detector_group="incident_management",
        metric_value=rate, threshold_value=limit, thresholds=thresholds)]


# ---------------------------------------------------------------------------
# IM-006  Investigation trace completeness
# ---------------------------------------------------------------------------

def detect_investigation_trace(metrics: dict, thresholds: dict, cases=None) -> list[dict]:
    """IM-006 - cases with no workflow record, and workflow events with no evidence."""
    findings = []
    gap_limit = float(thresholds.get("investigation_gap_flag", 0.30))
    ev_limit = float(thresholds.get("evidence_missing_flag", 0.40))

    total_cases = int(_metric(metrics, "total_cases"))
    gap = _metric(metrics, "investigation_gap_rate")
    if gap > gap_limit and total_cases >= 10:
        missing = int(_metric(metrics, "cases_without_investigation"))
        severity, score = severity_from_signal(gap, high=0.6, medium=0.4)
        findings.append(finalize_finding(
            rule_id="IM-006", title="Cases closed with no investigation workflow record",
            description=(f"{pct(gap)}% of cases have fewer than the expected workflow events "
                         f"({missing} with none at all)."),
            rationale=(
                f"{missing} of {total_cases} cases have no investigation workflow events at all, "
                f"and a further share have fewer than the configured minimum ({pct(gap)}% below the "
                f"minimum, flag level {pct(gap_limit)}%). A closed case with no recorded "
                f"investigation activity is not evidence of investigation; it is evidence of "
                f"closure. This is a negative-space finding: the expected artefact is absent."),
            weakness_type="negative_space", capability_tags=["C2", "C5", "C6"],
            severity=severity, severity_score=score, evidence_ids=[],
            detector_group="incident_management", metric_value=gap,
            threshold_value=gap_limit, thresholds=thresholds))

    ev_gap = _metric(metrics, "evidence_gap_rate")
    if ev_gap > ev_limit:
        severity, score = severity_from_signal(ev_gap, high=0.6, medium=0.45)
        findings.append(finalize_finding(
            rule_id="IM-006", title="Investigation steps recorded without evidence or result",
            description=f"{pct(ev_gap)}% of investigation workflow events record no evidence or result.",
            rationale=(
                f"{pct(ev_gap)}% of submitted investigation workflow events (flag level "
                f"{pct(ev_limit)}%) carry neither an evidence type nor an action result. The "
                f"workflow shows steps being taken without anything being examined or concluded, "
                f"which is the record-level signature of a procedural tick rather than an "
                f"investigation."),
            weakness_type="execution_gap", capability_tags=["C2", "C7"],
            severity=severity, severity_score=score, evidence_ids=[],
            detector_group="incident_management", metric_value=ev_gap,
            threshold_value=ev_limit, thresholds=thresholds))
    return findings


# ---------------------------------------------------------------------------
# IM-007  Accepted risk without authority
# ---------------------------------------------------------------------------

def detect_investigation_time_anomaly(metrics: dict, thresholds: dict,
                                      investigations=None) -> list[dict]:
    """IM-009 - cases recorded as investigated whose recorded work took seconds.

    This is the sharpest form of the "investigated on paper" gap: the case carries a
    plausible workflow (steps, activities, an analyst, a conclusion), but the time
    recorded against those steps adds up to less than two minutes. The steps exist;
    the work behind them does not. It is measured from the workflow's own duration
    column, so it does not depend on any timing the entity reports separately.
    """
    floor_minutes = float(thresholds.get("investigation_time_min_minutes", 2.0))
    limit = float(thresholds.get("investigation_time_anomaly_flag", 0.30))
    min_cases = int(thresholds.get("investigation_time_min_cases", 10))

    rate = _metric(metrics, "investigation_time_anomaly_rate")
    cases_with_events = int(_metric(metrics, "cases_with_events"))
    thin = int(_metric(metrics, "cases_with_thin_investigation"))
    if rate <= limit or cases_with_events < min_cases:
        return []

    median_minutes = _metric(metrics, "investigation_minutes_median")
    evidence = []
    if investigations is not None and not investigations.empty and \
            "duration_minutes" in investigations.columns and "case_id" in investigations.columns:
        durations = pd.to_numeric(investigations["duration_minutes"], errors="coerce").fillna(0)
        per_case = investigations.assign(_d=durations).groupby("case_id")["_d"].sum()
        evidence = [str(c) for c in per_case.index[per_case < floor_minutes].tolist()]

    severity, score = severity_from_signal(rate, high=0.7, medium=0.5)
    rationale = (
        f"{thin} of {cases_with_events} cases with a workflow record (the sample covers "
        f"{cases_with_events} of the entity's cases) show a total recorded investigation time "
        f"below {floor_minutes:.0f} minutes ({pct(rate)}%, flag level {pct(limit)}%). The median "
        f"case in that population records {median_minutes:.1f} minutes of investigative work "
        f"against a workflow containing multiple distinct activities. A record that lists "
        f"triage, evidence collection and root-cause analysis but accounts for seconds of work "
        f"is evidence of case administration rather than investigation, and it is the pattern "
        f"that makes an entity's investigation metrics look complete while nothing behind them "
        f"was actually examined."
    )
    return [finalize_finding(
        rule_id="IM-009",
        title="Cases investigated in seconds",
        description=(f"{pct(rate)}% of cases with a workflow record account for less than "
                     f"{floor_minutes:.0f} minutes of investigation time in total."),
        rationale=rationale, weakness_type="execution_gap",
        capability_tags=["C2", "C4", "C7"], severity=severity, severity_score=score,
        evidence_ids=evidence, detector_group="incident_management",
        metric_value=rate, threshold_value=limit, thresholds=thresholds)]


def detect_accepted_risk_authority(metrics: dict, thresholds: dict) -> list[dict]:
    """IM-007 - risks accepted with no recorded accepting authority."""
    limit = float(thresholds.get("risk_accept_no_authority_flag", 0.5))
    rate = _metric(metrics, "risk_accept_no_authority_rate")
    accepted = _metric(metrics, "risk_accept_rate")
    if rate <= limit or _metric(metrics, "total_dispositions") < 10:
        return []

    severity, score = severity_from_signal(rate, high=0.9, medium=0.7)
    return [finalize_finding(
        rule_id="IM-007", title="Risk accepted without a recorded accepting authority",
        description=f"{pct(rate)}% of accepted risks carry no named accepting authority.",
        rationale=(
            f"Risk acceptance is recorded on {pct(accepted)}% of closures, and {pct(rate)}% of "
            f"those acceptances name no authority (flag level {pct(limit)}%). An accepted risk "
            f"without a named authority is not a governed decision, and it removes the residual "
            f"risk from every downstream view of the entity's exposure. C6 Governance and "
            f"Oversight is the affected capability."),
        weakness_type="execution_gap", capability_tags=["C6", "C8"],
        severity=severity, severity_score=score, evidence_ids=[],
        detector_group="incident_management", metric_value=rate,
        threshold_value=limit, thresholds=thresholds)]


# ---------------------------------------------------------------------------
# IM-008  Declared performance vs operational evidence
# ---------------------------------------------------------------------------

# declared KPI key -> (metric key, comparison mode, default tolerance)
#   'pct'   : declared percentage vs measured percentage; flags when declared exceeds
#             measured by more than the tolerance (in percentage points)
#   'ratio' : declared minutes vs measured minutes; flags when measured exceeds
#             declared by more than the tolerance factor
#
# The tolerances themselves are NOT hardcoded here: they are read from the editable
# detection config (``thresholds['declared_kpi_tolerances']``) so a supervisor can
# tighten or relax them from the Settings page after the first shadow-run cycle,
# guided by what the examiner actually confirmed. This table holds only the mapping
# and the shipped default used when a key is absent from the config.
DECLARED_KPI_MAP = {
    "sla_compliance_pct": ("measured_sla_compliance_pct", "pct", 8.0),
    "critical_alert_ack_minutes_median": ("time_to_ack_median_critical", "ratio", 1.5),
    "critical_incident_containment_minutes_median": ("time_to_close_median_critical", "ratio", 1.5),
    "escalation_compliance_pct": ("measured_escalation_compliance_pct", "pct", 10.0),
    "investigation_records_completeness_pct": ("measured_investigation_completeness_pct", "pct", 10.0),
    "monitoring_coverage_production_pct": ("monitoring_coverage_pct", "pct", 5.0),
}


def declared_kpi_map(thresholds: dict | None = None) -> dict:
    """Effective declared-KPI comparison table, with configured tolerances applied."""
    overrides = (thresholds or {}).get("declared_kpi_tolerances") or {}
    resolved = {}
    for kpi, (metric_key, mode, default) in DECLARED_KPI_MAP.items():
        try:
            tolerance = float(overrides.get(kpi, default))
        except (TypeError, ValueError):
            tolerance = default
        resolved[kpi] = (metric_key, mode, tolerance)
    return resolved

KPI_LABELS = {
    "sla_compliance_pct": "SLA compliance",
    "critical_alert_ack_minutes_median": "Critical alert acknowledgement (minutes)",
    "critical_incident_containment_minutes_median": "Critical incident containment (minutes)",
    "escalation_compliance_pct": "Escalation compliance",
    "investigation_records_completeness_pct": "Investigation record completeness",
    "monitoring_coverage_production_pct": "Production monitoring coverage",
}


def declared_vs_measured(metrics: dict, declared_kpis: dict,
                         thresholds: dict | None = None) -> list[dict]:
    """Compare each declared KPI with the value the entity's own records support.

    Returns one row per KPI: {kpi, label, declared, measured, mode, delta, contradicted}.
    This is also used by the Reporting page, so the contradiction is visible as a
    table even where the detector does not fire.
    """
    rows = []
    if not declared_kpis:
        return rows
    for kpi, (metric_key, mode, tolerance) in declared_kpi_map(thresholds).items():
        if kpi not in declared_kpis:
            continue
        try:
            declared = float(declared_kpis.get(kpi))
        except (TypeError, ValueError):
            continue
        measured = metrics.get(metric_key)
        if measured is None:
            continue
        measured = float(measured)
        if mode == "pct":
            # higher declared than measured = over-claim
            delta = declared - measured
            contradicted = delta > tolerance
            detail = f"declared {declared:.1f}% vs measured {measured:.1f}% ({delta:+.1f} points)"
        else:
            # minutes: measured worse than declared
            ratio = safe_div(measured, declared, default=1.0)
            delta = ratio
            contradicted = ratio > tolerance
            detail = f"declared {declared:.0f} min vs measured {measured:.0f} min ({ratio:.1f}x)"
        rows.append(dict(kpi=kpi, label=KPI_LABELS.get(kpi, kpi), declared=declared,
                         measured=round(measured, 2), mode=mode, delta=round(delta, 3),
                         tolerance=tolerance, contradicted=bool(contradicted), detail=detail))
    return rows


def detect_declared_vs_evidence(metrics: dict, declared_kpis: dict,
                                thresholds: dict | None = None) -> list[dict]:
    """IM-008 - declared performance metrics contradicted by the entity's own records."""
    thresholds = thresholds or {}
    rows = [r for r in declared_vs_measured(metrics, declared_kpis, thresholds)
            if r["contradicted"]]
    # How many contradicted declarations make this a finding at all is configurable:
    # one borderline drift is a conversation, several mutually reinforcing ones are a
    # pattern. The level is stated in the rationale so the judgement is visible.
    min_count = int(thresholds.get("declared_kpi_min_contradictions", 2))
    if not rows or len(rows) < min_count:
        return []

    count = len(rows)
    severity, score = severity_from_signal(count, high=3, medium=2)
    worst = max(rows, key=lambda r: abs(r["delta"]))
    detail_text = "; ".join(r["detail"] for r in rows)
    rationale = (
        f"{count} of the entity's declared performance indicators cannot be reconciled with its "
        f"own submitted operational records: {detail_text}. The comparison uses the declared "
        f"values in the submission cover sheet against features measured from the alert, case, "
        f"closure and inventory records. Where a declared control or metric is contradicted by the "
        f"entity's own evidence, that is the classic execution gap - reported capability exceeding "
        f"operational reality. Largest divergence: {worst['label']} ({worst['detail']})."
    )
    return [finalize_finding(
        rule_id="IM-008", title="Declared performance contradicted by operational records",
        description=(f"{count} declared KPI(s) disagree with the entity's own records, largest "
                     f"divergence {worst['label']} ({worst['detail']})."),
        rationale=rationale, weakness_type="execution_gap",
        capability_tags=["C6", "C7"], severity=severity, severity_score=score,
        evidence_ids=[r["kpi"] for r in rows], detector_group="incident_management",
        metric_value=count, threshold_value=min_count, thresholds=thresholds)]


# ---------------------------------------------------------------------------
# Aggregate runner
# ---------------------------------------------------------------------------

def run_all(metrics: dict, thresholds: dict, *, cases=None, dispositions=None,
            investigations=None, declared_kpis=None) -> list[dict]:
    findings = []
    findings += detect_sla_integrity(metrics, thresholds, dispositions)
    findings += detect_severity_softening(metrics, thresholds, cases)
    findings += detect_root_cause_records(metrics, thresholds, dispositions)
    findings += detect_investigation_rework(metrics, thresholds, investigations)
    findings += detect_reopen_rate(metrics, thresholds, dispositions)
    findings += detect_investigation_trace(metrics, thresholds, cases)
    findings += detect_investigation_time_anomaly(metrics, thresholds, investigations)
    findings += detect_accepted_risk_authority(metrics, thresholds)
    findings += detect_declared_vs_evidence(metrics, declared_kpis or {}, thresholds)
    return findings
