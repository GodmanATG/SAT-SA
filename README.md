# SAT-SA — Supervisory Analytics Tool for SOC Assessment

**SIH 2026 · Problem Statement 26157 (NCIIPC) · Team Sirius**

SAT-SA is an **offline, supervisory analytics** tool. It ingests periodic, batch-submitted
SOC alert and case-management data from Critical Sector Entities (CSEs), finds the
operational weaknesses that conventional reporting hides, and tells an examiner **which
entities to look at first, and exactly which records to read**.

It is explicitly **not** a SOC, not a SIEM, not real-time monitoring, and not a log
collector. It never sees raw logs, packet captures or customer data — only metadata that a
CSE already produces (alert, case, investigation, escalation, closure and asset-inventory
exports).

> **Nothing leaves the machine.** No internet, no cloud service, no SaaS platform, no hosted
> AI model. Every number on screen is produced by local Python (pandas / scikit-learn /
> scipy) and every report is rendered locally with fpdf2.

---

## 1. Quick start

```bash
cd SAT-SA/app
python -m venv .venv                    # optional but recommended
# Windows:  .venv\Scripts\activate      Linux/macOS:  source .venv/bin/activate
pip install -r requirements.txt         # one-time; needs internet OR a local pip mirror

streamlit run app.py
```

The app opens at `http://localhost:8501`.

**The app starts blank.** Nothing is pre-loaded and nothing is analysed until you register at
least one entity and ingest its submission. That is deliberate: a supervisory tool must not
present a portfolio the supervisor did not provide.

**Then pick one of three routes:**

| Route | Where | What it does |
|---|---|---|
| **Register one CSE by hand** | Register & Submissions → *Register a CSE* | Profile (+ criticality tier, SOC arrangements, declared controls/KPIs) and upload its files. Stored permanently; editable afterwards. |
| **Bulk-ingest submissions** | Register & Submissions → *Bulk Ingest* | Point at a directory of CSE folders; every entity is registered and ingested in one pass. |
| **Generate a demo portfolio** | Register & Submissions → *Generate & Maintenance* | Writes 27 fictional CSEs across all 10 CII sectors (one click: generate → ingest → analyse). |

Then press **Run analytics across all registered entities**. Analytics always run across the
whole register, so peer comparison and sector benchmarking stay meaningful — a single entity
is analysed on its own and simply has no peers yet.

Command-line equivalent (useful for a headless / scheduled batch cycle):

```bash
cd SAT-SA/app
python -m synthetic.submissions                       # 27 CSE folders, 6 months, ~47k alerts
python -m ingestion.bulk                              # register + ingest every folder
python detection/engine.py                            # analyse, print a summary, log the run
```

After the packages are installed once, **every subsequent step works with no network**.

---

## 2. What it actually does

### 2.1 Input: the six submission field groups

A CSE submission is a **folder**, and the tool models the problem statement's data
environment field group for field group:

| File | Field group | Representative fields |
|---|---|---|
| `entity_profile.json` | Cover sheet | entity identity, criticality tier, SOC model / coverage window / headcount, **declared controls**, **declared KPIs** |
| `alerts_export.csv` | (i) Alert metadata | alert/detection timestamps, source system, rule id, name, category, type, severity, priority, risk score, confidence, status, asset, hostname/IPs, user, countries, detection source, parent alert, ack/close timestamps, disposition, case link |
| `cases_dump.json` | (ii) Case management | created/updated/closed/resolution timestamps, type, category, priority, severity, status, teams, analysts, alert count, affected assets/users, description, parent/related cases, investigation note text, linked alerts |
| `investigations_workflow.csv` | (iii) Investigation workflow | event id, case/alert, timestamp, sequence, activity + subtype, analyst, team, asset, evidence type, action result, duration, previous/next activity |
| `escalations.csv` | (iv) Escalation records | timestamp, from/to team and role, level, reason, severity and priority **at escalation**, decision, approver, response time, status |
| `dispositions.csv` | (v) Disposition & closure | disposition, closure reason/time/by/team, root cause, false-positive reason, true positive, risk accepted + **accepting authority**, remediation status/reference, reopen count, time-to-close, response target, SLA flag |
| `inventory.csv` | (vi) Asset & system inventory | asset identity, class, criticality, environment, owner, data classification, monitoring required/status/source, expected alert frequency, observed alert count, telemetry and scan timestamps, vulnerability status |

**Ingestion is idempotent and forgiving.** Every row is inserted against its natural primary
key, so a corrected re-submission updates in place; re-ingesting never duplicates. Column
synonyms resolve vendor naming (`ticket_id`, `created_at`, `resolution_note`, …). Files are
classified by their own signature, not by filename. A dangling cross-reference (an escalation
pointing at a case that was never submitted) is *kept*, because a missing investigation record
is supervisory evidence, not an upload error.

Some fields a detector needs are not columns in any single file — a case's root cause lives in
the closure record, a case's reopen count is recorded against the closure. Those are derived
once, in `ingestion/bulk.py::derive_linked_fields`, so every detector reads one schema and the
derivation is in one auditable place.

### 2.2 The register (permanent, editable, filterable)

Entities are stored in the database — not in a session, not in a file the user must keep.
`entity_id` is derived deterministically from the entity name (`database.entity_id_for`), so
adding a company by hand and later ingesting its submission folder bind to the *same* entity
instead of creating a duplicate, and re-registering updates in place.

Every profile field is editable: identity, sector, **criticality tier**, SOC delivery model and
coverage window, analyst headcount, contact details, review period, declared controls,
declared KPIs and notes. Criticality is a first-class attribute used two ways:

* it **filters the register and every analytics view** (Tier 1…Tier 4); and
* it produces a **supervisory priority** = risk score × tier weight (Tier 1 ×1.00 → Tier 4
  ×0.55), because two entities with identical gaps are not equally urgent.

Withdrawing an entity deletes its records, findings and scores from the register; the files on
disk are left alone.

### 2.3 Analytics

Feature engineering (`detection/features.py`) is vectorised pandas work over the six tables,
producing the entity-level and entity × month feature set every detector reads. Volume is
normalised by each entity's own monitored estate, which is what makes "unexpectedly low
activity" measurable rather than an artefact of report size.

26 detectors in five families:

* **Execution gaps (EG-001…EG-006)** — rubber-stamp closure, critical alerts without
  escalation, template-driven notes, chronic repeat alerts without root cause, bulk closures,
  shallow investigations.
* **Negative space (NS-001…NS-006)** — silent critical assets, absent alert categories,
  night-time blind spot, low activity vs peers, missing case/escalation records, assets
  declared for monitoring but not actually monitored. Negative-space findings are gated on a
  per-table data-quality score, so "no evidence" is never confused with "no reporting".
* **Absolute benchmarks (BM-001…BM-003)** — acknowledgement, containment and escalation
  compared against fixed reference values, so a sector-wide weakness cannot hide behind peer
  comparison ("everyone is equally bad" still flags). Reference values are editable and every
  finding repeats the reference it used.
* **Peer-relative & statistical (STAT-001…STAT-003)** — leave-one-out cohort medians,
  z-score deviation, and a multivariate Isolation Forest for entities that are odd only in
  combination.
* **Incident management & governance (IM-001…IM-008)** — the module aimed squarely at the
  problem statement's *execution gap* definition:

  | Rule | What it reads | What it catches |
  |---|---|---|
  | IM-001 | Disposition records | Closures that miss their recorded response target, **and** closures flagged SLA-compliant that their own time-to-close contradicts (metric gaming) |
  | IM-002 | Cases vs their alerts | Cases and escalations logged at a lower severity than the alerts they were opened from — the incident is re-labelled at intake |
  | IM-003 | Dispositions | Significant incidents closed with no root cause and/or no remediation reference |
  | IM-004 | Investigation workflow | Process mining: investigations cycling back through activities they already completed |
  | IM-005 | Dispositions | Closures later reopened (first-time-closure overstated) |
  | IM-006 | Cases + workflow | Cases with no investigation trace; workflow steps recording neither evidence nor a result |
  | IM-007 | Dispositions | Risk accepted with no named accepting authority |
  | IM-008 | Cover sheet vs records | **Declared KPIs contradicted by the entity's own operational records** — the declared-vs-evidence execution gap |

* Explainability is structural, not bolted on: every finding carries a rule id, the metric
  value and the threshold that triggered it, the capabilities it bears on, a templated
  rationale that names the comparison basis, and a capped sample of the exact evidence record
  ids (with the true count). Reference values and thresholds are never hidden.

### 2.4 Scoring

Two independently meaningful halves, blended 60/40 (editable):

* **metric index** — weighted average of normalised operational metric gaps (23 metrics);
* **capability scorecard C1–C8** — the eight supervisory capabilities from the problem
  statement, built from the severity of the findings tagged to each.

Higher = more supervisory concern. The score is a **prioritisation index**, not a probability,
and every component is displayed with its weight. Nothing is trained from data, because there
is no labelled ground truth in a real deployment.

### 2.5 The adjudication loop (human-in-the-loop, both halves)

1. **Capture the judgement.** Every finding can be adjudicated on the finding card itself or
   on the *Examiner Review* queue: **Confirmed / Not material / Expected / Needs more data /
   False positive**, with a mandatory rationale for anything but Confirmed. Verdicts are
   **appended, never overwritten**, so each finding keeps its full decision history and a change
   of view remains auditable.
2. **Let the judgement change the answer.** A finding adjudicated *False positive* or
   *Expected* stops contributing to the entity's capability scores and therefore to its risk
   score, while staying visible for audit. Finding ids are deterministic
   (`uuid5(entity, rule, concept)`), so verdicts re-attach automatically after a re-analysis —
   the examiner's work is never destroyed by a pipeline run.

The same page reports **per-detector examiner agreement** — the measured evidence that the
tool's prioritisation agrees with expert judgement, and the basis for tuning each rule's
threshold. The adjudication log exports to CSV for the pilot.

### 2.6 Output

* **Supervisory Overview** — prioritised entity list (orderable by supervisory priority, risk
  score or criticality), tier distribution, score decomposition component by component,
  C1–C8 scorecard.
* **Finding Cards** — rationale and evidence per finding, inline adjudication, plus a
  **prioritised alert/case sample list** for manual review (Requirement 10).
* **Entity Profile & Declarations** — one entity's profile, the declared-vs-measured
  reconciliation behind IM-008, which of the six field groups actually arrived, the full named
  feature set with raw metric keys, its findings and adjudication state, and its audit trail.
* **Evidence Drill-Down / Peer Comparison / Activity Heatmap / Trends** — verify the raw
  records, benchmark against peers, check coverage, and see what is *drifting* rather than
  merely bad today.
* **Report Export** — offline PDF per entity or portfolio, plus the audit log.
* **Validation & Methods** — detector performance against known ground truth, examiner
  agreement, and an explicit statement of what the evidence does not prove.

---

## 3. Page-by-page

| Page | Purpose |
|---|---|
| 📁 **Register & Submissions** | Register/edit/withdraw entities, upload or bulk-ingest submissions, coverage checklist, generate a synthetic portfolio, storage maintenance, run analytics |
| 📊 **Supervisory Overview** | Who to look at first; score decomposition; C1–C8 scorecard |
| 🚩 **Finding Cards** | Why each entity was flagged; inline adjudication; prioritised review sample list |
| 🧑‍⚖️ **Examiner Review** | Adjudication queue, verdict history, score impact, per-detector agreement |
| 🏢 **Entity Profile** | Profile, declared-vs-measured, submission coverage, measured features, audit trail |
| 🔍 **Evidence Drill-Down** | Read the underlying alerts/cases behind a finding |
| 📡 **Peer Comparison** | Sector cohorts, leave-one-out medians, deviation tables |
| 🕒 **Activity Heatmap** | Coverage by hour / day / asset class — spot the blind spots |
| 📈 **Trend Analysis** | Month-over-month drift on real per-month features |
| 📄 **Report Export** | Offline per-entity and portfolio PDF, audit log export |
| 🧪 **Validation & Methods** | Ground-truth precision/recall, examiner agreement, stated limitations |
| ⚙️ **Settings** | All thresholds (including the IM family), benchmarks, capability and risk weights |

---

## 4. Architecture

```
SAT-SA/
├── app/
│   ├── app.py                     Streamlit shell: navigation, filters, blank-start onboarding
│   ├── config.py                  sectors, criticality tiers, thresholds, benchmarks, weights
│   ├── database.py                SQLite schema + migrations, register CRUD, adjudications
│   ├── detection_config.json      runtime-editable config (written by Settings)
│   ├── ingestion/
│   │   ├── parsers.py             per-format readers
│   │   ├── normalizer.py          file-type detection + column-synonym mapping
│   │   └── bulk.py                folder ingest, register binding, derived fields
│   ├── detection/
│   │   ├── common.py              finding record shape, helpers
│   │   ├── features.py            entity & entity×month feature engineering
│   │   ├── execution_gaps.py      EG-001…EG-006
│   │   ├── negative_space.py      NS-001…NS-006 + data-quality gate
│   │   ├── benchmarks.py          BM-001…BM-003
│   │   ├── statistical.py         peer z-scores, trends, Isolation Forest
│   │   ├── incident_mgmt.py       IM-001…IM-008
│   │   ├── scoring.py             metric index, C1–C8 scorecard, risk score
│   │   └── engine.py              orchestration, persistence, audit logging
│   ├── synthetic/submissions.py   seeded generator for 27 CSEs + ground truth
│   ├── reports/pdf_gen.py         offline PDF rendering
│   └── views/                     one module per page
├── submissions/                   generated/uploaded CSE folders (gitignored)
└── docs/                          ARCHITECTURE.md, DEMO_SCRIPT.md, THRESHOLDS.md
```

**Storage.** A single portable `satsa.db` (SQLite, WAL, `auto_vacuum=FULL`). The full bundled
portfolio — 27 entities, six months, ~47,000 alerts, ~14,000 cases, ~83,000 investigation
workflow events, ~15,000 disposition records — occupies roughly 70 MB, and the storage panel on
the *Generate & Maintenance* tab reports the figure live with a one-click `VACUUM`. (It was
~27 MB before the investigation-workflow and disposition tables existed; those two tables are
what the IM detectors read, so the growth is the cost of the new findings.) Evidence samples are
capped per finding (the true population is kept as a count), and `__pycache__`, generated
submissions and the database are gitignored — the portfolio is regenerated in ~15 s rather than
shipped.

**Schema migration.** `database.migrate_schema` adds any column introduced by a later build on
startup, so a supervisor already holding a database does not have to throw away their register.
Adding a column in SQLite is lossless; nothing is ever dropped.

See `docs/ARCHITECTURE.md` for the two-page architecture document and `docs/DEMO_SCRIPT.md`
for the two-minute demo run-through.

---

## 5. Validation

The generator records exactly which weakness it injected where, so detector performance is
**measured** rather than asserted. On the bundled 27-entity portfolio:

* **30 of 30** injected weaknesses detected (recall 100%);
* **0** findings raised on the six deliberately clean control entities (measured false-positive
  rate, not an assumed one);
* peer-relative and trend detectors are reported separately, because a control entity can
  legitimately be a population outlier.

Those are **synthetic-ground-truth** numbers. They prove the detectors fire on the patterns
they claim to detect; they do **not** prove agreement with expert manual review. Only examiner
adjudication measures that, which is what the shadow-run pilot is for: run SAT-SA alongside one
real review cycle and compare findings entity-by-entity and rule-by-rule. The honest limitations
are stated on the Validation page itself, in the app the examiner actually uses.

---

## 6. Deployment

* **Air-gapped.** No network calls anywhere in the codebase. Install dependencies once from a
  local wheel mirror or offline installer bundle, then run indefinitely without a network.
* **Hardware.** Runs on a standard workstation; no GPU. Inference is statistical (`scikit-learn`
  Isolation Forest + TF-IDF cosine similarity), not a hosted model.
* **Portability.** Copy `app/` plus `satsa.db` — the tool works with no installation of its own
  beyond the Python packages.
* **Reproducibility.** The synthetic dataset is seeded; every analytics run logs the *complete*
  threshold set used to `audit_log`, so any historical result can be re-derived exactly.

---

## 7. Using it day to day

`docs/USER_GUIDE.md` is the operational walk-through: registering an entity, reading each page,
what every finding family means in practice, the expected question to put to the entity for each
finding, maintenance and backups. `docs/THRESHOLDS.md` lists every tunable value with the
method for tuning it. `docs/DEMO_SCRIPT.md` is a timed two-minute demonstration.

---

## 8. Known limitations / where to improve next

1. **Thresholds are defaults, not fitted values.** They are calibrated to published-style SOC
   medians. Re-tune on real data; the per-detector examiner agreement table is the evidence for
   doing so. A first tuning pass — moving each rule's threshold toward the point where examiner
   confirmations peak — is the highest-value next step.
2. **Synthetic ground truth ≠ NCIIPC expert review.** Run the shadow-run pilot. Nothing in this
   repository substitutes for it, and the tool says so on the Validation page.
3. **No cycle-over-cycle diffing yet.** Each run overwrites `entity_metrics`. Snapshotting runs
   would let the tool say "what changed since the last submission cycle", which is what turns a
   one-off review into continuous supervision.
4. **Single-machine scale.** Fine for dozens of entities and tens of thousands of records;
   cross-entity aggregation beyond that wants a columnar store (DuckDB/Parquet) behind the same
   feature layer.
5. **Sector reference profiles are illustrative.** `SECTOR_EXPECTED_CATEGORIES` and the absolute
   benchmarks should be replaced with NCIIPC's own published baselines before operational use.
6. **The five-slide technical presentation** is still to be produced from
   `docs/ARCHITECTURE.md` and the Validation page.

---

## 9. Thesis in one line

Conventional assurance reads what an entity **reports**; SAT-SA reads what its **records
actually show** — and where the two disagree, that disagreement is the finding.
