"""
Full-population evidence resolution
===================================
A finding stores a **capped sample** of its evidence ids (25 by default, see
``common.cap_evidence``) purely to keep the database small; the true population is
kept as ``evidence_count``. That is the right trade-off for storage, but it is the
wrong answer for an examiner who has decided a finding deserves a manual review and
wants *every* record behind it.

This module reconstructs the complete evidence set on demand, by re-deriving the
same masks the detectors use from the canonical tables. Nothing is guessed: each
rule names the table and the condition, and the count it produces is checked against
the finding's stored ``evidence_count`` so a mismatch is visible rather than silent.

Rules that are inherently *absence* findings (a missing night window, a silent
asset, a metric below a benchmark) have no per-record list to hand over. Those
return an empty list plus an explicit note saying so, which is more useful to an
examiner than an empty CSV with no explanation.
"""

from __future__ import annotations

import pathlib
import sys

import pandas as pd

_DET_DIR = pathlib.Path(__file__).resolve().parent
for _p in (str(_DET_DIR.parent), str(_DET_DIR)):
    if _p not in sys.path:
        sys.path.append(_p)

from features import (  # noqa: E402
    _text_col, enrich_alerts, rework_loop_cases, severity_rank,
)

# Columns worth exporting alongside the id, per table (a readable CSV, not a dump).
EXPORT_COLUMNS = {
    "alerts": ["alert_id", "severity", "alert_category", "asset_id", "hostname",
               "created_ts", "acknowledged_ts", "closed_ts", "disposition", "case_id",
               "assigned_analyst_id"],
    "cases": ["case_id", "opened_ts", "closed_ts", "severity", "priority", "status",
              "assigned_analyst", "investigation_note_text"],
    "dispositions": ["disposition_id", "alert_id", "case_id", "disposition", "closure_ts",
                     "time_to_close_minutes", "sla_target_minutes", "made_sla",
                     "true_positive", "risk_accepted", "reopen_count", "root_cause",
                     "remediation_status", "remediation_reference"],
    "investigations": ["investigation_id", "case_id", "ts", "sequence_number",
                       "activity_type", "analyst_id", "evidence_type", "action_result",
                       "duration_minutes"],
    "asset_inventory": ["asset_id", "asset_name", "asset_class", "criticality_tier",
                        "monitoring_status", "monitoring_source",
                        "last_telemetry_timestamp"],
    "escalations": ["escalation_id", "alert_id", "case_id", "escalated_ts",
                    "escalated_to_role", "decision", "severity_at_escalation"],
}

# rule id -> (canonical table its evidence lives in, human basis)
RULE_EVIDENCE_BASIS = {
    "EG-001": ("alerts", "critical/high alerts closed within the configured fast-closure window"),
    "EG-002": ("alerts", "critical alerts with no escalation record against the alert or its case"),
    "EG-003": ("cases", "cases whose investigation note is a near-duplicate of another note"),
    "EG-004": ("alerts", "alerts belonging to a chronic repeat (asset, category) pair"),
    "EG-005": ("alerts", "alerts closed inside an abnormally busy closure hour"),
    "EG-006": ("cases", "every case record submitted (the finding is about their content)"),
    "EG-007": ("alerts", "critical/high alerts owned by the most loaded analyst"),
    "NS-001": ("asset_inventory", "critical assets that generated no alert in the window"),
    "NS-002": ("alerts", "every alert submitted (the finding is about the categories absent)"),
    "NS-003": ("alerts", "alert rows that fall inside the night window"),
    "NS-004": ("alerts", "every alert submitted (the finding is about the volume)"),
    "NS-005": ("alerts", "critical/high alerts with no linked case-management record"),
    "NS-006": ("asset_inventory", "critical assets whose monitoring status is not active"),
    "NS-007": ("alerts", "alert rows that fall on a Saturday or Sunday"),
    "IM-001": ("dispositions", "closure records that missed their target or misreport compliance"),
    "IM-002": ("cases", "cases recorded below the severity of the alert they came from"),
    "IM-003": ("dispositions", "significant closures with no root cause or remediation record"),
    "IM-004": ("investigations", "workflow events of investigations that repeat a completed step"),
    "IM-005": ("dispositions", "closure records carrying a reopen count greater than zero"),
    "IM-006": ("cases", "cases with no (or fewer than the expected) workflow events"),
    "IM-007": ("dispositions", "accepted risks with no named accepting authority"),
    "IM-008": ("kpi", "the declared KPI keys the entity's own records contradict"),
    "IM-009": ("investigations", "workflow events of cases whose total recorded time is under the floor"),
}


def load_entity_frames(conn, entity_id: str) -> dict:
    """Read one entity's canonical tables into frames (the drill-down working set)."""
    def q(sql):
        return pd.read_sql_query(sql, conn, params=(entity_id,))
    return dict(
        alerts=q("SELECT * FROM alerts WHERE entity_id = ?"),
        cases=q("SELECT * FROM cases WHERE entity_id = ?"),
        escalations=q("SELECT * FROM escalations WHERE entity_id = ?"),
        assets=q("SELECT * FROM asset_inventory WHERE entity_id = ?"),
        investigations=q("SELECT * FROM investigations WHERE entity_id = ?"),
        dispositions=q("SELECT * FROM dispositions WHERE entity_id = ?"),
    )


def evidence_ids_for(rule_id: str, frames: dict, thresholds: dict | None = None) -> tuple[str, list[str], str]:
    """Reconstruct the full evidence set for one finding.

    Returns ``(table, ids, note)``. ``ids`` is empty and ``note`` explains why for
    findings that are derived from absence or from a population metric.
    """
    thresholds = thresholds or {}
    alerts, cases = frames.get("alerts"), frames.get("cases")
    dispositions, investigations = frames.get("dispositions"), frames.get("investigations")
    assets, escalations = frames.get("assets"), frames.get("escalations")
    table, basis = RULE_EVIDENCE_BASIS.get(rule_id, ("", ""))

    def col(frame, name, default=""):
        return frame[name] if frame is not None and name in frame.columns else pd.Series(dtype=object)

    if rule_id == "IM-008":
        return ("kpi", [], basis)

    if rule_id in ("EG-001", "EG-002", "EG-004", "EG-005", "EG-007", "NS-002", "NS-003",
                   "NS-004", "NS-005", "NS-007"):
        if alerts is None or alerts.empty:
            return (table, [], basis)
        enriched = enrich_alerts(alerts, cases, escalations, thresholds)
        if enriched.empty:
            return (table, [], basis)
        if rule_id == "EG-001":
            mask = enriched["is_crit_high"] & (
                enriched["close_minutes"] < float(thresholds.get("fast_closure_minutes", 5)))
        elif rule_id == "EG-002":
            mask = enriched["is_critical"] & ~enriched["escalated"]
        elif rule_id == "EG-004":
            mask = enriched["is_repeat"]
        elif rule_id == "EG-005":
            # Alerts closed inside an abnormally busy closure hour - the same
            # mean + sigma test the detector uses.
            closed = enriched[enriched["close_dt"].notna()]
            if closed.empty:
                return (table, [], basis)
            sigma = float(thresholds.get("bulk_closure_sigma", 3.0))
            counts = closed["close_dt"].dt.floor("h").value_counts()
            limit = counts.mean() + sigma * (counts.std(ddof=0) or 0.0)
            bulk_hours = counts[counts > limit].index
            mask = enriched["close_dt"].dt.floor("h").isin(bulk_hours)
        elif rule_id == "EG-007":
            sig = enriched[enriched["is_crit_high"]]
            owner = col(sig, "assigned_analyst_id").astype(str)
            if owner.empty:
                return (table, [], basis)
            counts = owner.value_counts()
            mask = enriched["is_crit_high"] & (
                col(enriched, "assigned_analyst_id").astype(str) == str(counts.index[0]))
        elif rule_id == "NS-003":
            mask = enriched["is_night"]
        elif rule_id == "NS-005":
            mask = enriched["missing_case_flag"]
        elif rule_id == "NS-007":
            mask = enriched["is_weekend"]
        else:  # NS-002 / NS-004 - population-level: the whole submitted set is the basis
            mask = pd.Series(True, index=enriched.index)
        return (table, [str(v) for v in enriched.loc[mask, "alert_id"].tolist()], basis)

    if rule_id in ("EG-003", "EG-006", "IM-002", "IM-006"):
        if cases is None or cases.empty:
            return (table, [], basis)
        if rule_id == "EG-003":
            from features import note_similarity_rate
            rate, dup_ids, _ = note_similarity_rate(
                cases["investigation_note_text"],
                float(thresholds.get("template_cosine_threshold", 0.85)),
                max_notes=int(thresholds.get("template_note_sample_cap", 5000)))
            # the stored sample holds case *indices* from the feature pass; resolve to ids
            resolved = [str(cases.loc[i, "case_id"]) for i in dup_ids if i in cases.index]
            return (table, resolved, basis)
        if rule_id == "EG-006":
            return (table, [str(c) for c in cases["case_id"].tolist()], basis)
        if rule_id == "IM-002":
            case_rank = severity_rank(col(cases, "severity"))
            alert_rank = pd.Series(dtype=float)
            if alerts is not None and not alerts.empty and "case_id" in alerts.columns:
                linked = alerts[alerts["case_id"].astype(str).str.strip() != ""]
                alert_rank = (linked.assign(r=severity_rank(linked["severity"]))
                              .groupby("case_id")["r"].max())
            ranks = cases["case_id"].map(alert_rank).fillna(0)
            mask = (ranks >= 2) & (case_rank.values < ranks.values)
            return (table, [str(c) for c in cases.loc[mask, "case_id"].tolist()], basis)
        # IM-006 - cases with fewer than the expected workflow events
        min_events = int(thresholds.get("investigation_event_min", 2))
        per_case = (investigations.groupby("case_id").size() if investigations is not None
                    and not investigations.empty else pd.Series(dtype=int))
        events = cases["case_id"].map(per_case).fillna(0)
        mask = events < min_events
        return (table, [str(c) for c in cases.loc[mask, "case_id"].tolist()], basis)

    if rule_id in ("IM-001", "IM-003", "IM-005", "IM-007"):
        if dispositions is None or dispositions.empty:
            return (table, [], basis)
        measured = pd.to_numeric(col(dispositions, "time_to_close_minutes"), errors="coerce")
        target = pd.to_numeric(col(dispositions, "sla_target_minutes"), errors="coerce")
        breach = (target > 0) & (measured > target)
        if rule_id == "IM-001":
            reported = _bool_col(dispositions, "made_sla")
            mask = breach | (breach & (reported == 1))
        elif rule_id == "IM-005":
            mask = pd.to_numeric(col(dispositions, "reopen_count"), errors="coerce").fillna(0) > 0
        elif rule_id == "IM-007":
            accepted = _bool_col(dispositions, "risk_accepted") == 1
            mask = accepted & (_text_col(dispositions, "risk_acceptance_authority") == "")
        else:  # IM-003
            rank = pd.Series(0.0, index=dispositions.index)
            if alerts is not None and not alerts.empty and "alert_id" in alerts.columns:
                a_rank = severity_rank(alerts.set_index("alert_id")["severity"])
                rank = col(dispositions, "alert_id").map(a_rank).fillna(0)
            true_pos = _bool_col(dispositions, "true_positive") == 1
            significant = (rank >= 2) | true_pos
            no_root = significant & (_text_col(dispositions, "root_cause") == "")
            no_remediation = significant & (
                ~_text_col(dispositions, "remediation_status").str.lower()
                .isin(["completed", "in_progress"])
                & (_text_col(dispositions, "remediation_reference") == ""))
            mask = no_root | no_remediation
        return (table, [str(v) for v in dispositions.loc[mask, "disposition_id"].tolist()], basis)

    if rule_id in ("IM-004", "IM-009"):
        if investigations is None or investigations.empty:
            return (table, [], basis)
        if rule_id == "IM-004":
            loop_cases = set(rework_loop_cases(investigations))
            mask = investigations["case_id"].astype(str).isin({str(c) for c in loop_cases})
        else:
            floor = float(thresholds.get("investigation_time_min_minutes", 2.0))
            durations = pd.to_numeric(col(investigations, "duration_minutes"), errors="coerce").fillna(0)
            per_case = investigations.assign(_d=durations).groupby("case_id")["_d"].sum()
            thin = {str(c) for c in per_case.index[per_case < floor]}
            mask = investigations["case_id"].astype(str).isin(thin)
        return (table, [str(v) for v in investigations.loc[mask, "investigation_id"].tolist()], basis)

    if rule_id in ("NS-001", "NS-006"):
        if assets is None or assets.empty:
            return (table, [], basis)
        crit = assets["criticality_tier"].astype(str).str.contains("critical|tier.?1", case=False, na=False, regex=True)
        if rule_id == "NS-006":
            status = _text_col(assets, "monitoring_status").str.lower()
            mask = crit & ~status.isin(["active", "enabled", "true", "1", "yes", ""])
        else:
            alerted = ({str(a) for a in alerts["asset_id"].dropna()} if alerts is not None
                       and not alerts.empty and "asset_id" in alerts.columns else set())
            mask = crit & ~assets["asset_id"].astype(str).isin(alerted)
        return (table, [str(v) for v in assets.loc[mask, "asset_id"].tolist()], basis)

    # Metric / population / absence findings carry no per-record list.
    return (table, [], basis)


def build_evidence_export(conn, finding: dict, frames: dict | None = None,
                          thresholds: dict | None = None) -> pd.DataFrame:
    """Full evidence table for one finding, ready to export as CSV.

    The ID list is reconstructed at full population, not from the stored sample; the
    rows are then joined back to their source table so the export carries the detail
    an examiner needs to check the finding by hand.
    """
    rule_id = str(finding.get("rule_id", ""))
    entity_id = str(finding.get("entity_id", ""))
    frames = frames if frames is not None else load_entity_frames(conn, entity_id)
    table, ids, basis = evidence_ids_for(rule_id, frames, thresholds)

    header = dict(
        rule_id=rule_id, entity_id=entity_id,
        finding_title=finding.get("title", ""),
        declared_by_tool=int(finding.get("evidence_count", 0) or 0),
        resolved_records=len(ids),
        evidence_basis=basis,
    )
    if rule_id == "IM-008":
        import json as _json
        try:
            keys = _json.loads(finding.get("evidence_ids") or "[]")
        except (TypeError, ValueError):
            keys = []
        # The evidence for this rule *is* the set of contradicted declarations, so the
        # resolved count must reflect the keys, not the (empty) record-id list.
        header = dict(header, resolved_records=len(keys))
        rows = [dict(header, evidence_id=str(k), evidence_table="declared_kpi") for k in keys]
        return pd.DataFrame(rows) if rows else pd.DataFrame(
            [dict(header, evidence_id="", evidence_table="declared_kpi")])

    if not ids:
        note = ("This finding is derived from the absence of expected records or from a "
                "population-level metric, so there is no per-record list to export. "
                "Basis: " + (basis or "see the finding rationale"))
        return pd.DataFrame([dict(**header, evidence_id="", evidence_table=table,
                                  note=note)])

    source = {"alerts": frames.get("alerts"), "cases": frames.get("cases"),
              "dispositions": frames.get("dispositions"),
              "investigations": frames.get("investigations"),
              "asset_inventory": frames.get("assets"),
              "escalations": frames.get("escalations")}.get(table)
    id_col = {"alerts": "alert_id", "cases": "case_id", "dispositions": "disposition_id",
              "investigations": "investigation_id", "asset_inventory": "asset_id",
              "escalations": "escalation_id"}.get(table)
    if source is None or source.empty or id_col not in source.columns:
        return pd.DataFrame([dict(**header, evidence_id=i, evidence_table=table) for i in ids])

    subset = source[source[id_col].astype(str).isin({str(i) for i in ids})].copy()
    cols = [c for c in EXPORT_COLUMNS.get(table, []) if c in subset.columns]
    subset = subset[cols]
    for key, value in header.items():
        subset[key] = value
    subset["evidence_table"] = table
    subset = subset.rename(columns={id_col: "evidence_id"})
    return subset


def _bool_col(frame: pd.DataFrame, col: str) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(0, index=frame.index)
    series = frame[col]
    if series.dtype == bool:
        return series.astype(int)
    return (series.astype(str).str.strip().str.lower()
            .isin(["1", "1.0", "true", "yes", "y", "t"])).astype(int)
