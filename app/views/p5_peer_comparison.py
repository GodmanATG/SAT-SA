"""
Page 5: Peer Comparison — Radar chart + sector benchmarking
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px


def render(filtered_metrics: pd.DataFrame):
    st.subheader("Peer Comparison")
    st.caption("Compare entities within the same sector across key operational metrics. "
               "Radar charts show how each entity measures up against its peers.")

    if filtered_metrics.empty:
        st.info("No entities match current filters.")
        return

    # ── Sector selection ─────────────────────────────────────────────────
    sectors = sorted(filtered_metrics["sector"].unique().tolist())
    sel_sector = st.selectbox("Select sector for peer comparison", sectors)

    sector_df = filtered_metrics[filtered_metrics["sector"] == sel_sector].copy()

    if len(sector_df) < 2:
        st.warning(f"Only {len(sector_df)} entity in {sel_sector}. Need at least 2 for comparison.")
        return

    st.caption(f"Comparing {len(sector_df)} entities in **{sel_sector}**")

    # ── Select entities to compare ───────────────────────────────────────
    entity_names = sorted(sector_df["entity_name"].tolist())
    selected = st.multiselect(
        "Select entities to compare",
        entity_names,
        default=entity_names[:min(4, len(entity_names))],
    )

    if len(selected) < 2:
        st.info("Select at least 2 entities to compare.")
        return

    compare_df = sector_df[sector_df["entity_name"].isin(selected)]

    # ── Radar chart ──────────────────────────────────────────────────────
    # Everything is oriented "higher = worse" on this chart, including the two
    # coverage-type measures which are inverted for it, and the axis is labelled with
    # that direction so a reader cannot mistake a large shape for a good entity.
    st.caption("Operational weakness profile by peer cohort — **every axis is oriented so that "
               "larger is worse**, including the coverage measure (inverted for the chart). "
               "For the capability scorecard, where the direction is deliberately flipped, see "
               "Risk Ranking.")
    radar_metrics = {
        "fast_closure_rate": "Fast Closure Rate",
        "crit_no_escalation_rate": "No-Escalation Rate",
        "template_note_rate": "Template Note Rate",
        "night_coverage_gap_pct": "Night Gap %",
        "repeat_alert_rate": "Repeat Alert Rate",
        "expected_category_coverage": "Category Coverage (inverted)",
        "sla_breach_rate": "SLA Breach %",
        "rework_loop_rate": "Investigation Rework %",
        "investigation_gap_rate": "Investigation Trace Gap %",
    }

    available_metrics = {k: v for k, v in radar_metrics.items() if k in compare_df.columns}

    if not available_metrics:
        st.warning("Insufficient metrics for radar comparison.")
        return

    fig = go.Figure()

    for _, row in compare_df.iterrows():
        values = []
        fraction_metrics = {
            "fast_closure_rate", "crit_no_escalation_rate", "template_note_rate",
            "expected_category_coverage", "escalation_rate_critical", "repeat_alert_rate",
            "shallow_investigation_rate", "weekend_activity_ratio",
            "sla_breach_rate", "rework_loop_rate", "investigation_gap_rate"
        }
        for metric_col in available_metrics.keys():
            val = row.get(metric_col, 0)
            # Invert coverage so that higher = worse (consistent direction)
            if metric_col == "expected_category_coverage":
                val = (1 - val) * 100 if val else 0
            elif metric_col in fraction_metrics:
                val = val * 100 if val else 0
            else:
                val = val or 0
            values.append(round(val, 1))

        fig.add_trace(go.Scatterpolar(
            r=values + [values[0]],  # close the polygon
            theta=list(available_metrics.values()) + [list(available_metrics.values())[0]],
            fill="toself",
            name=row["entity_name"],
            opacity=0.6,
        ))

    fig.update_layout(
        polar=dict(
            radialaxis=dict(visible=True, range=[0, 100],
                            title="Weakness (higher = worse)"),
        ),
        showlegend=True,
        height=500,
        margin=dict(l=80, r=80, t=40, b=40),
    )
    st.plotly_chart(fig, width="stretch")

    st.caption("💡 Higher values = worse performance (further from center = more concern). "
               "Category Coverage is inverted so gaps show outward.")

    # ── Comparison table ─────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Metric Comparison Table")

    table_cols = ["entity_name", "risk_score", "risk_tier"] + list(available_metrics.keys())
    available_table = [c for c in table_cols if c in compare_df.columns]

    display = compare_df[available_table].rename(columns={
        "entity_name": "Entity",
        "risk_score": "Risk Score",
        "risk_tier": "Tier",
        **available_metrics,
    })

    st.dataframe(display, width="stretch", hide_index=True)

    # ── Grouped bar chart ────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Side-by-Side Metric Comparison")

    # Melt for grouped bar
    melt_cols = list(available_metrics.keys())
    melt_df = compare_df[["entity_name"] + melt_cols].melt(
        id_vars="entity_name", var_name="metric_col", value_name="value"
    )
    
    fraction_metrics = {
        "fast_closure_rate", "crit_no_escalation_rate", "template_note_rate",
        "expected_category_coverage", "escalation_rate_critical", "repeat_alert_rate",
        "shallow_investigation_rate", "weekend_activity_ratio",
        "sla_breach_rate", "rework_loop_rate", "investigation_gap_rate"
    }

    def scale_val(row):
        v = row["value"] or 0
        if row["metric_col"] in fraction_metrics:
            v *= 100
        return round(v, 1)

    melt_df["value"] = melt_df.apply(scale_val, axis=1)
    melt_df["metric"] = melt_df["metric_col"].map(available_metrics)

    fig2 = px.bar(
        melt_df, x="metric", y="value", color="entity_name",
        barmode="group",
        labels={"value": "Percentage", "metric": "", "entity_name": "Entity"},
        height=400,
    )
    fig2.update_layout(margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig2, width="stretch")
