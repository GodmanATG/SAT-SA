"""
PDF Report Generator — offline reports using fpdf2
==================================================
Two deliverables, both generated entirely on the local machine:

* ``build_entity_report`` — one CSE: profile, risk assessment, the full measured
  metric set (including the incident-management and governance measures the earlier
  build's report did not carry), the declared-vs-measured reconciliation, the
  cycle-over-cycle movement, and every finding with its rationale, examiner verdict
  and evidence counts.
* ``build_portfolio_report`` — the whole portfolio: ranking with criticality,
  tier distribution, cycle-wide movement, and the finding population by detector.

Every section states its own basis (measured from the submission, configured
threshold, or examiner judgement) so a reader can tell evidence from interpretation
without the tool in front of them.
"""

import json
from datetime import datetime
from fpdf import FPDF


def _clean(text) -> str:
    """Sanitize text to be compatible with core PDF latin-1 fonts."""
    if text is None:
        return ""
    text = str(text)
    replacements = {
        "\u2014": "--",  # em dash
        "\u2013": "-",   # en dash
        "\u2012": "-",
        "\u2022": "*",   # bullet
        "\u2026": "...", # ellipsis
        "\u2018": "'",   # single quotes
        "\u2019": "'",
        "\u201c": '"',   # double quotes
        "\u201d": '"',
        "\u2192": "->",  # arrow
        "\u2190": "<-",
        "\u2194": "<->",
        "\u00b7": "-",   # middle dot
        "•": "*",
        "→": "->",
        "—": "--",
        "–": "-",
        "…": "...",
        "·": "-",
    }
    for orig, rep in replacements.items():
        text = text.replace(orig, rep)
    return text.encode("latin-1", "replace").decode("latin-1")


class SATSAReport(FPDF):
    """Custom PDF with SAT-SA branding."""

    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.cell(0, 8, _clean("SAT-SA | Supervisory Analytics Tool for SOC Assessment"), align="R")
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, _clean(f"Page {self.page_no()}/{{nb}} | Generated: "
                                f"{datetime.now().strftime('%Y-%m-%d %H:%M')} | CONFIDENTIAL"), align="C")

    def section_title(self, title: str):
        self.set_font("Helvetica", "B", 14)
        self.set_fill_color(41, 128, 185)
        self.set_text_color(255, 255, 255)
        self.cell(0, 10, _clean(f"  {title}"), fill=True, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(4)

    def sub_title(self, title: str):
        self.set_font("Helvetica", "B", 11)
        self.multi_cell(0, 6, _clean(title), new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def body_text(self, text: str, size: int = 10):
        self.set_x(self.l_margin)
        self.set_font("Helvetica", "", size)
        self.multi_cell(0, 5.5, _clean(text), new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def kv_row(self, key: str, value: str, key_width: int = 78):
        self.set_x(self.l_margin)
        self.set_font("Helvetica", "B", 10)
        self.cell(key_width, 6, _clean(key + ":"), new_x="END")
        self.set_font("Helvetica", "", 10)
        self.multi_cell(0, 6, _clean(str(value)), new_x="LMARGIN", new_y="NEXT")

    def table_header(self, cells, widths):
        self.set_x(self.l_margin)
        self.set_font("Helvetica", "B", 8)
        self.set_fill_color(220, 220, 220)
        for text, width in zip(cells, widths):
            self.cell(width, 6, _clean(str(text))[:28], border=1, fill=True)
        self.ln()

    def table_row(self, cells, widths):
        self.set_x(self.l_margin)
        self.set_font("Helvetica", "", 8)
        for text, width in zip(cells, widths):
            max_chars = max(8, int(width / 2.2))
            self.cell(width, 5.5, _clean(str(text))[:max_chars], border=1)
        self.ln()

    def tier_badge(self, tier: str, score: float):
        colors = {
            "Critical Attention": (192, 57, 43),
            "Elevated": (224, 142, 11),
            "Watch": (201, 169, 11),
            "Satisfactory": (30, 132, 73),
        }
        r, g, b = colors.get(tier, (100, 100, 100))
        self.set_fill_color(r, g, b)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 12)
        self.cell(80, 10, _clean(f"  {tier}  |  Score: {score:.1f}/100"), fill=True, align="C")
        self.set_text_color(0, 0, 0)
        self.ln(12)

    def disclaimer(self):
        self.ln(6)
        self.set_font("Helvetica", "I", 8)
        self.multi_cell(0, 5,
                        _clean("DISCLAIMER: This report supports supervisory judgement and does not "
                               "replace it. Findings are prioritisation signals derived from the "
                               "entity's own submitted records; all findings should be validated via "
                               "manual review before regulatory action. Generated fully offline -- no "
                               "data left the generating machine."))


def _pct(value, digits=1) -> str:
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "-"


def _num(value, digits=1) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _int(value) -> str:
    try:
        return f"{int(float(value)):,}"
    except (TypeError, ValueError):
        return "-"


def build_entity_report(entity_row: dict, findings: list, output_path: str,
                        *, declarations: list | None = None, controls: list | None = None,
                        cycle: dict | None = None, profile: dict | None = None,
                        thresholds: dict | None = None,
                        run_meta: dict | None = None) -> None:
    """Generate a PDF report for a single entity."""
    pdf = SATSAReport()
    pdf.alias_nb_pages()
    pdf.add_page()
    profile = profile or entity_row

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "Entity Supervisory Assessment Report", align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # ── Entity profile ───────────────────────────────────────────────────
    pdf.section_title("Entity Profile")
    pdf.kv_row("Entity name", entity_row.get("entity_name", "—"))
    pdf.kv_row("Entity ID", entity_row.get("entity_id", "—"))
    pdf.kv_row("Sector", f"{entity_row.get('sector', '—')}"
                         + (f" / {entity_row['sub_sector']}" if entity_row.get("sub_sector") else ""))
    pdf.kv_row("Criticality tier", str(profile.get("tier") or "—"))
    pdf.kv_row("SOC", f"{profile.get('soc_name') or '—'} · "
                      f"{profile.get('soc_model') or 'model not stated'} · "
                      f"{profile.get('soc_coverage') or 'coverage not stated'} · "
                      f"{_int(profile.get('analyst_count'))} analysts")
    pdf.kv_row("Review period submitted",
               f"{entity_row.get('review_period_start', '—')} → {entity_row.get('review_period_end', '—')}")
    pdf.kv_row("Records analysed",
               f"{_int(entity_row.get('total_alerts'))} alerts · "
               f"{_int(entity_row.get('total_cases'))} cases · "
               f"{_int(entity_row.get('investigation_events'))} workflow events · "
               f"{_int(entity_row.get('total_escalations'))} escalations · "
               f"{_int(entity_row.get('total_dispositions'))} closures · "
               f"{_int(entity_row.get('total_assets'))} assets")
    if run_meta:
        pdf.kv_row("Analytics run", f"{run_meta.get('cycle_label', '—')} "
                                    f"({run_meta.get('run_id', '—')}) at "
                                    f"{run_meta.get('taken_at', '—')}")
    pdf.ln(3)

    # ── Risk assessment ──────────────────────────────────────────────────
    pdf.section_title("Risk Assessment")
    tier = entity_row.get("risk_tier", "Satisfactory")
    score = float(entity_row.get("risk_score", 0) or 0)
    pdf.tier_badge(tier, score)
    pdf.body_text(
        f"Composite risk score {score:.1f}/100, ranked #{entity_row.get('risk_rank', '—')} of the "
        f"portfolio. The score blends the weighted operational metric gaps "
        f"({_num(entity_row.get('metric_index'))}/100) with the capability scorecard average "
        f"({_num(entity_row.get('capability_average'))}/100). It is a prioritisation index, not a "
        f"probability, and every component of it is listed below.", size=9)

    # ── Key measures ─────────────────────────────────────────────────────
    pdf.section_title("Measured Operational Measures")
    pdf.body_text("All values are measured from the entity's own submitted records.", size=8)
    rows = [
        ("Detection & acknowledgement", "", ""),
        ("Median acknowledgement, critical/high (min)",
         _num(entity_row.get("time_to_ack_median_critical")), "lower is better"),
        ("Median closure, critical (min)",
         _num(entity_row.get("time_to_close_median_critical")), "lower is better"),
        ("Critical/high closed within the fast-closure window",
         _pct(entity_row.get("fast_closure_rate")), "higher is worse"),
        ("Expected sector alert-category coverage",
         _pct(entity_row.get("expected_category_coverage")), "higher is better"),
        ("Activity shortfall vs sector peers",
         _pct(entity_row.get("activity_deviation")), "higher is worse"),
        ("Weekend activity vs weekday rate",
         f"{_num(entity_row.get('weekend_activity_ratio'), 2)}x", "lower is worse"),
        ("Night-time coverage gap", _num(entity_row.get("night_coverage_gap_pct")) + "%",
         "higher is worse"),
        ("Escalation & response", "", ""),
        ("Critical alerts without an escalation record",
         _pct(entity_row.get("crit_no_escalation_rate")), "higher is worse"),
        ("Closures exceeding their recorded target (SLA breach)",
         _pct(entity_row.get("sla_breach_rate")), "higher is worse"),
        ("Closures marked compliant that their own timeline contradicts",
         _pct(entity_row.get("sla_misreport_rate")), "higher is worse"),
        ("Closures subsequently reopened", _pct(entity_row.get("reopen_rate")),
         "higher is worse"),
        ("Severity softening (case below its alert's severity)",
         _pct(entity_row.get("severity_softening_rate")), "higher is worse"),
        ("Investigation quality", "", ""),
        ("Critical/high alerts with no case record",
         _pct(entity_row.get("missing_case_rate")), "higher is worse"),
        ("Cases with no adequate investigation trace",
         _pct(entity_row.get("investigation_gap_rate")), "higher is worse"),
        ("Workflow steps recording no evidence or result",
         _pct(entity_row.get("evidence_gap_rate")), "higher is worse"),
        ("Investigations repeating a completed step",
         _pct(entity_row.get("rework_loop_rate")), "higher is worse"),
        ("Cases with under two minutes of recorded investigation time",
         _pct(entity_row.get("investigation_time_anomaly_rate")), "higher is worse"),
        ("Near-duplicate investigation notes",
         _pct(entity_row.get("template_note_rate")), "higher is worse"),
        ("Governance & resilience", "", ""),
        ("Significant closures without a root cause",
         _pct(entity_row.get("no_root_cause_gap")), "higher is worse"),
        ("Significant closures without remediation evidence",
         _pct(entity_row.get("no_remediation_gap")), "higher is worse"),
        ("Accepted risks with no named authority",
         _pct(entity_row.get("risk_accept_no_authority_rate")), "higher is worse"),
        ("Critical alerts concentrated on the most loaded analyst",
         _pct(entity_row.get("top_analyst_share")), "higher is worse"),
        ("Critical estate monitoring coverage",
         _num(entity_row.get("critical_monitoring_coverage_pct")) + "%", "higher is better"),
        ("Critical assets generating no alerts",
         _int(entity_row.get("silent_critical_assets")), "higher is worse"),
        ("Critical assets not effectively monitored",
         _int(entity_row.get("unmonitored_critical_assets")), "higher is worse"),
        ("Declared KPIs contradicted by the records",
         _int(entity_row.get("declared_kpi_contradictions")), "higher is worse"),
        ("Findings removed by examiner judgement",
         _int(entity_row.get("examiner_suppressed")), "context"),
    ]
    widths = [118, 30, 32]
    pdf.table_header(["Measure", "Value", "Direction"], widths)
    for label, value, direction in rows:
        if not value and not direction:
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(sum(widths), 5.5, f"  {label}", new_x="LMARGIN", new_y="NEXT")
            continue
        pdf.table_row([label, value, direction], widths)
    pdf.ln(4)

    # ── Incident Management & Governance Integrity (IM Family) ───────────
    pdf.section_title("Incident Management & Governance Integrity (IM-001 - IM-009)")
    pdf.body_text("Targeted execution gap and integrity metrics measured from submitted case, disposition, "
                  "and workflow event records.", size=8)
    im_rows = [
        ("IM-001 SLA Misreporting Rate", _pct(entity_row.get("sla_misreport_rate")),
         f"{_int(entity_row.get('sla_misreport_count'))} compliant claims contradicted by timelines"),
        ("IM-001 SLA Breach Rate", _pct(entity_row.get("sla_breach_rate")),
         f"Median overrun: {_num(entity_row.get('close_time_over_target_median'))}x target"),
        ("IM-002 Severity Softening", _pct(entity_row.get("severity_softening_rate")),
         f"{_int(entity_row.get('severity_softening_count'))} case(s) logged below alert severity"),
        ("IM-002 Escalation Downgrade Rate", _pct(entity_row.get("escalation_downgrade_rate")),
         "Escalations downgraded or closed at source"),
        ("IM-003 Significant Closures Missing Root Cause", _pct(entity_row.get("no_root_cause_gap")),
         f"From {_int(entity_row.get('significant_closures'))} significant closure records"),
        ("IM-003 Significant Closures Missing Remediation", _pct(entity_row.get("no_remediation_gap")),
         "No remediation status or reference documented"),
        ("IM-004 Investigation Rework Loops", _pct(entity_row.get("rework_loop_rate")),
         f"{_int(entity_row.get('rework_cases'))} case(s) repeating completed steps"),
        ("IM-005 Post-Closure Reopen Rate", _pct(entity_row.get("reopen_rate")),
         f"{_int(entity_row.get('reopened_closures'))} closure(s) subsequently reopened"),
        ("IM-006 Investigation Trace Gap", _pct(entity_row.get("investigation_gap_rate")),
         f"{_int(entity_row.get('cases_without_investigation'))} case(s) with zero workflow records"),
        ("IM-006 Workflow Steps Missing Evidence/Result", _pct(entity_row.get("evidence_gap_rate")),
         f"From {_int(entity_row.get('investigation_events'))} total workflow events"),
        ("IM-007 Risk Accepted Without Named Authority", _pct(entity_row.get("risk_accept_no_authority_rate")),
         f"Overall risk acceptance rate: {_pct(entity_row.get('risk_accept_rate'))}"),
        ("IM-008 Declared KPI Contradictions", _int(entity_row.get("declared_kpi_contradictions")),
         "Declared KPI(s) contradicted by operational evidence"),
        ("IM-009 Investigations Under 2 Minutes", _pct(entity_row.get("investigation_time_anomaly_rate")),
         f"{_int(entity_row.get('cases_with_thin_investigation'))} case(s); median: {_num(entity_row.get('investigation_minutes_median'))} min"),
    ]
    im_widths = [72, 26, 82]
    pdf.table_header(["Metric / Detector", "Observed", "Operational Detail / Context"], im_widths)
    for label, val, detail in im_rows:
        pdf.table_row([label, val, detail], im_widths)
    pdf.ln(4)

    # ── Capability scorecard ─────────────────────────────────────────────
    pdf.section_title("Capability Scorecard")
    pdf.body_text("0 = strong, 100 = weakest. A score is the summed severity of the findings "
                  "tagged to that capability, so 0 means no finding bears on the dimension rather "
                  "than that it was measured and found perfect.", size=8)
    cap_names = {
        "c1_threat_detection": "C1 Threat Detection",
        "c2_investigation": "C2 Investigation",
        "c3_escalation": "C3 Escalation",
        "c4_incident_response": "C4 Incident Response",
        "c5_security_operations": "C5 Security Operations",
        "c6_governance": "C6 Governance & Oversight",
        "c7_operational_discipline": "C7 Operational Discipline",
        "c8_cyber_resilience": "C8 Cyber Resilience",
    }
    cap_widths = [88, 30, 62]
    pdf.table_header(["Capability", "Weakness", "Capability strength (100 - weakness)"], cap_widths)
    for col, name in cap_names.items():
        value = float(entity_row.get(col, 0) or 0)
        pdf.table_row([name, f"{value:.1f}", f"{max(0.0, 100 - value):.1f} (higher is better)"],
                      cap_widths)
    pdf.ln(4)

    # ── Declared vs measured ─────────────────────────────────────────────
    pdf.section_title("Declared Performance vs Operational Evidence")
    if declarations:
        pdf.body_text("What the entity declares in its submission cover sheet, set against the "
                      "same measure recomputed from its own records (rule IM-008).", size=8)
        d_widths = [62, 26, 30, 62]
        pdf.table_header(["Declared KPI", "Declared", "Measured", "Assessment"], d_widths)
        for row in declarations:
            pdf.table_row([
                row.get("label", ""), f"{row.get('declared', '')}",
                f"{row.get('measured', '')}",
                ("NOT SUPPORTED — " + str(row.get("detail", ""))) if row.get("contradicted")
                else ("consistent (" + str(row.get("detail", "")) + ")"),
            ], d_widths)
    else:
        pdf.body_text("No declared performance metrics were recorded for this entity. Their "
                      "absence limits the declared-vs-evidence comparison, which is itself noted "
                      "in the assessment.", size=9)
    if controls:
        pdf.ln(2)
        pdf.sub_title("Declared controls (treated as claims, tested against the records)")
        for control in controls[:12]:
            pdf.body_text(f"  • {control}", size=9)
    pdf.ln(3)

    # ── Cycle over cycle ─────────────────────────────────────────────────
    pdf.section_title("Movement Since the Previous Cycle")
    if cycle and cycle.get("previous") is not None:
        latest, previous = cycle["latest"], cycle["previous"]
        pdf.kv_row("Comparison",
                   f"{previous.get('cycle_label', '?')} ({previous.get('taken_at', '?')}) → "
                   f"{latest.get('cycle_label', '?')} ({latest.get('taken_at', '?')})")
        pdf.kv_row("Risk score",
                   f"{float(previous.get('risk_score', 0) or 0):.1f} → "
                   f"{float(latest.get('risk_score', 0) or 0):.1f} "
                   f"({float(latest.get('risk_score', 0) or 0) - float(previous.get('risk_score', 0) or 0):+.1f})")
        summary = cycle.get("summary", {})
        pdf.kv_row("Measures", f"{summary.get('better', 0)} improved · "
                              f"{summary.get('worse', 0)} deteriorated · "
                              f"{summary.get('flat', 0)} unchanged")
        movements = [c for c in cycle.get("changes", []) if c["verdict"] in ("better", "worse")]
        if movements:
            c_widths = [86, 28, 28, 38]
            pdf.table_header(["Measure", "Previous", "Latest", "Movement"], c_widths)
            for change in movements[:20]:
                pdf.table_row([change["label"], change["previous_display"], change["latest_display"],
                               f"{change['delta_display']} ({change['verdict']})"], c_widths)
    elif cycle and cycle.get("latest"):
        pdf.body_text(f"{cycle['latest'].get('cycle_label', 'This cycle')} is the first recorded "
                      f"run for this entity, so there is no previous cycle to compare against yet. "
                      f"Re-running after the next submission cycle will populate this section.",
                      size=9)
    else:
        pdf.body_text("No analytics snapshots recorded for this entity.", size=9)
    pdf.ln(3)

    # ── Findings ─────────────────────────────────────────────────────────
    pdf.section_title(f"Supervisory Findings ({len(findings)})")
    if not findings:
        pdf.body_text("No findings were raised for this entity in the last analytics run. That is "
                      "a statement about the evidence submitted, not a clean bill of health: an "
                      "entity that submits little cannot be found wanting on much.", size=9)

    for i, f in enumerate(findings, 1):
        verdict = f.get("examiner_verdict") or ""
        title = f"{f.get('title', 'Untitled')}" + (f"  [Examiner: {verdict}]" if verdict else "")
        pdf.sub_title(f"Finding {i}: {title}")
        pdf.kv_row("Rule / detector", f"{f.get('rule_id', '—')} ({f.get('detector_group', 'rule')})")
        pdf.kv_row("Weakness type", f.get("weakness_type", "—"))
        pdf.kv_row("Severity",
                   f"{f.get('severity', 'Medium')} ({float(f.get('severity_score', 0) or 0):.2f})")
        pdf.kv_row("Observed / threshold",
                   f"{float(f.get('metric_value', 0) or 0):.3f} / "
                   f"{float(f.get('threshold_value', 0) or 0):.3f}")
        pdf.kv_row("Evidence records", _int(f.get("evidence_count", 0)))
        pdf.body_text(f.get("description", ""), size=9)
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 5, _clean("Why it was flagged: " + str(f.get("rationale") or "-")),
                       new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        try:
            caps = json.loads(f.get("capability_tags") or "[]")
        except (TypeError, ValueError):
            caps = []
        if caps:
            pdf.kv_row("Capabilities affected", ", ".join(str(c) for c in caps))
        try:
            sample = json.loads(f.get("evidence_ids") or "[]")
        except (TypeError, ValueError):
            sample = []
        if sample:
            pdf.kv_row("Evidence sample", ", ".join(str(s) for s in sample[:5]))
        if verdict:
            pdf.kv_row("Examiner verdict", verdict)
            if f.get("examiner_rationale"):
                pdf.body_text("Examiner rationale: " + str(f["examiner_rationale"]), size=9)
        pdf.ln(2)

    pdf.disclaimer()
    pdf.output(output_path)


def build_portfolio_report(entities: list, findings: list, output_path: str,
                           *, changes: list | None = None, run_meta: dict | None = None,
                           tier_by_entity: dict | None = None) -> None:
    """Generate a summary PDF report for the full portfolio."""
    pdf = SATSAReport()
    pdf.alias_nb_pages()
    pdf.add_page()
    tier_by_entity = tier_by_entity or {}

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "Portfolio Supervisory Summary Report", align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # ── Overview ─────────────────────────────────────────────────────────
    pdf.section_title("Portfolio Overview")
    pdf.kv_row("Entities assessed", _int(len(entities)))
    pdf.kv_row("Findings raised", _int(len(findings)))
    by_group: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for f in findings:
        by_group[f.get("detector_group", "rule")] = by_group.get(f.get("detector_group", "rule"), 0) + 1
        by_type[f.get("weakness_type", "—")] = by_type.get(f.get("weakness_type", "—"), 0) + 1
    for key in ("rule", "incident_management", "benchmark", "peer", "trend"):
        if by_group.get(key):
            pdf.kv_row(f"  by detector family: {key}", _int(by_group[key]))
    for key, value in sorted(by_type.items()):
        pdf.kv_row(f"  by weakness type: {key}", _int(value))
    pdf.kv_row("Total alerts analysed",
               _int(sum(e.get("total_alerts", 0) or 0 for e in entities)))
    pdf.kv_row("Total closures analysed",
               _int(sum(e.get("total_dispositions", 0) or 0 for e in entities)))
    if run_meta:
        pdf.kv_row("Analytics run", f"{run_meta.get('cycle_label', '—')} "
                                    f"({run_meta.get('run_id', '—')}) at "
                                    f"{run_meta.get('taken_at', '—')}")

    tiers: dict[str, int] = {}
    for e in entities:
        t = e.get("risk_tier", "Satisfactory")
        tiers[t] = tiers.get(t, 0) + 1
    pdf.ln(2)
    pdf.sub_title("Prioritisation bands")
    for tier_name in ("Critical Attention", "Elevated", "Watch", "Satisfactory"):
        pdf.kv_row(f"  {tier_name}", _int(tiers.get(tier_name, 0)))
    scores = [float(e.get("risk_score", 0) or 0) for e in entities]
    if scores:
        pdf.kv_row("Mean / median risk score",
                   f"{sum(scores) / len(scores):.1f} / {sorted(scores)[len(scores) // 2]:.1f}")
    pdf.ln(3)

    # ── Ranking ──────────────────────────────────────────────────────────
    pdf.section_title("Entity Risk Ranking (priority order)")
    pdf.body_text("Priority order multiplies the risk score by the entity's criticality weight, so "
                  "identical gaps are not treated as equally urgent in a Tier 1 operator and a "
                  "Tier 4 one.", size=8)
    widths = [8, 52, 30, 14, 16, 30, 16]
    pdf.table_header(["#", "Entity", "Sector", "Score", "Priority", "Band", "Tier"], widths)
    weights = {"Tier 1 - Critical": 1.00, "Tier 2 - High": 0.85,
               "Tier 3 - Medium": 0.70, "Tier 4 - Low": 0.55}
    ordered = sorted(entities, key=lambda e: float(e.get("risk_score", 0) or 0)
                     * weights.get(str(tier_by_entity.get(e.get("entity_id"), "")), 0.7),
                     reverse=True)
    for i, e in enumerate(ordered, 1):
        tier = str(tier_by_entity.get(e.get("entity_id"), "—"))
        score = float(e.get("risk_score", 0) or 0)
        pdf.table_row([str(i), str(e.get("entity_name", "—"))[:30],
                       str(e.get("sector", "—"))[:18], f"{score:.1f}",
                       f"{score * weights.get(tier, 0.7):.1f}",
                       str(e.get("risk_tier", "—")), tier.replace("Tier ", "T")], widths)
    pdf.ln(4)

    # ── Cycle movement ───────────────────────────────────────────────────
    if changes:
        pdf.section_title("Movement Since the Previous Cycle")
        pdf.body_text("Entities whose measures moved materially between the two most recent "
                      "analytics runs. Deterioration first.", size=8)
        c_widths = [56, 30, 26, 22, 34]
        pdf.table_header(["Entity", "Risk: before → now", "Change", "Band",
                          "Largest deterioration"], c_widths)
        for row in changes[:20]:
            pdf.table_row([str(row.get("entity", ""))[:28],
                           f"{float(row.get('risk_before', 0) or 0):.1f} → "
                           f"{float(row.get('risk_now', 0) or 0):.1f}",
                           f"{float(row.get('risk_delta', 0) or 0):+.1f}",
                           str(row.get("tier", "")),
                           str(row.get("biggest_movement", ""))[:26]], c_widths)
    # ── Incident Management & Governance (Portfolio Summary) ─────────────
    pdf.section_title("Portfolio Incident Management & Integrity Overview")
    pdf.body_text("Aggregated execution gap and record integrity metrics across all registered CSEs.", size=8)

    total_misreported = sum(int(e.get("sla_misreport_count", 0) or 0) for e in entities)
    total_softened = sum(int(e.get("severity_softening_count", 0) or 0) for e in entities)
    total_silent_cases = sum(int(e.get("cases_without_investigation", 0) or 0) for e in entities)
    total_kpi_contradictions = sum(int(e.get("declared_kpi_contradictions", 0) or 0) for e in entities)

    im_flagged = [e for e in entities if (
        float(e.get("sla_misreport_rate", 0) or 0) > 0
        or float(e.get("severity_softening_rate", 0) or 0) > 0
        or float(e.get("no_root_cause_gap", 0) or 0) > 0.2
        or int(e.get("declared_kpi_contradictions", 0) or 0) > 0
    )]

    pdf.kv_row("SLA misreported closures across portfolio", _int(total_misreported))
    pdf.kv_row("Severity-softened incidents across portfolio", _int(total_softened))
    pdf.kv_row("Cases closed without investigation workflow", _int(total_silent_cases))
    pdf.kv_row("Declared KPI contradictions across portfolio", _int(total_kpi_contradictions))
    pdf.ln(2)

    if im_flagged:
        pdf.sub_title("Entities with elevated incident management or reporting integrity concerns")
        im_t_widths = [50, 24, 28, 28, 26, 24]
        pdf.table_header(["Entity", "SLA Breach", "SLA Misreport", "Softening", "No Root Cause", "KPI Gaps"], im_t_widths)
        for e in sorted(im_flagged, key=lambda x: (
            float(x.get("sla_misreport_rate", 0) or 0) + float(x.get("severity_softening_rate", 0) or 0)
        ), reverse=True)[:15]:
            pdf.table_row([
                str(e.get("entity_name", "—"))[:26],
                _pct(e.get("sla_breach_rate")),
                _pct(e.get("sla_misreport_rate")),
                _pct(e.get("severity_softening_rate")),
                _pct(e.get("no_root_cause_gap")),
                _int(e.get("declared_kpi_contradictions")),
            ], im_t_widths)
        pdf.ln(4)

    pdf.disclaimer()
    pdf.output(output_path)
