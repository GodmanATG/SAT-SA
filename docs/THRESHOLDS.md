# SAT-SA — Threshold reference and tuning guide

Every value below is a **configurable default**, not a fitted parameter. Nothing in SAT-SA is
trained from data: a supervisor can open **⚙️ Settings** and change any of these, re-run the
analytics and compare. All values live in `app/detection_config.json` (written by the Settings
page) and are recorded in `audit_log` with every analytics run, so any historical result can be
re-derived exactly.

**Tuning method.** Do not tune from intuition. Run one real cycle, adjudicate findings in
**Examiner Review**, then read the *Detector agreement* table: a rule with a high false-positive
share needs a stricter threshold; a rule examiners always confirm can be loosened to catch more.
Change one family at a time and re-run — the engine records the threshold set each time.

---

## 1. Execution-gap detectors (EG)

| Key | Default | Detector | What it controls |
|---|---|---|---|
| `fast_closure_minutes` | 5 | EG-001 | Closure faster than this counts as "too fast" |
| `fast_closure_rate_flag` | 0.50 | EG-001 | Flag if >50% of critical/high alerts close this fast |
| `escalation_rate_flag` | 0.30 | EG-002 | Flag if <30% of critical alerts are escalated |
| `template_cosine_threshold` | 0.85 | EG-003 | TF-IDF cosine similarity at which two notes count as duplicates |
| `template_note_rate_flag` | 0.40 | EG-003 | Flag if >40% of notes sit in duplicate clusters |
| `repeat_asset_min_count` | 10 | EG-004 | Alerts on one (asset, category) pair to call it chronic |
| `repeat_pair_concentration` | 4.0 | EG-004 | …and how many times the entity's median pair volume it must be |
| `repeat_pair_max_root_cause_rate` | 0.2 | EG-004 | …with at most this share of root causes documented |
| `repeat_alert_rate_flag` | 0.20 | EG-004 | Flag if >20% of alerts are chronic repeat traffic |
| `bulk_closure_sigma` | 3.0 | EG-005 | Closures per hour beyond mean + Nσ count as a batch |
| `investigation_depth_flag` | 0.40 | EG-006 | Flag if the mean investigation depth score is below this |

*EG-004 is deliberately **concentration-based**, not density-based: an absolute "N alerts in 30
days" rule would simply punish whichever entity submits the most data.*

## 2. Negative-space detectors (NS)

| Key | Default | Detector | What it controls |
|---|---|---|---|
| `unmonitored_asset_min` | 1 | NS-006 | Critical assets declared for monitoring but not monitored |
| `category_coverage_flag` | 0.50 | NS-002 | Flag if less than half the sector's expected categories appear |
| `night_gap_flag` | 40.0 | NS-003 | Flag if night activity is >40% below the cohort norm |
| `night_gap_absolute_floor` | 0.05 | NS-003 | Always flag if under this share of alerts is at night |
| `low_activity_deviation_flag` | 0.60 | NS-004 | Flag if alerts per monitored asset is >60% below the sector median |
| `missing_case_rate_flag` | 0.50 | NS-005 | Flag if >50% of critical/high alerts have no case record |
| `data_quality_minimum` | 0.60 | all NS | Below this completeness, negative-space findings are withheld |

*The data-quality gate is what stops "no evidence" being confused with "no reporting". An entity
that submits an empty export is not silently scored as having a blind spot — it is reported as a
data-quality issue instead.*

## 3. Absolute benchmarks (BM)

| Key | Default | Detector | What it controls |
|---|---|---|---|
| `benchmark_ack_minutes` | 45.0 | BM-001 | Flag level for median acknowledgement of critical/high alerts |
| `benchmark_close_minutes_critical` | 360.0 | BM-002 | Flag level for median closure of critical alerts |

Reference medians live under `benchmarks` in the config file and are **editable**:

| Key | Default | Meaning |
|---|---|---|
| `median_time_to_ack_minutes_critical` | 15.0 | Reference MTTA for critical/high alerts |
| `median_time_to_close_minutes_critical` | 240.0 | Reference MTTR for critical alerts |
| `escalation_rate_critical` | 0.60 | Reference share of criticals escalated |
| `reference_label` | *illustrative survey medians* | Printed inside every BM finding |

**Replace these with the exact reference table cited in your submission.** The whole point of
the BM family is that a sector-wide weakness still flags even when every entity looks normal
against its peers; that argument is only credible if the reference numbers are citable.

## 4. Peer-relative and statistical (STAT)

| Key | Default | Detector | What it controls |
|---|---|---|---|
| `zscore_threshold` | 2.5 | STAT-001 | Flag on \|z\| above this against the leave-one-out cohort |
| `min_cohort_size` | 3 | STAT-001 | Minimum cohort size; below it, fall back sector → portfolio |
| `isolation_forest_contamination` | 0.10 | STAT-003 | Expected fraction of multivariate outliers |
| `isolation_forest_min_entities` | 12 | STAT-003 | Below this entity count, skip multivariate detection |
| `trend_pvalue_threshold` | 0.05 | STAT-002 | Flag month-over-month drift below this p-value |

*Peer detectors are reported separately from rule precision, because a control entity can
legitimately be a population outlier. Their findings are labelled weaker evidence on the card.*

## 5. Incident management & governance (IM)

| Key | Default | Detector | What it controls |
|---|---|---|---|
| `sla_breach_rate_flag` | 0.10 | IM-001 | Share of closures exceeding their recorded response target |
| `sla_misreport_threshold` | 0.15 | IM-001 | Closures flagged SLA-compliant that their own timeline contradicts |
| `sla_breach_min_cases` | 5 | IM-001 | Ignore samples smaller than this |
| `severity_softening_flag` | 0.25 | IM-002 | Cases logged below the severity of the alerts they came from |
| `no_root_cause_flag` | 0.35 | IM-003 | Significant closures with an empty root cause |
| `no_remediation_flag` | 0.40 | IM-003 | Significant closures with no remediation status or reference |
| `rework_loop_flag` | 0.30 | IM-004 | Investigations returning to a completed activity |
| `reopen_rate_flag` | 0.05 | IM-005 | Share of closures later reopened |
| `investigation_event_min` | 2 | IM-006 | Expected workflow events per case |
| `investigation_gap_flag` | 0.30 | IM-006 | Cases below that minimum |
| `evidence_missing_flag` | 0.40 | IM-006 | Workflow steps recording neither evidence nor a result |
| `risk_accept_no_authority_flag` | 0.5 | IM-007 | Accepted risks with no named accepting authority |

**IM-008 has no threshold slider by design.** It compares a declared KPI against the same metric
recomputed from the entity's own records, using a per-KPI tolerance (percentage points for
percentages, a ratio for minutes) defined in `detection/incident_mgmt.py::DECLARED_KPI_MAP`. The
tolerances exist to absorb rounding and reporting lag, not to excuse a gap: the reconciliation
table is shown for **every** declared KPI on the Entity Profile page, whether or not a finding
fires, so a supervisor can read the comparison directly.

## 6. Evidence handling and scoring

| Key | Default | Where | What it controls |
|---|---|---|---|
| `evidence_sample_cap` | 25 | findings | Evidence ids stored per finding (the true count is always kept) |

Scoring weights live in `config.py` (`RISK_SCORE_WEIGHTS`, `RISK_SCORE_BLEND`,
`DEFAULT_CAPABILITY_WEIGHTS`, `RISK_TIERS`, `CRITICALITY_WEIGHTS`). The 23 risk components are
weighted gaps, each normalised to 0–1 against its worst case, and the displayed score is
`60% metric index + 40% capability scorecard average` by default. Capability weights (C1–C8) are
editable from Settings; the tier weights are what turn a risk score into a **supervisory
priority** and are intentionally kept in code, because they encode exposure policy rather than
detection sensitivity.

## 7. Reference: alert-severity vocabulary

Detectors assume `severity ∈ {critical, high, medium, low}`. Ingestion normalises common
aliases (`P1`, `urgent`, `sev1`, `1`, …) onto that vocabulary, so a CSE exporting priority codes
instead of severity labels still loads — but that normalisation is a mapping, and if a CSE's
priority scheme is unusual it is worth checking the parsed values on the Entity Profile page
before trusting a threshold-based finding about it.
