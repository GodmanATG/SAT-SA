"""
Canonicalisation / normalisation layer
======================================
Everything that turns a CSE's own export format into the tool's canonical schema
lives here, so that the single-entity upload path and the bulk submission-folder
loader cannot drift apart.

Three jobs, in order:

1. **Table classification** - which canonical table does this file populate?
   (``auto_detect_table_type``)
2. **Column mapping** - which of the file's columns map onto that table's
   canonical columns? Mapping is deliberately **scoped to the target table**: a
   synonym dictionary shared across every table would happily bind an escalation
   export's ``id`` column onto ``alert_id``, because both tables have an identifier
   column with the same plausible name. Scoping removes that class of silent
   mis-mapping. (``guess_column_mapping``)
3. **Value canonicalisation** - severities onto critical/high/medium/low, booleans
   onto 0/1, numbers onto floats, and *every timestamp column onto ISO-8601
   (YYYY-MM-DDTHH:MM:SS)* before it reaches SQLite. Raw strings in a TIMESTAMP
   column are the classic silent failure: the value stores fine, then every
   downstream comparison, sort and duration calculation quietly misbehaves.
   (``coerce_dates`` / ``to_iso8601``)
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pandas as pd

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from database import ensure_schema, get_db

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S"

# ---------------------------------------------------------------------------
# Canonical schema
#
# Single source of truth for which columns exist per table. The bulk loader
# imports these lists rather than redeclaring them, so a column added here is
# immediately available to both ingestion paths.
# ---------------------------------------------------------------------------

TABLE_COLUMNS = {
    "alerts": ["alert_id", "asset_id", "alert_category", "alert_type", "alert_name",
               "severity", "priority", "risk_score", "confidence", "status",
               "created_ts", "detected_ts", "acknowledged_ts", "closed_ts",
               "disposition", "sla_target_minutes", "assigned_analyst_id", "case_id",
               "parent_alert_id", "source_system", "detection_source",
               "detection_rule_id", "hostname", "ip_address", "destination_ip",
               "source_country", "destination_country", "asset_type", "user_id",
               "created_by", "investigator_notes"],
    "cases": ["case_id", "opened_ts", "updated_ts", "closed_ts", "resolution_ts", "status",
              "case_type", "case_category", "priority", "severity", "case_description",
              "investigation_note_text", "root_cause", "root_cause_documented", "remediation_documented",
              "reopened_count", "assigned_analyst", "assigned_team", "created_from_alert",
              "number_of_alerts", "affected_asset_count", "affected_user_count",
              "parent_case_id", "related_case_id", "linked_alert_ids"],
    "escalations": ["escalation_id", "case_id", "alert_id", "escalated_ts",
                    "escalated_to_role", "escalation_reason", "resolved_ts",
                    "from_team", "to_team", "from_role", "to_role", "escalation_level",
                    "severity_at_escalation", "priority_at_escalation", "decision",
                    "approved_by", "response_ts", "escalation_status"],
    "asset_inventory": ["asset_id", "asset_name", "hostname", "ip_address", "asset_type",
                        "asset_class", "criticality_tier", "business_criticality",
                        "environment", "business_unit", "location", "data_classification",
                        "owner", "system_owner", "monitoring_required", "monitoring_status",
                        "monitoring_source", "expected_alert_frequency", "actual_alert_count",
                        "last_telemetry_timestamp", "last_scan_timestamp",
                        "vulnerability_status"],
    "investigations": ["investigation_id", "case_id", "alert_id", "ts", "sequence_number",
                       "activity_type", "activity_subtype", "analyst_id", "team_id",
                       "asset_id", "evidence_type", "action_result", "duration_minutes",
                       "previous_activity", "next_activity"],
    "dispositions": ["disposition_id", "alert_id", "case_id", "disposition", "closure_reason",
                     "closure_ts", "closed_by", "closure_team", "root_cause",
                     "false_positive_reason", "true_positive", "risk_accepted",
                     "risk_acceptance_authority", "remediation_status",
                     "remediation_reference", "reopen_count", "time_to_close_minutes",
                     "sla_target_minutes", "made_sla"],
}

TABLE_ID = {"alerts": "alert_id", "cases": "case_id", "escalations": "escalation_id",
            "asset_inventory": "asset_id", "investigations": "investigation_id",
            "dispositions": "disposition_id"}

# Which canonical columns must be stored as ISO-8601 timestamps. Anything else
# that looks like a date is reported by the validation summary but not persisted,
# because an unmapped column is not part of the canonical schema.
DATE_COLUMNS = {
    "alerts": ["created_ts", "detected_ts", "acknowledged_ts", "closed_ts"],
    "cases": ["opened_ts", "updated_ts", "closed_ts", "resolution_ts"],
    "escalations": ["escalated_ts", "resolved_ts", "response_ts"],
    "asset_inventory": ["last_telemetry_timestamp", "last_scan_timestamp"],
    "investigations": ["ts"],
    "dispositions": ["closure_ts"],
}

# Values that must land as numbers (SQLite is loosely typed, so storing them as
# text would silently break later arithmetic).
NUMERIC_COLUMNS = {
    "risk_score", "confidence", "sla_target_minutes", "expected_alert_frequency",
    "actual_alert_count", "duration_minutes", "time_to_close_minutes",
    "sequence_number", "escalation_level", "reopened_count", "reopen_count",
    "number_of_alerts", "affected_asset_count", "affected_user_count",
    "analyst_count", "made_sla", "true_positive", "risk_accepted",
    "created_from_alert", "root_cause_documented", "remediation_documented",
}

# Values that must land as booleans-as-integers, never as text.
BOOL_COLUMNS = {"made_sla", "true_positive", "risk_accepted", "created_from_alert",
                "root_cause_documented", "remediation_documented"}

# Required for analysis, per table: if one of these never mapped, the validator
# says so explicitly rather than letting a detector silently read zeros.
REQUIRED_FOR_ANALYSIS = {
    "alerts": ["alert_category", "severity", "created_ts", "closed_ts"],
    "cases": ["investigation_note_text", "root_cause_documented"],
    "escalations": ["escalated_ts", "escalated_to_role"],
    "asset_inventory": ["asset_class", "criticality_tier"],
    "investigations": ["activity_type", "ts"],
    "dispositions": ["disposition", "closure_ts"],
}

# ---------------------------------------------------------------------------
# Source-column synonyms, keyed by canonical column.
#
# The dictionary is intentionally broad because CSEs submit exports from very
# different tools (ServiceNow, Jira, a bespoke CSV dump, a spreadsheet). Breadth
# is safe *only* because ``guess_column_mapping`` intersects it with the target
# table's own column list.
# ---------------------------------------------------------------------------

COLUMN_SYNONYMS: dict[str, list[str]] = {
    # identifiers
    "alert_id": ["alert_id", "alertid", "alert_ref", "alert_number", "alert_no", "id",
                 "ticket_id", "ticket", "number", "incident_id", "event_id", "siem_id",
                 "alert_name", "sys_id", "issue_key", "issue_id", "key",
                 "from_alert_id", "source_alert_id"],
    "case_id": ["case_id", "caseid", "case_number", "case_no", "case_ref",
                "investigation_id", "investigation_ref", "record_id",
                "parent_incident", "parent_issue"],
    "escalation_id": ["escalation_id", "escalation_ref", "escalation_number", "esc_id", "id"],
    "disposition_id": ["disposition_id", "disposition_ref", "closure_id", "close_id"],
    "investigation_id": ["investigation_id", "investigation_event_id", "event_id",
                         "workflow_event_id", "activity_id", "step_id"],
    "asset_id": ["asset_id", "asset", "asset_ref", "device_id", "host_id", "ci", "ci_id",
                 "configuration_item", "system_id", "node_id", "machine_id",
                 "cmdb_ci", "cmdb_ci_id", "configuration_item_id"],
    "entity_id": ["entity_id", "entity", "cse_id", "customer_id", "org_id",
                  "organisation_id", "organization_id", "tenant_id"],
    # people / teams
    "assigned_analyst_id": ["assigned_analyst_id", "assignee_id", "owner_id", "analyst_id",
                            "assigned_to", "owner", "assignee", "analyst"],
    "assigned_analyst": ["assigned_analyst", "analyst_name", "assignee_name",
                         "analyst_id", "assigned_to", "owner"],
    "analyst_id": ["analyst_id", "performed_by", "handler", "analyst", "assignee"],
    "assigned_team": ["assigned_team", "assignment_group", "team", "group", "queue",
                      "assignment_group_name"],
    "team_id": ["team_id", "performing_team", "team", "group"],
    "from_team": ["from_team", "escalated_from", "source_team", "escalated_from_team"],
    "to_team": ["to_team", "escalated_to_team", "target_team"],
    "from_role": ["from_role", "escalated_from_role", "source_role"],
    "to_role": ["to_role", "escalated_to_role", "target_role"],
    "closed_by": ["closed_by", "closed_by_user", "resolved_by", "closure_user"],
    "closure_team": ["closure_team", "closed_by_group", "closing_team"],
    "approved_by": ["approved_by", "approver", "authorised_by", "authorized_by"],
    "risk_acceptance_authority": ["risk_acceptance_authority", "accepted_by",
                                  "risk_accept_authority", "accepting_authority",
                                  "risk_owner"],
    # alert content
    "severity": ["severity", "sev", "level", "criticality", "urgency",
                 "risk_level", "severity_level", "priority"],
    "alert_category": ["alert_category", "category", "type", "classification",
                       "alert_type", "attack_type", "threat_category", "event_category"],
    "alert_type": ["alert_type", "event_type", "detection_type", "rule_type",
                   "incident_type", "issue_type"],
    "alert_name": ["alert_name", "rule_name", "detection_name", "signature",
                   "detection_rule", "title", "summary", "issue_summary"],
    "priority": ["priority", "pri", "case_priority", "urgency"],
    "risk_score": ["risk_score", "risk", "risk_rating", "score"],
    "confidence": ["confidence", "confidence_score", "certainty"],
    "status": ["status", "state", "alert_status", "case_status", "workflow_state",
               "record_state", "state_name"],
    "disposition": ["disposition", "resolution", "outcome", "verdict", "close_code",
                    "closing_code", "resolution_code", "close_notes_code"],
    "source_system": ["source_system", "source", "tool", "sensor", "platform",
                      "product", "log_source"],
    "detection_source": ["detection_source", "detection_method", "analytics_source",
                         "detection_engine"],
    "detection_rule_id": ["detection_rule_id", "rule_id", "signature_id",
                          "detection_rule"],
    "hostname": ["hostname", "host", "computer", "machine_name", "device_name",
                 "fqdn", "node"],
    "ip_address": ["ip_address", "ip", "source_ip", "src_ip", "address"],
    "destination_ip": ["destination_ip", "dest_ip", "target_ip", "dst_ip"],
    "source_country": ["source_country", "src_country", "geo_src", "src_geo"],
    "destination_country": ["destination_country", "dest_country", "dst_country",
                            "geo_dest"],
    "asset_type": ["asset_type", "device_type", "asset_kind", "platform_type"],
    "user_id": ["user_id", "username", "account", "identity", "user", "account_name",
                "caller_id", "reporter", "reporter_id"],
    "created_by": ["created_by", "raised_by", "opened_by", "reported_by"],
    "investigator_notes": ["investigator_notes", "notes", "comments", "description",
                           "investigation_note_text", "analyst_comments", "work_notes",
                           "activity_notes", "resolution_notes"],
    # timestamps
    "created_ts": ["created_ts", "created_at", "created_date", "creation_ts",
                   "alert_timestamp", "timestamp", "generated_ts", "open_date",
                   "opened_ts", "opened_at", "opened_date", "date", "start_ts",
                   "sys_created_on", "sys_created_at", "created"],
    "detected_ts": ["detected_ts", "detection_timestamp", "detected_at",
                    "detection_ts", "first_seen"],
    "acknowledged_ts": ["acknowledged_ts", "acknowledged_at", "ack_ts", "acked_ts",
                        "acknowledged", "first_response_ts", "responded_at"],
    "closed_ts": ["closed_ts", "closed_at", "close_ts", "close_date", "resolved_at",
                  "resolved_ts", "resolution_ts", "closure_ts", "closed_timestamp",
                  "resolution_time", "end_ts", "resolved_on", "resolutiondate"],
    "updated_ts": ["updated_ts", "updated_at", "last_updated", "modified_ts",
                   "last_modified", "case_updated_at", "sys_updated_on",
                   "sys_updated_at"],
    # The case/closure lifecycle timestamps have their own canonical columns on the
    # cases, escalations and dispositions tables. Without an entry here they were
    # silently unmappable: the source column loaded as NULL, the case/monthly
    # features built on it went to zero, and the completeness gate then fired on
    # every entity. ``audit_column_coverage`` below exists to keep that from
    # recurring - it is asserted by the test suite and shown on the Validation page.
    "opened_ts": ["opened_ts", "opened_at", "opened_on", "case_opened", "open_time",
                  "created_ts", "created_at", "sys_created_on", "detected_at",
                  "first_seen"],
    "resolution_ts": ["resolution_ts", "resolution_timestamp", "resolved_at",
                      "resolved_ts", "resolved_on", "resolutiondate"],
    "resolved_ts": ["resolved_ts", "resolved_at", "resolved_on", "resolution_ts",
                    "escalation_closed_at", "closed_at"],
    "closure_ts": ["closure_ts", "closure_timestamp", "closed_at", "closed_ts",
                   "close_ts", "close_date", "resolved_at", "resolved_ts",
                   "disposition_ts", "closure_date"],
    "escalated_ts": ["escalated_ts", "escalated_at", "escalation_time", "escalation_ts"],
    "response_ts": ["response_ts", "response_timestamp", "responded_at",
                    "first_response_ts"],
    "ts": ["ts", "timestamp", "event_timestamp", "activity_timestamp",
           "activity_ts", "performed_at", "started_at"],
    "last_telemetry_timestamp": ["last_telemetry_timestamp", "last_seen",
                                 "last_event_ts", "last_telemetry_ts",
                                 "last_log_received", "last_heartbeat"],
    "last_scan_timestamp": ["last_scan_timestamp", "last_scan", "last_scanned",
                            "last_assessment"],
    # SLA / closure
    "sla_target_minutes": ["sla_target_minutes", "sla_target", "response_target_minutes",
                          "sla_minutes", "target_minutes", "resolution_target_minutes"],
    "time_to_close_minutes": ["time_to_close_minutes", "time_to_close", "ttc",
                              "response_time_minutes", "duration_minutes_close"],
    "made_sla": ["made_sla", "sla_met", "within_sla", "sla_compliant",
                 "sla_achieved"],
    "closure_reason": ["closure_reason", "close_reason", "closing_reason",
                       "disposition_reason"],
    "root_cause": ["root_cause", "root_cause_text", "rca", "root_cause_summary"],
    "root_cause_documented": ["root_cause_documented", "root_cause_present", "rca_documented"],
    "remediation_documented": ["remediation_documented", "fix_documented",
                              "remediation_present"],
    "remediation_status": ["remediation_status", "fix_status", "mitigation_status"],
    "remediation_reference": ["remediation_reference", "remediation_ticket", "fix_ref",
                              "change_ref", "change_id"],
    "false_positive_reason": ["false_positive_reason", "fp_reason", "benign_reason"],
    "true_positive": ["true_positive", "is_true_positive", "confirmed_incident"],
    "risk_accepted": ["risk_accepted", "risk_acceptance", "accepted_risk"],
    "reopen_count": ["reopen_count", "reopened_count", "reopen_count_total", "reopens",
                     "times_reopened"],
    "reopened_count": ["reopened_count", "reopen_count", "reopens", "times_reopened"],
    # case content
    "case_type": ["case_type", "record_type", "investigation_type"],
    "case_category": ["case_category", "incident_category", "case_classification"],
    "case_description": ["case_description", "short_description", "incident_description",
                         "summary", "description_text", "sys_description"],
    "investigation_note_text": ["investigation_note_text", "investigation_notes",
                                "analyst_notes", "case_notes", "note_text",
                                "resolution_note", "work_notes", "notes_text"],
    "created_from_alert": ["created_from_alert", "from_alert", "alert_derived"],
    "number_of_alerts": ["number_of_alerts", "alert_count", "related_alert_count",
                         "linked_alert_count"],
    "affected_asset_count": ["affected_asset_count", "asset_count", "affected_assets"],
    "affected_user_count": ["affected_user_count", "user_count", "affected_users"],
    "parent_case_id": ["parent_case_id", "parent_case", "master_case_id"],
    "related_case_id": ["related_case_id", "related_case", "linked_case_id"],
    "linked_alert_ids": ["linked_alert_ids", "linked_alerts", "alert_ids",
                         "related_alert_ids"],
    "parent_alert_id": ["parent_alert_id", "parent_alert", "master_alert_id"],
    # workflow events
    "sequence_number": ["sequence_number", "sequence", "step_number", "step_no",
                        "order_index", "event_order", "seq"],
    "activity_type": ["activity_type", "activity", "step", "task", "action",
                      "workflow_step", "state_transition"],
    "activity_subtype": ["activity_subtype", "sub_activity", "action_detail"],
    "evidence_type": ["evidence_type", "evidence", "artefact", "artifact"],
    "action_result": ["action_result", "result", "outcome_detail", "finding",
                      "conclusion"],
    "duration_minutes": ["duration_minutes", "duration", "time_spent_minutes",
                         "effort_minutes"],
    "previous_activity": ["previous_activity", "prev_activity", "from_activity"],
    "next_activity": ["next_activity", "following_activity", "to_activity"],
    # escalation content
    "escalated_to_role": ["escalated_to_role", "escalated_to", "escalation_target",
                          "to_role", "escalation_owner"],
    "escalation_reason": ["escalation_reason", "reason", "escalation_notes",
                          "escalation_justification"],
    "escalation_level": ["escalation_level", "tier", "escalation_tier"],
    "severity_at_escalation": ["severity_at_escalation", "severity_on_escalation"],
    "priority_at_escalation": ["priority_at_escalation", "priority_on_escalation"],
    "decision": ["decision", "escalation_decision", "escalation_outcome"],
    "escalation_status": ["escalation_status", "escalation_state"],
    # inventory
    "asset_name": ["asset_name", "device_name", "name", "asset_desc", "asset_label"],
    "asset_class": ["asset_class", "asset_category", "device_class", "ot_it_class",
                    "system_class"],
    "criticality_tier": ["criticality_tier", "criticality", "cis_tier",
                         "business_criticality", "asset_criticality"],
    "business_criticality": ["business_criticality", "criticality_rating"],
    "environment": ["environment", "env", "stage", "deployment_environment"],
    "business_unit": ["business_unit", "bu", "department", "owner_team"],
    "location": ["location", "site", "site_name", "facility"],
    "data_classification": ["data_classification", "classification_level",
                            "sensitivity"],
    "owner": ["owner", "asset_owner", "custodian"],
    "system_owner": ["system_owner", "application_owner", "service_owner"],
    "monitoring_required": ["monitoring_required", "requires_monitoring",
                            "should_be_monitored"],
    "monitoring_status": ["monitoring_status", "monitored", "telemetry_status",
                          "logging_status", "collection_status"],
    "monitoring_source": ["monitoring_source", "sensor", "telemetry_source",
                          "log_source"],
    "expected_alert_frequency": ["expected_alert_frequency", "expected_alerts_per_month",
                                 "expected_alert_rate", "expected_events"],
    "actual_alert_count": ["actual_alert_count", "observed_alerts", "alert_volume"],
    "vulnerability_status": ["vulnerability_status", "patch_status",
                             "vulnerability_state"],
}

# Mapping-quality preference: an exact column-name match always beats a
# prefix/suffix match, so ``alert_id`` wins over ``alert_id_legacy``.
MATCH_EXACT, MATCH_SUFFIX, MATCH_PREFIX = 3, 2, 1


def _norm(name: Any) -> str:
    """Normalise a source column header for comparison."""
    return str(name).strip().lower().replace(" ", "_").replace("-", "_").replace(".", "_")


def audit_column_coverage() -> dict:
    """Self-check: canonical columns that no synonym entry can ever map.

    Every canonical column must appear as a key in ``COLUMN_SYNONYMS`` (or be the
    table's identifier, which is matched directly). A canonical column with no
    synonym entry is not merely unmapped on one file - it can never be populated by
    *any* submission, so the feature built on it is permanently zero. That failure is
    invisible in a successful ingest, which is exactly why it is checked explicitly
    and surfaced on the Validation page.
    """
    blind = {}
    for table, cols in TABLE_COLUMNS.items():
        unmappable = [c for c in cols if c not in COLUMN_SYNONYMS and c != TABLE_ID[table]]
        if unmappable:
            blind[table] = unmappable
    return blind


def guess_column_mapping(df: pd.DataFrame, target_table: str) -> dict:
    """Map canonical columns -> source columns, **scoped to ``target_table``**.

    Only canonical columns that actually exist on the target table are considered,
    so the same synonym list can be shared across all tables without an alert
    column ever being bound onto a case field. The best-scoring match wins per
    canonical column (exact > suffix > prefix).
    """
    columns = {_norm(c): c for c in df.columns}
    allowed = set(TABLE_COLUMNS.get(target_table, list(TABLE_COLUMNS["alerts"])))
    mapping: dict[str, Any] = {}

    for canon_col, variants in COLUMN_SYNONYMS.items():
        if canon_col not in allowed or canon_col in mapping:
            continue
        best_rank, best_source = 0, None
        for variant in variants:
            variant = _norm(variant)
            for norm_col, original in columns.items():
                if norm_col == variant:
                    rank = MATCH_EXACT
                elif norm_col.endswith("_" + variant):
                    rank = MATCH_SUFFIX
                elif norm_col.startswith(variant + "_"):
                    rank = MATCH_PREFIX
                else:
                    continue
                if rank > best_rank:
                    best_rank, best_source = rank, original
            if best_rank == MATCH_EXACT:
                break
        if best_source is not None:
            mapping[canon_col] = best_source

    # The target table's own identifier always wins over a synonym: if a file has
    # both ``id`` and ``alert_id``, ``alert_id`` is the alert identifier.
    id_col = TABLE_ID.get(target_table)
    if id_col and id_col in columns:
        mapping[id_col] = columns[id_col]
    return mapping


def mapping_report(df: pd.DataFrame, target_table: str, mapping: dict) -> dict:
    """Summarise how well a file mapped onto its target table (UI + validation).

    Returns row/column counts plus the canonical columns that were **not** found
    in the file, which is the honest statement of what the ingestion could not see.
    """
    expected = TABLE_COLUMNS.get(target_table, [])
    matched = [c for c in expected if c in mapping]
    missing = [c for c in expected if c not in mapping]
    required = REQUIRED_FOR_ANALYSIS.get(target_table, [])
    return dict(
        table=target_table,
        rows=int(len(df)),
        columns_in_file=int(len(df.columns)),
        columns_matched=len(matched),
        columns_expected=len(expected),
        matched_columns=matched,
        missing_columns=missing,
        unmatched_required=[c for c in required if c not in mapping],
        # informational: columns the file carries that the canonical schema ignores
        extra_columns=[str(c) for c in df.columns if c not in set(mapping.values())][:40],
    )


# ---------------------------------------------------------------------------
# Value canonicalisation
# ---------------------------------------------------------------------------

def normalize_severity(value: Any) -> str:
    """Convert various severity formats to standard: critical/high/medium/low."""
    if pd.isna(value):
        return 'low'

    val_str = str(value).lower().strip()

    if val_str in ['1', 'p1', 'critical', 'urgent', 'severe', 'sev1', 's1', 'catastrophic']:
        return 'critical'
    if val_str in ['2', 'p2', 'high', 'sev2', 's2', 'major']:
        return 'high'
    if val_str in ['3', 'p3', 'medium', 'med', 'sev3', 's3', 'moderate']:
        return 'medium'
    if val_str in ['4', 'p4', 'low', 'info', 'informational', 'sev4', 's4', 'minor']:
        return 'low'

    return 'medium'  # default fallback


_NULL_STRINGS = {"", "nan", "none", "nat", "null", "n/a", "na", "-", "--"}


def _is_blank(value) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower() in _NULL_STRINGS


def _as_bool_int(value) -> int:
    """Accept true/false, yes/no, 1/0 and the strings pandas produces."""
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "yes", "y", "t", "1", "1.0"):
            return 1
        if text in ("false", "no", "n", "f", "0", "0.0", ""):
            return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def clean_value(col: str, value):
    """Coerce one cell into something the canonical schema accepts."""
    if _is_blank(value):
        return None
    if col == "severity":
        return normalize_severity(value)
    if col in BOOL_COLUMNS:
        return _as_bool_int(value)
    if col in NUMERIC_COLUMNS:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    text = str(value).strip()
    return text if text.lower() not in ("nan", "none", "nat") else None


# ---------------------------------------------------------------------------
# ISO-8601 date standardisation
# ---------------------------------------------------------------------------

def _parse_datetimes(series: pd.Series) -> pd.Series:
    """Parse a mixed bag of source date formats, returning a datetime Series.

    Handles the three shapes CSE exports actually arrive in:

    * already-ISO / unambiguous strings (``2026-01-31T04:12:00``)
    * locale-style strings (``31/01/2026 04:12``) - retried with ``dayfirst``
      when the first pass leaves most values unparsed
    * epoch numbers (seconds, milliseconds or nanoseconds)
    """
    if series is None or len(series) == 0:
        return pd.Series(dtype="datetime64[ns]")

    numeric = pd.to_numeric(series, errors="coerce")
    numeric_share = float(numeric.notna().mean()) if len(series) else 0.0
    if numeric_share > 0.8:
        # Epoch timestamps. Pick the unit from the magnitude of the median value so
        # both second- and millisecond-precision exports land in the right century.
        median = float(numeric.dropna().median()) if numeric.notna().any() else 0.0
        if median > 1e17:
            unit = "ns"
        elif median > 1e14:
            unit = "us"
        elif median > 1e11:
            unit = "ms"
        else:
            unit = "s"
        return pd.to_datetime(numeric, unit=unit, errors="coerce")

    text = series.astype(str).str.strip()
    text = text.mask(text.str.lower().isin(_NULL_STRINGS))
    try:
        parsed = pd.to_datetime(text, errors="coerce", format="mixed")
    except (TypeError, ValueError):
        parsed = pd.to_datetime(text, errors="coerce")
    if parsed.isna().mean() > 0.5 and text.notna().mean() > 0.5:
        try:
            alt = pd.to_datetime(text, errors="coerce", dayfirst=True, format="mixed")
        except (TypeError, ValueError):
            alt = pd.to_datetime(text, errors="coerce", dayfirst=True)
        if alt.isna().mean() < parsed.isna().mean():
            parsed = alt
    return parsed


def to_iso8601(series: pd.Series) -> pd.Series:
    """Convert any date-ish series to ISO-8601 (YYYY-MM-DDTHH:MM:SS) or None."""
    if series is None or len(series) == 0:
        return series
    parsed = _parse_datetimes(series)
    if parsed.isna().all():
        # Nothing parsed: fall back to the original text rather than destroying it,
        # so the validator can report the column as unparseable instead of empty.
        return series.where(~series.map(_is_blank), None)
    formatted = parsed.dt.strftime(ISO_FORMAT)
    return formatted.where(parsed.notna(), None)


def coerce_dates(frame: pd.DataFrame, target_table: str) -> pd.DataFrame:
    """Convert every canonical timestamp column of ``target_table`` to ISO-8601.

    Runs on the *mapped* frame, so only columns that survived mapping are touched.
    Returns the frame with those columns as ISO-8601 strings (or None).
    """
    if frame is None or frame.empty:
        return frame
    for col in DATE_COLUMNS.get(target_table, []):
        if col in frame.columns:
            frame[col] = to_iso8601(frame[col])
    return frame


def find_unmapped_date_columns(df: pd.DataFrame, mapping: dict) -> list[str]:
    """Name source columns that look like dates but were not mapped.

    Purely informational - surfaced in the ingestion report so a supervisor can see
    that a submission carried a timestamp the canonical schema does not use.
    """
    mapped_sources = {str(v) for v in mapping.values()}
    found = []
    for col in df.columns:
        if str(col) in mapped_sources:
            continue
        name = _norm(col)
        if not any(token in name for token in ("date", "time", "ts", "timestamp", "at")):
            continue
        sample = df[col].dropna()
        if sample.empty:
            continue
        parsed = _parse_datetimes(sample.head(50))
        if float(parsed.notna().mean()) > 0.8:
            found.append(str(col))
    return found[:20]


# ---------------------------------------------------------------------------
# Record building (shared by the single-file UI path and the bulk loader)
# ---------------------------------------------------------------------------

def build_records(df: pd.DataFrame, entity_id: str, target_table: str, mapping: dict):
    """Map, canonicalise and identify one file's rows.

    Returns ``(records, report)`` where ``records`` are ready for parameterised
    insertion and ``report`` summarises the mapping quality for that file.
    """
    canon_cols = [c for c in TABLE_COLUMNS.get(target_table, []) if c in mapping]
    if not canon_cols:
        return [], mapping_report(df, target_table, mapping)

    mapped = pd.DataFrame({c: df[mapping[c]] for c in canon_cols})
    # Dates first: an ISO string cannot then be re-parsed into something else.
    mapped = coerce_dates(mapped, target_table)

    id_col = TABLE_ID[target_table]
    if id_col in mapped.columns:
        missing = mapped[id_col].map(_is_blank)
        if missing.any():
            mapped.loc[missing, id_col] = [str(uuid.uuid4()) for _ in range(int(missing.sum()))]
    else:
        mapped[id_col] = [str(uuid.uuid4()) for _ in range(len(mapped))]

    records = []
    for row in mapped.to_dict("records"):
        rec = {k: clean_value(k, v) for k, v in row.items()}
        rec[id_col] = str(rec.get(id_col) or uuid.uuid4())
        rec["entity_id"] = entity_id
        records.append(rec)

    report = mapping_report(df, target_table, mapping)
    report["unmapped_date_columns"] = find_unmapped_date_columns(df, mapping)
    return records, report


def insert_records(conn, target_table: str, records: list[dict], *, replace: bool = True,
                   entity_id: str | None = None) -> int:
    """Insert records, replacing this entity's rows for that table in one transaction.

    ``replace=True`` is what makes re-ingestion idempotent *and* corrective: a
    corrected re-submission replaces the previous upload for that table rather
    than being ignored by ``INSERT OR IGNORE``, so fixes actually land. The DELETE
    and the INSERT share a single transaction, so a failure part-way leaves the
    previously ingested rows intact.
    """
    if not records:
        return 0
    # Headless ingestion (python -m ingestion.bulk on a fresh checkout) can run before
    # the app has ever opened the database, so the schema is created on first write.
    # Skipped when the caller already holds a transaction on another connection — the
    # schema must then already exist, and creating it would take a write lock.
    if not conn.in_transaction:
        ensure_schema()
    cols = [c for c in TABLE_COLUMNS[target_table] if c in records[0]] + ["entity_id"]
    cols = list(dict.fromkeys(cols))
    if replace and entity_id:
        conn.execute(f"DELETE FROM {target_table} WHERE entity_id = ?", (entity_id,))
    stmt = (f"INSERT INTO {target_table} ({', '.join(cols)}) "
            f"VALUES ({', '.join(':' + c for c in cols)})")
    conn.executemany(stmt, [{c: r.get(c) for c in cols} for r in records])
    return len(records)


def ingest_structured_file(df: pd.DataFrame, entity_id: str, target_table: str,
                           column_mapping: dict, db_path=None):
    """Apply mapping, normalise, and bulk insert (single-file API).

    Kept for callers that already hold a DataFrame; the bulk loader uses
    ``build_records`` + ``insert_records`` directly so it can batch one transaction
    per submission folder.
    """
    if df is None or df.empty:
        return {}

    records, report = build_records(df, entity_id, target_table, column_mapping)
    if not records:
        return report

    with get_db(db_path) as conn:
        try:
            insert_records(conn, target_table, records, replace=True, entity_id=entity_id)
            conn.execute(
                "INSERT INTO audit_log (action, entity_id, details) VALUES (?, ?, ?)",
                (f"INGEST_{target_table.upper()}", entity_id,
                 f"Ingested {len(records)} rows into {target_table} "
                 f"({report['columns_matched']}/{report['columns_expected']} canonical columns matched)")
            )
        except Exception as e:
            raise ValueError(f"Database insertion failed: {e}")
    return report


def ingest_document(doc_data: dict, entity_id: str, filename: str, file_type: str, db_path=None):
    """Insert text and metadata into documents table."""
    doc_id = str(uuid.uuid4())
    full_text = doc_data.get('full_text', '')
    referenced_ids = json.dumps(doc_data.get('referenced_ids', []))

    with get_db(db_path) as conn:
        conn.execute(
            """INSERT INTO documents
               (doc_id, entity_id, filename, file_type, full_text, referenced_ids)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (doc_id, entity_id, filename, file_type, full_text, referenced_ids)
        )
        conn.execute(
            "INSERT INTO audit_log (action, entity_id, details) VALUES (?, ?, ?)",
            ("INGEST_DOCUMENT", entity_id, f"Ingested document {filename}")
        )


def compute_data_quality_score(df: pd.DataFrame, required_columns: list) -> float:
    """Fraction of non-null values in required columns."""
    if df.empty or not required_columns:
        return 0.0

    existing_cols = [c for c in required_columns if c in df.columns]
    if not existing_cols:
        return 0.0

    total_cells = len(df) * len(existing_cols)
    non_null_cells = df[existing_cols].notna().sum().sum()

    return float(non_null_cells / total_cells)


def auto_detect_table_type(df: pd.DataFrame) -> str:
    """Guess which canonical table a submitted file populates.

    Order matters, because the submission files cross-reference each other:

    * escalation exports reference ``alert_id``, so they must be tested before alerts;
    * alert exports carry a ``case_id`` (the case an alert was linked to), so a naive
      ``case_id -> cases`` rule would misclassify the single most important file type;
    * disposition/closure records also carry ``disposition``, which alerts have too,
      so their own identifier columns are checked first.

    Most specific signature first, generic default last.
    """
    cols = {_norm(c) for c in df.columns}

    def has(*names):
        return any(n in cols for n in names)

    # 1. Investigation workflow events (process mining)
    if has('investigation_event_id', 'activity_type', 'sequence_number') and not has('alert_category'):
        return 'investigations'

    # 2. Disposition & closure records
    if has('disposition_id') or has('made_sla', 'closure_reason', 'time_to_close_minutes'):
        return 'dispositions'

    # 3. Escalation records
    if has('escalation_id', 'escalated_to_role', 'escalation_reason', 'from_team', 'to_team'):
        return 'escalations'
    if has('escalated_ts') and not has('acknowledged_ts', 'acknowledged_at', 'closed_ts'):
        return 'escalations'

    # 4. Alerts: an alert identifier and/or the ack -> close lifecycle
    if has('alert_id', 'acknowledged_ts', 'acknowledged_at', 'alert_category') and \
            not has('case_status', 'case_description'):
        return 'alerts'

    # 5. Cases: a case/workflow record with investigation evidence
    if has('case_id') and has('investigation_note_text', 'investigation_notes',
                              'root_cause_documented', 'case_status', 'case_description',
                              'opened_ts', 'reopened_count'):
        return 'cases'

    # 6. Asset inventory
    if has('asset_id', 'asset_class', 'criticality_tier', 'monitoring_status') and not has('alert_id'):
        return 'asset_inventory'

    if has('case_id'):
        return 'cases'

    return 'alerts'  # default: most ingested files are alert exports
