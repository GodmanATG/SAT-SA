# SAT-SA — Architecture

**Supervisory Analytics Tool for SOC Assessment · NCIIPC Problem Statement 26157**
*Two-page architecture summary. Companion documents: `README.md` (full guide),
`DEMO_SCRIPT.md` (demo run-through), `THRESHOLDS.md` (every tunable value),
`USER_GUIDE.md` (operational walk-through).*

---

## 1. What the system is

SAT-SA is a **supervisory analytics capability**, not an operational security capability. It
ingests periodic, batch-submitted SOC alert and case-management evidence from Critical Sector
Entities, detects the operational weaknesses that conventional reporting mechanisms cannot
show, and prioritises the entities, capabilities and individual records an examiner should
read by hand. It does not monitor, does not collect telemetry, does not run a SOC, and never
replaces supervisory judgement — it concentrates it.

**Deployment envelope.** Fully offline / air-gapped. No network calls in the codebase, no
cloud service, no SaaS dependency, no externally hosted model or API. Inference is local
statistics (pandas, scikit-learn, scipy); reports are rendered locally (fpdf2). The entire
store is one portable SQLite file.

---

## 2. Pipeline

```
        CSE submission folders                 (periodic, batch, multi-CSE)
                 │
                 ▼
   INGESTION ────────────────────────────────────────────────────────────────
   signature-based file classification → column-synonym mapping →
   canonical tables (alerts, cases, investigations, escalations,
   dispositions, asset_inventory) → derived cross-file fields →
   entity register (permanent, editable, idempotent re-submission)
                 │
                 ▼
   FEATURE ENGINEERING ─────────────────────────────────────────────────────
   vectorised pandas → entity-level features + entity×month features;
   volume normalised by each entity's own monitored estate
                 │
                 ▼
   DETECTION ───────────────────────────────────────────────────────────────
   EG-001…006   execution gaps          (deterministic thresholds)
   NS-001…006   negative space          (expected evidence absent, DQ-gated)
   IM-001…008   incident mgmt/governance(SLA integrity, severity softening,
                                         RCA, rework, reopens, trace, risk
                                         acceptance, declared-vs-evidence)
   BM-001…003   absolute benchmarks     (peer-independent reference values)
   STAT-001…003 peer deviation, trend drift, multivariate outlier
                 │
                 ▼
   SCORING ────────────────────────────────────────────────────────────────
   metric index (60%) + C1–C8 capability scorecard (40%) → risk score,
   risk tier, supervisory priority (risk × criticality tier)
                 │
                 ▼
   OUTPUT ─────────────────────────────────────────────────────────────────
   prioritised entity list · finding cards with rationale + evidence ·
   prioritised manual-review samples · peer/heatmap/trend views ·
   offline PDF + audit log · validation & examiner adjudication
```

Every finding is a record of the form

```
(rule_id, title, rationale, weakness_type ∈ {execution_gap, negative_space,
 peer_anomaly, data_quality}, capability_tags ⊂ {C1…C8}, severity, severity_score,
 metric_value, threshold_value, evidence_ids[capped], evidence_count)
```

so a finding can always be traced back to the value that produced it and the records behind it.

---

## 3. Data model

Seven submitted artefacts per CSE map onto six canonical tables plus the register:

| Artefact | Table | Notes |
|---|---|---|
| `entity_profile.json` | `entities` | identity, criticality tier, SOC arrangements, **declared controls + declared KPIs** |
| `alerts_export.*` | `alerts` | alert metadata, ack/close timing, disposition, case link |
| `cases_dump.*` | `cases` | case management + investigation note text |
| `investigations_workflow.*` | `investigations` | process-mining event log |
| `escalations.*` | `escalations` | escalation decisions incl. severity/priority at escalation |
| `dispositions.*` | `dispositions` | authoritative closure evidence, SLA target vs actual, root cause, risk acceptance |
| `inventory.*` | `asset_inventory` | estate with monitoring required/actual state |

Plus `findings`, `entity_metrics`, `monthly_metrics`, `adjudications`, `documents` and
`audit_log`.

Two design decisions worth stating explicitly:

* **Missing evidence is data, not an error.** Foreign keys are relaxed during ingestion: an
  escalation referencing a non-submitted case is kept, because the absent investigation record
  is itself a supervisory signal (rule NS-005/IM-006). Negative-space detectors are gated on a
  per-table data-quality score so that "no evidence" is never confused with "no reporting".
* **Examiner decisions are immutable.** `adjudications` carries no foreign key to `findings`,
  because the findings table is rebuilt on every analytics run while a verdict is a permanent
  record of human judgement.

---

## 4. Explainability and auditability

* **No black boxes.** Nothing is trained from data. Every detector is a named, deterministic
  function of named features; the Isolation Forest is used only for multivariate *triage* and is
  labelled as weaker evidence than a single-metric breach; note-similarity uses TF-IDF cosine
  with a documented fallback.
* **Templated rationales.** Each finding states what was measured, against which threshold or
  reference, over how many records, and what it is compared against (cohort norm, absolute
  reference value, or the entity's own declaration).
* **Declared vs measured.** Declared KPIs are recomputed from the entity's own records and the
  reconciliation is displayed whether or not a finding fires (rule IM-008).
* **Full traceability.** Every analytics run writes the complete threshold set to `audit_log`,
  so any historical result is exactly reproducible.
* **Human-in-the-loop.** Adjudications are append-only with rationale; verdicts of *False
  positive* / *Expected* remove a finding from scoring while keeping it visible; per-detector
  examiner agreement is reported as the evidence base for threshold tuning.

---

## 5. Deployment, scale and limits

* **Runtime:** standard workstation, no GPU; ~47,000 alerts + ~83,000 investigation events +
  27 entities analyse in under 30 s (single process, pandas).
* **Storage:** one SQLite file (~40 MB for that portfolio) with `auto_vacuum=FULL`, capped
  evidence samples, and no duplicate note storage.
* **Portability:** copy `app/` + `satsa.db`; schema migrations run on startup so an existing
  register is brought forward without data loss.
* **Scale ceiling:** comfortable into the low hundreds of entities / millions of rows; beyond
  that the same feature layer should sit on a columnar store (DuckDB/Parquet).
* **Unfinished:** cycle-over-cycle snapshot diffing, and the five-slide technical presentation.

---

## 6. Validation methodology

Because no real labelled dataset can be shipped, the tool validates against **known injected
ground truth**: the seeded generator records which weakness it placed in which entity, the
detection engine runs without seeing that file, and findings are matched back to produce
per-detector precision/recall. Six entities are deliberately left clean, making the
false-positive rate measured rather than assumed. Current result on the bundled portfolio:
30/30 injected weaknesses detected, 0 findings on control entities.

Those numbers prove the detectors fire on the patterns they claim to detect. They do not prove
agreement with expert review — that requires the shadow-run pilot: run SAT-SA alongside one real
manual review cycle and measure entity-by-entity and rule-by-rule overlap, using the
adjudication log as the common record.
