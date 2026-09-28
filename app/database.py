"""
SAT-SA Database Module
======================
SQLite database setup, schema creation, and helper functions.
Single-file database — copy satsa.db to any machine and the app works.
"""

import re
import sqlite3
import pathlib
from datetime import datetime
from contextlib import contextmanager

DB_PATH = pathlib.Path(__file__).parent / "satsa.db"


def get_connection(db_path: str | pathlib.Path | None = None) -> sqlite3.Connection:
    """Get a SQLite connection with row_factory set."""
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # NORMAL is safe with WAL and materially faster for the batched bulk ingest
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db(db_path=None):
    """Context manager for database connections."""
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_schema_ready = False


def ensure_schema(db_path=None) -> bool:
    """Create the schema if this process has not already done so. Returns True if it ran.

    Any ingestion path can be used headlessly (``python -m ingestion.bulk`` on a fresh
    checkout, before the Streamlit app has ever been started), so the schema cannot be
    assumed to already exist. This is the once-per-process guard those paths call.
    """
    global _schema_ready
    if _schema_ready:
        return False
    init_db(db_path)
    _schema_ready = True
    return True


def init_db(db_path=None):
    """Create all tables if they don't exist, then bring older databases forward."""
    with get_db(db_path) as conn:
        # auto_vacuum must be set before the first table is created; it then keeps
        # the file from retaining freed space after deletes (the previous build of
        # this prototype had a 102 MB database that was ~99% free pages).
        mode = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
        if mode == 0:
            conn.execute("PRAGMA auto_vacuum=FULL")
        conn.executescript(SCHEMA_SQL)
        applied = migrate_schema(conn)
        # Insert audit log entry for initialization
        conn.execute(
            "INSERT INTO audit_log (action, details) VALUES (?, ?)",
            ("DB_INIT", f"Database initialized at {datetime.now().isoformat()}"
                        + (f"; migrated: {', '.join(applied)}" if applied else ""))
        )


# ---------------------------------------------------------------------------
# Schema migration
#
# A supervisor may already be holding a database from an earlier build. Rather
# than telling them to throw away their register and submissions, every column
# added by a later build is declared here and added in place on startup. Adding a
# column is safe and lossless in SQLite; nothing is ever dropped.
# ---------------------------------------------------------------------------

MIGRATION_COLUMNS = {
    "entities": {
        "cin": "TEXT DEFAULT ''", "state": "TEXT DEFAULT ''",
        "soc_model": "TEXT DEFAULT ''", "soc_coverage": "TEXT DEFAULT ''",
        "analyst_count": "INTEGER DEFAULT 0", "contact_email": "TEXT DEFAULT ''",
        "contact_phone": "TEXT DEFAULT ''", "review_period_start": "TEXT DEFAULT ''",
        "review_period_end": "TEXT DEFAULT ''", "declared_controls": "TEXT DEFAULT '[]'",
        "declared_kpis": "TEXT DEFAULT '{}'", "source": "TEXT DEFAULT 'manual'",
        "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    },
    "alerts": {
        "alert_type": "TEXT DEFAULT ''", "alert_name": "TEXT DEFAULT ''",
        "priority": "TEXT DEFAULT ''", "risk_score": "REAL DEFAULT 0",
        "confidence": "REAL DEFAULT 0", "status": "TEXT DEFAULT ''",
        "detected_ts": "TIMESTAMP", "sla_target_minutes": "REAL DEFAULT 0",
        "parent_alert_id": "TEXT DEFAULT ''", "detection_source": "TEXT DEFAULT ''",
        "destination_ip": "TEXT DEFAULT ''", "source_country": "TEXT DEFAULT ''",
        "destination_country": "TEXT DEFAULT ''", "user_id": "TEXT DEFAULT ''",
        "created_by": "TEXT DEFAULT ''", "asset_type": "TEXT DEFAULT ''",
    },
    "cases": {
        "updated_ts": "TIMESTAMP", "resolution_ts": "TIMESTAMP",
        "case_type": "TEXT DEFAULT ''", "case_category": "TEXT DEFAULT ''",
        "case_description": "TEXT DEFAULT ''", "created_from_alert": "INTEGER DEFAULT 0",
        "number_of_alerts": "INTEGER DEFAULT 0", "affected_asset_count": "INTEGER DEFAULT 0",
        "affected_user_count": "INTEGER DEFAULT 0", "parent_case_id": "TEXT DEFAULT ''",
        "related_case_id": "TEXT DEFAULT ''",
    },
    "escalations": {
        "from_role": "TEXT DEFAULT ''", "to_role": "TEXT DEFAULT ''",
        "severity_at_escalation": "TEXT DEFAULT ''",
        "priority_at_escalation": "TEXT DEFAULT ''", "decision": "TEXT DEFAULT ''",
        "approved_by": "TEXT DEFAULT ''", "response_ts": "TIMESTAMP",
    },
    "asset_inventory": {
        "ip_address": "TEXT DEFAULT ''", "business_criticality": "TEXT DEFAULT ''",
        "location": "TEXT DEFAULT ''", "data_classification": "TEXT DEFAULT ''",
        "owner": "TEXT DEFAULT ''", "system_owner": "TEXT DEFAULT ''",
        "monitoring_required": "TEXT DEFAULT 'true'", "monitoring_source": "TEXT DEFAULT ''",
        "expected_alert_frequency": "REAL DEFAULT 0", "actual_alert_count": "INTEGER DEFAULT 0",
        "last_scan_timestamp": "TIMESTAMP", "vulnerability_status": "TEXT DEFAULT ''",
    },
    "entity_metrics": {
        "total_dispositions": "INTEGER DEFAULT 0", "sla_breach_rate": "REAL DEFAULT 0",
        "sla_misreport_rate": "REAL DEFAULT 0", "sla_misreport_count": "INTEGER DEFAULT 0",
        "close_time_over_target_median": "REAL DEFAULT 0",
        "true_positive_rate": "REAL DEFAULT 0", "risk_accept_rate": "REAL DEFAULT 0",
        "risk_accept_no_authority_rate": "REAL DEFAULT 0",
        "no_root_cause_gap": "REAL DEFAULT 0", "no_remediation_gap": "REAL DEFAULT 0",
        "significant_closures": "INTEGER DEFAULT 0",
        "investigation_events": "INTEGER DEFAULT 0", "cases_with_events": "INTEGER DEFAULT 0",
        "cases_without_investigation": "INTEGER DEFAULT 0",
        "avg_events_per_case": "REAL DEFAULT 0", "investigation_gap_rate": "REAL DEFAULT 0",
        "evidence_gap_rate": "REAL DEFAULT 0", "rework_loop_rate": "REAL DEFAULT 0",
        "rework_cases": "INTEGER DEFAULT 0", "significant_cases": "INTEGER DEFAULT 0",
        "cases_from_significant_alerts": "INTEGER DEFAULT 0",
        "severity_softening_rate": "REAL DEFAULT 0",
        "severity_softening_count": "INTEGER DEFAULT 0",
        "escalation_downgrade_rate": "REAL DEFAULT 0",
        "monitoring_coverage_pct": "REAL DEFAULT 0",
        "critical_monitoring_coverage_pct": "REAL DEFAULT 0",
        "telemetry_silence_pct": "REAL DEFAULT 0",
        "declared_kpi_contradictions": "INTEGER DEFAULT 0",
        "declared_kpi_details": "TEXT DEFAULT '[]'",
        "examiner_suppressed": "INTEGER DEFAULT 0",
        "top_analyst_share": "REAL DEFAULT 0",
        "critical_analysts_active": "INTEGER DEFAULT 0",
        "critical_alerts_assigned": "INTEGER DEFAULT 0",
        "weekend_alerts": "INTEGER DEFAULT 0",
        "weekend_activity_ratio": "REAL DEFAULT 1",
        "cases_with_thin_investigation": "INTEGER DEFAULT 0",
        "investigation_time_anomaly_rate": "REAL DEFAULT 0",
        "investigation_minutes_median": "REAL DEFAULT 0",
        "note_similarity_sample_size": "INTEGER DEFAULT 0",
        "note_similarity_sampled": "INTEGER DEFAULT 0",
        "data_quality_escalations": "REAL DEFAULT 0",
        "data_quality_investigations": "REAL DEFAULT 0",
        "data_quality_dispositions": "REAL DEFAULT 0",
    },
    "monthly_metrics": {
        "total_cases": "INTEGER DEFAULT 0", "total_dispositions": "INTEGER DEFAULT 0",
        "sla_breach_rate": "REAL DEFAULT 0", "rework_cases": "INTEGER DEFAULT 0",
        "investigation_events": "INTEGER DEFAULT 0",
        "weekend_alerts": "INTEGER DEFAULT 0", "weekend_activity_ratio": "REAL DEFAULT 1",
    },
    "findings": {
        "rationale": "TEXT DEFAULT ''", "detector_group": "TEXT DEFAULT 'rule'",
        "metric_value": "REAL DEFAULT 0", "threshold_value": "REAL DEFAULT 0",
        "examiner_verdict": "TEXT DEFAULT ''",
    },
}


# Tables whose natural key must be scoped to the entity. An earlier build keyed each
# on the record id alone, which made a second CSE's identical ticket id a hard
# IntegrityError. SQLite cannot ALTER a primary key, so these are rebuilt in place.
SUBJECT_ID_COLUMNS = {
    "alerts": "alert_id",
    "cases": "case_id",
    "investigations": "investigation_id",
    "dispositions": "disposition_id",
    "escalations": "escalation_id",
    "asset_inventory": "asset_id",
}


def _schema_statements() -> list[str]:
    """SCHEMA_SQL split into individual statements, comments stripped."""
    text = "\n".join(line.split("--")[0] for line in SCHEMA_SQL.splitlines())
    return [s.strip() + ";" for s in text.split(";") if s.strip()]


def _ddl_for_table(table: str) -> tuple[str | None, list[str]]:
    """The CREATE TABLE and CREATE INDEX statements for one table, from the schema."""
    create: str | None = None
    indexes: list[str] = []
    for statement in _schema_statements():
        if re.search(rf"CREATE TABLE IF NOT EXISTS\s+{re.escape(table)}\b", statement, re.I):
            create = statement
        elif re.search(rf"CREATE INDEX IF NOT EXISTS\s+\w+\s+ON\s+{re.escape(table)}\b",
                       statement, re.I):
            indexes.append(statement)
    return create, indexes


def _rebuild_table(conn, table: str) -> bool:
    """Recreate ``table`` with the current schema, preserving every existing row.

    Rows are copied through a shadow table so a failure leaves the original intact;
    the whole rebuild runs inside the caller's transaction.
    """
    create, indexes = _ddl_for_table(table)
    if not create:
        return False
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    column_list = ", ".join(f'"{c}"' for c in columns)
    shadow = f"{table}__rebuild"
    conn.execute(f'DROP TABLE IF EXISTS "{shadow}"')
    conn.execute(f'CREATE TABLE "{shadow}" AS SELECT * FROM "{table}"')
    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(create)
    conn.execute(f'INSERT OR IGNORE INTO "{table}" ({column_list}) '
                 f'SELECT {column_list} FROM "{shadow}"')
    conn.execute(f'DROP TABLE "{shadow}"')
    for statement in indexes:
        conn.execute(statement)
    return True


def migrate_schema(conn) -> list[str]:
    """Add missing columns and re-key any table still on a single-column primary key."""
    applied = []
    for table, columns in MIGRATION_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if not existing:
            continue  # table does not exist in this build; the schema script creates it
        for column, ddl in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                applied.append(f"{table}.{column}")

    conn.execute("PRAGMA foreign_keys=OFF")
    for table, id_column in SUBJECT_ID_COLUMNS.items():
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if not info:
            continue
        primary_key = [row[1] for row in sorted((r for r in info if r[5]), key=lambda r: r[5])]
        if primary_key == [id_column] and _rebuild_table(conn, table):
            applied.append(f"{table} re-keyed on (entity_id, {id_column})")
    conn.execute("PRAGMA foreign_keys=ON")
    return applied


def vacuum_db(db_path=None) -> dict:
    """Reclaim unused space and flush the WAL. Returns before/after sizes (bytes)."""
    path = pathlib.Path(db_path or DB_PATH)
    before = path.stat().st_size if path.exists() else 0
    with get_db(path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        conn.execute("PRAGMA optimize")
    after = path.stat().st_size if path.exists() else 0
    return {"before": before, "after": after,
            "saved": max(0, before - after),
            "size_mb": round(after / 1024 / 1024, 2)}


def db_stats(db_path=None) -> dict:
    """Row counts + on-disk footprint, used by the Settings / Data pages."""
    path = pathlib.Path(db_path or DB_PATH)
    tables = ["entities", "alerts", "cases", "escalations", "asset_inventory",
              "investigations", "dispositions", "findings", "adjudications",
              "entity_metrics", "monthly_metrics", "metric_snapshots", "documents",
              "audit_log"]
    counts = {}
    with get_db(path) as conn:
        for t in tables:
            try:
                counts[t] = conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
            except sqlite3.Error:
                counts[t] = 0
        free_pages = conn.execute("PRAGMA freelist_count").fetchone()[0]
    size = path.stat().st_size if path.exists() else 0
    return dict(path=str(path), size_bytes=size, size_mb=round(size / 1024 / 1024, 2),
                free_pages=free_pages, counts=counts)


# ---------------------------------------------------------------------------
# Schema Definition
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
-- Entities (companies / CSEs being reviewed)
--
-- One row per CSE. Created either by a supervisor typing a profile in, or by
-- the bulk folder ingest reading an entity_profile.json. The register is the
-- single source of truth for the review scope, so every profile field is
-- editable and re-runnable.
CREATE TABLE IF NOT EXISTS entities (
    entity_id       TEXT PRIMARY KEY,
    entity_name     TEXT NOT NULL,
    sector          TEXT NOT NULL,
    sub_sector      TEXT DEFAULT '',
    tier            TEXT DEFAULT 'Tier 3 - Medium',
    cin             TEXT DEFAULT '',
    hq              TEXT DEFAULT '',
    state           TEXT DEFAULT '',
    website         TEXT DEFAULT '',
    nciipc_id       TEXT DEFAULT '',
    soc_name        TEXT DEFAULT '',
    soc_model       TEXT DEFAULT '',        -- in-house / managed (MSSP) / hybrid
    soc_coverage    TEXT DEFAULT '',        -- 24x7 / extended hours / business hours
    analyst_count   INTEGER DEFAULT 0,
    contact         TEXT DEFAULT '',
    contact_email   TEXT DEFAULT '',
    contact_phone   TEXT DEFAULT '',
    review_period_start TEXT DEFAULT '',
    review_period_end   TEXT DEFAULT '',
    -- What the entity *declares* about itself. Kept so declared performance can be
    -- compared against its own operational records (rule IM-008, the declared-vs-
    -- evidence execution gap the problem statement describes).
    declared_controls   TEXT DEFAULT '[]',
    declared_kpis       TEXT DEFAULT '{}',
    source          TEXT DEFAULT 'manual',  -- manual | bulk | demo
    notes           TEXT DEFAULT '',
    upload_folder   TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Alerts (the core operational evidence)
-- The natural key of every submission record is (entity_id, <record id>), NOT the
-- record id alone. Two CSEs will routinely both submit an "INC0012345": ticket ids
-- are unique inside one entity's platform and nowhere else. A single-column primary
-- key made the second entity's upload fail with an IntegrityError, so the key is
-- entity-scoped throughout.
CREATE TABLE IF NOT EXISTS alerts (
    alert_id            TEXT NOT NULL,
    entity_id           TEXT NOT NULL REFERENCES entities(entity_id),
    asset_id            TEXT DEFAULT '',
    alert_category      TEXT DEFAULT '',
    alert_type          TEXT DEFAULT '',
    alert_name          TEXT DEFAULT '',
    severity            TEXT CHECK(severity IN ('critical','high','medium','low')),
    priority            TEXT DEFAULT '',
    risk_score          REAL DEFAULT 0,
    confidence          REAL DEFAULT 0,
    status              TEXT DEFAULT '',
    created_ts          TIMESTAMP,
    detected_ts         TIMESTAMP,
    acknowledged_ts     TIMESTAMP,
    closed_ts           TIMESTAMP,
    disposition         TEXT DEFAULT '',
    sla_target_minutes  REAL DEFAULT 0,
    assigned_analyst_id TEXT DEFAULT '',
    case_id             TEXT DEFAULT '',
    parent_alert_id     TEXT DEFAULT '',
    source_system       TEXT DEFAULT '',
    detection_source    TEXT DEFAULT '',
    detection_rule_id   TEXT DEFAULT '',
    hostname            TEXT DEFAULT '',
    ip_address          TEXT DEFAULT '',
    destination_ip      TEXT DEFAULT '',
    source_country      TEXT DEFAULT '',
    destination_country TEXT DEFAULT '',
    asset_type          TEXT DEFAULT '',
    user_id             TEXT DEFAULT '',
    created_by          TEXT DEFAULT '',
    investigator_notes  TEXT DEFAULT '',
    PRIMARY KEY (entity_id, alert_id)
);

CREATE INDEX IF NOT EXISTS idx_alerts_entity ON alerts(entity_id);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_ts);
CREATE INDEX IF NOT EXISTS idx_alerts_asset ON alerts(asset_id);

-- Cases (investigation / case-management records)
CREATE TABLE IF NOT EXISTS cases (
    case_id                 TEXT NOT NULL,
    entity_id               TEXT NOT NULL REFERENCES entities(entity_id),
    opened_ts               TIMESTAMP,
    updated_ts              TIMESTAMP,
    closed_ts               TIMESTAMP,
    resolution_ts           TIMESTAMP,
    status                  TEXT DEFAULT 'open',
    case_type               TEXT DEFAULT '',
    case_category           TEXT DEFAULT '',
    priority                TEXT DEFAULT '',
    severity                TEXT DEFAULT '',
    case_description        TEXT DEFAULT '',
    investigation_note_text TEXT DEFAULT '',
    root_cause_documented   INTEGER DEFAULT 0,
    remediation_documented  INTEGER DEFAULT 0,
    reopened_count          INTEGER DEFAULT 0,
    assigned_analyst        TEXT DEFAULT '',
    assigned_team           TEXT DEFAULT '',
    created_from_alert      INTEGER DEFAULT 0,
    number_of_alerts        INTEGER DEFAULT 0,
    affected_asset_count    INTEGER DEFAULT 0,
    affected_user_count     INTEGER DEFAULT 0,
    parent_case_id          TEXT DEFAULT '',
    related_case_id         TEXT DEFAULT '',
    linked_alert_ids        TEXT DEFAULT '',
    PRIMARY KEY (entity_id, case_id)
);

CREATE INDEX IF NOT EXISTS idx_cases_entity ON cases(entity_id);

-- Investigation workflow events (process mining input)
--
-- One row per workflow action. This is what lets the tool reconstruct how an
-- investigation actually proceeded - reassignment churn, activity loops,
-- whether any evidence was recorded - rather than trusting the case status.
CREATE TABLE IF NOT EXISTS investigations (
    investigation_id    TEXT NOT NULL,
    entity_id           TEXT NOT NULL REFERENCES entities(entity_id),
    case_id             TEXT DEFAULT '',
    alert_id            TEXT DEFAULT '',
    ts                  TIMESTAMP,
    sequence_number     INTEGER DEFAULT 0,
    activity_type       TEXT DEFAULT '',
    activity_subtype    TEXT DEFAULT '',
    analyst_id          TEXT DEFAULT '',
    team_id             TEXT DEFAULT '',
    asset_id            TEXT DEFAULT '',
    evidence_type       TEXT DEFAULT '',
    action_result       TEXT DEFAULT '',
    duration_minutes    REAL DEFAULT 0,
    previous_activity   TEXT DEFAULT '',
    next_activity       TEXT DEFAULT '',
    PRIMARY KEY (entity_id, investigation_id)
);

CREATE INDEX IF NOT EXISTS idx_inv_entity ON investigations(entity_id);
CREATE INDEX IF NOT EXISTS idx_inv_case ON investigations(case_id);

-- Disposition and closure records (the authoritative closure evidence)
CREATE TABLE IF NOT EXISTS dispositions (
    disposition_id          TEXT NOT NULL,
    entity_id               TEXT NOT NULL REFERENCES entities(entity_id),
    alert_id                TEXT DEFAULT '',
    case_id                 TEXT DEFAULT '',
    disposition             TEXT DEFAULT '',
    closure_reason          TEXT DEFAULT '',
    closure_ts              TIMESTAMP,
    closed_by               TEXT DEFAULT '',
    closure_team            TEXT DEFAULT '',
    root_cause              TEXT DEFAULT '',
    false_positive_reason   TEXT DEFAULT '',
    true_positive           INTEGER DEFAULT 0,
    risk_accepted           INTEGER DEFAULT 0,
    risk_acceptance_authority TEXT DEFAULT '',
    remediation_status      TEXT DEFAULT '',
    remediation_reference   TEXT DEFAULT '',
    reopen_count            INTEGER DEFAULT 0,
    time_to_close_minutes   REAL DEFAULT 0,
    sla_target_minutes      REAL DEFAULT 0,
    made_sla                INTEGER DEFAULT 1,
    PRIMARY KEY (entity_id, disposition_id)
);

CREATE INDEX IF NOT EXISTS idx_disp_entity ON dispositions(entity_id);
CREATE INDEX IF NOT EXISTS idx_disp_alert ON dispositions(alert_id);
CREATE INDEX IF NOT EXISTS idx_disp_case ON dispositions(case_id);

-- Escalations
CREATE TABLE IF NOT EXISTS escalations (
    escalation_id       TEXT NOT NULL,
    -- deliberately NOT a foreign key: a dangling case reference is supervisory
    -- evidence (an escalation pointing at a case that does not exist), not an
    -- upload error to be rejected.
    case_id             TEXT DEFAULT '',
    alert_id            TEXT DEFAULT '',
    entity_id           TEXT NOT NULL REFERENCES entities(entity_id),
    escalated_ts        TIMESTAMP,
    escalated_to_role   TEXT DEFAULT '',
    escalation_reason   TEXT DEFAULT '',
    resolved_ts         TIMESTAMP,
    from_team           TEXT DEFAULT '',
    to_team             TEXT DEFAULT '',
    from_role           TEXT DEFAULT '',
    to_role             TEXT DEFAULT '',
    escalation_level    INTEGER DEFAULT 1,
    severity_at_escalation TEXT DEFAULT '',
    priority_at_escalation TEXT DEFAULT '',
    decision            TEXT DEFAULT '',
    approved_by         TEXT DEFAULT '',
    response_ts         TIMESTAMP,
    escalation_status   TEXT DEFAULT 'open',
    PRIMARY KEY (entity_id, escalation_id)
);

CREATE INDEX IF NOT EXISTS idx_esc_entity ON escalations(entity_id);
CREATE INDEX IF NOT EXISTS idx_esc_case ON escalations(case_id);
CREATE INDEX IF NOT EXISTS idx_esc_alert ON escalations(alert_id);

-- Asset Inventory (as submitted; sparse by design - not all entities report this)
CREATE TABLE IF NOT EXISTS asset_inventory (
    asset_id                TEXT NOT NULL,
    entity_id               TEXT NOT NULL REFERENCES entities(entity_id),
    asset_name              TEXT DEFAULT '',
    hostname                TEXT DEFAULT '',
    ip_address              TEXT DEFAULT '',
    asset_type              TEXT DEFAULT '',
    asset_class             TEXT DEFAULT 'standard_it',
    criticality_tier        TEXT DEFAULT 'standard',
    business_criticality    TEXT DEFAULT '',
    environment             TEXT DEFAULT '',
    business_unit           TEXT DEFAULT '',
    location                TEXT DEFAULT '',
    data_classification     TEXT DEFAULT '',
    owner                   TEXT DEFAULT '',
    system_owner            TEXT DEFAULT '',
    monitoring_required     TEXT DEFAULT 'true',
    monitoring_status       TEXT DEFAULT 'active',
    monitoring_source       TEXT DEFAULT '',
    expected_alert_frequency REAL DEFAULT 0,
    actual_alert_count      INTEGER DEFAULT 0,
    last_telemetry_timestamp TIMESTAMP,
    last_scan_timestamp     TIMESTAMP,
    vulnerability_status    TEXT DEFAULT '',
    PRIMARY KEY (entity_id, asset_id)
);

CREATE INDEX IF NOT EXISTS idx_assets_entity ON asset_inventory(entity_id);
CREATE INDEX IF NOT EXISTS idx_assets_class ON asset_inventory(asset_class);

-- Findings (output of the detection engine)
CREATE TABLE IF NOT EXISTS findings (
    finding_id      TEXT PRIMARY KEY,
    entity_id       TEXT NOT NULL REFERENCES entities(entity_id),
    period          TEXT DEFAULT '',
    capability_tags TEXT DEFAULT '[]',
    weakness_type   TEXT CHECK(weakness_type IN (
        'execution_gap','negative_space','peer_anomaly','data_quality'
    )),
    rule_id         TEXT DEFAULT '',
    title           TEXT NOT NULL,
    description     TEXT DEFAULT '',
    severity_score  REAL DEFAULT 0.0,
    severity        TEXT CHECK(severity IN ('High','Medium','Low')) DEFAULT 'Medium',
    evidence_ids    TEXT DEFAULT '[]',
    evidence_count  INTEGER DEFAULT 0,
    -- latest examiner verdict mirrored here so list views do not need a join
    examiner_verdict TEXT DEFAULT '',
    rationale       TEXT DEFAULT '',
    detector_group  TEXT DEFAULT 'rule',
    metric_value    REAL DEFAULT 0,
    threshold_value REAL DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_findings_entity ON findings(entity_id);
CREATE INDEX IF NOT EXISTS idx_findings_type ON findings(weakness_type);

-- Entity Metrics (computed features per entity)
CREATE TABLE IF NOT EXISTS entity_metrics (
    entity_id                       TEXT PRIMARY KEY REFERENCES entities(entity_id),
    entity_name                     TEXT DEFAULT '',
    sector                          TEXT DEFAULT '',
    period                          TEXT DEFAULT '',
    total_alerts                    INTEGER DEFAULT 0,
    total_cases                     INTEGER DEFAULT 0,
    total_assets                    INTEGER DEFAULT 0,
    total_escalations               INTEGER DEFAULT 0,
    monitored_assets                INTEGER DEFAULT 0,
    critical_assets                 INTEGER DEFAULT 0,
    unmonitored_critical_assets     INTEGER DEFAULT 0,
    alerts_per_monitored_asset      REAL DEFAULT 0,
    time_to_close_median_critical   REAL DEFAULT 0,
    missing_case_rate               REAL DEFAULT 0,
    peer_outlier_score              REAL DEFAULT 0,
    review_period_start             TEXT DEFAULT '',
    review_period_end               TEXT DEFAULT '',
    -- Alert-level metrics
    time_to_ack_median              REAL DEFAULT 0,
    time_to_ack_median_critical     REAL DEFAULT 0,
    time_to_close_median            REAL DEFAULT 0,
    pct_critical_closed_under_5min  REAL DEFAULT 0,
    escalation_rate_critical        REAL DEFAULT 0,
    repeat_alert_rate               REAL DEFAULT 0,
    fast_closure_rate               REAL DEFAULT 0,
    crit_no_escalation_rate         REAL DEFAULT 0,
    -- Case-level metrics
    investigation_note_similarity   REAL DEFAULT 0,
    reopen_rate                     REAL DEFAULT 0,
    avg_investigation_depth         REAL DEFAULT 0,
    template_note_rate              REAL DEFAULT 0,
    -- Negative-space metrics
    expected_category_coverage      REAL DEFAULT 0,
    silent_critical_assets          INTEGER DEFAULT 0,
    night_coverage_gap_pct          REAL DEFAULT 0,
    activity_deviation              REAL DEFAULT 0,
    -- Root cause
    root_cause_rate                 REAL DEFAULT 0,
    -- Scoring
    risk_score                      REAL DEFAULT 0,
    risk_tier                       TEXT DEFAULT 'Satisfactory',
    risk_rank                       INTEGER DEFAULT 0,
    metric_index                    REAL DEFAULT 0,
    capability_average              REAL DEFAULT 0,
    -- 8 Capability scores
    c1_threat_detection             REAL DEFAULT 0,
    c2_investigation                REAL DEFAULT 0,
    c3_escalation                   REAL DEFAULT 0,
    c4_incident_response            REAL DEFAULT 0,
    c5_security_operations          REAL DEFAULT 0,
    c6_governance                   REAL DEFAULT 0,
    c7_operational_discipline       REAL DEFAULT 0,
    c8_cyber_resilience             REAL DEFAULT 0,
    -- Data quality scores (one per submitted field group, so a supervisor can see which
    -- group the completeness gate actually tripped on)
    data_quality_alerts             REAL DEFAULT 0,
    data_quality_cases              REAL DEFAULT 0,
    data_quality_assets             REAL DEFAULT 0,
    data_quality_escalations        REAL DEFAULT 0,
    data_quality_investigations     REAL DEFAULT 0,
    data_quality_dispositions       REAL DEFAULT 0,
    -- Disposition / closure integrity (IM-001, IM-003, IM-005, IM-007)
    total_dispositions              INTEGER DEFAULT 0,
    sla_breach_rate                 REAL DEFAULT 0,
    sla_misreport_rate              REAL DEFAULT 0,
    sla_misreport_count             INTEGER DEFAULT 0,
    close_time_over_target_median   REAL DEFAULT 0,
    true_positive_rate              REAL DEFAULT 0,
    risk_accept_rate                REAL DEFAULT 0,
    risk_accept_no_authority_rate   REAL DEFAULT 0,
    no_root_cause_gap               REAL DEFAULT 0,
    no_remediation_gap              REAL DEFAULT 0,
    significant_closures            INTEGER DEFAULT 0,
    -- Investigation workflow (IM-004, IM-006)
    investigation_events            INTEGER DEFAULT 0,
    cases_with_events               INTEGER DEFAULT 0,
    cases_without_investigation     INTEGER DEFAULT 0,
    avg_events_per_case             REAL DEFAULT 0,
    investigation_gap_rate          REAL DEFAULT 0,
    evidence_gap_rate               REAL DEFAULT 0,
    rework_loop_rate                REAL DEFAULT 0,
    rework_cases                    INTEGER DEFAULT 0,
    -- Severity softening (IM-002)
    significant_cases               INTEGER DEFAULT 0,
    cases_from_significant_alerts   INTEGER DEFAULT 0,
    severity_softening_rate         REAL DEFAULT 0,
    severity_softening_count        INTEGER DEFAULT 0,
    escalation_downgrade_rate       REAL DEFAULT 0,
    -- Estate coverage
    monitoring_coverage_pct         REAL DEFAULT 0,
    critical_monitoring_coverage_pct REAL DEFAULT 0,
    telemetry_silence_pct           REAL DEFAULT 0,
    -- Declared vs measured (IM-008)
    declared_kpi_contradictions     INTEGER DEFAULT 0,
    declared_kpi_details            TEXT DEFAULT '[]',
    -- Examiner adjudication effect (findings not counted after review)
    examiner_suppressed             INTEGER DEFAULT 0,
    -- Workforce concentration (EG-007)
    top_analyst_share               REAL DEFAULT 0,
    critical_analysts_active        INTEGER DEFAULT 0,
    critical_alerts_assigned        INTEGER DEFAULT 0,
    -- Weekend coverage (NS-007)
    weekend_alerts                  INTEGER DEFAULT 0,
    weekend_activity_ratio          REAL DEFAULT 1,
    -- Investigation time (IM-009)
    cases_with_thin_investigation   INTEGER DEFAULT 0,
    investigation_time_anomaly_rate REAL DEFAULT 0,
    investigation_minutes_median    REAL DEFAULT 0,
    -- Text-similarity sampling (EG-003 scale guard)
    note_similarity_sample_size     INTEGER DEFAULT 0,
    note_similarity_sampled         INTEGER DEFAULT 0,
    -- Timestamp
    computed_at                     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Documents (uploaded Word/PDF files with extracted text)
CREATE TABLE IF NOT EXISTS documents (
    doc_id          TEXT PRIMARY KEY,
    entity_id       TEXT NOT NULL REFERENCES entities(entity_id),
    filename        TEXT DEFAULT '',
    file_type       TEXT DEFAULT '',
    full_text       TEXT DEFAULT '',
    referenced_ids  TEXT DEFAULT '[]',
    uploaded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_docs_entity ON documents(entity_id);

-- Monthly trend metrics (for trend analysis page)
CREATE TABLE IF NOT EXISTS monthly_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id       TEXT NOT NULL REFERENCES entities(entity_id),
    entity_name     TEXT DEFAULT '',
    month           TEXT NOT NULL,
    fast_closure_rate           REAL DEFAULT 0,
    crit_no_escalation_rate     REAL DEFAULT 0,
    template_note_rate          REAL DEFAULT 0,
    total_alerts                INTEGER DEFAULT 0,
    night_coverage_gap_pct      REAL DEFAULT 0,
    repeat_alert_rate           REAL DEFAULT 0,
    root_cause_rate             REAL DEFAULT 0,
    escalation_rate_critical    REAL DEFAULT 0,
    missing_case_rate           REAL DEFAULT 0,
    risk_score                  REAL DEFAULT 0,
    -- case / closure trend lines
    total_cases                 INTEGER DEFAULT 0,
    total_dispositions          INTEGER DEFAULT 0,
    sla_breach_rate             REAL DEFAULT 0,
    rework_cases                INTEGER DEFAULT 0,
    investigation_events        INTEGER DEFAULT 0,
    weekend_alerts              INTEGER DEFAULT 0,
    weekend_activity_ratio      REAL DEFAULT 1,
    UNIQUE(entity_id, month)
);

-- Metric snapshots (cycle-over-cycle supervision)
--
-- One row per entity per analytics run. The live entity_metrics table is overwritten
-- on every run, which makes it impossible to answer the question that matters most in
-- continuous supervision: "is this entity getting better or worse?". Snapshots are
-- append-only and retained, so each run can be diffed against the previous cycle.
CREATE TABLE IF NOT EXISTS metric_snapshots (
    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL,
    taken_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    cycle_label             TEXT DEFAULT '',
    entity_id               TEXT NOT NULL,
    entity_name             TEXT DEFAULT '',
    sector                  TEXT DEFAULT '',
    criticality_tier        TEXT DEFAULT '',
    risk_score              REAL DEFAULT 0,
    risk_tier               TEXT DEFAULT '',
    risk_rank               INTEGER DEFAULT 0,
    capability_average      REAL DEFAULT 0,
    metric_index            REAL DEFAULT 0,
    findings_count          INTEGER DEFAULT 0,
    findings_high           INTEGER DEFAULT 0,
    -- the headline operational measures, so a diff needs no join
    fast_closure_rate       REAL DEFAULT 0,
    crit_no_escalation_rate REAL DEFAULT 0,
    template_note_rate      REAL DEFAULT 0,
    repeat_alert_rate       REAL DEFAULT 0,
    missing_case_rate       REAL DEFAULT 0,
    root_cause_rate         REAL DEFAULT 0,
    night_coverage_gap_pct  REAL DEFAULT 0,
    expected_category_coverage REAL DEFAULT 0,
    activity_deviation      REAL DEFAULT 0,
    silent_critical_assets  INTEGER DEFAULT 0,
    unmonitored_critical_assets INTEGER DEFAULT 0,
    sla_breach_rate         REAL DEFAULT 0,
    sla_misreport_rate      REAL DEFAULT 0,
    severity_softening_rate REAL DEFAULT 0,
    no_root_cause_gap       REAL DEFAULT 0,
    no_remediation_gap      REAL DEFAULT 0,
    rework_loop_rate        REAL DEFAULT 0,
    reopen_rate             REAL DEFAULT 0,
    investigation_gap_rate  REAL DEFAULT 0,
    evidence_gap_rate       REAL DEFAULT 0,
    risk_accept_no_authority_rate REAL DEFAULT 0,
    telemetry_silence_pct   REAL DEFAULT 0,
    critical_monitoring_coverage_pct REAL DEFAULT 0,
    top_analyst_share       REAL DEFAULT 0,
    weekend_activity_ratio  REAL DEFAULT 1,
    investigation_time_anomaly_rate REAL DEFAULT 0,
    declared_kpi_contradictions INTEGER DEFAULT 0,
    total_alerts            INTEGER DEFAULT 0,
    total_cases             INTEGER DEFAULT 0,
    total_escalations       INTEGER DEFAULT 0,
    total_dispositions      INTEGER DEFAULT 0,
    c1_threat_detection     REAL DEFAULT 0,
    c2_investigation        REAL DEFAULT 0,
    c3_escalation           REAL DEFAULT 0,
    c4_incident_response    REAL DEFAULT 0,
    c5_security_operations  REAL DEFAULT 0,
    c6_governance           REAL DEFAULT 0,
    c7_operational_discipline REAL DEFAULT 0,
    c8_cyber_resilience     REAL DEFAULT 0,
    examiner_suppressed     INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_snap_entity ON metric_snapshots(entity_id);
CREATE INDEX IF NOT EXISTS idx_snap_run ON metric_snapshots(run_id);

-- Examiner adjudications (human-in-the-loop feedback on findings)
--
-- Every verdict is appended, never overwritten, so the full decision history of a
-- finding is preserved for audit. The latest row per finding is the current verdict;
-- earlier rows show how the examiner's judgement evolved.
--
-- Deliberately NO foreign key to findings: the findings table is rebuilt on every
-- analytics run, while an examiner's decision is a permanent record. A verdict must
-- survive the finding being regenerated - or even no longer firing - otherwise the
-- audit trail of human judgement would be destroyed by a re-run.
CREATE TABLE IF NOT EXISTS adjudications (
    adjudication_id TEXT PRIMARY KEY,
    finding_id      TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    verdict         TEXT NOT NULL,
    rationale       TEXT DEFAULT '',
    examiner        TEXT DEFAULT 'supervisor',
    decided_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_adj_finding ON adjudications(finding_id);
CREATE INDEX IF NOT EXISTS idx_adj_entity ON adjudications(entity_id);

-- Audit log (immutable record of all actions)
CREATE TABLE IF NOT EXISTS audit_log (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    action      TEXT NOT NULL,
    entity_id   TEXT DEFAULT '',
    details     TEXT DEFAULT '',
    user_name   TEXT DEFAULT 'supervisor'
);
"""


# ---------------------------------------------------------------------------
# Helper functions for common queries
# ---------------------------------------------------------------------------

def get_all_entities(conn) -> list[dict]:
    """Return all entities as list of dicts."""
    rows = conn.execute("SELECT * FROM entities ORDER BY entity_name").fetchall()
    return [dict(r) for r in rows]


def get_entity_metrics(conn) -> list[dict]:
    """Return all entity metrics as list of dicts."""
    rows = conn.execute(
        "SELECT * FROM entity_metrics ORDER BY risk_rank"
    ).fetchall()
    return [dict(r) for r in rows]


def get_findings(conn, entity_id: str | None = None) -> list[dict]:
    """Return findings, optionally filtered by entity."""
    if entity_id:
        rows = conn.execute(
            "SELECT * FROM findings WHERE entity_id = ? ORDER BY severity_score DESC",
            (entity_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM findings ORDER BY severity_score DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_alerts(conn, entity_id: str | None = None, limit: int = 5000) -> list[dict]:
    """Return alerts, optionally filtered by entity."""
    if entity_id:
        rows = conn.execute(
            "SELECT * FROM alerts WHERE entity_id = ? ORDER BY created_ts DESC LIMIT ?",
            (entity_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY created_ts DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_monthly_metrics(conn, entity_id: str | None = None) -> list[dict]:
    """Return monthly trend data."""
    if entity_id:
        rows = conn.execute(
            "SELECT * FROM monthly_metrics WHERE entity_id = ? ORDER BY month",
            (entity_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM monthly_metrics ORDER BY entity_name, month"
        ).fetchall()
    return [dict(r) for r in rows]


def log_action(conn, action: str, entity_id: str = "", details: str = ""):
    """Insert an audit log entry."""
    conn.execute(
        "INSERT INTO audit_log (action, entity_id, details) VALUES (?, ?, ?)",
        (action, entity_id, details)
    )


def has_data(db_path=None) -> bool:
    """Check if the database has any entity data."""
    with get_db(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM entities").fetchone()
        return row["cnt"] > 0


# ---------------------------------------------------------------------------
# Entity register helpers (create / update / delete the review scope)
# ---------------------------------------------------------------------------

# Columns a supervisor is allowed to set from the UI. Keeping this list explicit
# means an UPDATE built from a form can never touch primary keys or bookkeeping
# columns by accident.
def entity_id_for(entity_name: str) -> str:
    """Deterministic entity id from the entity name.

    One function, used by both the manual register form and the bulk submission
    loader, so the two paths can never disagree about identity - if they did, adding
    a company by hand and then ingesting its folder would silently create a second,
    empty entity with the same name.
    """
    import uuid as _uuid
    key = " ".join(str(entity_name or "").split()).lower()
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"sat-sa:cse:{key}"))


ENTITY_PROFILE_FIELDS = [
    "entity_name", "sector", "sub_sector", "tier", "cin", "hq", "state",
    "website", "nciipc_id", "soc_name", "soc_model", "soc_coverage",
    "analyst_count", "contact", "contact_email", "contact_phone",
    "review_period_start", "review_period_end", "declared_controls",
    "declared_kpis", "notes",
]


def upsert_entity(conn, entity_id: str, **fields) -> str:
    """Insert or update an entity profile. Returns 'created' or 'updated'.

    Only ENTITY_PROFILE_FIELDS plus source/upload_folder are written, so a partial
    form submission cannot blank out unrelated columns.
    """
    allowed = set(ENTITY_PROFILE_FIELDS) | {"source", "upload_folder"}
    payload = {k: v for k, v in fields.items() if k in allowed and v is not None}
    payload = {k: ("" if v is None else v) for k, v in payload.items()}

    exists = conn.execute(
        "SELECT 1 FROM entities WHERE entity_id = ?", (entity_id,)
    ).fetchone() is not None

    if exists:
        if payload:
            sets = ", ".join(f"{k} = ?" for k in payload)
            conn.execute(
                f"UPDATE entities SET {sets}, updated_at = CURRENT_TIMESTAMP "
                f"WHERE entity_id = ?",
                (*payload.values(), entity_id),
            )
        return "updated"

    payload.setdefault("entity_name", entity_id)
    payload.setdefault("sector", "")
    cols = ["entity_id", *payload.keys()]
    marks = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO entities ({', '.join(cols)}) VALUES ({marks})",
        (entity_id, *payload.values()),
    )
    return "created"


def delete_entity(conn, entity_id: str) -> dict:
    """Remove an entity and every record submitted under it (withdraw a CSE)."""
    counts = {}
    for table in ("alerts", "cases", "escalations", "asset_inventory",
                  "investigations", "dispositions", "findings", "entity_metrics",
                  "monthly_metrics", "metric_snapshots", "documents", "adjudications"):
        try:
            cur = conn.execute(f"DELETE FROM {table} WHERE entity_id = ?", (entity_id,))
            counts[table] = cur.rowcount
        except sqlite3.Error:
            counts[table] = 0
    cur = conn.execute("DELETE FROM entities WHERE entity_id = ?", (entity_id,))
    counts["entities"] = cur.rowcount
    return counts


# ---------------------------------------------------------------------------
# Adjudication helpers
# ---------------------------------------------------------------------------

def latest_adjudications(conn, entity_id: str | None = None) -> dict:
    """Current verdict per finding (the newest row wins).

    Returns {finding_id: {verdict, rationale, examiner, decided_at, history}}.
    """
    # Newest row per finding by insertion order: decided_at has second resolution, so
    # two verdicts recorded in the same second would otherwise be ambiguous.
    sql = """
        SELECT a.* FROM adjudications a
        JOIN (SELECT finding_id, MAX(rowid) AS newest FROM adjudications GROUP BY finding_id) m
          ON a.rowid = m.newest
    """
    params: tuple = ()
    if entity_id:
        sql += " WHERE a.entity_id = ?"
        params = (entity_id,)
    sql += " ORDER BY a.decided_at DESC"
    latest: dict[str, dict] = {}
    for row in conn.execute(sql, params).fetchall():
        rec = dict(row)
        latest[rec["finding_id"]] = rec
    # attach decision history so the UI can show how the judgement changed
    for fid, rec in latest.items():
        hist = conn.execute(
            "SELECT verdict, rationale, examiner, decided_at FROM adjudications "
            "WHERE finding_id = ? ORDER BY decided_at", (fid,)
        ).fetchall()
        rec["history"] = [dict(h) for h in hist]
    return latest


def record_adjudication(conn, finding_id: str, entity_id: str, verdict: str,
                        rationale: str = "", examiner: str = "supervisor") -> str:
    """Append an examiner verdict on a finding. Returns the adjudication id.

    ``adjudications`` is the append-only record of judgement; ``findings.examiner_verdict``
    is a denormalised mirror of the *current* verdict so a page can show the state without
    joining. Both are written here, in the one function every caller goes through, because
    several pages read the mirror directly (the portfolio KPI, the validation verdict
    distribution, the PDF report) and previously it was only refreshed by the next
    analytics run - so a verdict recorded and then read back showed as outstanding.
    """
    import uuid as _uuid
    adj_id = f"ADJ-{_uuid.uuid4().hex[:10].upper()}"
    conn.execute(
        "INSERT INTO adjudications (adjudication_id, finding_id, entity_id, verdict, "
        "rationale, examiner) VALUES (?, ?, ?, ?, ?, ?)",
        (adj_id, finding_id, entity_id, verdict, rationale, examiner),
    )
    conn.execute("UPDATE findings SET examiner_verdict = ? WHERE finding_id = ?",
                 (verdict, finding_id))
    return adj_id
