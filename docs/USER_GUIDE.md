# SAT-SA — How to use it, what it does, what to improve

A working guide for the person sitting in front of the tool. Written to be read once,
end to end, in about ten minutes.

---

## 1. What this tool is for

NCIIPC examines samples of alerts and case-management records from Critical Sector Entities.
That manual review finds things no dashboard shows. SAT-SA does the **reading and the
arithmetic at scale** so that examiner time is spent on judgement rather than on sampling:

1. **Which entities need attention?** Prioritised list, risk score decomposed component by
   component, weighted by criticality tier.
2. **Which capability is weak?** The eight supervisory capabilities from the problem statement
   (detection, investigation, escalation, incident response, security operations, governance,
   operational discipline, cyber resilience), scored per entity.
3. **Which records should I actually read?** A concrete, ranked list of alert and case samples
   sitting behind the highest-severity findings.

It does not decide anything. It narrows what a human should look at, and shows its reasoning
so the human can disagree.

**Two kinds of problem it looks for** — worth internalising, because every finding is one or
the other:

* **Execution gap** — the entity says a control exists, and its records show it is not
  working. *Examples:* SLA compliance declared at 99% while its own closure records show 30%
  missed the target; a case logged at low priority for the alert that was flagged critical.
* **Negative space** — the evidence that should exist does not. *Examples:* a critical plant
  asset with no telemetry for months; an entity with no escalations at all; cases with no
  investigation steps recorded.

---

## 2. Getting started (the app starts blank)

Nothing is pre-loaded. That is deliberate — the register is what you provide.

**Route A — register one entity by hand** (the normal case)

1. Sidebar → **📁 Register & Submissions** → *Register a CSE*.
2. Fill in identity (name, sector, **criticality tier** — Tier 1 means critical national
   infrastructure operator), SOC arrangements (model, coverage window, headcount), contact and
   review period.
3. Fill in **what the entity declares**: the controls it claims and its performance metrics
   (SLA compliance, ack median, containment median, escalation compliance, investigation
   completeness, monitoring coverage). This is optional but is what makes the strongest
   finding possible — declared-vs-evidence gaps (rule IM-008).
4. Attach its files (alert metadata, case management, investigation workflow, escalations,
   dispositions, inventory — any mix), then **Save entity profile** (or *Save, ingest files and
   run analytics*).
5. The profile is stored permanently and every field can be edited afterwards from the
   *Entity Register* tab.

**Route B — bulk load** a directory of CSE submission folders → *Bulk Ingest* → point at the
directory → *Register & ingest submissions*.

**Route C — for a demo or a dry run**: *Generate & Maintenance* → **⚡ Generate, ingest and
analyse** builds a 27-CSE synthetic portfolio (10 CII sectors, six months, ~47k alerts) and
runs everything. It is seeded, so the numbers are reproducible — useful for the demo video.

Then press **Run analytics across all registered entities**. Analytics always run across the
whole register so peer comparison stays meaningful.

> **Adding or correcting data later:** re-ingest the entity's folder. Ingestion is idempotent —
> corrected files update records in place, they never duplicate. Then re-run the analytics.

---

## 3. Reading the results, page by page

**📊 Supervisory Overview** — the answer to "who first?".
* *Review order* switches between supervisory priority (risk × criticality), risk score alone,
  and criticality-first. Use priority for scheduling, risk score when you want the pure
  evidence gap.
* The score breakdown chart shows every weighted component. Nothing is hidden behind a single
  number.
* The C1–C8 table shows *which capability* is weak, not just that the entity is weak.

**🚩 Finding Cards** — the answer to "why was this flagged?".
Each card states the rule, severity, the capabilities affected, the measured value against its
threshold, the rationale (including the comparison basis and record counts) and the evidence
samples. You can record your verdict right there (see §4).

**🧑‍⚖️ Examiner Review** — the adjudication queue plus the effect of your decisions: how many
findings were confirmed, how many stopped counting, and the agreement rate per rule.

**🏢 Entity Profile** — keep this open while reading a submission: profile and SOC
arrangements, **declared vs measured** reconciliation, which of the six submission field groups
actually arrived (an absent group is itself a finding), the full named feature set with the raw
metric keys used in threshold configuration, findings with adjudication state, and the entity's
audit trail.

**🔍 Evidence Drill-Down** — pull the underlying alerts and cases and verify by hand.

**📡 Peer Comparison / 🕒 Activity Heatmap / 📈 Trend Analysis** — peers tell you whether a
pattern is local or sector-wide; the heatmap exposes monitoring blind spots by hour/day; trends
show what is *getting worse* rather than merely being bad today.

**📄 Report Export** — offline PDF per entity or portfolio, plus the audit log.

**🧪 Validation & Methods** — how the detectors perform against known ground truth, how they
perform against *your* verdicts, and an explicit statement of what neither number proves.

**⚙️ Settings** — every threshold, benchmark and weight is editable, including the whole IM
family. Change → re-run → compare.

---

## 4. The adjudication loop (use it from day one)

For each finding, record one of: **Confirmed · Not material · Expected · Needs more data ·
False positive**, with a rationale for anything but Confirmed.

Three things follow, and they matter:

* Verdicts are **append-only** — the history of your reasoning survives, including a change of
  mind.
* *False positive* and *Expected* **remove the finding from scoring** (and therefore from the
  entity's risk score) while keeping it visible for audit. Your judgement, not the detector,
  has the last word.
* The **agreement table** accumulates the measured accuracy of each rule against human
  judgement. That table, not the synthetic numbers, is the evidence for tuning thresholds — and
  it is the dataset the shadow-run pilot produces.

---

## 5. What each family of findings means in practice

| Finding | What it usually indicates | What to ask the entity |
|---|---|---|
| EG-001 fast closure | Alerts acknowledged and closed in minutes | What was actually examined before closure? |
| EG-002 no escalation | Criticals never reach escalation | Who was notified, and when? |
| EG-003 template notes | Investigations are form-filling | Show me two investigations end to end |
| EG-004 repeat alerts | Same asset/category re-alerting, no root cause | What changed after the first occurrence? |
| EG-005 bulk closure | Batches closed inside one hour | Was this a sweep to clear a backlog? |
| EG-006 shallow investigation | Low depth score on the records | Reconcile with your depth policy |
| NS-001 silent critical assets | Critical assets generating nothing | Is telemetry actually reaching the SIEM? |
| NS-002 category coverage | Whole alert categories absent | Is the sensor estate complete? |
| NS-003 night gap | No activity outside office hours | Who is on shift at 03:00? |
| NS-004 low activity | Volume far below peers per monitored asset | Is anything being suppressed? |
| NS-005/IM-006 missing records | Criticals with no case/investigation trace | Where is the record? |
| NS-006 unmonitored assets | Declared for monitoring, not monitored | Why is the agent absent? |
| BM-001/002/003 | Outside published-style medians | Explain the timelines |
| IM-001 SLA integrity | Breaches, or compliance claimed contrary to the timeline | Reconcile reported compliance with closure data |
| IM-002 severity softening | Cases logged below their alert severity | Why was severity reduced at intake? |
| IM-003 no root cause/remediation | Significant closures with no RCA or fix | Show the RCA and the change record |
| IM-004 rework loops | Investigations cycling through completed steps | What blocked completion? |
| IM-005 reopens | Closures later reopened | What was missed first time? |
| IM-007 ungoverned risk acceptance | Accepted risk with no accepting authority | Who accepted it, under what authority? |
| IM-008 declared vs evidence | Declared KPIs contradicted by its own records | Reconcile the declaration |

---

## 6. What to improve next, in priority order

1. **Re-tune the thresholds on real data.** The defaults are calibrated to published-style SOC
   medians. After the first pilot cycle, use the per-detector examiner agreement table: raise
   thresholds for rules with a high false-positive share, lower them where examiners confirm
   everything. Every threshold is in Settings and in `docs/THRESHOLDS.md`.
2. **Replace the illustrative reference values.** `SECTOR_EXPECTED_CATEGORIES` and the three
   absolute benchmarks (`BENCHMARK_REFERENCES`) should be NCIIPC's own baselines, cited in the
   submission. The detectors already print the reference value they used, so this is a
   configuration change, not a code change.
3. **Run the shadow-run pilot.** One real review cycle, SAT-SA alongside, verdicts adjudicated
   in the app. That converts every number in the Validation page from "synthetic" to
   "measured against expert judgement", and it is the single highest-value thing left to do.
4. **Add cycle-over-cycle diffing.** Snapshot each run's `entity_metrics`; then the tool can say
   "what changed since the last submission cycle" — which is what turns a one-off review into
   continuous supervision.
5. **Enrich the estate view.** If CSEs can submit expected alert volumes per asset class, NS-001
   and NS-004 become far sharper (the field `expected_alert_frequency` already exists and is
   used; it just needs real values).
6. **Widen the evidence trail.** Attach the letters/notices raised per finding so the tool holds
   the full supervision lifecycle, not just the analytics half.
7. **Scale out.** For hundreds of entities, move the feature layer onto DuckDB/Parquet behind
   the same interfaces; nothing above the feature layer needs to change.
8. **Produce the five-slide technical presentation** from `docs/ARCHITECTURE.md` plus the
   Validation page.

---

## 7. Maintenance and housekeeping

* **Storage** — Register & Submissions → *Generate & Maintenance* shows database size, row
  counts and free pages; *Compact database* runs `VACUUM`. A 27-entity portfolio sits around
  40 MB; it is one portable SQLite file.
* **Backups** — copy `app/satsa.db`. That file *is* the register, the submissions and the
  findings. Adjudications live in it too.
* **Audit** — every ingest, registration, edit, withdrawal, analytics run and adjudication is
  written to `audit_log`, viewable per entity on the Entity Profile page and exportable with the
  report.
* **Withdrawing an entity** — Entity Register → select → *Withdraw*. Records, findings and
  scores are removed from the register; the files on disk are left untouched.

---

## 8. One-line summary

Conventional assurance reads what an entity **reports**. SAT-SA reads what its records **show** —
and where the two disagree, that disagreement is the finding.
