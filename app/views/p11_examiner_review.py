"""
Page 11: Examiner Review (the adjudication loop, both halves)
============================================================
The tool prioritises; the examiner decides. This page closes the loop in both
directions:

**Part 1 — capture the judgement.** For every finding the examiner records one of
the fixed verdicts (Confirmed / Not material / Expected / Needs more data / False
positive) with a free-text rationale. Verdicts are *appended*, never overwritten, so
each finding keeps its full decision history and the reasoning behind a change of
view survives.

**Part 2 — let the judgement change the answer.** A finding adjudicated *False
positive* or *Expected* stops contributing to the entity's capability scores and
therefore to its risk score, while remaining visible for audit. The next analytics
run applies this automatically (findings are keyed deterministically so the verdict
re-attaches to the regenerated finding). The panel at the bottom quantifies the
effect, and the agreement statistics per detector are the evidence base for
threshold tuning — which is exactly what a shadow-run pilot needs.
"""

import hashlib
import json
import math
import pathlib
import sys

import pandas as pd
import streamlit as st

APP_DIR = pathlib.Path(__file__).parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config import (  # noqa: E402
    ADJUDICATION_VERDICTS, CAPABILITY_NAMES, SEVERITY_ICON, is_non_counting,
)
from database import get_db, latest_adjudications, log_action, record_adjudication  # noqa: E402
from views.components import (  # noqa: E402
    full_evidence_export, recalculate_button, verdict_display,
)


def _load_findings(entity_filter=None):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT f.*, e.entity_name, e.tier, e.sector
            FROM findings f JOIN entities e ON f.entity_id = e.entity_id
            ORDER BY f.severity_score DESC, e.entity_name
        """).fetchall()
        adjudications = latest_adjudications(conn)
        metrics = {r["entity_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM entity_metrics")}
    findings = [dict(r) for r in rows]
    for finding in findings:
        record = adjudications.get(finding["finding_id"])
        finding["verdict"] = record["verdict"] if record else ""
        finding["examiner_rationale"] = record["rationale"] if record else ""
        finding["decided_at"] = record["decided_at"] if record else ""
        finding["history"] = record.get("history", []) if record else []
    if entity_filter:
        findings = [f for f in findings if f["entity_id"] in entity_filter]
    return findings, adjudications, metrics


def render(entity_ids=None):
    st.title("🧑‍⚖️ Examiner Review")
    st.caption("Adjudicate each finding. Your verdict is stored with its rationale, survives "
               "re-analysis, and changes how the finding counts towards the entity's score.")

    findings, adjudications, metrics = _load_findings(entity_ids)
    if not findings:
        st.info("No findings to review. Register an entity, ingest a submission and run the "
                "analytics first.")
        return

    decided = [f for f in findings if f["verdict"]]
    confirmed = [f for f in decided if f["verdict"] == "Confirmed"]
    non_counting = [f for f in decided if is_non_counting(f["verdict"])]

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Findings in view", len(findings))
    k2.metric("Adjudicated", len(decided),
              delta=f"{len(findings) - len(decided)} outstanding" if len(decided) < len(findings)
              else "queue clear")
    k3.metric("Confirmed", len(confirmed))
    k4.metric("Suppressed from scoring", len(non_counting),
              help="Adjudicated 'False positive' or 'Expected'. These stay visible for audit but "
                   "stop inflating the entity's score at the next analytics run.")
    agreement = round(len(confirmed) / len(decided) * 100, 1) if decided else 0.0
    k5.metric("Agreement with detectors", f"{agreement}%",
              help="Share of adjudicated findings the examiner confirmed. This is the number to take "
                   "into the shadow-run pilot, and the number threshold tuning should move.")

    # The recalculate control sits here, above the queue, rather than inside the review form:
    # a supervisor who adjudicates five findings in a row, or who comes back to the page the
    # next morning, could otherwise not apply their decisions without first re-selecting a row.
    recalculate_button(
        "examiner_page",
        label="🔄 Recalculate scores now",
        caption="A verdict of *False positive* or *Expected* stops a finding counting towards the "
                "entity's capability scores and risk score, but only once the analytics re-run. "
                "This also refreshes every other entity and records a new cycle snapshot.",
        columns=(1, 2))

    st.markdown("---")

    # ── Part 1: the queue ────────────────────────────────────────────────
    st.subheader("1 · Review queue")
    # One control decides both *which* findings are in the queue and *how* they are
    # ordered. These used to be two - a Queue radio and a Show dropdown - with
    # overlapping options, so choosing "Adjudicated only" in one and "Outstanding only"
    # in the other produced an empty list with no explanation of why.
    f1, f2, f3, f4 = st.columns(4)
    queue_mode = f1.radio(
        "Queue", ["Outstanding first", "Worst severity first", "Outstanding only",
                  "Adjudicated only"],
        horizontal=False, key="adj_queue_mode",
        help="Controls both the contents and the ordering of the queue below.")
    tier_options = sorted({f["tier"] for f in findings if f.get("tier")})
    tier_pick = f2.multiselect("Criticality tier", tier_options, default=tier_options,
                              key="adj_tier")
    rule_options = sorted({f["rule_id"] for f in findings if f.get("rule_id")})
    rule_pick = f3.multiselect("Rule", rule_options, default=rule_options, key="adj_rule")
    sev_pick = f4.multiselect("Severity", ["High", "Medium", "Low"],
                              default=["High", "Medium", "Low"], key="adj_sev")

    s1, s2 = st.columns([3, 1])
    search = s1.text_input("Search entity, rule or finding text", key="adj_search",
                           placeholder="e.g. escalation, Kaveri, IM-003").strip().lower()
    page_size = int(s2.selectbox("Rows per page", [25, 50, 100, 250], index=0,
                                 key="adj_page_size"))

    def _matches(f):
        if f["tier"] not in tier_pick or f["rule_id"] not in rule_pick:
            return False
        if f["severity"] not in sev_pick:
            return False
        if queue_mode == "Outstanding only" and f["verdict"]:
            return False
        if queue_mode == "Adjudicated only" and not f["verdict"]:
            return False
        if search:
            haystack = " ".join(str(f.get(k, "")) for k in
                                ("entity_name", "rule_id", "title", "description",
                                 "rationale", "tier", "detector_group")).lower()
            if search not in haystack:
                return False
        return True

    queue = [f for f in findings if _matches(f)]
    # Outstanding first is the default: it puts the work still to be done at the top and
    # then ranks it by severity, so the examiner does not have to re-sort after each verdict.
    if queue_mode == "Worst severity first":
        queue.sort(key=lambda f: -f["severity_score"])
    else:
        queue.sort(key=lambda f: (bool(f["verdict"]), -f["severity_score"]))

    if not queue:
        st.info("Nothing matches the filters. Widen the tier, rule or severity selection, clear "
                "the search box, or set **Queue** back to *Outstanding first*.")
        return

    # ── paginated, searchable queue ──────────────────────────────────────
    # A paginated, searchable review queue table: an examiner working a portfolio
    # of hundreds or thousands of findings can search it, page through it, and see
    # the ranking around the item they are reviewing without any 60-finding cap.
    total_pages = max(1, math.ceil(len(queue) / page_size))
    page = int(st.session_state.get("adj_page", 0))
    if page >= total_pages:
        page = total_pages - 1
    if page < 0:
        page = 0
    st.session_state["adj_page"] = page

    st.caption(f"**{len(queue)}** finding(s) in review queue · page {page + 1} of {total_pages} · "
               f"ranked by severity. Select a row to review and adjudicate it below.")

    page_slice = queue[page * page_size:(page + 1) * page_size]
    frame = pd.DataFrame([{
        "Severity": f"{SEVERITY_ICON.get(f['severity'], '')} {f['severity']}",
        "Rule": f["rule_id"],
        "Entity": f["entity_name"],
        "Criticality": f.get("tier", ""),
        "Finding": f["title"],
        "Type": str(f["weakness_type"]).replace("_", " "),
        "Score": f["severity_score"],
        "Evidence": f.get("evidence_count", 0),
        "Verdict": verdict_display(f["verdict"]),
    } for f in page_slice])
    # The widget key encodes the whole filter state: Streamlit keeps a table's selection in
    # session state, so without this a row selected under one set of filters stays selected
    # after the filters change - and the examiner would be adjudicating a row that is no
    # longer on screen.
    filter_signature = hashlib.md5(
        f"{page}|{page_size}|{queue_mode}|{search}|{len(queue)}".encode()).hexdigest()[:8]
    event = st.dataframe(
        frame, width="stretch", hide_index=True, on_select="rerun",
        selection_mode="single-row", key=f"adj_queue_table_{filter_signature}",
        column_config={"Score": st.column_config.NumberColumn("Score", format="%.2f")})

    selected_rows = list(getattr(getattr(event, "selection", None), "rows", []) or [])
    if selected_rows and selected_rows[0] < len(page_slice):
        st.session_state["adj_selected_id"] = page_slice[selected_rows[0]]["finding_id"]

    nav_first, nav_prev, nav_page, nav_next, nav_last = st.columns([1, 1, 2, 1, 1])
    if nav_first.button("⏮️ First", disabled=page == 0, key="adj_first"):
        st.session_state["adj_page"] = 0
        st.rerun()
    if nav_prev.button("◀️ Prev", disabled=page == 0, key="adj_prev"):
        st.session_state["adj_page"] = max(0, page - 1)
        st.rerun()
    new_page = nav_page.number_input(
        f"Page (of {total_pages})", min_value=1, max_value=total_pages,
        value=page + 1, key="adj_page_num"
    )
    if new_page - 1 != page:
        st.session_state["adj_page"] = int(new_page) - 1
        st.rerun()
    if nav_next.button("Next ▶️", disabled=page >= total_pages - 1, key="adj_next"):
        st.session_state["adj_page"] = min(total_pages - 1, page + 1)
        st.rerun()
    if nav_last.button("Last ⏭️", disabled=page >= total_pages - 1, key="adj_last"):
        st.session_state["adj_page"] = total_pages - 1
        st.rerun()

    # Synchronized item selector for the current page
    page_options = {
        f["finding_id"]: f"{SEVERITY_ICON.get(f['severity'], '')} {f['rule_id']} · {f['entity_name']} · {f['title']}"
        for f in page_slice
    }
    current_selected = st.session_state.get("adj_selected_id")
    current_idx = list(page_options.keys()).index(current_selected) if current_selected in page_options else 0

    chosen_id = st.selectbox(
        "Select finding to review from this page (or click row in table above):",
        options=list(page_options.keys()),
        format_func=lambda fid: page_options.get(fid, fid),
        index=current_idx if page_options else 0,
        key=f"adj_pick_{page}"
    )
    if chosen_id:
        st.session_state["adj_selected_id"] = chosen_id

    selected_id = st.session_state.get("adj_selected_id")
    if not selected_id:
        st.info("Select a finding from the table above to review it. Use the filters and the "
                "search box to narrow the queue — every finding is reachable across all pages.")
        return

    match = next((f for f in findings if f["finding_id"] == selected_id), None)
    if match is None:
        st.session_state.pop("adj_selected_id", None)
        st.rerun()
        return
    finding = match
    with get_db() as conn:
        entity_names = {r["entity_id"]: r["entity_name"] for r in
                        conn.execute("SELECT entity_id, entity_name FROM entities")}
    st.markdown("---")
    st.markdown(f"#### Reviewing · {SEVERITY_ICON.get(finding['severity'], '')} "
                f"`{finding['rule_id']}` · "
                f"{entity_names.get(finding['entity_id'], finding['entity_id'])}")

    meta1, meta2, meta3, meta4 = st.columns(4)
    meta1.markdown(f"**Entity**  \n{finding['entity_name']}  \n`{finding['tier']}`")
    meta2.markdown(f"**Rule**  \n`{finding['rule_id']}`  \n{finding.get('detector_group', '')}")
    meta3.markdown(f"**Type**  \n{finding['weakness_type'].replace('_', ' ')}  \n"
                   f"{SEVERITY_ICON.get(finding['severity'], '')} {finding['severity']} "
                   f"(score {finding['severity_score']})")
    caps = finding.get("capability_tags") or "[]"
    try:
        cap_list = json.loads(caps) if isinstance(caps, str) else caps
    except (TypeError, ValueError, json.JSONDecodeError):
        cap_list = []
    meta4.markdown("**Capabilities**  \n" +
                   (", ".join(CAPABILITY_NAMES.get(c, c) for c in cap_list) or "—"))

    st.markdown(f"#### {finding['title']}")
    st.markdown(f"**What was measured.** {finding['description']}")
    st.info(f"**Why it was flagged.** {finding['rationale']}")
    if finding.get("evidence_count"):
        st.caption(f"{finding['evidence_count']:,} supporting record(s); sample stored: "
                   f"{(finding.get('evidence_ids') or '[]')[:400]}")
    with st.expander("📎 Full evidence export"):
        st.caption("Every record behind this finding, reconstructed at full population rather "
                   "than the capped sample stored with it.")
        full_evidence_export(finding, key=f"adj_{finding['finding_id']}")

    # Check for just completed adjudication / recalculation messages
    just_adj = st.session_state.pop("just_adjudicated", None)
    if just_adj:
        st.success(f"Recorded **{just_adj['verdict']}** for `{just_adj['rule_id']}`.")
        rc_c1, rc_c2 = st.columns([1, 2])
        if rc_c1.button("🔄 Recalculate Scores Now", key=f"instant_recalc_btn_{just_adj['finding_id']}", type="primary"):
            from views.components import run_detection_with_progress
            summary = run_detection_with_progress("Recalculating scores")
            st.success(f"✅ Recalculated — {summary['entities']} entities · {summary['findings']} findings.")
            st.rerun()
        rc_c2.caption("Instantly refresh entity risk scores and capability scorecards without leaving this review page.")

    just_recalc = st.session_state.pop("just_recalculated", None)
    if just_recalc:
        st.success(f"✅ Verdict saved and scores instantly recalculated! {just_recalc['entities']} entities · "
                   f"{just_recalc['findings']} findings · Duration: {just_recalc['duration_s']}s.")

    with st.form(key=f"adj_form_{finding['finding_id']}", clear_on_submit=False):
        verdict = st.radio("Examiner verdict", ADJUDICATION_VERDICTS,
                           index=ADJUDICATION_VERDICTS.index(finding["verdict"])
                           if finding["verdict"] in ADJUDICATION_VERDICTS else 0,
                           horizontal=True, key="adj_verdict")
        rationale = st.text_area(
            "Examiner rationale (required for any verdict other than Confirmed)",
            value=finding.get("examiner_rationale", ""), height=90,
            key="adj_rationale",
            placeholder="Explain the decision in the examiner's own words — what was checked, what "
                        "was found, and why the finding does or does not stand. This becomes part of "
                        "the audit record.")
        examiner = st.text_input("Examiner name / initials", value="supervisor", key="adj_examiner")
        
        btn_c1, btn_c2 = st.columns([1, 1])
        submitted = btn_c1.form_submit_button("💾 Save Verdict", type="secondary")
        submitted_recalc = btn_c2.form_submit_button("⚡ Save & Recalculate Now", type="primary")

    if submitted or submitted_recalc:
        if verdict != "Confirmed" and not rationale.strip():
            st.error("A rationale is required unless the finding is confirmed.")
        else:
            with get_db() as conn:
                adj_id = record_adjudication(conn, finding["finding_id"], finding["entity_id"],
                                             verdict, rationale.strip(), examiner.strip() or "supervisor")
                log_action(conn, "FINDING_ADJUDICATED", finding["entity_id"],
                           f"{finding['rule_id']} {finding['finding_id'][:8]} → {verdict}"
                           f" ({adj_id})")
            if submitted_recalc:
                from views.components import run_detection_with_progress
                summary = run_detection_with_progress("Recalculating scores after verdict override")
                st.session_state["just_recalculated"] = summary
            else:
                st.session_state["just_adjudicated"] = {
                    "rule_id": finding["rule_id"],
                    "verdict": verdict,
                    "finding_id": finding["finding_id"]
                }
            st.rerun()

    # Recalculate option right on the review section
    recalculate_button(
        f"inline_{finding['finding_id']}",
        label="🔄 Recalculate scores now",
        caption="Instantly apply verdicts — findings judged False positive or Expected will be excluded from scoring.",
        columns=(1, 2)
    )

    if finding["history"]:
        with st.expander(f"Decision history ({len(finding['history'])} earlier entry/entries)"):
            st.dataframe(pd.DataFrame(finding["history"]), width="stretch", hide_index=True)
        st.caption("Nothing is overwritten: earlier verdicts stay on the record so a change of view "
                   "can be audited.")

    # ── Part 2: what the judgement changed ───────────────────────────────
    st.markdown("---")
    st.subheader("2 · Effect of adjudication on the supervisory score")
    if not decided:
        st.info("No adjudications recorded yet. Once a finding is adjudicated *False positive* or "
                "*Expected*, it stops contributing to the entity's capability scores — re-run the "
                "analytics from the Register page to apply it.")
    else:
        rows = []
        for entity_id, group in pd.DataFrame(findings).groupby("entity_id"):
            decided_rows = group[group["verdict"] != ""]
            suppressed = decided_rows[decided_rows["verdict"].map(is_non_counting)]
            row = metrics.get(entity_id, {})
            sev_supp = 0.0
            if "severity_score" in suppressed.columns and not suppressed.empty:
                sev_supp = round(float(suppressed["severity_score"].fillna(0).sum()), 2)
            rows.append(dict(
                entity=group["entity_name"].iloc[0],
                tier=group["tier"].iloc[0],
                findings=len(group),
                adjudicated=len(decided_rows),
                suppressed=len(suppressed),
                severity_suppressed=sev_supp,
                risk_score=row.get("risk_score", ""),
                examiner_suppressed_metric=row.get("examiner_suppressed", 0),
            ))
        effect = pd.DataFrame(rows).sort_values("suppressed", ascending=False)
        st.dataframe(effect, width="stretch", hide_index=True)

        dec_df = pd.DataFrame(decided)
        total_sev = 0.0
        if "severity_score" in dec_df.columns and not dec_df.empty:
            total_sev = round(float(dec_df["severity_score"]
                                    .where(dec_df["verdict"].map(is_non_counting), 0)
                                    .fillna(0).sum()), 2)
        st.caption(
            f"Across the portfolio, **{len(non_counting)}** finding(s) carrying **{total_sev}** severity "
            f"points have been taken out of scoring by examiner judgement. The "
            f"'suppressed' column above shows how many the last analytics run already excluded; if it "
            f"lags the count here, press **Recalculate scores now** at the top of this page."
        )

        # ── per-detector agreement: the threshold-tuning evidence base ──
        st.markdown("#### Detector agreement (evidence for threshold tuning)")
        st.caption("How often each rule's findings survived examiner scrutiny. A rule with a high "
                   "false-positive share should have its threshold tightened; a rule that is always "
                   "confirmed is carrying real supervisory weight.")
        by_rule = []
        for rule_id, group in pd.DataFrame(findings).groupby("rule_id"):
            decided_group = group[group["verdict"] != ""]
            if decided_group.empty:
                by_rule.append(dict(rule_id=rule_id, adjudicated=0, confirmed=0,
                                    not_material=0, false_positive=0, agreement_pct=None))
                continue
            counts = decided_group["verdict"].value_counts().to_dict()
            confirmed_n = counts.get("Confirmed", 0)
            by_rule.append(dict(
                rule_id=rule_id,
                adjudicated=len(decided_group),
                confirmed=confirmed_n,
                not_material=counts.get("Not material", 0),
                expected=counts.get("Expected", 0),
                false_positive=counts.get("False positive", 0),
                needs_more_data=counts.get("Needs more data", 0),
                agreement_pct=round(confirmed_n / len(decided_group) * 100, 1),
            ))
        st.dataframe(pd.DataFrame(by_rule).sort_values("adjudicated", ascending=False),
                     width="stretch", hide_index=True)

        export = pd.DataFrame([{
            "entity": f["entity_name"], "rule_id": f["rule_id"], "title": f["title"],
            "severity": f["severity"], "severity_score": f["severity_score"],
            "verdict": f["verdict"], "examiner_rationale": f["examiner_rationale"],
            "decided_at": f["decided_at"],
        } for f in findings if f["verdict"]])
        st.download_button("⬇️ Export adjudication log (CSV)",
                           export.to_csv(index=False).encode("utf-8"),
                           file_name="sat-sa-adjudications.csv", mime="text/csv")
        st.caption("This log is the measured evidence that the tool's prioritisation agrees with "
                   "expert judgement — the comparison the problem statement's validation requirement "
                   "asks for.")
