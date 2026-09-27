"""
Page 7: Trend Analysis — Time-series view of metrics over months
=================================================================
Deterioration over time is the signal a single-snapshot review cannot see, and it is
what rule STAT-002 tests for. The metric list is built from the columns the engine
actually writes to ``monthly_metrics``, so a metric added to the pipeline becomes
chartable here without a second edit — an earlier hardcoded list of seven had already
fallen behind the incident-management metrics the detectors rely on.
"""

import streamlit as st
import pandas as pd
import plotly.express as px

from database import get_db

# metric key -> chart label, and whether a rise is a deterioration. Anything stored in
# monthly_metrics but absent from here is still chartable; it just uses its column name.
TREND_METRICS = {
    "fast_closure_rate": ("Fast closure rate (%)", True),
    "crit_no_escalation_rate": ("Critical alerts without escalation (%)", True),
    "template_note_rate": ("Near-duplicate investigation notes (%)", True),
    "night_coverage_gap_pct": ("Night coverage gap (%)", True),
    "repeat_alert_rate": ("Chronic repeat alert traffic (%)", True),
    "missing_case_rate": ("Critical alerts with no case record (%)", True),
    "root_cause_rate": ("Root cause documented (%)", False),
    "escalation_rate_critical": ("Critical escalation rate (%)", False),
    "sla_breach_rate": ("Closures that missed their SLA target (%)", True),
    "weekend_activity_ratio": ("Weekend activity vs weekday (ratio)", False),
    "rework_cases": ("Cases looped back through rework", True),
    "investigation_events": ("Investigation workflow events", None),
    "weekend_alerts": ("Weekend alert volume", None),
    "total_alerts": ("Total alert volume", None),
    "total_cases": ("Case records", None),
    "total_dispositions": ("Disposition records", None),
    "risk_score": ("Composite risk score", True),
}

# Rate metrics are stored as fractions (0-1) and drawn as percentages; counts and the
# risk score are already in their natural units. Matched on substring rather than suffix
# so qualified names such as ``escalation_rate_critical`` are not missed — a global "rate"
# can sit anywhere in the key.
RATE_METRICS = {k for k in TREND_METRICS
                if "_rate" in k or k.endswith("_pct") or k.endswith("_ratio")}


def render(filtered_metrics: pd.DataFrame):
    st.subheader("Behavioural Trend Analysis (6-Month Window)")
    st.caption(
        "How key indicators for an entity have moved over time — useful for spotting "
        "deteriorating or improving operational discipline."
    )

    if filtered_metrics.empty:
        st.info("No entities match current filters.")
        return

    # Load monthly metrics
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM monthly_metrics ORDER BY entity_name, month").fetchall()

    if not rows:
        st.info("No trend data available. Run the detection engine to compute monthly metrics.")
        return

    trend_df = pd.DataFrame([dict(r) for r in rows])

    # Filter to entities in view
    entity_ids = set(filtered_metrics["entity_id"])
    trend_df = trend_df[trend_df["entity_id"].isin(entity_ids)]

    if trend_df.empty:
        st.info("No trend data for the selected entities.")
        return

    # ── Entity selector ──────────────────────────────────────────────────
    entity_names = sorted(trend_df["entity_name"].dropna().unique().tolist())
    selected_entities = st.multiselect(
        "Select entities to compare",
        entity_names,
        default=entity_names[:min(3, len(entity_names))],
    )

    if not selected_entities:
        st.info("Select at least one entity.")
        return

    tdf = trend_df[trend_df["entity_name"].isin(selected_entities)]

    # ── Metric selector ──────────────────────────────────────────────────
    # Known metrics first, in the order the detectors care about them, then anything else
    # the engine has stored. `month`/`entity_*` are identity columns, not measures.
    skip = {"month", "entity_id", "entity_name"}
    known = [(col, TREND_METRICS[col][0]) for col in TREND_METRICS if col in tdf.columns]
    extra = [(col, col.replace("_", " ")) for col in sorted(tdf.columns)
             if col not in skip and col not in TREND_METRICS
             and pd.api.types.is_numeric_dtype(tdf[col])]
    available_metrics = known + extra
    if not available_metrics:
        st.warning("No metrics available for trend analysis.")
        return

    sel_metric = st.selectbox(
        "Metric",
        available_metrics,
        format_func=lambda x: x[1],
    )

    metric_col, metric_label = sel_metric

    # Scale percentage metrics for display only; the direction test below works on the
    # stored values so it stays correct either way.
    plot_df = tdf.copy()
    if metric_col in RATE_METRICS and plot_df[metric_col].max() <= 1:
        plot_df[metric_col] = plot_df[metric_col] * 100

    # ── Line chart ───────────────────────────────────────────────────────
    fig = px.line(
        plot_df, x="month", y=metric_col, color="entity_name",
        markers=True,
        labels={"month": "Month", metric_col: metric_label, "entity_name": "Entity"},
        height=450,
    )
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, width="stretch")

    # ── Pivot table ──────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Monthly Data")

    pivot = tdf.pivot(index="month", columns="entity_name", values=metric_col)
    if metric_col in RATE_METRICS and pivot.max().max() <= 1:
        pivot = pivot * 100

    st.dataframe(pivot.round(2), width="stretch")

    # ── Trend direction indicators ───────────────────────────────────────
    st.markdown("---")
    st.subheader("Trend Direction")

    # Direction is read from the declared meaning of the measure, not from its name: a
    # falling escalation rate is an improvement, a falling root-cause rate is not. For
    # measures with no inherent direction (volumes) this reports movement only.
    worse_is_up = TREND_METRICS.get(metric_col, ("", None))[1]
    noise_floor = 2.0 if metric_col == "risk_score" else 0.02

    for entity in selected_entities:
        edf = tdf[tdf["entity_name"] == entity].sort_values("month")
        vals = edf[metric_col].dropna().values
        if len(vals) < 2:
            continue
        first_half = float(vals[:len(vals) // 2].mean())
        second_half = float(vals[len(vals) // 2:].mean())
        change = second_half - first_half

        if worse_is_up is None:
            direction = "📊 Volume"
        elif abs(change) <= noise_floor:
            direction = "➡️ Stable"
        elif (change > 0) == worse_is_up:
            direction = "🔺 Worsening"
        else:
            direction = "🔻 Improving"

        st.caption(f"**{entity}**: {direction} (first half vs second half: {change:+.2f})")

    st.caption("The halves are a simple split of the review window, shown alongside the chart "
               "rather than instead of it. STAT-002 in *Validation & Methods* is the statistical "
               "test of the same question and is what a finding is raised on.")
