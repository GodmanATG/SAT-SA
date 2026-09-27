-- Alerts table
CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY,
    cse_id TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
    source_system TEXT NOT NULL,
    target_asset_id TEXT,
    alert_category TEXT NOT NULL,
    acknowledged_at TIMESTAMP,
    closed_at TIMESTAMP,
    disposition TEXT CHECK (disposition IN ('true_positive', 'false_positive', 'benign', 'escalated', 'suppressed', 'pending')),
    assigned_analyst TEXT,
    FOREIGN KEY (cse_id) REFERENCES cse_profiles(cse_id)
);

-- Cases table
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    cse_id TEXT NOT NULL,
    linked_alert_ids TEXT[], -- array of alert IDs
    opened_at TIMESTAMP NOT NULL,
    closed_at TIMESTAMP,
    priority TEXT NOT NULL CHECK (priority IN ('P1', 'P2', 'P3', 'P4')),
    escalated BOOLEAN DEFAULT FALSE,
    escalated_to TEXT,
    investigation_notes TEXT,
    root_cause_identified BOOLEAN DEFAULT FALSE,
    remediation_actions TEXT,
    closure_reason TEXT,
    assigned_analyst TEXT,
    FOREIGN KEY (cse_id) REFERENCES cse_profiles(cse_id)
);

-- Assets table
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    cse_id TEXT NOT NULL,
    hostname TEXT,
    asset_type TEXT NOT NULL CHECK (asset_type IN ('server', 'workstation', 'network_device', 'database', 'web_application', 'iot_device', 'cloud_service')),
    criticality TEXT NOT NULL CHECK (criticality IN ('critical', 'high', 'medium', 'low')),
    monitored BOOLEAN DEFAULT TRUE,
    environment TEXT CHECK (environment IN ('production', 'staging', 'development', 'dr')),
    FOREIGN KEY (cse_id) REFERENCES cse_profiles(cse_id)
);

-- CSE Profiles table
CREATE TABLE IF NOT EXISTS cse_profiles (
    cse_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sector TEXT NOT NULL,
    size_tier TEXT NOT NULL CHECK (size_tier IN ('large', 'medium', 'small')),
    soc_model TEXT CHECK (soc_model IN ('in_house', 'mssp', 'hybrid')),
    analyst_count INTEGER,
    submission_period_start DATE,
    submission_period_end DATE
);

-- Findings table (output of analytics engine)
CREATE TABLE IF NOT EXISTS findings (
    finding_id TEXT PRIMARY KEY,
    cse_id TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('execution_gap', 'negative_space', 'peer_anomaly')),
    rule_id TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    summary TEXT NOT NULL,
    evidence JSONB NOT NULL,
    confidence FLOAT,
    recommendation TEXT,
    peer_context TEXT,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cse_id) REFERENCES cse_profiles(cse_id)
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_alerts_cse ON alerts(cse_id);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp);
CREATE INDEX IF NOT EXISTS idx_alerts_target_asset ON alerts(target_asset_id);
CREATE INDEX IF NOT EXISTS idx_cases_cse ON cases(cse_id);
CREATE INDEX IF NOT EXISTS idx_assets_cse ON assets(cse_id);
CREATE INDEX IF NOT EXISTS idx_findings_cse ON findings(cse_id);
CREATE INDEX IF NOT EXISTS idx_findings_category ON findings(category);
