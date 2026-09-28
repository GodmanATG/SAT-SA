"""
Page 6: Activity Heatmap — Alert volume by hour × day-of-week
=============================================================
The visual form of the negative-space detectors: telemetry that is missing rather
than wrong. Both judgements on this page are made against the *configured*
thresholds (night gap, weekend ratio, minimum sample), not against numbers typed
into the page, so what the chart calls a gap is what the detectors call a gap.
"""

import streamlit as st
import pandas as pd
import plotly.express as px

from config import load_config
from database import get_db

# Same night window the feature layer uses to compute ``is_night``
# (detection/features.py): 22:00-06:00.
NIGHT_HOURS = [22, 23, 0, 1, 2, 3, 4, 5]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def render(filtered_metrics: pd.DataFrame):
    st.info("💡 **How to read this:** This heatmap tracks alert volume over time. Look for distinct vertical gaps (e.g., weekends or nights) which may indicate an entity's SOC is shutting down or failing to monitor critical hours.")

    st.subheader("SOC Activity Heatmap")
    st.caption(
        "Alert volume by hour of day and day of week. A genuinely 24×7 SOC should show "
        "reasonably consistent coverage. Dark/empty bands at night or weekends can indicate "
        "monitoring or staffing gaps."
    )

    if filtered_metrics.empty:
        st.info("No entities match current filters.")
        return

    # Entity selector
    entity_names = sorted(filtered_metrics["entity_name"].tolist())
    sel_entity = st.selectbox("Select entity", entity_names, key="hm_entity")

    sel_row = filtered_metrics[filtered_metrics["entity_name"] == sel_entity]
    if sel_row.empty:
        return
    entity_id = sel_row.iloc[0]["entity_id"]

    # Load alerts for this entity
    with get_db() as conn:
        rows = conn.execute(
            "SELECT created_ts FROM alerts WHERE entity_id = ? AND created_ts IS NOT NULL",
            (entity_id,)
        ).fetchall()

    if not rows:
        st.info("No alert timestamps available for this entity.")
        return

    df = pd.DataFrame([dict(r) for r in rows])
    df["created_ts"] = pd.to_datetime(df["created_ts"], errors="coerce")
    df = df.dropna(subset=["created_ts"])

    df["hour"] = df["created_ts"].dt.hour
    df["dow"] = df["created_ts"].dt.day_name()

    # Build pivot
    dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    pivot = df.groupby(["dow", "hour"]).size().reset_index(name="count")
    pivot_table = pivot.pivot(index="dow", columns="hour", values="count").fillna(0)

    # Reindex
    pivot_table = pivot_table.reindex(dow_order)
    for h in range(24):
        if h not in pivot_table.columns:
            pivot_table[h] = 0
    pivot_table = pivot_table[sorted(pivot_table.columns)]

    # Plot
    fig = px.imshow(
        pivot_table,
        labels=dict(x="Hour of Day", y="Day of Week", color="Alert Count"),
        color_continuous_scale="YlOrRd",
        aspect="auto",
        height=420,
    )
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, width="stretch")

    thresholds = load_config()
    night_gap = sel_row.iloc[0].get("night_coverage_gap_pct", 0) or 0
    night_share = df["hour"].isin(NIGHT_HOURS).mean()
    night_flag = float(thresholds.get("night_gap_flag", 40))
    if night_gap >= night_flag:
        st.warning(
            f"️ **{night_share:.1%}** of this entity's alerts fall in the 22:00–06:00 window. "
            f"That is **{night_gap:.0f}%** below what an evenly-spread monitoring load would "
            f"produce (the gap threshold is {night_flag:.0f}%). Possible staffing or logging gap."
        )
    else:
        st.success(
            f" **{night_share:.1%}** of alerts fall in the night window — a gap of "
            f"{night_gap:.0f}% against an evenly-spread load, below the {night_flag:.0f}% "
            f"threshold. Consistent with continuous monitoring."
        )

    # ── Day-of-week distribution ─────────────────────────────────────────
    st.markdown("---")
    st.subheader("Alert Distribution by Day of Week")

    dow_counts = df["dow"].value_counts().reindex(dow_order).fillna(0)
    fig2 = px.bar(
        x=dow_counts.index, y=dow_counts.values,
        labels={"x": "Day", "y": "Alert Count"},
        height=300,
    )
    fig2.update_layout(margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig2, width="stretch")

    # Weekend vs weekday ratio, judged against the NS-007 threshold rather than a second
    # opinion typed into this page. The minimum-sample guard matters: on a small estate the
    # ratio is noise, and the detector declines to call it for the same reason.
    st.caption("Weekend telemetry is compared here exactly as the NS-007 (weekend blind spot) "
               "detector compares it, including its minimum-sample guard.")
    weekend_ratio_flag = float(thresholds.get("weekend_activity_ratio_flag", 0.15))
    weekend_min_alerts = float(thresholds.get("weekend_min_alerts", 150))
    weekend_total = float(dow_counts[['Saturday', 'Sunday']].sum())
    total_alerts = float(dow_counts.sum())
    weekday_avg = float(dow_counts[WEEKDAYS].mean())
    weekend_avg = float(dow_counts[["Saturday", "Sunday"]].mean())
    if total_alerts < weekend_min_alerts or weekday_avg <= 0:
        st.info(f"{total_alerts:,.0f} total alert(s) is below the {weekend_min_alerts:,.0f} "
                f"minimum sample the detector requires, so no weekend judgement is made from "
                f"this data.")
    else:
        ratio = weekend_avg / weekday_avg
        if ratio < weekend_ratio_flag:
            st.warning(f"️ Weekend activity is only {ratio:.1%} of weekday activity, below the "
                       f"{weekend_ratio_flag:.0%} threshold — possible weekend blind spot.")
        else:
            st.success(f" Weekend activity is {ratio:.1%} of weekday activity, at or above the "
                       f"{weekend_ratio_flag:.0%} threshold.")
