# SAT-SA — Two-minute demo script

**Setup before recording (do this once, off camera):**

```bash
cd SAT-SA/app
streamlit run app.py
```

Leave the app **blank** (empty register) and pre-generate the demo submissions so nothing waits
on the clock:

```bash
python -m synthetic.submissions        # ~15 s, off camera
```

The demo submission folders now sit in `SAT-SA/submissions/`. Total recording time: 2:00.

---

## 0:00–0:15 — The blank start

Open on **📁 Register & Submissions**, register empty.

> "SAT-SA starts with an empty register. It doesn't invent a portfolio and it doesn't analyse
> anything until a CSE has been registered and a submission ingested — because a supervisory
> tool must work from the evidence the supervisor actually holds."

Point at the three routes: register one CSE by hand, bulk-ingest a folder, or generate a
synthetic portfolio for a dry run.

## 0:15–0:35 — Register one CSE (the input story)

Open *Register a CSE*, show the profile: **criticality tier**, SOC model and coverage window,
headcount — and then the part that matters: **declared controls and declared KPIs**.

> "An entity tells us what it does. We store exactly what it declares, because the strongest
> supervisory finding is where a declaration and the entity's own records disagree."

Point at *Entity Register* → the filter row and the coverage checklist: criticality is a register
attribute, it filters the whole register, and an **absent submission field group is itself
visible** before any analysis runs.

## 0:35–0:50 — Generate, ingest, analyse

**⚙ Generate & Maintenance → ⚡ Generate, ingest and analyse.**

> "27 fictional CSEs across all 10 CII sectors, six months of alert metadata, case-management
> records, investigation workflow events, escalation records, disposition and closure records and
> asset inventory — about 47,000 alerts and 83,000 investigation events. Fictional names are
> deliberate: a synthetic dataset must never imply that a real organisation is underperforming."

(On camera this is the only wait, ~30 s. Say what is happening while it runs.)

## 0:50–1:05 — Supervisory Overview

* Switch **Review order** to *Supervisory priority (risk × criticality)*.

> "Priority is risk weighted by criticality: the same evidence gap is not equally urgent in a
> Tier 1 operator and a Tier 3 one. Risk score alone is one click away when you want the pure
> evidence gap."

* Open the score breakdown for the top entity, then the C1–C8 scorecard — Kaveri Refineries
  lights up Escalation and Incident Response.

## 1:05–1:25 — Finding Cards

Open the top card (Kaveri Refineries):

> "97% of critical alerts have no escalation record — and here is the reasoning, the measured
> value, the threshold, and the exact records behind it. Rule IM-008 is the declared-vs-evidence
> one: the entity declared 99% SLA compliance while its own closure records show 30% breaching
> the target."

Scroll to the **prioritised review sample list** — the point of the exercise:

> "This is the examiner's actual to-do list: not a random sample, the specific alerts and cases
> behind the highest-severity findings."

Record a verdict on the card's **Examiner verdict** panel (Confirmed), citing the reason.

## 1:25–1:40 — Examiner Review and the loop closing

Sidebar → **🧑⚖️ Examiner Review**.

> "Every finding can be adjudicated — confirmed, not material, expected, needs more data, false
> positive — with a rationale, appended and never overwritten. False positive and Expected stop
> the finding counting towards the score, while keeping it visible for audit."

Point at *Effect of adjudication* and the **detector agreement** table:

> "And here is the number that matters for the pilot: how often each rule's findings survive
> examiner scrutiny. That is how thresholds get tuned — on evidence, not opinion."

## 1:40–1:52 — Entity Profile: declarations under one roof

Sidebar → **🏢 Entity Profile**.

> "One entity, everything we hold: profile and SOC arrangements, the declared-vs-measured
> reconciliation for every KPI whether or not a finding fired, which submission field groups
> actually arrived, the named feature set with the raw metric keys, and the audit trail."

## 1:52–2:00 — Validation and the honest ending

Sidebar → **🧪 Validation & Methods**.

> "30 of 30 injected weaknesses detected, zero findings on the six clean control entities. Those
> are synthetic-ground-truth numbers: they prove the detectors fire on the patterns they claim
> to, not that they agree with expert review. Only the shadow-run pilot — SAT-SA alongside a real
> review cycle, adjudicated in the app — can measure that. Fully offline throughout: no network
> call anywhere in the codebase."

Cut.

---

## Likely questions and short answers

**"Is this a SIEM or a SOC?"**
No. It ingests periodic batch submissions, holds no raw logs and collects no telemetry. It is a
supervisory analytics capability layered on evidence a CSE already produces.

**"How does it work with no internet or hosted AI?"**
Local Python only: pandas feature engineering, scikit-learn (TF-IDF, Isolation Forest) and
scipy. Nothing is trained from data; every detector is a named, deterministic function of named
features, so results are reproducible and explainable.

**"What if an entity submits bad or incomplete data?"**
Missing evidence is treated as data, not as an error: an absent field group is reported on the
Entity Profile page, and negative-space detectors are gated on a per-table data-quality score so
"no evidence" is never confused with "no reporting".

**"How is it validated?"**
Two levels. Measured recall/precision against known injected weaknesses (30/30, zero control
false positives), and — for real evidence — examiner adjudication: the per-detector agreement
table, produced by running the tool alongside a real review cycle.

**"What is not finished?"**
Cycle-over-cycle snapshot diffing, and threshold calibration on real data. Both are named in the
README and on the Validation page rather than glossed over.
