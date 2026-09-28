"""
SAT-SA Configuration
====================
All detection thresholds, scoring weights, and constants.
These are stored in an editable JSON config file at runtime.
Supervisor can tune these without touching code.
"""

import json
import pathlib
import re

CONFIG_PATH = pathlib.Path(__file__).parent / "detection_config.json"

# ---------------------------------------------------------------------------
# Default thresholds (loaded from JSON if available, otherwise these defaults)
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS = {
    # ── Execution Gap Detectors ──
    "fast_closure_minutes": 5,              # Alg 1: minutes to count as "too fast"
    "fast_closure_rate_flag": 0.50,         # Alg 1: flag if >50% of criticals closed this fast
    "escalation_rate_flag": 0.30,           # Alg 2: flag if <30% of criticals are escalated
    "template_cosine_threshold": 0.85,      # Alg 3: pairwise similarity to count as "duplicate"
    "template_note_rate_flag": 0.40,        # Alg 3: flag if >40% notes are duplicates
    "repeat_alert_window_days": 30,         # Alg 4: reporting window for repeat alerts
    "repeat_asset_min_count": 10,           # Alg 4: alerts of one category on one asset to call it chronic
    "repeat_pair_concentration": 4.0,       # Alg 4: how many times the entity's median pair volume
    "repeat_pair_max_root_cause_rate": 0.2, # Alg 4: a chronic pair documents almost no root cause
    "repeat_alert_rate_flag": 0.20,         # Alg 4: flag if >20% of alerts are repeat traffic
    "bulk_closure_sigma": 3.0,              # Alg 10: flag closures > mean + 3σ per hour
    "investigation_depth_flag": 0.40,       # Alg 11: flag if avg depth score < 0.4
    "analyst_concentration_flag": 0.80,     # EG-007: one analyst's share of critical/high alerts
    "analyst_concentration_min_analysts": 3,   # EG-007: a one-person SOC is not "imbalanced"
    "analyst_concentration_min_alerts": 20,    # EG-007: minimum sample to judge concentration
    "template_note_sample_cap": 5000,       # Alg 3: max notes per entity in the O(n^2) cosine matrix
                                            # (deterministic sample; keeps a 100k-case CSE tractable)

    # ── Negative Space Detectors ──
    "category_coverage_flag": 0.50,         # Alg 6: flag if coverage < 50%
    "night_gap_flag": 40.0,                 # Alg 7: flag if nightly activity is >40% below the cohort norm
    "night_gap_absolute_floor": 0.05,       # Alg 7: always flag if less than this share of alerts is at night
    "data_quality_minimum": 0.60,           # Min DQ score to run negative-space detectors

    "unmonitored_asset_min": 1,             # Alg 13: flag if >= this many critical assets are not monitored
    "weekend_activity_ratio_flag": 0.15,    # NS-007: weekend alert rate vs weekday rate
    "weekend_min_alerts": 150,              # NS-007: below this the ratio is noise, not a blind spot

    # ── Absolute Benchmark Detectors (peer-independent, Stage 4b) ──
    "benchmark_ack_minutes": 45.0,          # flag if median time-to-ack exceeds this
    "benchmark_close_minutes_critical": 360.0,  # flag if median time-to-close (critical) exceeds this

    # ── Activity / missing-record detectors ──
    "low_activity_deviation_flag": 0.60,    # Alg 14: flag if alerts-per-monitored-asset is >60% below sector median
    "missing_case_rate_flag": 0.50,         # Alg 15: flag if >50% of critical/high alerts have no case record

    # ── Statistical Detectors ──
    "zscore_threshold": 2.5,                # Alg 8: flag if |z| > 2.5 (keeps chance flags low on 11 metrics)
    "min_cohort_size": 3,                   # Alg 8: minimum entities per cohort for peer comparison
    "isolation_forest_contamination": 0.10, # Alg 9: expected fraction of outliers
    "isolation_forest_min_entities": 12,    # Alg 9: below this, skip (too few points to isolate)
    "trend_pvalue_threshold": 0.05,         # Alg 12: flag if trend p-value < 0.05

    # ── Evidence handling ──
    "evidence_sample_cap": 25,              # max evidence record ids stored per finding
                                            # (full count kept in evidence_count; keeps the DB small)

    # ── Incident-management / governance detectors (IM-xxx) ──
    "sla_breach_rate_flag": 0.10,           # IM-001: flag if >10% of closures missed the SLA target
    "sla_misreport_threshold": 0.15,        # IM-001: closure claims compliance its own time contradicts
    "sla_breach_min_cases": 5,              # IM-001: ignore very small samples
    "investigation_gap_flag": 0.30,         # IM-006: cases with no/minimal workflow record
    "severity_softening_flag": 0.25,        # IM-002: flag if >25% of cases sit below their worst alert severity
    "no_root_cause_flag": 0.35,             # IM-003: flag if >35% of significant closures record no root cause
    "no_remediation_flag": 0.40,            # IM-003: ... and no remediation reference
    "rework_loop_flag": 0.30,               # IM-004: flag if >30% of cases show a repeated activity loop
    "reopen_rate_flag": 0.05,               # IM-005: flag if >5% of closures are reopened
    "investigation_event_min": 2,           # IM-006: expected workflow events per case
    "evidence_missing_flag": 0.40,          # IM-006: flag if >40% of workflow events carry no evidence/result
    "risk_accept_no_authority_flag": 0.5,   # IM-007: flag if >50% of accepted risks lack a recorded authority
    "investigation_time_min_minutes": 2.0,  # IM-009: recorded investigation time below which a case
                                            # cannot credibly have been investigated
    "investigation_time_anomaly_flag": 0.30,   # IM-009: share of cases below that floor
    "investigation_time_min_cases": 10,     # IM-009: ignore very small samples

    # ── IM-008 declared-vs-evidence tolerances (supervisor-editable) ──
    # Each key is a declared KPI from the submission cover sheet, mapped to what an
    # examiner will accept as "consistent with the records":
    #   *pct_compliance*  -> tolerance in percentage points of over-claim
    #   *minutes_median*  -> tolerance factor by which the measured median may exceed
    #                        the declared value before the declaration is contradicted
    "declared_kpi_tolerances": {
        "sla_compliance_pct": 8.0,
        "escalation_compliance_pct": 10.0,
        "investigation_records_completeness_pct": 10.0,
        "monitoring_coverage_production_pct": 5.0,
        "critical_alert_ack_minutes_median": 1.5,
        "critical_incident_containment_minutes_median": 1.5,
    },
    # How many contradicted declarations make rule IM-008 a finding at all. The default
    # raises a finding on a single contradicted declaration, because "my own records do
    # not support what I declared" is material on its own. A supervisor who wants to see
    # only mutually reinforcing patterns can raise this to 2 or 3 in Settings.
    "declared_kpi_min_contradictions": 1,
}

# ---------------------------------------------------------------------------
# Absolute benchmark reference values (peer-independent)
#
# These are illustrative reference points of the kind published in annual SOC
# surveys (acknowledgement and containment medians). Replace the numbers below
# with the exact reference table you cite in the submission - the point of this
# detector is that a sector-wide systemic weakness is still flagged even when an
# entity looks "normal" relative to its peers.
# ---------------------------------------------------------------------------

BENCHMARK_REFERENCES = {
    "median_time_to_ack_minutes": 15.0,         # industry median MTTA (critical/high alerts)
    "median_time_to_ack_minutes_critical": 15.0,   # same reference, used by BM-001
    "median_time_to_close_minutes_critical": 240.0,   # industry median MTTR (critical)
    "median_time_to_close_minutes_all": 480.0,
    "escalation_rate_critical": 0.60,           # expected share of criticals escalated
    "reference_label": "Illustrative SOC survey medians (SANS/Ponemon-style)",
}

# ---------------------------------------------------------------------------
# Capability Dimension Weights (supervisor-tunable)
# ---------------------------------------------------------------------------

DEFAULT_CAPABILITY_WEIGHTS = {
    "C1": 1.0,   # Threat Detection
    "C2": 1.0,   # Investigation
    "C3": 1.0,   # Escalation
    "C4": 1.0,   # Incident Response
    "C5": 1.0,   # Security Operations
    "C6": 1.0,   # Governance and Oversight
    "C7": 1.0,   # Operational Discipline
    "C8": 1.0,   # Cyber Resilience
}

CAPABILITY_NAMES = {
    "C1": "Threat Detection",
    "C2": "Investigation",
    "C3": "Escalation",
    "C4": "Incident Response",
    "C5": "Security Operations",
    "C6": "Governance & Oversight",
    "C7": "Operational Discipline",
    "C8": "Cyber Resilience",
}

# Dimension code -> column in ``entity_metrics``. This mapping is the contract between
# the scoring layer, the stored metrics and every page that draws the scorecard, so it
# is defined exactly once here. Four separate copies had drifted apart in the UI layer,
# which is the kind of duplication that silently breaks a chart when a column moves.
CAPABILITY_COLUMNS = {
    "C1": "c1_threat_detection",
    "C2": "c2_investigation",
    "C3": "c3_escalation",
    "C4": "c4_incident_response",
    "C5": "c5_security_operations",
    "C6": "c6_governance",
    "C7": "c7_operational_discipline",
    "C8": "c8_cyber_resilience",
}

# Severity label -> indicator. Used by every page that lists findings, so that a High
# finding looks the same on the Finding Cards, the Examiner queue and the Entity Profile.
SEVERITY_ICON = {"High": "🔴", "Medium": "🟠", "Low": "🟡"}

# ---------------------------------------------------------------------------
# Risk Tier Definitions
# ---------------------------------------------------------------------------

# Tier cut points calibrated so that "Critical Attention" requires a materially
# weak capability profile rather than a single bad indicator.
RISK_TIERS = [
    ("Critical Attention", 32, 101),
    ("Elevated",           24, 32),
    ("Watch",              14, 24),
    ("Satisfactory",        0, 14),
]

TIER_COLORS = {
    "Critical Attention": "#c0392b",
    "Elevated":           "#e08e0b",
    "Watch":              "#c9a90b",
    "Satisfactory":       "#1e8449",
}

# ---------------------------------------------------------------------------
# Sectors (NCIIPC CII sector classification)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Criticality tiers
#
# Canonical tier labels. These are stored verbatim so that "Tier 1 - Critical"
# from a manual form, a submission cover sheet and a bulk import all compare
# equal, and so the register can be filtered by criticality.
# ---------------------------------------------------------------------------

CRITICALITY_TIERS = [
    "Tier 1 - Critical",
    "Tier 2 - High",
    "Tier 3 - Medium",
    "Tier 4 - Low",
]

# Exposure weight per tier, used only by the *supervisory priority* column: two
# entities with identical operational gaps are not equally urgent if one is a
# Tier 1 critical national infrastructure operator and the other is Tier 3.
CRITICALITY_WEIGHTS = {
    "Tier 1 - Critical": 1.00,
    "Tier 2 - High": 0.85,
    "Tier 3 - Medium": 0.70,
    "Tier 4 - Low": 0.55,
}

SECTOR_GROUPS = {
    "Energy & Utilities": ["Power & Energy", "Oil & Gas", "Water & Sanitation"],
    "Finance": ["Banking & Finance"],
    "Communications": ["Telecom", "IT & ITES"],
    "Public Safety & Transport": ["Transport", "Government"],
    "Strategic & Social": ["Strategic & Public Enterprises", "Healthcare"],
}

SECTORS = [
    "Power & Energy",
    "Banking & Finance",
    "Telecom",
    "Transport",
    "Government",
    "Strategic & Public Enterprises",
    "Healthcare",
    "Oil & Gas",
    "Water & Sanitation",
    "IT & ITES",
]

# ---------------------------------------------------------------------------
# Sector Reference Profiles — expected alert categories per sector
# Used by Algorithm 6 (Missing Category Coverage)
# ---------------------------------------------------------------------------

SECTOR_EXPECTED_CATEGORIES = {
    "Power & Energy":    ["malware", "unauthorized_access", "dos", "phishing",
                          "anomalous_traffic", "policy_violation", "insider_threat",
                          "scada_anomaly"],
    "Banking & Finance": ["malware", "unauthorized_access", "phishing", "data_exfiltration",
                          "brute_force", "anomalous_traffic", "insider_threat",
                          "web_attack", "credential_abuse", "fraud_detection"],
    "Telecom":           ["malware", "unauthorized_access", "dos", "anomalous_traffic",
                          "phishing", "dns_anomaly", "brute_force", "policy_violation"],
    "Transport":         ["malware", "unauthorized_access", "phishing", "anomalous_traffic",
                          "policy_violation", "dos", "insider_threat"],
    "Government":        ["malware", "unauthorized_access", "phishing", "data_exfiltration",
                          "anomalous_traffic", "web_attack", "brute_force",
                          "insider_threat", "policy_violation"],
    "Strategic & Public Enterprises": ["malware", "unauthorized_access", "phishing",
                                        "anomalous_traffic", "data_exfiltration",
                                        "insider_threat", "dos", "policy_violation"],
    "Healthcare":        ["malware", "unauthorized_access", "phishing", "data_exfiltration",
                          "ransomware", "anomalous_traffic", "policy_violation"],
    "Oil & Gas":         ["malware", "unauthorized_access", "dos", "phishing",
                          "anomalous_traffic", "scada_anomaly", "policy_violation",
                          "insider_threat"],
    "Water & Sanitation": ["malware", "unauthorized_access", "anomalous_traffic",
                           "scada_anomaly", "policy_violation", "dos"],
    "IT & ITES":         ["malware", "unauthorized_access", "data_exfiltration",
                          "web_attack", "brute_force", "anomalous_traffic",
                          "policy_violation", "credential_abuse"],
}

# ---------------------------------------------------------------------------
# Risk Score Weights for composite calculation
# ---------------------------------------------------------------------------

# Weights of the composite supervisory risk score. Each key names a normalised
# metric gap (see detection/scoring.py::metric_gaps); the weights sum to 1.0 and
# are editable so a supervisor can re-prioritise without touching code.
_raw_weights = {
    "fast_closure_rate":        0.12,
    "crit_no_escalation_rate":  0.12,
    "template_note_rate":       0.09,
    "night_coverage_gap_pct":   0.07,
    "repeat_alert_rate":        0.07,   # share of alerts that are chronic repeat traffic
    "category_coverage_gap":    0.06,   # 1 - expected_category_coverage
    "root_cause_gap":           0.05,   # 1 - root_cause_rate
    "missing_case_rate":        0.07,   # critical/high alerts with no case record
    "activity_deviation":       0.06,   # alert volume vs sector peers, per monitored asset
    "silent_asset_gap":         0.04,   # silent critical assets (saturates at 5)
    # ── incident-management / governance gaps (IM detectors) ──
    "sla_breach_rate":          0.08,   # closures that missed the SLA target
    "severity_softening_rate":  0.06,   # cases recorded below the severity of their own alerts
    "no_root_cause_gap":        0.06,   # significant closures with no root cause on record
    "rework_loop_rate":         0.05,   # investigations sent back through the same activity
    "unmonitored_asset_gap":    0.04,   # critical assets required to be monitored but not
    "investigation_gap":        0.03,   # cases with no investigation workflow at all
    "top_analyst_gap":          0.03,   # critical alerts concentrated on a single analyst
    "weekend_activity_gap":     0.03,   # telemetry rate collapsing at weekends
    "thin_investigation_gap":   0.03,   # cases "investigated" in seconds
}
RISK_SCORE_WEIGHTS = {k: v / sum(_raw_weights.values()) for k, v in _raw_weights.items()}

# ---------------------------------------------------------------------------
# Severity Score Mapping
# ---------------------------------------------------------------------------

def severity_score_from_level(level: str) -> float:
    """Convert a severity label to a numeric score (0-1)."""
    return {"High": 0.9, "Medium": 0.5, "Low": 0.2}.get(level, 0.3)


# The Overall Risk Score blends two independently meaningful indices:
#   * metric_index      - weighted average of normalised operational metric gaps
#   * capability_average - weighted average of the 8 capability scores (findings)
# A finding-only score cannot separate two entities that have no findings, which is
# common for small entities; a metric-only score can miss a severe one-off finding.
# Both halves, and this split, are shown to the supervisor and are config-editable.
RISK_SCORE_BLEND = {
    "metric_index": 0.6,
    "capability_average": 0.4,
}


# ---------------------------------------------------------------------------
# Examiner adjudication (the human-in-the-loop feedback stage)
# ---------------------------------------------------------------------------

ADJUDICATION_VERDICTS = [
    "Confirmed",        # examiner agrees: a real supervisory weakness worth acting on
    "Not material",     # real but below the materiality bar for this assessment
    "Expected",         # explained by a legitimate local condition (not a weakness)
    "Needs more data",  # cannot be decided without asking the entity for clarification
    "False positive",   # the detector was wrong about the underlying evidence
]

# Verdict -> indicator, kept beside the verdict list so a new verdict cannot be added
# without its icon being visible in the same place.
VERDICT_ICON = {
    "Confirmed": "✅",
    "Not material": "➖",
    "Expected": "ℹ️",
    "Needs more data": "❓",
    "False positive": "❌",
}

# Verdicts that stop a finding from counting towards the entity's score. Findings
# that are merely "not material" or "expected" stay visible for audit but no
# longer inflate risk, so the score reflects the examiner's judgement.
NON_COUNTING_VERDICTS = {"False positive", "Expected"}


def is_non_counting(verdict) -> bool:
    """Whether a recorded verdict removes a finding from scoring.

    Matched on the verdict *label*, not on string equality: an examiner may qualify a
    verdict ("False positive - duplicate of INC-4471"), and a qualified verdict must have
    exactly the same effect on the score as the bare one. Seven separate exact-match
    comparisons used to guard this, and every one of them silently let a qualified
    verdict keep inflating risk.
    """
    text = str(verdict or "").strip().lower()
    if not text:
        return False
    return any(re.search(r'\b' + re.escape(label.lower()) + r'\b', text) for label in NON_COUNTING_VERDICTS)


def canonical_verdict_label(verdict: str | None) -> str:
    """The configured verdict label a recorded verdict belongs to, or the text itself.

    Used wherever a verdict is displayed or grouped, so a qualified verdict is still
    counted as, and shown as, the verdict it is.
    """
    text = str(verdict or "").strip()
    if not text:
        return ""
    return next((label for label in ADJUDICATION_VERDICTS if label.lower() in text.lower()),
                text)


def risk_tier_from_score(score: float) -> str:
    """Get risk tier label from a 0-100 risk score (lower bound inclusive)."""
    for tier_name, low, high in RISK_TIERS:
        if low <= score < high:
            return tier_name
    return RISK_TIERS[0][0] if score >= RISK_TIERS[0][1] else RISK_TIERS[-1][0]


# ---------------------------------------------------------------------------
# Config Persistence (load/save thresholds to JSON)
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load thresholds from JSON config file, or return defaults."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                saved = json.load(f)
            # Merge with defaults (in case new keys were added)
            merged = {**DEFAULT_THRESHOLDS, **saved.get("thresholds", {})}
            return merged
        except Exception:
            return DEFAULT_THRESHOLDS.copy()
    return DEFAULT_THRESHOLDS.copy()


def _read_config_file() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_config(thresholds: dict, extra: dict | None = None):
    """Save thresholds to the JSON config file, preserving any other sections.

    ``extra`` may carry additional sections (e.g. capability_weights) so callers
    never silently clobber parts of the config they did not touch.
    """
    data = _read_config_file()
    data["thresholds"] = thresholds
    data.setdefault("capability_weights", DEFAULT_CAPABILITY_WEIGHTS)
    if extra:
        data.update(extra)
    data["last_updated"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    CONFIG_PATH.write_text(json.dumps(data, indent=2))


def load_capability_weights() -> dict:
    """Load capability weights from JSON config file."""
    saved = _read_config_file()
    return {**DEFAULT_CAPABILITY_WEIGHTS, **saved.get("capability_weights", {})}


def load_benchmarks() -> dict:
    """Load absolute-benchmark reference values (editable in the config file)."""
    saved = _read_config_file()
    return {**BENCHMARK_REFERENCES, **saved.get("benchmarks", {})}


def config_snapshot() -> dict:
    """Everything needed to reproduce a detection run (audit / traceability)."""
    return {
        "thresholds": load_config(),
        "capability_weights": load_capability_weights(),
        "benchmarks": load_benchmarks(),
        "risk_weights": RISK_SCORE_WEIGHTS,
        "risk_tiers": RISK_TIERS,
        "criticality_weights": CRITICALITY_WEIGHTS,
    }


def normalise_tier(value) -> str:
    """Map any tier spelling onto a canonical CRITICALITY_TIERS label."""
    if value is None:
        return CRITICALITY_TIERS[2]
    text = str(value).strip().replace("\u2013", "-").replace("\u2014", "-").lower()
    for tier in CRITICALITY_TIERS:
        if tier.lower() in text:
            return tier
    if "critical" in text or text in ("tier1", "tier-1", "1"):
        return CRITICALITY_TIERS[0]
    if "high" in text or text in ("tier2", "tier-2", "2"):
        return CRITICALITY_TIERS[1]
    if "medium" in text or text in ("tier3", "tier-3", "3"):
        return CRITICALITY_TIERS[2]
    if "low" in text or text in ("tier4", "tier-4", "4"):
        return CRITICALITY_TIERS[3]
    return CRITICALITY_TIERS[2]


def criticality_weight(value) -> float:
    return CRITICALITY_WEIGHTS.get(normalise_tier(value), 0.7)
