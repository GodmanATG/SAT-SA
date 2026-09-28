# SAT-SA — Two-Minute Demo Video Script

**Total duration: 2:00 | Target: Judges evaluating asynchronously (no team present)**

---

## Pre-Recording Checklist

Before you hit record, do ALL of this:

1. **Use the GitHub-hosted version on Streamlit Cloud** (`sat-sa-demo.streamlit.app`)
   - Open the site in an **Incognito/Private window** so you get a fresh, blank session
   - This proves the app is not hardcoded — judges see it start empty
2. **Screen resolution:** Set browser to 100% zoom, full-screen (F11), 1920×1080 if possible
3. **Recording tool:** OBS Studio (free) or Windows Game Bar (Win+G). Record at 1080p 30fps
4. **Audio:** Use a quiet room. Speak slowly and clearly. If your accent is strong, consider adding text captions in a video editor afterwards
5. **Browser:** Chrome or Edge. Close all other tabs. Hide bookmarks bar (Ctrl+Shift+B)
6. **Your 24-second intro** will play first. The script below covers 0:24 → 2:00

---

## THE SCRIPT

---

### SEGMENT 1: The Blank Start + Data Generation [0:24 → 0:50] — 26 seconds

**What's on screen:** The app opens to "Register & Submissions". The register is completely empty. The main area shows the empty-state welcome message.

**Action:** Point mouse at the empty register area. Then click the **"Generate & Maintenance"** sub-tab. Click the **"Generate, Ingest, and Analyse"** button.

**Voiceover (speak slowly — you have 26 seconds):**

> *"SAT-SA starts completely blank. No data is pre-loaded, no results are pre-computed. This proves the system is not hardcoded.*
>
> *Here, I am generating a synthetic portfolio of 47 Critical Sector Entities across all CII sectors — over 170,000 security alerts, 57,000 case records, and 83,000 investigation workflow events.*
>
> *The entire pipeline — ingestion, feature engineering, and all 30 detection rules — runs fully offline with zero cloud dependency."*

**While the progress bar runs:** Keep talking. The generation takes ~30 seconds on the cloud. If it finishes before you finish speaking, that's fine — it makes the tool look fast.

---

### SEGMENT 2: Risk Ranking — Who to Audit First [0:50 → 1:10] — 20 seconds

**Action:** Click **"Supervisory Overview"** in the sidebar. The risk ranking table and charts load. Point your mouse at the top-ranked entity (the red/high-risk one). Then point at the pie chart showing risk distribution.

**Voiceover:**

> *"The Supervisory Overview immediately answers the core question: which entities need attention first.*
>
> *Every entity receives a composite risk score — 60% from measured operational metrics, 40% from an 8-capability scorecard mapped to NCIIPC's assessment framework. Entities are ranked and colour-coded by risk tier.*
>
> *Critically, the same evidence gap is weighted by the entity's criticality tier — a Tier 1 national infrastructure operator with the same gap ranks higher than a Tier 3."*

---

### SEGMENT 3: Finding Cards — Execution Gaps & Negative Space [1:10 → 1:35] — 25 seconds

**Action:** Click **"Finding Cards"** in the sidebar. Scroll to a finding card that shows an **Execution Gap** (e.g., EG-003: Template-driven investigation notes, or EG-001: Critical alerts closed without escalation). Point at the rationale text, the measured value vs. threshold, and the evidence record IDs. Then scroll to a **Negative Space** finding (e.g., NS-001 or NS-003).

**Voiceover:**

> *"The Finding Cards page is where the core supervisory problems are surfaced.*
>
> *This is an Execution Gap — rule EG-003 has detected that 80% of this entity's investigation notes are near-identical copy-paste templates, detected using TF-IDF cosine similarity. The entity appears compliant on paper, but the operational evidence shows superficial investigation.*
>
> *Below it, a Negative Space finding flags a critical system generating zero security telemetry — the expected evidence is simply absent.*
>
> *Every finding shows the exact measured value, the threshold that triggered it, and the specific alert and case record IDs the examiner should request."*

---

### SEGMENT 4: Entity Profile + Examiner Adjudication [1:35 → 1:50] — 15 seconds

**Action:** Click **"Entity Profile"** in the sidebar. Select any high-risk entity from the dropdown. Quickly scroll past the profile summary to the **Findings table** at the bottom. Point at the "Examiner verdict" column.

**Voiceover:**

> *"The Entity Profile gives the examiner everything under one roof — what the entity declared versus what the engine measured from their own records.*
>
> *The examiner can adjudicate any finding — confirming it, marking it as a false positive, or flagging it for further review. Verdicts are immutable, persist across pipeline reruns, and directly update the entity's risk score. This is the human-in-the-loop."*

---

### SEGMENT 5: Validation + PDF Export — The Honest Ending [1:50 → 2:00] — 10 seconds

**Action:** Click **"Validation & Methods"**. Point at the **33/33 recall, 100% precision, 0 control false positives** metrics at the top. Then quickly click **"Report Export"** and point at the PDF download button.

**Voiceover:**

> *"Against synthetic ground truth, all 33 injected weaknesses were detected with zero false positives on control entities. And examiners can export fully offline PDF audit reports with one click.*
>
> *Fully air-gapped. No cloud. No hosted AI. Thank you."*

**Cut. End of video.**

---

## RECORDING TIPS

1. **Practice the voiceover 3 times** before recording. Time yourself — each segment has a strict duration
2. **Move your mouse slowly and deliberately.** Judges are watching a recording, not a live demo. Hover over important numbers for 2-3 seconds so they can read them
3. **If the app takes time to load** between page clicks, keep talking. Dead silence while a spinner loads looks bad
4. **If you make a mistake**, don't restart the whole thing. Just re-record that one segment and splice it in a video editor (CapCut is free)
5. **Do NOT show the terminal or any code.** The video is for evaluators, not developers. They want to see the tool working, not how it was built
6. **End with confidence.** Don't trail off. Say "Thank you" firmly and cut

---

## WHAT JUDGES ARE LOOKING FOR (from the PS)

Map this to what you're showing:

| PS Evaluation Criterion | Where you show it in the video |
|---|---|
| Ability to Support Supervisory Assessment | Segment 2: Risk ranking + prioritisation |
| Detection of Execution Gaps | Segment 3: EG-003 template notes finding |
| Detection of Negative Space | Segment 3: NS finding (missing telemetry) |
| Explainability and Auditability | Segment 3: Rationale + evidence IDs. Segment 4: Examiner verdicts |
| Scalability and Performance | Segment 1: 170K alerts processed. Segment 5: Validation metrics |
| Innovation and Additional Supervisory Insights | Segment 3: IM-008 declared-vs-measured KPI contradiction (mention briefly) |
