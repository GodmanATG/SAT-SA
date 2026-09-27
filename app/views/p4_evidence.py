"""
Page 4: Evidence Drill-Down — Raw alert/case rows behind any finding
=========================================================================
The verification page: a supervisor who does not believe a finding can pull the
underlying alert rows here and read them. Filters are deliberately alert-level
(severity, disposition, category, free-text) because those are the records the
findings cite as evidence.

Every column this page reads is optional in a real submission — a CSE may deliver
alerts without a disposition column at all — so each filter degrades to "not
available" rather than raising.
"""

import streamlit as st
import pandas as pd

from config import load_config
from database import get_db

# Display cap. The query cap above it is deliberately larger: filters and search apply
# to the full set, and only the rendered table is trimmed.
DISPLAY_LIMIT = 500
QUERY_LIMIT = 50_000


def render(filtered_metrics: pd.DataFrame, filtered_entity_ids: set):
    st.subheader("Evidence Drill-Down")
    st.caption("The raw alert rows behind any finding or risk score — for manual verification.")

    if not filtered_entity_ids:
        st.info("No entities match current filters.")
        return

    thresholds = load_config()
    fast_minutes = float(thresholds.get("fast_closure_minutes", 5))

    # Load alerts for the entities in view. Ids are bound as parameters rather than
    # interpolated into the SQL text.
    ids = sorted(filtered_entity_ids)
    placeholders = ",".join("?" * len(ids))
    with get_db() as conn:
        alerts_rows = conn.execute(
            f"SELECT a.*, e.entity_name FROM alerts a "
            f"JOIN entities e ON a.entity_id = e.entity_id "
            f"WHERE a.entity_id IN ({placeholders}) "
            f"ORDER BY a.created_ts DESC LIMIT {QUERY_LIMIT}",
            tuple(ids),
        ).fetchall()

    if not alerts_rows:
        st.info("No alert records found for the selected entities.")
        return

    alerts_df = pd.DataFrame([dict(r) for r in alerts_rows])

    # Convert timestamps
    for col in ["created_ts", "acknowledged_ts", "closed_ts"]:
        if col in alerts_df.columns:
            alerts_df[col] = pd.to_datetime(alerts_df[col], errors="coerce")

    # Compute derived columns
    if "created_ts" in alerts_df.columns and "closed_ts" in alerts_df.columns:
        alerts_df["close_minutes"] = (
            (alerts_df["closed_ts"] - alerts_df["created_ts"]).dt.total_seconds() / 60
        ).round(1)
    else:
        alerts_df["close_minutes"] = None

    def _column(name: str) -> pd.Series:
        """One optional column, present as an empty series when the submission lacks it."""
        return alerts_df[name] if name in alerts_df.columns else pd.Series("", index=alerts_df.index)

    # ── Filters ──────────────────────────────────────────────────────────
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        dd_entity = st.selectbox(
            "Entity", ["All"] + sorted(alerts_df["entity_name"].dropna().unique().tolist()),
            key="dd_entity"
        )
    with f2:
        sev_options = [s for s in ["critical", "high", "medium", "low"]
                       if s in _column("severity").values]
        dd_sev = st.multiselect("Severity", sev_options, default=sev_options)
    with f3:
        disp_options = sorted(_column("disposition").dropna().unique().tolist())
        dd_disp = st.multiselect("Disposition", disp_options, default=disp_options,
                                 help="Blank when the submission carried no disposition records - "
                                      "alert disposition is a separate field group in the "
                                      "submission schema.")
    with f4:
        dd_flag = st.selectbox("Quick filter", [
            "None",
            f"Closed within {fast_minutes:g} minutes",
            "Critical alerts only",
            "Closed without a case record",
        ], help="The first filter uses the same fast-closure threshold the EG-001 detector "
                "reads, configured on the Settings page.")

    ddf = alerts_df.copy()
    if dd_entity != "All":
        ddf = ddf[ddf["entity_name"] == dd_entity]
    if dd_sev:
        ddf = ddf[ddf["severity"].isin(dd_sev)]
    if dd_disp:
        ddf = ddf[ddf["disposition"].isin(dd_disp)]

    if dd_flag.startswith("Closed within"):
        ddf = ddf[ddf["close_minutes"].notna() & (ddf["close_minutes"] < fast_minutes)]
    elif dd_flag == "Critical alerts only":
        ddf = ddf[_column("severity").astype(str).str.lower() == "critical"]
    elif dd_flag == "Closed without a case record":
        case_ids = ddf[_column("case_id")].astype(str).str.strip()
        ddf = ddf[case_ids.isin(["", "nan", "None"])]

    # Search
    search = st.text_input("🔎 Search alert ID, hostname, category or notes")
    if search:
        mask = pd.Series(False, index=ddf.index)
        for col in ["alert_id", "hostname", "investigator_notes", "alert_category",
                    "case_id", "assigned_analyst_id"]:
            if col in ddf.columns:
                mask = mask | ddf[col].astype(str).str.contains(search, case=False, na=False)
        ddf = ddf[mask]

    shown = min(DISPLAY_LIMIT, len(ddf))
    st.caption(f"{len(ddf):,} record(s) match the filters · showing {shown:,}"
               + (" (narrow the filters to see the rest, or export the full set)"
                  if len(ddf) > DISPLAY_LIMIT else ""))

    # ── Display table ────────────────────────────────────────────────────
    display_cols = ["alert_id", "entity_name", "created_ts", "severity", "alert_category",
                    "hostname", "disposition", "close_minutes", "assigned_analyst_id",
                    "investigator_notes"]
    available = [c for c in display_cols if c in ddf.columns]

    st.dataframe(
        ddf[available].head(DISPLAY_LIMIT),
        width="stretch",
        hide_index=True,
    )

    # ── Export ────────────────────────────────────────────────────────────
    # The export is the whole filtered set, not the displayed page: exporting only what
    # fits on screen would silently truncate the evidence an examiner is verifying.
    csv = ddf[available].to_csv(index=False).encode("utf-8")
    st.download_button(f"⬇️ Export all {len(ddf):,} filtered record(s) as CSV", csv,
                       "sat-sa-alert-evidence.csv", "text/csv")
