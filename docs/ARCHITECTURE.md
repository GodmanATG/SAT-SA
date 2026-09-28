# SAT-SA — Architecture Document

**Supervisory Analytics Tool for SOC Assessment**
**SIH 2026 | Problem Statement 26157 (NCIIPC) | Team Sirius**

---

## 1. System Overview

SAT-SA is a **supervisory analytics capability** — not a SOC, not a SIEM, not a monitoring platform. It ingests periodic, batch-submitted SOC alert and case-management metadata from Critical Sector Entities (CSEs), detects the operational weaknesses that policies, audits, KPI dashboards, and compliance documentation cannot surface, and tells the examiner **which entities to audit first, which capabilities are weak, and exactly which records to read**.

### Deployment Constraints (fully satisfied)

| Constraint | Implementation |
|---|---|
| Fully offline / air-gapped | Zero network calls in the entire codebase |
| No cloud / SaaS dependency | Local SQLite storage, local Python processing |
| No externally hosted AI/API | scikit-learn Isolation Forest + TF-IDF run locally, CPU-only |
| Local data processing only | All ingestion, detection, scoring, and PDF export run on the local machine |
| Hardware | Standard workstation (8-core CPU, 16 GB RAM recommended). No GPU required |

---

## 2. Architecture & Data Flow

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │                     CSE SUBMISSION FOLDERS                          │
 │   (CSV / JSON — 6 data types per entity, periodic batch delivery)  │
 └────────────────────────────────┬─────────────────────────────────────┘
                                  │
                                  ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │  LAYER 1: INGESTION                                                 │
 │  • Signature-based file-type classification                         │
 │  • Column-synonym mapping (schema-agnostic normalisation)           │
 │  • Idempotent load into 6 canonical tables + entity register        │
 │  • Derived cross-file fields (e.g. case↔alert severity linkage)     │
 └────────────────────────────────┬─────────────────────────────────────┘
                                  │
                                  ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │  LAYER 2: FEATURE ENGINEERING                                       │
 │  • 23+ normalised entity-level operational metrics                  │
 │  • Entity × month time-series features for trend detection          │
 │  • Volume normalised by each entity's own monitored estate          │
 └────────────────────────────────┬─────────────────────────────────────┘
                                  │
                                  ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │  LAYER 3: DETECTION ENGINE — 30 Rules across 5 Analytical Families  │
 │                                                                     │
 │  EG-001…EG-007  Execution Gaps         (deterministic thresholds)   │
 │  NS-001…NS-007  Negative Space         (missing evidence, DQ-gated)│
 │  IM-001…IM-009  Incident Management    (SLA, severity, rework, RCA) │
 │  BM-001…BM-003  Absolute Benchmarks    (peer-independent baselines) │
 │  STAT-001…002   Peer Deviation & Multivariate Outlier (Iso. Forest) │
 │                                                                     │
 │  + DQ-001 Data Quality gate for negative-space detectors            │
 └────────────────────────────────┬─────────────────────────────────────┘
                                  │
                                  ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │  LAYER 4: SCORING & PRIORITISATION                                  │
 │  • Metric Index (60%) — weighted gap across all operational metrics │
 │  • C1–C8 Capability Scorecard (40%) — mapped to NCIIPC's 8 caps    │
 │  • Composite Risk Score → Risk Tier (Critical/High/Medium/Low)      │
 │  • Supervisory Priority = Risk Score × Criticality Tier weight      │
 └────────────────────────────────┬─────────────────────────────────────┘
                                  │
                                  ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │  LAYER 5: OUTPUT & HUMAN-IN-THE-LOOP                                │
 │  • Streamlit multi-page supervisory dashboard (12 views)            │
 │  • Finding cards with rationale, evidence IDs, and capability tags   │
 │  • Examiner adjudication (append-only verdicts, score exclusion)    │
 │  • Offline PDF reports (fpdf2) — per-entity and portfolio-wide      │
 │  • Complete audit log of every analytics run + threshold snapshot    │
 └──────────────────────────────────────────────────────────────────────┘
```

---

## 3. Data Model

Six submitted artefact types per CSE map to six canonical database tables:

| CSE Submission File | Database Table | What it contains |
|---|---|---|
| `entity_profile.json` | `entities` | Identity, criticality tier, SOC arrangements, declared controls & KPIs |
| `alerts_export.csv` | `alerts` | Alert metadata, severity, ack/close timestamps, case linkage |
| `cases_dump.csv` | `cases` | Case management records, investigation note text |
| `investigations_workflow.csv` | `investigations` | Process-mining event log (activity, timestamps, evidence) |
| `escalations.csv` | `escalations` | Escalation decisions, severity at escalation point |
| `dispositions.csv` | `dispositions` | Closure evidence, SLA target vs. actual, root cause, risk acceptance |
| `inventory.csv` *(optional)* | `asset_inventory` | Monitored estate with required vs. actual monitoring state |

**Storage:** Single portable SQLite file (WAL mode, `auto_vacuum=FULL`). Full 47-entity portfolio with 170K+ alerts occupies ~340 MB. Evidence samples capped per finding; full population reconstructed on-demand at export.

**Key design decisions:**
- **Missing evidence is data, not an error.** Foreign keys are relaxed during ingestion so that an escalation referencing a non-submitted case is retained — the absent record is itself a supervisory signal.
- **Examiner decisions are immutable.** Adjudications carry no FK to findings (which are rebuilt each run). Verdicts persist via deterministic `uuid5` finding IDs.

---

## 4. AI/ML Specification

| Component | Architecture | Purpose | Training Data | Offline? |
|---|---|---|---|---|
| **Isolation Forest** | scikit-learn `IsolationForest` | Multivariate anomaly triage — flags entities that appear normal per individual metric but are anomalous in combination | Unsupervised — fits on the entity feature matrix at each run | Yes, fully local CPU |
| **TF-IDF + Cosine Similarity** | scikit-learn `TfidfVectorizer` | Detects template-driven / copy-paste investigation notes (rule EG-003) | Unsupervised — vectorises the entity's own investigation notes | Yes, fully local CPU |
| **Leave-one-out Peer Medians** | scipy + pandas | Peer benchmarking — computes sector cohort statistics excluding the entity under analysis | No training — pure descriptive statistics | Yes |

**Model update mechanism:** Models are stateless and refit from scratch on every analytics run using the current data. No persistent model artefacts are stored. Thresholds are stored in `detection_config.json` and are editable via the Settings page.

**Explainability controls:** Every detector is a named, deterministic function of named features. No neural networks, no opaque embeddings. The Isolation Forest is labelled as weaker evidence than a direct threshold breach. Every finding states the measured value, the threshold, the evidence records, and the capability tags.

---

## 5. Validation Methodology

| Method | What it measures | Current result |
|---|---|---|
| **Synthetic ground truth** | Seeded generator injects known weaknesses into specific entities; detectors run blind; findings are matched back per rule | 33/33 injected weaknesses detected (100% recall), 0 findings on clean control entities (0% FP) |
| **Examiner adjudication** | Per-detector agreement rate between the tool's findings and examiner verdicts (Confirmed / False Positive / Expected) | Framework built; awaiting real-data shadow-run pilot |

The synthetic numbers prove detectors fire on claimed patterns. Real-world validation requires running SAT-SA alongside one NCIIPC manual review cycle and comparing findings entity-by-entity using the built-in adjudication log.

---

## 6. Technology Stack

| Layer | Technology | Version |
|---|---|---|
| Language | Python | 3.10+ |
| Dashboard | Streamlit | ≥ 1.35 |
| Data processing | pandas, NumPy | ≥ 2.0, ≥ 1.24 |
| Machine Learning | scikit-learn | ≥ 1.3 |
| Statistics | SciPy | ≥ 1.11 |
| Visualisation | Plotly | ≥ 5.20 |
| PDF Reporting | fpdf2 | ≥ 2.7 |
| Database | SQLite (WAL mode) | Built-in |
| Deployment | Local / air-gapped | No containers required |
