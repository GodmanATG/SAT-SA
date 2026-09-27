"""
Synthetic CSE submission generator
==================================
Writes one folder per Critical Sector Entity (CSE) containing the batch of
periodic artefacts a supervisor would actually receive.  The field groups follow
the NCIIPC problem statement's data environment one-for-one:

    <Entity Name>/
        entity_profile.json           <- cover sheet: who the entity is, what it
                                         declares about its controls and KPIs
        alerts_export.csv             <- (i)   alert metadata
        cases_dump.json               <- (ii)  case-management records
        investigations_workflow.csv   <- (iii) investigation workflow events
        escalations.csv               <- (iv)  escalation records
        dispositions.csv              <- (v)   disposition & closure records
        inventory.csv                 <- (vi)  asset & system inventory

...plus a repo-level ``ground_truth.json`` recording exactly which weaknesses
were deliberately injected into which entity, which is what makes honest
detector validation possible (precision / recall on the Validation page) without
ever pretending the numbers are real-world expert-review numbers.

Design notes
------------
* **Metadata only.**  No raw logs, no packet captures, no customer data.
* **Fictional entities.**  A synthetic dataset must never imply that a named real
  organisation is underperforming, so every entity name is invented.
* **Volume is a property of the estate, not a flat number.**  Alerts per monitored
  asset per day is the driving parameter, which is what makes "unexpectedly low
  activity" measurable rather than an artefact of how big a report happened to be.
* **The data can contradict itself on purpose.**  Declared KPIs, disposition SLA
  flags and case priorities are generated so that an entity which games its
  metrics produces records that disagree with each other - that disagreement is
  the execution gap the tool is supposed to surface.

CLI:
    python -m synthetic.submissions                          # 27 CSEs, default volume
    python -m synthetic.submissions --companies 4 --days 90  # quick subset
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

APP_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config import SECTOR_EXPECTED_CATEGORIES  # noqa: E402

DEFAULT_OUT = APP_DIR.parent / "submissions"

# ---------------------------------------------------------------------------
# Entity roster - 27 fictional CSEs across all 10 CII sectors.
#
# "anomalies" lists what is deliberately injected (recorded in ground_truth.json);
# the rule ids that should catch each one are in ANOMALY_RULES below.  Six entities
# are deliberately clean so that false positives can be measured, not assumed.
# ---------------------------------------------------------------------------
ROSTER = [
    # ── Power & Energy ────────────────────────────────────────────────────
    dict(name="Deccan PowerGrid Corporation", sector="Power & Energy",
         sub_sector="Transmission & Grid Operations", tier="Tier 1 - Critical",
         city="Hyderabad", state="Telangana", soc_name="Deccan Grid SOC",
         soc_model="In-house", soc_coverage="24x7", analyst_count=22,
         estate=32, anomalies=["rubber_stamping", "kpi_overclaim"],
         note="Grid operator with 11 regional load despatch centres; in-house 24x7 SOC."),
    dict(name="Vidyut Thermal Energy Ltd", sector="Power & Energy",
         sub_sector="Thermal Generation", tier="Tier 2 - High",
         city="Nagpur", state="Maharashtra", soc_name="Vidyut CERT",
         soc_model="Hybrid (MSSP + in-house Tier 3)", soc_coverage="24x7", analyst_count=14,
         estate=26, anomalies=["clean"],
         note="Thermal generation utility; MSSP triage with in-house escalation."),
    dict(name="Narmada Hydro Power Corporation", sector="Power & Energy",
         sub_sector="Hydro Generation", tier="Tier 2 - High",
         city="Bhopal", state="Madhya Pradesh", soc_name="Narmada Hydro Security Cell",
         soc_model="In-house", soc_coverage="Extended hours (06:00-24:00)", analyst_count=8,
         estate=21, anomalies=["slow_ack"],
         note="Hydro stations across the Narmada basin; dam-site OT on isolated segments."),
    # ── Banking & Finance ─────────────────────────────────────────────────
    dict(name="Bharat National Bank", sector="Banking & Finance",
         sub_sector="Retail & Corporate Banking", tier="Tier 1 - Critical",
         city="Mumbai", state="Maharashtra", soc_name="BNB Cyber Defence Centre",
         soc_model="In-house", soc_coverage="24x7", analyst_count=45,
         estate=44, anomalies=["no_escalation", "kpi_overclaim"],
         note="Large retail bank; three-tier 24x7 SOC with a dedicated fraud fusion desk."),
    dict(name="Sahyadri Cooperative Bank", sector="Banking & Finance",
         sub_sector="Cooperative Banking", tier="Tier 3 - Medium",
         city="Pune", state="Maharashtra", soc_name="Sahyadri Shared SOC",
         soc_model="Managed (shared services)", soc_coverage="24x7", analyst_count=6,
         estate=18, anomalies=["clean"],
         note="Regional cooperative bank; SOC delivered through a shared services model."),
    dict(name="Pramukh Financial Services", sector="Banking & Finance",
         sub_sector="Payments & Clearing", tier="Tier 1 - Critical",
         city="Ahmedabad", state="Gujarat", soc_name="Pramukh Payments SOC",
         soc_model="In-house", soc_coverage="24x7", analyst_count=26,
         estate=30, anomalies=["severity_softening"],
         note="Payment switch and clearing house operator; very high transaction-criticality estate."),
    # ── Telecom ───────────────────────────────────────────────────────────
    dict(name="Astra Telecom Networks", sector="Telecom",
         sub_sector="Mobile & Fixed Line", tier="Tier 1 - Critical",
         city="Bengaluru", state="Karnataka", soc_name="Astra NOC-SOC",
         soc_model="In-house", soc_coverage="24x7", analyst_count=38,
         estate=48, anomalies=["template_notes"],
         note="Pan-India operator; very high alert volume handled by a tiered triage model."),
    dict(name="BharatLink Infocom", sector="Telecom",
         sub_sector="Broadband & Enterprise Connectivity", tier="Tier 2 - High",
         city="Chennai", state="Tamil Nadu", soc_name="BharatLink CERT",
         soc_model="In-house", soc_coverage="24x7", analyst_count=12,
         estate=24, anomalies=["clean"],
         note="Enterprise connectivity provider with a small but well-documented SOC."),
    dict(name="Trikon Wireless Ltd", sector="Telecom",
         sub_sector="Tower Infrastructure", tier="Tier 2 - High",
         city="Jaipur", state="Rajasthan", soc_name="Trikon Tower Security Ops",
         soc_model="Managed (MSSP)", soc_coverage="24x7", analyst_count=9,
         estate=29, anomalies=["repeat_alerts"],
         note="Passive tower and backhaul infrastructure across eight states."),
    # ── Transport ─────────────────────────────────────────────────────────
    dict(name="National Metro Rail Corp", sector="Transport",
         sub_sector="Urban Rail Transit", tier="Tier 1 - Critical",
         city="Delhi", state="Delhi", soc_name="NMRC Security Operations",
         soc_model="In-house", soc_coverage="24x7", analyst_count=20,
         estate=34, anomalies=["bulk_closure"],
         note="Metro rail operator; integrated OT and IT monitoring across stations and depots."),
    dict(name="Konkan Port Authority", sector="Transport",
         sub_sector="Ports & Maritime Logistics", tier="Tier 2 - High",
         city="Mangaluru", state="Karnataka", soc_name="KPA Cyber Cell",
         soc_model="Hybrid (MSSP + in-house Tier 3)", soc_coverage="Business hours (08:00-20:00)",
         analyst_count=7, estate=25, anomalies=["night_gap", "long_closure", "weekend_gap"],
         note="Major port; business-hours SOC staffing with on-call escalation outside shift."),
    dict(name="Eastern Freight Corridor Corp", sector="Transport",
         sub_sector="Rail Freight & Logistics", tier="Tier 2 - High",
         city="Kolkata", state="West Bengal", soc_name="EFCC Integrated Control Centre",
         soc_model="In-house", soc_coverage="24x7", analyst_count=11,
         estate=23, anomalies=["no_root_cause"],
         note="Dedicated freight corridor; signalling and track-side telemetry estate."),
    # ── Government ────────────────────────────────────────────────────────
    dict(name="Directorate of Citizen Services", sector="Government",
         sub_sector="e-Governance Service Delivery", tier="Tier 2 - High",
         city="Bhopal", state="Madhya Pradesh", soc_name="DCS Monitoring Cell",
         soc_model="In-house", soc_coverage="Business hours (09:00-18:00)", analyst_count=5,
         estate=20, anomalies=["night_gap"],
         note="State e-governance services portal; monitoring confined to office hours."),
    dict(name="State Data Centre Authority", sector="Government",
         sub_sector="Government Cloud & Hosting", tier="Tier 1 - Critical",
         city="Gandhinagar", state="Gujarat", soc_name="SDC Cyber Security Cell",
         soc_model="In-house", soc_coverage="24x7", analyst_count=16,
         estate=36, anomalies=["clean"],
         note="Hosts citizen-facing state applications on shared infrastructure."),
    dict(name="Municipal Services Board", sector="Government",
         sub_sector="Urban Local Body Services", tier="Tier 3 - Medium",
         city="Indore", state="Madhya Pradesh", soc_name="MSB IT Security Desk",
         soc_model="Managed (MSSP)", soc_coverage="Business hours (09:00-18:00)", analyst_count=3,
         estate=16, anomalies=["low_activity"],
         note="Municipal property tax, water billing and grievance platforms."),
    # ── Strategic & Public Enterprises ────────────────────────────────────
    dict(name="Bharat Steel & Mining Ltd", sector="Strategic & Public Enterprises",
         sub_sector="Steel & Heavy Industry", tier="Tier 1 - Critical",
         city="Bokaro", state="Jharkhand", soc_name="BSM Integrated Security Centre",
         soc_model="In-house", soc_coverage="24x7", analyst_count=19,
         estate=41, anomalies=["silent_critical_assets"],
         note="Integrated steel plant with a large distributed control system estate."),
    dict(name="Indraprastha Defence Systems", sector="Strategic & Public Enterprises",
         sub_sector="Defence Manufacturing", tier="Tier 1 - Critical",
         city="Kanpur", state="Uttar Pradesh", soc_name="IDS Secure Operations Centre",
         soc_model="In-house", soc_coverage="24x7", analyst_count=24,
         estate=27, anomalies=["clean"],
         note="Defence production unit operating an air-gapped engineering network."),
    dict(name="Rashtriya Machine Tools Ltd", sector="Strategic & Public Enterprises",
         sub_sector="Machine Tools & Precision Engineering", tier="Tier 2 - High",
         city="Coimbatore", state="Tamil Nadu", soc_name="RMT Cyber Operations",
         soc_model="Hybrid (MSSP + in-house Tier 3)", soc_coverage="Extended hours (06:00-24:00)",
         analyst_count=9, estate=22,
         anomalies=["rework_loops", "thin_investigation_time"],
         note="Precision manufacturing; CNC and MES estate with frequent analyst turnover."),
    # ── Healthcare ────────────────────────────────────────────────────────
    dict(name="Sanjeevani Healthcare Network", sector="Healthcare",
         sub_sector="Hospitals & Diagnostic Chains", tier="Tier 2 - High",
         city="Kolkata", state="West Bengal", soc_name="Sanjeevani SOC (MSSP)",
         soc_model="Managed (MSSP)", soc_coverage="Business hours with on-call", analyst_count=5,
         estate=19, anomalies=["missing_categories", "template_notes"],
         note="Hospital chain; partially outsourced SOC with a single analyst shift."),
    dict(name="Arogya Medical Institute", sector="Healthcare",
         sub_sector="Teaching Hospital & Research", tier="Tier 3 - Medium",
         city="Chandigarh", state="Chandigarh", soc_name="AMI IT Security Team",
         soc_model="In-house", soc_coverage="Business hours (08:00-20:00)", analyst_count=4,
         estate=17, anomalies=["no_investigation_records"],
         note="Teaching hospital; monitoring limited to clinical and billing applications."),
    # ── Oil & Gas ─────────────────────────────────────────────────────────
    dict(name="Kaveri Refineries Ltd", sector="Oil & Gas",
         sub_sector="Refining & Cross-Country Pipelines", tier="Tier 1 - Critical",
         city="Jamnagar", state="Gujarat", soc_name="Kaveri Refinery SOC",
         soc_model="In-house", soc_coverage="24x7", analyst_count=18,
         estate=38, anomalies=["no_escalation", "missing_case_records", "kpi_overclaim"],
         note="Refinery complex plus cross-country pipeline SCADA footprint."),
    dict(name="Sagar Offshore Gas Ltd", sector="Oil & Gas",
         sub_sector="Offshore Exploration & Production", tier="Tier 1 - Critical",
         city="Mumbai", state="Maharashtra", soc_name="Sagar Marine Cyber Ops",
         soc_model="Hybrid (MSSP + in-house Tier 3)", soc_coverage="24x7", analyst_count=13,
         estate=24, anomalies=["slow_ack", "risk_accept_no_authority", "analyst_overload"],
         note="Offshore platforms linked by satellite backhaul; ack times constrained by link latency."),
    # ── Water & Sanitation ────────────────────────────────────────────────
    dict(name="Jal Jeevan Utilities Board", sector="Water & Sanitation",
         sub_sector="Urban Water Supply", tier="Tier 2 - High",
         city="Jaipur", state="Rajasthan", soc_name="JJUB Monitoring Cell",
         soc_model="In-house", soc_coverage="Business hours (07:00-19:00)", analyst_count=4,
         estate=18, anomalies=["low_activity"],
         note="City water utility; SCADA estate monitored by a four-person team."),
    dict(name="Metro Water Supply Corporation", sector="Water & Sanitation",
         sub_sector="Water Treatment & Distribution", tier="Tier 2 - High",
         city="Kochi", state="Kerala", soc_name="MWSC OT-IT Security",
         soc_model="Managed (MSSP)", soc_coverage="Extended hours (06:00-22:00)", analyst_count=6,
         estate=28, anomalies=["silent_critical_assets", "unmonitored_assets"],
         note="Treatment plants and pumping stations distributed across the district."),
    # ── IT & ITES ─────────────────────────────────────────────────────────
    dict(name="CloudBharat Services Ltd", sector="IT & ITES",
         sub_sector="Managed Hosting & Cloud Services", tier="Tier 2 - High",
         city="Noida", state="Uttar Pradesh", soc_name="CloudBharat Security Operations",
         soc_model="In-house", soc_coverage="24x7", analyst_count=15,
         estate=33, anomalies=["repeat_alerts", "sla_gaming"],
         note="Managed service provider hosting government and enterprise workloads."),
    dict(name="Nimbus Data Centres", sector="IT & ITES",
         sub_sector="Colocation & Data Centre Services", tier="Tier 2 - High",
         city="Pune", state="Maharashtra", soc_name="Nimbus NOC-SOC",
         soc_model="In-house", soc_coverage="24x7", analyst_count=12,
         estate=29, anomalies=["clean"],
         note="Tier III colocation provider with contractual monitoring obligations."),
    dict(name="BharatPay Technologies", sector="IT & ITES",
         sub_sector="Payment Technology Platform", tier="Tier 1 - Critical",
         city="Hyderabad", state="Telangana", soc_name="BharatPay SecOps",
         soc_model="In-house", soc_coverage="24x7", analyst_count=21,
         estate=31, anomalies=["reopened_cases"],
         note="UPI-scale payment platform; high-volume automated detection pipeline."),
]

# Anomaly -> (expected rule ids, human-readable description).
# The rule ids are the *contract* between generator and detectors: the Validation
# page scores detectors against exactly these expectations.
ANOMALY_RULES = {
    # execution gaps
    "rubber_stamping":   (["EG-001"], "Critical/high alerts acknowledged and closed within "
                                      "minutes across the whole estate"),
    "no_escalation":     (["EG-002"], "Critically severe alerts almost never reach an "
                                      "escalation record"),
    "template_notes":    (["EG-003"], "Investigation notes are near-identical template fills"),
    "repeat_alerts":     (["EG-004"], "A few asset/category pairs keep re-alerting with no "
                                      "root cause on file"),
    "bulk_closure":      (["EG-005"], "Large batches of alerts closed inside a single hour"),
    "analyst_overload":  (["EG-007"], "One analyst carries almost every critical alert - "
                                       "triage concentrated on a single person"),
    # negative space
    "missing_categories": (["NS-002"], "Most alert categories expected for the sector never appear"),
    "night_gap":          (["NS-003"], "No alert activity at all outside office hours - "
                                       "monitoring blind spot"),
    "silent_critical_assets": (["NS-001"], "Critical assets present in inventory but generating "
                                           "zero telemetry or alerts"),
    "low_activity":       (["NS-004"], "Alert volume far below peers for the size of the "
                                       "monitored estate"),
    "missing_case_records": (["NS-005"], "Critical alerts with no investigation record and no "
                                         "escalation trail"),
    "unmonitored_assets": (["NS-006"], "Critical assets declared for monitoring but not "
                                       "effectively monitored"),
    "weekend_gap":        (["NS-007"], "Telemetry stops at the weekend - Saturday/Sunday "
                                       "blind spot"),
    # absolute benchmarks
    "long_closure":       (["BM-002"], "Containment on critical incidents runs far beyond the "
                                       "reference median"),
    "slow_ack":           (["BM-001"], "Median acknowledgement time well outside published SOC "
                                       "benchmarks"),
    # incident management / governance (new)
    "sla_gaming":         (["IM-001"], "Disposition records claim SLA compliance on closures "
                                       "that demonstrably missed the target"),
    "severity_softening": (["IM-002"], "Case records are logged at a lower severity than the "
                                       "alerts they were opened from"),
    "no_root_cause":      (["IM-003"], "Significant incidents closed with no root cause or "
                                       "remediation reference recorded"),
    "rework_loops":       (["IM-004"], "Investigations cycle repeatedly through the same "
                                       "workflow activities"),
    "reopened_cases":     (["IM-005"], "A material share of closures are later reopened"),
    "no_investigation_records": (["IM-006"], "Cases closed with no (or one-line) investigation "
                                             "workflow record"),
    "risk_accept_no_authority": (["IM-007"], "Risks accepted without a recorded accepting "
                                             "authority"),
    "kpi_overclaim":      (["IM-008"], "Declared performance metrics are contradicted by the "
                                       "entity's own operational records"),
    "thin_investigation_time": (["IM-009"], "Cases marked investigated whose total recorded "
                                             "investigation time is under two minutes"),
    # control
    "clean":              ([], "No weakness injected - control entity for false-positive "
                               "measurement"),
}

SEVERITIES = ["critical", "high", "medium", "low"]
SEV_P = [0.04, 0.16, 0.40, 0.40]
SEV_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}

SEV_CLOSE_MEDIAN = {"critical": 180, "high": 120, "medium": 240, "low": 300}
SEV_ACK_MEDIAN = {"critical": 10, "high": 14, "medium": 45, "low": 75}
# Response target per priority (minutes) - the SLA the entity is measured against.
SLA_TARGET_BY_PRIORITY = {"P1": 240, "P2": 480, "P3": 1440, "P4": 2880}

DISPOSITION_KINDS = ["true_positive", "false_positive", "benign", "risk_accepted"]
CLOSURE_TEAMS = ["SOC Tier 1", "SOC Tier 2", "SOC Tier 3", "Incident Response", "MSSP Desk"]

# ── alert naming per category (so alert_name/alert_type look like real detections) ──
CATEGORY_DETECTIONS = {
    "malware": ("Malicious binary execution", "malware_execution", "EDR-SIG-1042"),
    "unauthorized_access": ("Unauthorised access attempt", "access_violation", "IAM-RULE-2207"),
    "phishing": ("Phishing infrastructure contact", "email_threat", "MAIL-GW-3310"),
    "dos": ("Denial of service traffic pattern", "network_flood", "NET-DOS-1180"),
    "anomalous_traffic": ("Anomalous outbound traffic volume", "network_anomaly", "NDR-ANOM-771"),
    "policy_violation": ("Security policy violation", "policy_breach", "POL-COMP-4502"),
    "insider_threat": ("Suspicious privileged activity", "insider_activity", "UBA-BEHAV-610"),
    "scada_anomaly": ("Industrial control anomaly", "ot_anomaly", "OT-IDS-2201"),
    "data_exfiltration": ("Possible data exfiltration", "data_loss", "DLP-EGRESS-950"),
    "brute_force": ("Repeated authentication failures", "credential_attack", "AD-BRUTE-330"),
    "web_attack": ("Web application attack attempt", "exploit_attempt", "WAF-APPL-8120"),
    "credential_abuse": ("Credential abuse pattern", "identity_anomaly", "IAM-UEBA-540"),
    "fraud_detection": ("Transaction fraud pattern", "fraud", "FRAUD-ML-770"),
    "ransomware": ("Ransomware precursor behaviour", "ransomware", "EDR-RANS-2001"),
    "vulnerability_exploitation": ("Exploitation of known vulnerability", "exploit_attempt",
                                   "VM-EXPL-660"),
}
ALERT_SOURCES = ["SIEM-Correlation", "EDR", "Network IDS", "Firewall", "Cloud Security Posture",
                 "Identity Analytics", "OT-IDS", "DLP"]
DETECTION_SOURCES = ["Correlation rule", "Behavioural analytics", "Signature", "Threat intel match",
                     "Anomaly model", "Manual review"]
COUNTRIES = ["IN", "IN", "IN", "SG", "US", "NL", "RU", "CN", "AE", "DE", "RU", "CN"]
USER_IDS = ["svc_backup", "svc_web", "a.sharma", "r.iyer", "p.nair", "m.khan", "j.das",
            "admin_local", "svc_batch", "k.rao"]
PROCS = ["powershell.exe", "cmd.exe", "rundll32.exe", "wmic.exe", "svchost.exe", "curl",
         "python.exe", "mimikatz.exe", "certutil.exe", "lsass.exe"]

TEMPLATE_NOTES = [
    "Investigated {cat} alert on {host}. Finding: benign activity. Disposition: {disp}.",
    "Alert reviewed. No action required. Closing as false positive.",
    "Investigated and resolved. Standard procedure followed.",
    "Alert triaged. No threat detected. Case closed.",
    "Checked alert on {host}. Looks like expected activity. Closing.",
]

# Substantive notes are *composed* from independent clause pools rather than picked
# from a fixed list: a handful of fixed sentences would make every good note look
# like every other good note to a similarity detector, which is exactly the
# distinction the tool is supposed to draw.
NOTE_OPEN = [
    "Correlated the {cat} alert on {host} against endpoint and network telemetry",
    "Triaged the {cat} detection raised on {host} by the {source} sensor",
    "Reviewed the {cat} signal on {host} with the asset owner",
    "Reconstructed the activity behind the {cat} alert on {host} from available records",
    "Analysed {proc} execution as the trigger for the {cat} alert on {host}",
    "Validated the {cat} coverage gap reported on {host} during the daily review",
]
NOTE_EVIDENCE = [
    "evidence showed {proc} establishing an outbound session to {ip}:{port}",
    "the process tree included {proc} launched from a temporary directory",
    "proxy and authentication records pointed to {ip} over port {port}",
    "hash comparison placed the payload in a low-prevalence cluster on port {port}",
    "a scheduled task invoked {proc} outside the approved change window",
    "correlation with firewall logs attributed the session to {ip}",
]
NOTE_ACTION = [
    "the host was isolated and the affected service account credential rotated",
    "a tuning request was raised and the detection annotated with the false-positive reason",
    "the endpoint was reimaged and the adjacent network segment swept for lateral movement",
    "the configuration was corrected and re-verified against the hardened baseline",
    "the identity team disabled the account and preserved the session artefacts for review",
    "enhanced monitoring was applied to the asset for the next 72 hours",
    "the finding was escalated to the incident manager with the supporting timeline attached",
]
NOTE_CLOSE = [
    "Case closed after asset-owner confirmation.",
    "Root cause recorded and remediation ticket REM-{n} raised.",
    "Closed as {finding}; no further action required.",
    "Monitoring period completed with no recurrence observed.",
    "Closed with residual risk accepted by the risk owner.",
    "Retained open pending software-owner confirmation of the unsigned binary.",
]
RICH_FINDINGS = ["expected behaviour after user confirmation", "misconfiguration",
                 "actual threat, contained", "policy violation", "scanning activity",
                 "legitimate administrative activity"]

# Investigation workflow vocabulary (process mining vocabulary)
WORKFLOW_FOR_CASE = ["Case created", "Initial triage", "Evidence collection", "Enrichment",
                     "Scope assessment", "Containment action", "Root cause analysis",
                     "Remediation", "Verification", "Case closure"]
ACTIVITY_SUBTYPES = {
    "Case created": ["auto", "from alert"],
    "Initial triage": ["SIEM query", "EDR triage", "ticket review"],
    "Evidence collection": ["log excerpt", "hash lookup", "process tree"],
    "Enrichment": ["threat intel", "asset context", "identity context"],
    "Scope assessment": ["lateral movement check", "data impact review"],
    "Containment action": ["host isolation", "account disable", "ACL block"],
    "Root cause analysis": ["timeline reconstruction", "control review"],
    "Remediation": ["patching", "configuration change", "credential rotation"],
    "Verification": ["re-scan", "monitoring check"],
    "Case closure": ["disposition recorded", "documentation complete"],
}
EVIDENCE_TYPES = ["log excerpt", "hash", "process tree", "network capture summary",
                  "identity record", "asset record", "threat intel hit"]
ACTION_RESULTS = ["confirmed", "not confirmed", "inconclusive", "escalated", "closed",
                  "additional data requested"]


def _iso(ts: datetime) -> str:
    return ts.replace(microsecond=0).isoformat()


def _analysts(rng, n):
    return [f"ANL-{i + 1:03d}" for i in range(n)]


def _pick_ip(rng) -> str:
    return (f"{int(rng.integers(1, 224))}.{int(rng.integers(0, 256))}."
            f"{int(rng.integers(0, 256))}.{int(rng.integers(1, 255))}")


def compose_note(rng, cat, host, proc, source_ip, port) -> str:
    """Build a substantive, lexically varied investigation note."""
    return " ".join([
        str(rng.choice(NOTE_OPEN)).format(cat=cat.replace("_", " "), host=host, proc=proc,
                                          source="SIEM"),
        "- " + str(rng.choice(NOTE_EVIDENCE)).format(proc=proc, ip=source_ip, port=port),
        str(rng.choice(NOTE_ACTION)) + ".",
        str(rng.choice(NOTE_CLOSE)).format(n=int(rng.integers(1000, 9999)),
                                           finding=rng.choice(RICH_FINDINGS)),
    ])


# ---------------------------------------------------------------------------
# Asset inventory
# ---------------------------------------------------------------------------

def _make_assets(rng, idx, sector, n_assets, flags):
    """Asset inventory, including the assets that never generate alerts.

    Returns (DataFrame, silent_ids, unmonitored_ids).
    """
    ot_sector = sector in ("Power & Energy", "Oil & Gas", "Transport", "Water & Sanitation",
                           "Strategic & Public Enterprises", "Healthcare")
    assets = []
    for i in range(n_assets):
        is_crit = rng.random() < 0.24
        if is_crit and ot_sector and rng.random() < 0.5:
            asset_class, atype, bu = "critical_ot", "plc_hmi", "OT Operations"
        elif is_crit:
            asset_class, atype, bu = "critical_it", "server", "IT Infrastructure"
        else:
            asset_class = "standard_it"
            atype = str(rng.choice(["server", "workstation", "network_device", "database",
                                    "web_app", "storage"]))
            bu = str(rng.choice(["IT Infrastructure", "Business Operations", "Shared Services"]))
        assets.append(dict(
            asset_id=f"AST-{idx:02d}-{i:03d}",
            asset_name=f"{atype.replace('_', ' ').title()} {i:03d}",
            hostname=f"host-{int(rng.integers(1000, 9999))}",
            ip_address=f"10.{idx}.{i // 250}.{i % 250 + 1}",
            asset_type=atype,
            asset_class=asset_class,
            criticality_tier="critical" if is_crit else str(rng.choice(["standard", "standard", "high"])),
            business_criticality="high" if is_crit else str(rng.choice(["medium", "low"])),
            environment=str(rng.choice(["production", "production", "production", "DR"])),
            business_unit=bu,
            location=str(rng.choice(["DC-Primary", "DC-Secondary", "HQ", "Regional Office",
                                    "Plant Site", "Field Station"])),
            data_classification=str(rng.choice(["internal", "confidential", "restricted"])),
            owner=f"owner-{int(rng.integers(1, 40)):02d}",
            system_owner=f"sysowner-{int(rng.integers(1, 25)):02d}",
            monitoring_required="true",
            monitoring_status="active",
            monitoring_source=("OT sensor" if asset_class == "critical_ot" else
                               str(rng.choice(["SIEM agent", "EDR agent", "syslog forwarder",
                                               "cloud connector", "network tap"]))),
            expected_alert_frequency=0.0,   # filled from observed volume in _finalise_inventory
            actual_alert_count=0,
            last_telemetry_timestamp="",
            last_scan_timestamp="",
            vulnerability_status=str(rng.choice(["patched", "patched", "pending_patch",
                                                "exception_approved"])),
        ))

    silent_ids, unmonitored_ids = set(), set()

    # Negative space: critical OT/IT assets that exist but never appear in any alert.
    if "silent_critical_assets" in flags:
        for i in range(int(rng.integers(4, 8))):
            aid = f"SILENT-CRIT-{idx:02d}-{i:02d}"
            assets.append(dict(
                asset_id=aid, asset_name=f"Safety Instrumented Controller {i:02d}",
                hostname=f"ot-sis-{int(rng.integers(10, 99))}",
                ip_address=f"10.{idx}.250.{i + 10}", asset_type="plc_hmi",
                asset_class="critical_ot", criticality_tier="critical",
                business_criticality="high", environment="production", business_unit="OT Operations",
                location="Plant Site", data_classification="restricted",
                owner="ot-owner-01", system_owner="sysowner-ot-01",
                monitoring_required="true", monitoring_status="active",
                monitoring_source="OT sensor", expected_alert_frequency=0.0,
                actual_alert_count=0, last_telemetry_timestamp="", last_scan_timestamp="",
                vulnerability_status="pending_patch"))
            silent_ids.add(aid)

    # "Controls deployed but not effectively monitored".
    if "unmonitored_assets" in flags:
        for i in range(int(rng.integers(3, 6))):
            aid = f"UNMON-CRIT-{idx:02d}-{i:02d}"
            assets.append(dict(
                asset_id=aid, asset_name=f"Pump Station Controller {i:02d}",
                hostname=f"pump-ctrl-{int(rng.integers(10, 99))}",
                ip_address=f"10.{idx}.251.{i + 10}", asset_type="plc_hmi",
                asset_class="critical_ot", criticality_tier="critical",
                business_criticality="high", environment="production", business_unit="OT Operations",
                location="Field Station", data_classification="restricted",
                owner="ot-owner-02", system_owner="sysowner-ot-02",
                monitoring_required="true", monitoring_status="not_configured",
                monitoring_source="", expected_alert_frequency=0.0,
                actual_alert_count=0, last_telemetry_timestamp="", last_scan_timestamp="",
                vulnerability_status="unknown"))
            unmonitored_ids.add(aid)

    return pd.DataFrame(assets), silent_ids, unmonitored_ids


# ---------------------------------------------------------------------------
# Declared profile (cover sheet)
# ---------------------------------------------------------------------------

def _declared_profile(comp, idx, flags, start_date, end_date, counts, measured):
    """Cover sheet, including the controls/KPIs the entity *claims*.

    The KPIs are where an execution gap becomes visible: an entity can declare 98%
    SLA compliance while its own disposition records show the opposite. Honest
    entities declare what they actually measured (with reporting noise); only the
    entities whose profile includes ``kpi_overclaim`` declare a flattering number.
    Because the declared value is derived from the same generated records, the
    declared-vs-evidence comparison (IM-008) has a real, traceable basis.
    """
    overclaim = "kpi_overclaim" in flags
    name_slug = comp["name"].split()[0].lower()
    noise = np.random.default_rng(idx + 101)

    def honest(key, default, spread=0.02, floor=0.0, ceiling=100.0, digits=1):
        value = measured.get(key, default)
        if value is None:
            value = default
        jittered = float(value) * (1 + float(noise.uniform(-spread, spread)))
        return round(min(ceiling, max(floor, jittered)), digits)

    declared_controls = [
        "Documented incident management procedure approved by CISO",
        "24x7 security monitoring where declared in SOC coverage",
        "Critical alert escalation matrix with named owners",
        "Root cause analysis mandatory for P1/P2 incidents",
        "SLA measurement reported monthly to the board",
        "Asset inventory reviewed every quarter",
    ]
    if overclaim:
        declared_controls += [
            "Independent assurance review of SOC records completed in the reporting period",
            "Automated tuning suppression for known benign detections in production",
        ]

    declared_kpis = {
        "sla_compliance_pct": 99.2 if overclaim else honest("sla_compliance_pct", 93.0, 0.02),
        "critical_alert_ack_minutes_median": 3 if overclaim else honest(
            "critical_alert_ack_minutes_median", 15.0, 0.10, floor=0.5, ceiling=600.0),
        "critical_incident_containment_minutes_median": 95 if overclaim else honest(
            "critical_incident_containment_minutes_median", 240.0, 0.10,
            floor=5.0, ceiling=5000.0),
        "escalation_compliance_pct": 98.5 if overclaim else honest(
            "escalation_compliance_pct", 85.0, 0.03),
        "investigation_records_completeness_pct": 97 if overclaim else honest(
            "investigation_records_completeness_pct", 92.0, 0.02),
        "monitoring_coverage_production_pct": 99.5 if overclaim else honest(
            "monitoring_coverage_production_pct", 94.0, 0.02),
        "reporting_period": f"{start_date.date()} to {end_date.date()}",
    }

    return dict(
        cse_id=f"CSE-{idx + 1001:04d}",
        entity_name=comp["name"],
        sector=comp["sector"],
        sub_sector=comp["sub_sector"],
        criticality_tier=comp["tier"],
        cin=f"U{int(np.random.default_rng(idx + 3).integers(10000, 99999))}MH2001PLC"
            f"{int(np.random.default_rng(idx + 5).integers(100000, 999999))}",
        registered_address=f"{comp['city']}, {comp['state']}",
        hq=comp["city"],
        state=comp["state"],
        website=f"https://www.{name_slug}.example.in",
        nciipc_id=f"NCIIPC-CSE-{idx + 1001}",
        sector_regulator=("Ministry of Power" if comp["sector"] == "Power & Energy" else
                          "RBI / SEBI" if comp["sector"] == "Banking & Finance" else
                          "DoT" if comp["sector"] == "Telecom" else
                          "Ministry of Ports, Shipping & Waterways" if comp["sector"] == "Transport" else
                          "MeitY / State IT Department" if comp["sector"] == "Government" else
                          "Ministry of Health & Family Welfare" if comp["sector"] == "Healthcare" else
                          "Ministry of Petroleum & Natural Gas" if comp["sector"] == "Oil & Gas" else
                          "Ministry of Jal Shakti" if comp["sector"] == "Water & Sanitation" else
                          "MeitY"),
        soc=dict(name=comp["soc_name"], model=comp["soc_model"],
                 coverage=comp["soc_coverage"], analyst_count=comp["analyst_count"],
                 ticketing_platform="ServiceNow-style incident module",
                 siem_platform="On-premise SIEM (log archive retained 180 days)"),
        contact_person=f"Head - {comp['soc_name']}",
        contact_email=f"soc-contact@{name_slug}.example.in",
        contact_phone=f"+91-{int(np.random.default_rng(idx + 29).integers(11, 99))}-"
                      f"{int(np.random.default_rng(idx + 31).integers(10000000, 99999999))}",
        review_period_start=_iso(start_date),
        review_period_end=_iso(end_date),
        declared_controls=declared_controls,
        declared_kpis=declared_kpis,
        declared_estate=dict(
            monitored_assets=counts.get("assets", 0),
            production_critical_assets=counts.get("critical_assets", 0),
            ot_assets=counts.get("ot_assets", 0),
        ),
        submitted_records=counts,
        submission_note=comp["note"],
        prepared_by=f"SOC Compliance Desk, {comp['name']}",
    )


# ---------------------------------------------------------------------------
# Per-entity generation
# ---------------------------------------------------------------------------

def generate_entity(comp: dict, idx: int, *, days: int, alerts_per_asset_day: float,
                    end_date: datetime, seed: int):
    """Generate one entity's submission files. Returns (files dict, ground truth row)."""
    rng = np.random.default_rng(seed + idx * 977)
    flags = set(comp["anomalies"])

    assets_df, silent_ids, unmonitored_ids = _make_assets(
        rng, idx, comp["sector"], int(comp["estate"]), flags)
    not_monitoring = silent_ids | unmonitored_ids
    monitored = assets_df[~assets_df.asset_id.isin(not_monitoring)]
    monitored_ids = monitored.asset_id.tolist()
    analysts = _analysts(rng, int(comp["analyst_count"]))
    # The analyst who carries almost every critical alert when workload is concentrated
    # on one person (rule EG-007). Alert ownership is a normal ticketing field, so this
    # is a realistic single point of failure rather than an artificial marker.
    overload_analyst = analysts[0]
    asset_lookup = assets_df.set_index("asset_id")

    # ── categories expected for this sector ──
    expected_cats = list(SECTOR_EXPECTED_CATEGORIES.get(comp["sector"], ["malware", "phishing"]))
    present_cats = list(expected_cats)
    if "missing_categories" in flags:
        n_drop = max(2, int(round(len(present_cats) * 0.6)))
        drop = set(rng.choice(present_cats, size=n_drop, replace=False).tolist())
        present_cats = [c for c in present_cats if c not in drop]
        rng.shuffle(present_cats)

    start_date = end_date - timedelta(days=days)
    total_seconds = int((end_date - start_date).total_seconds())

    # Volume is a property of the estate: alerts per monitored asset per day.
    estate_size = max(1, len(monitored))
    rate = alerts_per_asset_day * (0.15 if "low_activity" in flags else float(rng.uniform(0.85, 1.25)))
    n_target = int(round(estate_size * rate * days))

    # ── arrivals: random instants reshaped onto a diurnal curve ──
    hour_weights = np.array([1.0] * 24)
    hour_weights[8:19] *= 1.25
    hour_weights[[0, 1, 2, 3, 4, 5]] *= 0.95
    if "night_gap" in flags:
        hour_weights[[22, 23, 0, 1, 2, 3, 4, 5]] = 0.0
    hour_weights /= hour_weights.sum()

    rng_offsets = rng.integers(0, total_seconds, size=n_target)
    rng_hours = rng.choice(24, size=n_target, p=hour_weights).tolist()

    alerts, cases, escalations, events, dispositions = [], [], [], [], []
    repeat_pairs = [(a, c) for a in monitored_ids[:4] for c in present_cats[:3]]

    bulk_close_hour = None
    if "bulk_closure" in flags:
        bulk_close_hour = start_date + timedelta(days=int(rng.integers(30, max(31, days - 30))),
                                                 hours=int(rng.integers(22, 24)))
    # asset -> alert counter, used later for actual_alert_count
    asset_counts = {a: 0 for a in monitored_ids}
    # case counter used to build ids
    case_seq = 0

    for k in range(n_target):
        # The random offset picks the day; the sampled hour then *replaces* the clock
        # hour, which is what makes the diurnal (and night-blind-spot) profile real.
        created = (start_date + timedelta(seconds=int(rng_offsets[k]))).replace(
            hour=int(rng_hours[k]), minute=int(rng.integers(0, 60)),
            second=int(rng.integers(0, 60)), microsecond=0)
        if created >= end_date:
            continue
        # Weekend blind spot: the weekend days produce nothing at all. Volume is
        # preserved by shifting those arrivals onto the following weekday, so the
        # entity is not accidentally also "low activity".
        if "weekend_gap" in flags and created.weekday() >= 5:
            created = created + timedelta(days=(7 - created.weekday()))
            if created >= end_date:
                created = created - timedelta(days=7)

        sev = str(rng.choice(SEVERITIES, p=SEV_P))

        # ── asset + category selection (with chronic-repeat injection) ──
        if "repeat_alerts" in flags and k % 3 == 0 and repeat_pairs:
            asset_id, cat = repeat_pairs[int(rng.integers(0, len(repeat_pairs)))]
            asset = asset_lookup.loc[asset_id]
        else:
            asset_id = monitored_ids[int(rng.integers(0, len(monitored_ids)))]
            asset = asset_lookup.loc[asset_id]
            cat = str(rng.choice(present_cats))

        asset_counts[asset_id] = asset_counts.get(asset_id, 0) + 1
        det_name, det_type, rule_id = CATEGORY_DETECTIONS.get(
            cat, ("Anomalous security event", "other", "GEN-000"))
        source_ip = _pick_ip(rng)
        dest_ip = _pick_ip(rng)
        proc = str(rng.choice(PROCS))
        port = int(rng.choice([22, 80, 443, 445, 1433, 3389, 8080, 502, 102]))

        confidence = round(float(rng.uniform(0.55, 0.99)), 2)
        risk_score = round(min(1.0, (SEV_RANK[sev] + 1) / 4 * float(rng.uniform(0.7, 1.1))), 2)

        # ── acknowledgement ──
        ack_median = SEV_ACK_MEDIAN[sev]
        if "slow_ack" in flags:
            ack_median *= 4.5 if sev in ("critical", "high") else 2.0
        ack_delay = float(rng.lognormal(np.log(max(1.5, ack_median)), 0.55))

        # ── closure ──
        stamping = ("rubber_stamping" in flags and sev in ("critical", "high")
                    and rng.random() < 0.85)
        if stamping:
            ack_delay = float(rng.uniform(0.2, 1.0))
            close_delay = float(rng.uniform(0.3, 2.0))
        else:
            close_median = SEV_CLOSE_MEDIAN[sev]
            if "long_closure" in flags and sev in ("critical", "high"):
                close_median *= 4.5
            close_delay = float(rng.lognormal(np.log(close_median), 0.7))
            # An entity that reports SLA compliance it did not achieve must first have
            # closures that missed the target - otherwise there is nothing to misreport.
            if "sla_gaming" in flags and rng.random() < 0.40:
                close_delay *= 6.0

        ack_ts = created + timedelta(minutes=ack_delay)
        close_ts = ack_ts + timedelta(minutes=close_delay)
        if bulk_close_hour is not None and 0.06 < rng.random() < 0.15:
            close_ts = bulk_close_hour + timedelta(minutes=int(rng.integers(0, 12)))
            if close_ts <= ack_ts:
                close_ts = ack_ts + timedelta(minutes=1)
        if close_ts > end_date + timedelta(days=1):
            close_ts = end_date - timedelta(minutes=5)

        # ── priority: severity, lifted by asset criticality ──
        crit_asset = str(asset.criticality_tier).lower() == "critical"
        priority = {"critical": "P1", "high": "P2", "medium": "P3", "low": "P4"}[sev]
        if sev == "high" and crit_asset and rng.random() < 0.5:
            priority = "P1"
        sla_target = float(SLA_TARGET_BY_PRIORITY[priority])

        alert_id = f"ALT-{idx:02d}-{k + 1:06d}"

        # ── case record ──
        open_case = rng.random() < (0.94 if sev in ("critical", "high") else 0.16)
        if "missing_case_records" in flags and sev in ("critical", "high"):
            open_case = rng.random() < 0.22

        case_id = ""
        case_sev = sev
        disposition = str(rng.choice(DISPOSITION_KINDS, p=[0.30, 0.26, 0.35, 0.09]))
        if open_case:
            case_seq += 1
            case_id = f"CS-{idx:02d}-{k + 1:06d}"

            # Severity softening: the case is logged below the alert it came from.
            if "severity_softening" in flags and sev in ("critical", "high") and rng.random() < 0.65:
                case_sev = "medium" if sev == "critical" else "low"

            template_share = 0.88 if "template_notes" in flags else 0.08
            if rng.random() < template_share:
                note = str(rng.choice(TEMPLATE_NOTES)).format(
                    cat=cat.replace("_", " "), host=asset.hostname, disp=disposition)
            else:
                note = compose_note(rng, cat, asset.hostname, proc, source_ip, port)

            # Cases with no workflow record at all (negative space inside the case data)
            no_events = ("no_investigation_records" in flags and rng.random() < 0.75)
            case_events = []
            if not no_events:
                activity_plan = (["Case created", "Initial triage", "Evidence collection",
                                  "Enrichment", "Root cause analysis", "Remediation",
                                  "Verification", "Case closure"]
                                 if sev in ("critical", "high") else
                                 ["Case created", "Initial triage", "Case closure"])
                # Rework loops: the same activities repeat, separated by a different one
                rework_case = "rework_loops" in flags and rng.random() < 0.55
                if rework_case:
                    activity_plan = ["Case created", "Initial triage", "Enrichment",
                                     "Initial triage", "Enrichment", "Initial triage",
                                     "Containment action", "Evidence collection",
                                     "Containment action", "Case closure"]
                elif rng.random() < 0.12:  # natural, occasional rework
                    activity_plan = ["Case created", "Initial triage", "Enrichment",
                                     "Initial triage", "Case closure"]
                # Thin investigation time (rule IM-009): the case carries a full-looking
                # workflow record, but the recorded time spent on it is seconds. The
                # steps exist; the work behind them does not.
                # Kept mutually exclusive with the rework pattern so that an entity
                # carrying both injections exercises both rules: a short, reworked
                # activity list would look like neither.
                thin_time = ("thin_investigation_time" in flags and not rework_case
                             and rng.random() < 0.7)
                if thin_time:
                    activity_plan = ["Case created", "Initial triage", "Case closure"]
                ev_delay = float(rng.uniform(3, 30))
                ev_ts = ack_ts
                for seq, activity in enumerate(activity_plan, start=1):
                    ev_missing = ("no_investigation_records" in flags and rng.random() < 0.6)
                    if thin_time:
                        ev_duration = round(float(rng.uniform(0.05, 0.4)), 2)
                    else:
                        ev_duration = round(float(rng.uniform(2, 90)), 1)
                    case_events.append(dict(
                        investigation_id=f"INV-{idx:02d}-{len(events) + seq:07d}",
                        case_id=case_id, alert_id=alert_id, ts=ev_ts,
                        sequence_number=seq, activity_type=activity,
                        activity_subtype=str(rng.choice(ACTIVITY_SUBTYPES.get(activity, [""]))),
                        analyst_id=str(rng.choice(analysts)),
                        team_id=str(rng.choice(["SOC Tier 1", "SOC Tier 2", "SOC Tier 3"])),
                        asset_id=asset_id,
                        evidence_type="" if ev_missing else str(rng.choice(EVIDENCE_TYPES)),
                        action_result="" if ev_missing else str(rng.choice(ACTION_RESULTS)),
                        duration_minutes=ev_duration,
                        previous_activity=activity_plan[seq - 2] if seq > 1 else "",
                        next_activity=activity_plan[seq] if seq < len(activity_plan) else "",
                    ))
                    ev_ts = ev_ts + timedelta(minutes=ev_delay)
                    ev_delay = float(rng.uniform(3, 60))
                events.extend(case_events)

            root_cause_text = ""
            remediation_status = "completed"
            remediation_reference = ""
            if disposition == "true_positive" or sev in ("critical", "high"):
                has_root = rng.random() < (0.15 if "no_root_cause" in flags else 0.82)
                # chronic repeat traffic is the signal EG-004 looks for, and it is only
                # chronic if nothing was ever recorded about why it keeps happening
                if "repeat_alerts" in flags and k % 3 == 0:
                    has_root = False
                if has_root:
                    root_cause_text = str(rng.choice([
                        "Unpatched internet-facing service exploited via publicly known CVE",
                        "Misconfigured access control on the affected application",
                        "Compromised service-account credential reused across hosts",
                        "Unmanaged third-party software introduced by a business unit",
                        "Phishing attachment executed by an end user outside mail filtering",
                        "Legacy protocol without authentication exposed on the OT segment",
                    ]))
                    remediation_reference = f"REM-{int(rng.integers(10000, 99999))}"
                else:
                    remediation_status = str(rng.choice(["not_started", "in_progress", ""]))

            cases.append(dict(
                case_id=case_id, opened_ts=ack_ts, updated_ts=close_ts, closed_ts=close_ts,
                resolution_ts=close_ts, status="closed",
                case_type=str(rng.choice(["Security Incident", "Security Incident", "Security Event",
                                          "Investigation Request"])),
                case_category=det_type, priority={"critical": "P1", "high": "P2", "medium": "P3",
                                                  "low": "P4"}.get(case_sev, "P3"),
                severity=case_sev, case_description=det_name,
                investigation_note_text=note,
                reopened_count=0,
                assigned_analyst=str(rng.choice(analysts)),
                assigned_team=str(rng.choice(["SOC Tier 1", "SOC Tier 2", "SOC Tier 3"])),
                created_from_alert=1, number_of_alerts=1, affected_asset_count=1,
                affected_user_count=int(rng.random() < 0.2),
                parent_case_id="", related_case_id="", case_events=case_events,
                root_cause_text=root_cause_text, remediation_status=remediation_status,
                remediation_reference=remediation_reference,
            ))

        # ── escalation record ──
        if sev == "critical" or (sev == "high" and rng.random() < 0.3):
            esc_prob = 0.06 if "no_escalation" in flags else 0.75
            if "missing_case_records" in flags:
                esc_prob *= 0.30
            if rng.random() < esc_prob:
                # an escalation can never post-date the closure it belongs to
                esc_delay = min(float(rng.uniform(5, 90)),
                                max(1.0, (close_ts - ack_ts).total_seconds() / 60.0 * 0.5))
                esc_sev = case_sev
                decision = "Escalated"
                if "severity_softening" in flags and rng.random() < 0.6:
                    esc_sev = "medium"
                    decision = str(rng.choice(["Downgraded", "Closed at source", "Reassigned"]))
                escalations.append(dict(
                    escalation_id=f"ESC-{idx:02d}-{len(escalations) + 1:06d}",
                    case_id=case_id, alert_id=alert_id,
                    escalated_ts=ack_ts + timedelta(minutes=esc_delay),
                    escalated_to_role=str(rng.choice(["Tier 3 Analyst", "Incident Manager",
                                                      "CISO Office", "Asset Owner"])),
                    escalation_reason=str(rng.choice([
                        "Critical severity incident", "Confirmed true positive",
                        "Potential CII impact", "Regulatory reporting required",
                        "Cross-team response required"])),
                    resolved_ts=close_ts,
                    from_team="SOC Tier 2", to_team=str(rng.choice(["SOC Tier 3", "IR Team",
                                                                   "CISO Office"])),
                    from_role="Tier 2 Analyst", to_role="Incident Manager",
                    escalation_level=int(rng.choice([1, 2, 2, 3])),
                    severity_at_escalation=esc_sev,
                    priority_at_escalation={"critical": "P1", "high": "P2", "medium": "P3",
                                            "low": "P4"}.get(esc_sev, "P3"),
                    decision=decision,
                    approved_by=str(rng.choice(["IR-Manager", "Shift Lead", ""])),
                    response_ts=close_ts,
                    escalation_status="closed",
                ))

        # ── disposition & closure record ──
        # One per closed case, plus one per closed critical/high alert that never got a
        # case - the "closed without investigation" pattern has to leave a trace.
        if case_id or sev in ("critical", "high"):
            measured = (close_ts - ack_ts).total_seconds() / 60.0
            measured_total = (close_ts - created).total_seconds() / 60.0
            made_sla = measured_total <= sla_target
            reported_sla = made_sla
            if "sla_gaming" in flags and not made_sla and rng.random() < 0.85:
                reported_sla = True          # the record claims compliance the data contradicts
            risk_accepted = disposition == "risk_accepted"
            authority = ""
            if risk_accepted:
                authority = ("" if ("risk_accept_no_authority" in flags and rng.random() < 0.8)
                             else str(rng.choice(["CISO", "Risk Committee", "Asset Owner"])))
            reopen_count = int(rng.random() < (0.22 if "reopened_cases" in flags else 0.03))
            dispositions.append(dict(
                disposition_id=f"DSP-{idx:02d}-{len(dispositions) + 1:06d}",
                alert_id=alert_id, case_id=case_id, disposition=disposition,
                closure_reason=str(rng.choice([
                    "Investigation completed", "Confirmed benign after triage",
                    "Duplicate of earlier incident", "Handled by automated response",
                    "No action required after review"])),
                closure_ts=close_ts,
                closed_by=str(rng.choice(analysts)),
                closure_team=str(rng.choice(CLOSURE_TEAMS)),
                root_cause=root_cause_text if case_id else "",
                false_positive_reason=("Signature matched authorised administrative tooling"
                                       if disposition == "false_positive" else ""),
                true_positive=int(disposition == "true_positive"),
                risk_accepted=int(risk_accepted),
                risk_acceptance_authority=authority,
                remediation_status=(remediation_status if case_id else "not_started"),
                remediation_reference=(remediation_reference if case_id else ""),
                reopen_count=reopen_count,
                time_to_close_minutes=round(measured, 1),
                sla_target_minutes=sla_target,
                made_sla=int(reported_sla),
            ))

        # Alert ownership. Under 'analyst_overload' one analyst absorbs nearly every
        # critical/high alert while the rest of the team handles routine traffic.
        owner = str(rng.choice(analysts))
        if "analyst_overload" in flags and sev in ("critical", "high") and rng.random() < 0.92:
            owner = overload_analyst

        alerts.append(dict(
            alert_id=alert_id, alert_timestamp=created, detection_timestamp=created,
            assigned_analyst_id=owner,
            source_system=str(rng.choice(ALERT_SOURCES)), detection_rule_id=rule_id,
            alert_name=det_name, alert_category=cat, alert_type=det_type, severity=sev,
            priority=priority, risk_score=risk_score, confidence=confidence,
            status="closed", asset_id=asset_id, asset_type=str(asset.asset_type),
            hostname=str(asset.hostname), ip_address=str(asset.ip_address),
            user_id=str(rng.choice(USER_IDS)), source_ip=source_ip, destination_ip=dest_ip,
            source_country=str(rng.choice(COUNTRIES)),
            destination_country=str(rng.choice(COUNTRIES)), detection_source=str(
                rng.choice(DETECTION_SOURCES)),
            parent_alert_id="", created_by=str(rng.choice(["SIEM", "EDR", "Analyst", "Automation"])),
            acknowledged_ts=ack_ts, closed_ts=close_ts, disposition=disposition,
            case_id=case_id, sla_target_minutes=sla_target,
        ))

    alerts_df = pd.DataFrame(alerts)
    if alerts_df.empty:
        alerts_df = pd.DataFrame(columns=["alert_id", "alert_timestamp"])
    alerts_df = alerts_df.sort_values("alert_timestamp").reset_index(drop=True)
    cases_df = pd.DataFrame(cases)
    escal_df = pd.DataFrame(escalations)
    events_df = pd.DataFrame(events)
    disp_df = pd.DataFrame(dispositions)

    # ── realism: a case can absorb follow-on alerts on the same asset ──
    alerts_df, cases_df = _coalesce_cases(alerts_df, cases_df, rng)

    # ── case -> alert linkage (what the case records in its own field) ──
    if not cases_df.empty and not alerts_df.empty:
        linked = (alerts_df[alerts_df.case_id.astype(str).str.strip() != ""]
                  .groupby("case_id").alert_id.apply(lambda s: "|".join(list(s)[:8])))
        cases_df["linked_alert_ids"] = cases_df.case_id.map(linked).fillna("")
        alert_counts = (alerts_df[alerts_df.case_id.astype(str).str.strip() != ""]
                        .groupby("case_id").agg(n=("alert_id", "size"),
                                                assets=("asset_id", "nunique"),
                                                users=("user_id", "nunique")))
        cases_df["number_of_alerts"] = cases_df.case_id.map(alert_counts["n"]).fillna(1).astype(int)
        cases_df["affected_asset_count"] = cases_df.case_id.map(alert_counts["assets"]).fillna(1).astype(int)
        cases_df["affected_user_count"] = cases_df.case_id.map(alert_counts["users"]).fillna(0).astype(int)

    # ── inventory: fill in the observed telemetry state per asset ──
    assets_df = _finalise_inventory(assets_df, alerts_df, rng, end_date, not_monitoring, days)

    crit_mask = assets_df["criticality_tier"].astype(str).str.lower().eq("critical")
    measured_kpis = _measure_entity_kpis(alerts_df, cases_df, disp_df, events_df, assets_df,
                                         escal_df)
    counts = dict(alerts=len(alerts_df), cases=len(cases_df), escalations=len(escal_df),
                  investigation_events=len(events_df), dispositions=len(disp_df),
                  assets=len(assets_df),
                  monitored_assets=int(assets_df["asset_id"].isin(monitored_ids).sum()),
                  critical_assets=int(crit_mask.sum()),
                  ot_assets=int(assets_df["asset_class"].astype(str)
                               .eq("critical_ot").sum()))
    profile = _declared_profile(comp, idx, flags, start_date, end_date, counts, measured_kpis)
    profile["cse_id"] = f"CSE-{idx + 1001:04d}"
    # keep the ingest-visible keys in the profile itself (not only nested)
    profile["entity_name"] = comp["name"]
    profile["tier"] = comp["tier"]
    profile["soc_name"] = profile["soc"]["name"]
    profile["soc_model"] = profile["soc"]["model"]
    profile["soc_coverage"] = profile["soc"]["coverage"]
    profile["analyst_count"] = profile["soc"]["analyst_count"]
    profile["contact"] = profile["contact_person"]
    profile["notes"] = comp["note"]

    # ground truth for this entity
    expected, descriptions = [], []
    for key in comp["anomalies"]:
        rules, description = ANOMALY_RULES.get(key, ([], ""))
        expected += [r for r in rules if r not in expected]
        if description:
            descriptions.append(description)

    files = dict(
        entity_profile=profile,
        alerts=alerts_df,
        cases=cases_df,
        escalations=escal_df,
        investigations=events_df,
        dispositions=disp_df,
        inventory=assets_df,
    )
    gt = dict(entity_name=comp["name"], cse_id=profile["cse_id"], sector=comp["sector"],
              injected=list(comp["anomalies"]), expected_rules=expected,
              description="; ".join(descriptions))
    return files, gt


def _measure_entity_kpis(alerts_df, cases_df, disp_df, events_df, assets_df,
                         escal_df=None) -> dict:
    """What the entity's own records actually show, for the declaration to be measured against.

    Honest entities declare these numbers; the over-claiming ones declare better ones.
    Either way the declaration is traceable back to the submission.
    """
    measured = {
        "sla_compliance_pct": 95.0,
        "critical_alert_ack_minutes_median": 15.0,
        "critical_incident_containment_minutes_median": 240.0,
        "escalation_compliance_pct": 85.0,
        "investigation_records_completeness_pct": 92.0,
        "monitoring_coverage_production_pct": 94.0,
    }
    if disp_df is not None and not disp_df.empty:
        target = pd.to_numeric(disp_df.get("sla_target_minutes"), errors="coerce")
        measured_close = pd.to_numeric(disp_df.get("time_to_close_minutes"), errors="coerce")
        breaches = int(((target > 0) & (measured_close > target)).sum())
        measured["sla_compliance_pct"] = round(max(0.0, 1 - breaches / max(1, len(disp_df))) * 100, 1)
    if alerts_df is not None and not alerts_df.empty:
        ack = (pd.to_datetime(alerts_df.get("acknowledged_ts"), errors="coerce")
               - pd.to_datetime(alerts_df.get("alert_timestamp"), errors="coerce"))
        ack_min = ack.dt.total_seconds() / 60.0
        close_min = ((pd.to_datetime(alerts_df.get("closed_ts"), errors="coerce")
                      - pd.to_datetime(alerts_df.get("alert_timestamp"), errors="coerce"))
                     .dt.total_seconds() / 60.0)
        sev = alerts_df.get("severity", pd.Series(dtype=str)).astype(str).str.lower()
        crit_high = sev.isin(["critical", "high"])
        crit = sev.eq("critical")
        if crit_high.any():
            measured["critical_alert_ack_minutes_median"] = round(float(ack_min[crit_high].median()), 1)
        if crit.any():
            measured["critical_incident_containment_minutes_median"] = round(
                float(close_min[crit].median()), 1)
    if cases_df is not None and not cases_df.empty and events_df is not None:
        cases_with_events = set(events_df.get("case_id", pd.Series(dtype=str)).dropna())
        complete = len(set(cases_df["case_id"]) & cases_with_events)
        measured["investigation_records_completeness_pct"] = round(
            max(0.0, complete / max(1, len(cases_df))) * 100, 1)
    if alerts_df is not None and not alerts_df.empty and escal_df is not None:
        crit_ids = set(alerts_df.loc[
            alerts_df.get("severity", pd.Series(dtype=str)).astype(str).str.lower().eq("critical"),
            "alert_id"])
        if crit_ids:
            esc_alert_ids = set(escal_df.get("alert_id", pd.Series(dtype=str)).dropna()) if \
                not escal_df.empty else set()
            esc_case_ids = set(escal_df.get("case_id", pd.Series(dtype=str)).dropna()) if \
                not escal_df.empty else set()
            crit_rows = alerts_df[alerts_df["alert_id"].isin(crit_ids)]
            escalated = (crit_rows["alert_id"].isin(esc_alert_ids)
                         | crit_rows.get("case_id", pd.Series(dtype=str)).isin(esc_case_ids))
            measured["escalation_compliance_pct"] = round(
                float(escalated.sum()) / max(1, len(crit_rows)) * 100, 1)
    if assets_df is not None and not assets_df.empty:
        monitored = assets_df["monitoring_status"].astype(str).str.lower().isin(
            ["active", "enabled", "true", "1", "yes", ""]).sum()
        measured["monitoring_coverage_production_pct"] = round(
            float(monitored) / max(1, len(assets_df)) * 100, 1)
    return measured


def _coalesce_cases(alerts_df, cases_df, rng, window_hours: int = 48, share: float = 0.18):
    """Attach some follow-on alerts on the same asset to an existing case.

    Real case management groups related alerts; a 1:1 alert-to-case dataset would
    make the "number of alerts per case" and case-duration analysis meaningless.
    """
    if alerts_df.empty or cases_df.empty:
        return alerts_df, cases_df

    alerts_df = alerts_df.copy()
    alerts_df["alert_ts_dt"] = pd.to_datetime(alerts_df["alert_timestamp"], errors="coerce")
    case_ts = pd.to_datetime(cases_df["opened_ts"], errors="coerce")
    case_asset = (alerts_df.loc[alerts_df.case_id.astype(str).str.strip() != ""]
                  .drop_duplicates("case_id").set_index("case_id")["asset_id"].to_dict())
    unassigned = alerts_df[alerts_df.case_id.astype(str).str.strip() == ""]

    for case_id, opened in zip(cases_df["case_id"], case_ts):
        asset_id = case_asset.get(case_id)
        if not asset_id or opened is pd.NaT or rng.random() > share:
            continue
        candidates = unassigned[
            (unassigned.asset_id == asset_id) &
            (unassigned["alert_ts_dt"] >= opened) &
            (unassigned["alert_ts_dt"] <= opened + timedelta(hours=window_hours))
        ]
        if candidates.empty:
            continue
        take = candidates.head(int(rng.integers(1, 4)))
        alerts_df.loc[alerts_df.alert_id.isin(take.alert_id), "case_id"] = case_id
        unassigned = unassigned[~unassigned.alert_id.isin(take.alert_id)]

    return alerts_df.drop(columns=["alert_ts_dt"]), cases_df


def _finalise_inventory(assets_df, alerts_df, rng, end_date, not_monitoring, days=180):
    """Fill observed telemetry state so inventory can be cross-checked against alerts.

    ``expected_alert_frequency`` is expressed in alerts per month and is set from the
    asset class plus the estate's own observed rate, so that "expected but absent"
    (a silent critical asset) is a meaningful comparison rather than a random number.
    """
    if assets_df.empty:
        return assets_df
    assets_df = assets_df.copy()
    counts = alerts_df.groupby("asset_id").size().to_dict() if not alerts_df.empty else {}
    last_seen = (alerts_df.groupby("asset_id")["alert_timestamp"].max().to_dict()
                 if not alerts_df.empty else {})
    assets_df["actual_alert_count"] = assets_df.asset_id.map(counts).fillna(0).astype(int)

    months = max(1.0, days / 30.0)
    # estate baseline: alerts per asset per month across monitored assets
    observed = assets_df.loc[assets_df.actual_alert_count > 0, "actual_alert_count"]
    baseline = float(observed.mean()) / months if len(observed) else 5.0

    def expected(row):
        if row.actual_alert_count > 0:
            value = row.actual_alert_count / months * float(rng.uniform(0.8, 1.2))
        else:
            # an asset that is monitored but silent still has an expected rate; a
            # critical one visibly so, because that is what makes silence notable
            is_crit = str(row.criticality_tier).lower() == "critical"
            value = baseline * float(rng.uniform(0.5, 1.3)) if is_crit else \
                baseline * float(rng.uniform(0.05, 0.4))
        return round(max(0.1, value), 1)

    assets_df["expected_alert_frequency"] = assets_df.apply(expected, axis=1)

    def telemetry(row):
        if row.asset_id in not_monitoring:
            return ""
        ts = last_seen.get(row.asset_id)
        if ts:
            return _iso(pd.to_datetime(ts).to_pydatetime())
        # monitored asset with no alerts: telemetry may still be arriving silently
        if rng.random() < 0.55:
            return _iso(end_date - timedelta(hours=int(rng.integers(1, 96))))
        return ""

    assets_df["last_telemetry_timestamp"] = assets_df.apply(telemetry, axis=1)
    assets_df["last_scan_timestamp"] = [
        _iso(end_date - timedelta(days=int(rng.integers(1, 45)))) for _ in range(len(assets_df))
    ]
    return assets_df


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Cycle drift
#
# The tool's whole commercial story is continuous supervision, and the question that
# matters in a second review cycle is "what changed?" - which cannot be demonstrated
# on a single dataset. This map describes a plausible next submission cycle: three
# entities deteriorate, two improve, one stays flat. It is applied only when cycle > 1,
# so cycle 1 remains the clean baseline.
#
# Entities are named because the drift is a *narrative* the deck needs ("this one got
# worse, this one fixed it"), not a random perturbation generator.
# ---------------------------------------------------------------------------

CYCLE_DRIFT = {
    2: {
        # ── deteriorated ──
        "Bharat Steel & Mining Ltd": {
            "add": ["no_root_cause", "sla_gaming"], "remove": [],
            "note": "Silent critical OT assets persisted through the second period, and "
                    "root-cause recording and SLA reporting degraded alongside them"},
        "CloudBharat Services Ltd": {
            "add": ["weekend_gap"], "remove": [],
            "note": "Weekend telemetry collection stopped during the second review period "
                    "(platform migration left the weekend scheduler disabled)"},
        "Eastern Freight Corridor Corp": {
            "add": ["thin_investigation_time"], "remove": [],
            "note": "Investigation records thinned out: workflows are complete on paper but "
                    "account for almost no recorded work"},
        # ── improved (the tool should stop flagging these) ──
        "Astra Telecom Networks": {
            "add": [], "remove": ["template_notes"],
            "note": "Investigation notes were rewritten with substantive findings; the "
                    "template-note pattern no longer appears"},
        "Directorate of Citizen Services": {
            "add": [], "remove": ["night_gap"],
            "note": "Monitoring was extended to 24x7, closing the night blind spot"},
        "BharatPay Technologies": {
            "add": [], "remove": ["reopened_cases"],
            "note": "First-time-closure quality improved; reopen activity fell away"},
        # ── unchanged control ──
        "Indraprastha Defence Systems": {
            "add": [], "remove": [],
            "note": "No change between cycles - included to show the comparison does not "
                    "manufacture movement where none exists"},
    },
}


def _cycle_anomalies(comp: dict, cycle: int) -> tuple[list[str], str]:
    """Anomaly set (and an optional note) for the entity in a given submission cycle."""
    anomalies = list(comp["anomalies"])
    note = comp.get("note", "")
    if cycle <= 1:
        return anomalies, note
    drift = CYCLE_DRIFT.get(cycle, {}).get(comp["name"])
    if not drift:
        return anomalies, note
    for flag in drift.get("remove", []):
        if flag in anomalies:
            anomalies.remove(flag)
    for flag in drift.get("add", []):
        if flag not in anomalies:
            anomalies.append(flag)
    # a fully improved entity becomes a control entity for the new cycle
    if not anomalies:
        anomalies = ["clean"]
    return anomalies, (drift.get("note") or note)


def write_submissions(out_dir=DEFAULT_OUT, *, companies=27, days=180,
                      alerts_per_asset_day=0.34, seed=42, end_date=None, quiet=False,
                      cycle=1):
    """Write submission folders + ground_truth.json. Returns a summary dict.

    ``cycle`` selects which submission period to generate. Cycle 1 is the baseline;
    cycle 2 applies ``CYCLE_DRIFT`` so a second ingest + analytics run demonstrates the
    cycle-over-cycle comparison with real movement (that is the only way to show the
    feature working rather than asserting that it would work).
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    end_date = end_date or datetime.now().replace(hour=23, minute=59, second=0, microsecond=0)

    roster = []
    for comp in ROSTER[:companies]:
        anomalies, note = _cycle_anomalies(comp, cycle)
        roster.append({**comp, "anomalies": anomalies, "note": note})
    ground_truth, summary = [], []
    for idx, comp in enumerate(roster):
        files, gt = generate_entity(comp, idx, days=days,
                                    alerts_per_asset_day=alerts_per_asset_day,
                                    end_date=end_date, seed=seed)
        comp_dir = out_dir / comp["name"]
        comp_dir.mkdir(parents=True, exist_ok=True)

        (comp_dir / "entity_profile.json").write_text(
            json.dumps(files["entity_profile"], indent=2), encoding="utf-8")
        files["alerts"].to_csv(comp_dir / "alerts_export.csv", index=False)

        # cases_dump.json keeps the investigation note (the evidence a supervisor reads)
        cases_out = files["cases"].drop(columns=["case_events"], errors="ignore")
        for col in ("opened_ts", "updated_ts", "closed_ts", "resolution_ts"):
            if col in cases_out.columns:
                cases_out[col] = pd.to_datetime(cases_out[col], errors="coerce").dt.strftime(
                    "%Y-%m-%dT%H:%M:%S")
        (comp_dir / "cases_dump.json").write_text(
            cases_out.to_json(orient="records", indent=None), encoding="utf-8")

        # investigation workflow events carried inside the case dump, mirroring how a
        # ticketing export lands in practice
        events_out = files["investigations"].copy()
        if not events_out.empty:
            events_out["timestamp"] = pd.to_datetime(
                events_out["ts"], errors="coerce").dt.strftime("%Y-%m-%dT%H:%M:%S")
            events_out = events_out.drop(columns=["ts"])
            events_out.to_csv(comp_dir / "investigations_workflow.csv", index=False)

        files["inventory"].to_csv(comp_dir / "inventory.csv", index=False)
        if not files["escalations"].empty:
            files["escalations"].to_csv(comp_dir / "escalations.csv", index=False)
        else:
            (comp_dir / "escalations.csv").write_text(
                "escalation_id,case_id,alert_id,cse_id,timestamp,from_team,to_team\n",
                encoding="utf-8")
        if not files["dispositions"].empty:
            files["dispositions"].to_csv(comp_dir / "dispositions.csv", index=False)

        ground_truth.append(gt)
        summary.append(dict(entity=comp["name"], cse_id=gt["cse_id"], sector=comp["sector"],
                            tier=comp["tier"],
                            alerts=len(files["alerts"]), cases=len(files["cases"]),
                            escalations=len(files["escalations"]),
                            investigations=len(files["investigations"]),
                            dispositions=len(files["dispositions"]),
                            assets=len(files["inventory"]),
                            injected=list(comp["anomalies"])))
        if not quiet:
            print(f"  [{idx + 1:02d}/{len(roster)}] {comp['name']:<42} "
                  f"alerts={len(files['alerts']):>5} cases={len(files['cases']):>5} "
                  f"events={len(files['investigations']):>6} "
                  f"disp={len(files['dispositions']):>5}")

    (out_dir / "ground_truth.json").write_text(json.dumps(dict(
        generated_at=datetime.now().isoformat(timespec="seconds"), seed=seed, days=days,
        cycle=cycle, alerts_per_asset_day=alerts_per_asset_day, entities=ground_truth,
    ), indent=2), encoding="utf-8")

    return dict(out_dir=str(out_dir), companies=len(summary), cycle=cycle,
                total_alerts=sum(s["alerts"] for s in summary),
                total_cases=sum(s["cases"] for s in summary),
                total_escalations=sum(s["escalations"] for s in summary),
                total_investigations=sum(s["investigations"] for s in summary),
                total_dispositions=sum(s["dispositions"] for s in summary),
                entities=summary)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate synthetic CSE submission folders")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output directory")
    ap.add_argument("--companies", type=int, default=len(ROSTER),
                    help=f"number of CSEs (max {len(ROSTER)})")
    ap.add_argument("--days", type=int, default=180, help="review window in days")
    ap.add_argument("--rate", type=float, default=0.34,
                    help="alerts per monitored asset per day")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--cycle", type=int, default=1,
                    help="submission period: 1 = baseline, >1 applies the cycle drift "
                         "recorded in CYCLE_DRIFT so the cycle-over-cycle comparison has "
                         "something real to compare")
    args = ap.parse_args(argv)

    res = write_submissions(args.out, companies=args.companies, days=args.days,
                            alerts_per_asset_day=args.rate, seed=args.seed, cycle=args.cycle)
    print(f"\nWrote {res['companies']} CSE submission folders (cycle {res['cycle']}) "
          f"to {res['out_dir']}")
    print(f"  alerts={res['total_alerts']:,}  cases={res['total_cases']:,}  "
          f"escalations={res['total_escalations']:,}")
    print(f"  investigation events={res['total_investigations']:,}  "
          f"dispositions={res['total_dispositions']:,}")
    print("  ground_truth.json written for detector validation")
    return res


if __name__ == "__main__":
    main()
