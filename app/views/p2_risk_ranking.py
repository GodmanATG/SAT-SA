"""
Page 2: Risk Ranking — the supervisory prioritisation list
=========================================================
Requirement 10 of the problem statement asks for three prioritised lists: entities,
controls/processes (capabilities) and alert samples.  This page owns the first two.

Every number shown here is explainable: the score breakdown panel decomposes the
composite risk score into its weighted components, and the capability scorecard shows
which of the eight supervisory capabilities is carrying the risk.
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from config import CAPABILITY_COLUMNS, CAPABILITY_NAMES, RISK_SCORE_BLEND, TIER_COLORS
from detection.scoring import metric_gaps, risk_contributions
from detection.snapshots import list_cycles, portfolio_changes
from database import get_db
from views.components import (capability_radar, capability_source_note, weakness_bar)

# rates are stored as fractions (0-1); percentages are produced for display only
PERCENT_COLUMNS = {
    "fast_closure_rate": "Fast closure %",
    "crit_no_escalation_rate": "Critical w/o escalation %",
    "template_note_rate": "Template notes %",
    "repeat_alert_rate": "Repeat alert traffic %",
    "missing_case_rate": "Critical w/o case record %",
    "root_cause_rate": "Root cause documented %",
    "expected_category_coverage": "Sector category coverage %",
    "escalation_rate_critical": "Critical escalation rate %",
    "avg_investigation_depth": "Investigation depth (0-1)",
    "activity_deviation": "Activity below peers %",
    "night_coverage_gap_pct": "Night coverage gap %",
    "silent_critical_assets": "Silent critical assets",
}


def _display_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Copy with fraction columns converted to percentages for readability."""
    out = df.copy()
    for col in PERCENT_COLUMNS:
        if col in out.columns and col not in ("avg_investigation_depth",
                                              "silent_critical_assets",
                                              "night_coverage_gap_pct",
                                              "activity_deviation"):
            out[col] = pd.to_numeric(out[col], errors="coerce") * 100
    if "activity_deviation" in out.columns:
        out["activity_deviation"] = pd.to_numeric(out["activity_deviation"], errors="coerce") * 100
    return out


def _cycle_movement_panel():
    """Portfolio-wide "what changed since the last cycle" table.

    A single-run tool can only say which entity looks worst today; the supervisory
    question is which entity is *moving*. This panel answers it from the retained
    snapshots, and it is empty-by-design until a second run has been recorded.
    """
    with get_db() as conn:
        cycles = list_cycles(conn)
        if len(cycles) < 2:
            if cycles:
                st.info(f"**{cycles[0]['cycle_label']}** is currently the only recorded analytics "
                        f"run, so there is no cycle-over-cycle movement to show yet. Re-run the "
                        f"analytics after the next submission cycle — or generate and ingest "
                        f"cycle 2 from the Generate tab — and this panel fills in.")
            return
        changes = portfolio_changes(conn)

    if changes.empty:
        return
    moved = changes[(changes["worse"] > 0) | (changes["better"] > 0)]
    st.subheader("Movement since the previous cycle")
    c1, c2, c3 = st.columns(3)
    c1.metric("Entities deteriorating", int((changes["worse"] > 0).sum()),
              help="Entities with at least one measure that moved the wrong way since the "
                   "previous recorded run.")
    c2.metric("Entities improving", int((changes["better"] > 0).sum()))
    c3.metric("Comparison", f"{cycles[1]['cycle_label']} → {cycles[0]['cycle_label']}")
    st.dataframe(
        moved.rename(columns={"entity": "Entity", "latest_cycle": "Latest",
                              "previous_cycle": "Previous", "risk_now": "Risk now",
                              "risk_before": "Risk before", "risk_delta": "Δ risk",
                              "tier": "Band", "worse": "Measures worse",
                              "better": "Measures better",
                              "biggest_movement": "Largest deterioration"})[[
            "Entity", "Risk before", "Risk now", "Δ risk", "Band", "Measures worse",
            "Measures better", "Largest deterioration"]],
        width="stretch", hide_index=True)
    st.caption("Every measure is compared against its own noise floor, so a rounding artefact is "
               "never reported as a deterioration. Open **Entity Profile → Changes since the last "
               "cycle** for the measure-by-measure detail on one entity.")
    st.markdown("---")


def render(filtered_metrics: pd.DataFrame):
    st.info("💡 **How to read this dashboard:** This chart ranks all entities by risk. A higher score (Red) indicates severe operational gaps and a critical need for an audit. The pie chart shows the overall health of the portfolio.")

    st.subheader("Entity Risk Ranking")
    st.caption(
        "Entities ordered by the composite supervisory risk score. The score is a weighted "
        "**prioritisation index** (0 = no gap indicators observed, 100 = every indicator at its "
        "worst), not a probability of compromise — it answers \"who should I look at first?\"."
    )

    if filtered_metrics.empty:
        st.info("No entities match current filters.")
        return

    col1, col2 = st.columns([2, 1])
    with col1:
        fig = px.bar(
            filtered_metrics.sort_values("risk_score"),
            x="risk_score", y="entity_name", orientation="h",
            color="risk_tier", color_discrete_map=TIER_COLORS,
            labels={"risk_score": "Supervisory Risk Score", "entity_name": "", "risk_tier": "Tier"},
            height=max(360, 32 * len(filtered_metrics)),
        )
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), legend_title_text="")
        st.plotly_chart(fig, width="stretch")

    with col2:
        tier_options = ["Critical Attention", "Elevated", "Watch", "Satisfactory"]
        tier_counts = filtered_metrics["risk_tier"].value_counts().reindex(tier_options).fillna(0)
        fig2 = px.pie(names=tier_counts.index, values=tier_counts.values,
                      color=tier_counts.index, color_discrete_map=TIER_COLORS, hole=0.55)
        fig2.update_layout(margin=dict(l=0, r=0, t=10, b=0), showlegend=True)
        st.plotly_chart(fig2, width="stretch")
        st.caption("Prioritisation bands: Critical Attention ≥32, Elevated ≥24, Watch ≥14.")

    st.markdown("---")
    st.subheader("Prioritised entities")

    order = st.radio(
        "Review order", ["Supervisory priority (risk x criticality)", "Risk score alone",
                         "Criticality tier, then risk"],
        horizontal=True, key="rank_order",
        help="Criticality is kept separate from the risk score on purpose: the score measures how "
             "much operational evidence is missing, while criticality measures how much it matters "
             "if it is. Two entities with the same gaps are not equally urgent.")
    ranked = filtered_metrics.copy()
    if order.startswith("Supervisory priority"):
        ranked = ranked.sort_values(["supervisory_priority", "risk_score"], ascending=False)
    elif order.startswith("Criticality"):
        ranked = ranked.sort_values(["criticality_tier", "risk_score"],
                                    ascending=[True, False])
    else:
        ranked = ranked.sort_values("risk_score", ascending=False)
    ranked.insert(0, "review_order", range(1, len(ranked) + 1))

    show = ["review_order", "entity_name", "criticality_tier", "sector", "risk_score",
            "supervisory_priority", "risk_tier", "metric_index", "capability_average",
            "total_alerts", "fast_closure_rate", "crit_no_escalation_rate",
            "template_note_rate", "repeat_alert_rate", "missing_case_rate",
            "sla_breach_rate", "severity_softening_rate", "rework_loop_rate",
            "investigation_gap_rate",
            "night_coverage_gap_pct", "silent_critical_assets", "unmonitored_critical_assets",
            "declared_kpi_contradictions"]
    available = [c for c in show if c in ranked.columns]
    display = _display_frame(ranked[available]).rename(columns={
        "review_order": "Order", "entity_name": "Entity",
        "criticality_tier": "Criticality", "sector": "Sector",
        "risk_score": "Risk score", "supervisory_priority": "Priority",
        "risk_tier": "Risk tier", "total_alerts": "Alerts",
        "metric_index": "Metric index", "capability_average": "Capability avg",
        "unmonitored_critical_assets": "Unmonitored critical assets",
        "declared_kpi_contradictions": "Declared KPI conflicts",
        **PERCENT_COLUMNS,
    })
    st.dataframe(
        display, width="stretch", hide_index=True,
        column_config={
            "Risk score": st.column_config.ProgressColumn("Risk score", min_value=0, max_value=100,
                                                          format="%.1f"),
            "Metric index": st.column_config.NumberColumn(
                "Metric index", help="Weighted average of operational metric gaps", format="%.1f"),
            "Capability avg": st.column_config.NumberColumn(
                "Capability avg", help="Average of the 8 capability scores", format="%.1f"),
            "Priority": st.column_config.NumberColumn(
                "Priority",
                help="Risk score weighted by the entity's criticality tier (Tier 1 x1.00, Tier 2 "
                     "x0.85, Tier 3 x0.70, Tier 4 x0.55). Use this column to decide review order; "
                     "use the risk score to see the evidence gap on its own.",
                format="%.1f"),
        },
    )
    st.caption(f"Risk score = {RISK_SCORE_BLEND['metric_index']:.0%} operational metric index + "
               f"{RISK_SCORE_BLEND['capability_average']:.0%} capability scorecard average. "
               "Both halves can be re-weighted in Settings. The Priority column multiplies that "
               "score by the entity's criticality tier, so a Tier 1 operator with the same evidence "
               "gap is reviewed before a Tier 4 one.")

    st.download_button(
        "⬇️ Export prioritised entity list (CSV)",
        display.to_csv(index=False).encode("utf-8"),
        "sat_sa_entity_prioritisation.csv", "text/csv",
    )

    # ── Why did this entity score what it scored? ────────────────────────
    st.markdown("---")
    st.subheader("Why this score — component breakdown")
    st.caption("Every component of the score, in the order it contributes. No hidden weights.")

    entity = st.selectbox("Entity", sorted(filtered_metrics["entity_name"].tolist()),
                          key="score_entity")
    row = filtered_metrics[filtered_metrics["entity_name"] == entity].iloc[0]

    c1, c2 = st.columns([3, 2])
    with c1:
        contrib = risk_contributions(row.to_dict())
        contrib["component"] = contrib["component"].str.replace("_", " ")
        fig3 = px.bar(contrib, x="score_contribution", y="component", orientation="h",
                      labels={"score_contribution": "Points contributed", "component": ""},
                      height=360)
        fig3.update_layout(margin=dict(l=0, r=0, t=10, b=0), yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig3, width="stretch")
    with c2:
        gaps = metric_gaps(row.to_dict())
        st.markdown(f"**Composite risk score: {row['risk_score']:.1f}** "
                    f"({row['risk_tier']})")
        st.markdown(f"- Operational metric index: **{row.get('metric_index', 0):.1f}**")
        st.markdown(f"- Capability scorecard average: **{row.get('capability_average', 0):.1f}**")
        worst = sorted(gaps.items(), key=lambda kv: kv[1], reverse=True)[:3]
        st.markdown("**Largest normalised gaps** (1.0 = fully saturated):")
        for name, value in worst:
            st.markdown(f"- {name.replace('_', ' ')}: `{value:.2f}`")
        st.caption("Each component is a named metric divided by its worst case, so two supervisors "
                   "reading the same entity see the same arithmetic.")

    # ── Capability scorecard ─────────────────────────────────────────────
    _cycle_movement_panel()

    st.markdown("---")
    st.subheader("Capability Scorecard (C1–C8)")
    st.caption("Which supervisory capability is carrying the risk. The table and bar chart below "
               "are expressed as **weakness** (0 = strong, 100 = weakest); the radar is inverted to "
               "**capability strength**, so on that one chart a larger shape is a better entity. "
               "Driven by the severity of findings tagged to each capability.")

    cap_columns = list(CAPABILITY_COLUMNS.values())
    available_caps = [c for c in cap_columns if c in filtered_metrics.columns]
    if available_caps:
        # Rendered in C1..C8 order, so the table can never silently reorder itself if the
        # capability-to-column mapping changes.
        cap_df = filtered_metrics[["entity_name"] + available_caps].rename(columns={
            "entity_name": "Entity",
            **{col: CAPABILITY_NAMES[cap] for cap, col in CAPABILITY_COLUMNS.items()},
        })
        st.dataframe(cap_df, width="stretch", hide_index=True)

        with st.expander("Capability profile for one entity", expanded=False):
            radar_entity = st.selectbox("Entity", sorted(filtered_metrics["entity_name"].tolist()),
                                        key="radar_entity")
            rrow = filtered_metrics[filtered_metrics["entity_name"] == radar_entity].iloc[0]
            compare_options = ["None"] + [n for n in sorted(filtered_metrics["entity_name"].tolist())
                                          if n != radar_entity]
            compare_name = st.selectbox("Overlay a second entity (optional)", compare_options,
                                        key="radar_compare")
            compare_row = None
            if compare_name != "None":
                compare_row = filtered_metrics[
                    filtered_metrics["entity_name"] == compare_name].iloc[0]
            view2, view3 = st.tabs(["Radar (capability strength)", "Bars (weakness score)"])
            with view2:
                capability_radar(rrow, name=radar_entity, key="rank_radar",
                                 compare_row=compare_row,
                                 compare_name=compare_name if compare_row is not None else None)
                st.caption("Every axis is oriented so that **larger is better**, which is the "
                           "opposite of the stored scorecard value. The overlay is only shown "
                           "with the same inversion applied, so the two shapes are comparable.")
            with view3:
                weakness_bar(rrow, key="rank_bars")
            capability_source_note()

        # computed on the original column names (cap_df is renamed for display)
        cap_means = filtered_metrics[available_caps].apply(pd.to_numeric, errors="coerce").mean()
        if cap_means.notna().any():
            # cap_means is indexed by the stored column name, so map it straight back to
            # its C-code through the shared mapping rather than a positional list.
            worst_col = cap_means.idxmax()
            worst_name = CAPABILITY_NAMES[next(
                code for code, col in CAPABILITY_COLUMNS.items() if col == worst_col)]
            st.caption(f"Portfolio-wide, the weakest capability across the entities in view is "
                       f"**{worst_name}** (mean score {cap_means.max():.1f}/100).")
