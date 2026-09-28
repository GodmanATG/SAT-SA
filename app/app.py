"""
SAT-SA — Supervisory Analytics Tool for SOC Assessment
======================================================
Main Streamlit application.
Run with:  streamlit run app.py

100% OFFLINE — no cloud no external APIs. All analysis runs locally.
"""

import sys
import pathlib

# Ensure the app directory is on the Python path
APP_DIR = pathlib.Path(__file__).parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0 str(APP_DIR))

import streamlit as st
import pandas as pd
from database import init_db get_db has_data
from config import CRITICALITY_TIERS criticality_weight normalise_tier

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="SAT-SA | Supervisory Analytics"
    layout="wide"
    page_icon="️"
)

# ---------------------------------------------------------------------------
# Initialize database on first run
# ---------------------------------------------------------------------------
init_db()

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
st.markdown("""
<style>
/* Dark-themed sidebar nav buttons */
div[data-testid="stSidebar"] button {
    width: 100%;
    text-align: left !important;
    border-radius: 8px;
    margin-bottom: 2px;
    font-size: 0.92rem;
    padding: 0.5rem 0.75rem;
}
/* Risk tier colored badges */
.tier-critical { color: #c0392b; font-weight: bold; }
.tier-elevated { color: #e08e0b; font-weight: bold; }
.tier-watch    { color: #c9a90b; font-weight: bold; }
.tier-ok       { color: #1e8449; font-weight: bold; }
</style>
""" unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

NAV_GROUPS = {
    "Setup & Data": [
        ("Register & Submissions" "cse_input")
    ]
    "Supervisory Dashboards": [
        ("Supervisory Overview"   "risk_ranking")
        ("Finding Cards"          "finding_cards")
        ("Activity Heatmap"       "activity_heatmap")
        ("Peer Comparison"        "peer_comparison")
        ("Trend Analysis"         "trend_analysis")
    ]
    "Deep Dives & Review": [
        ("Entity Profile"         "entity_profile")
        ("Evidence Drill-Down"    "evidence")
        ("Examiner Review"        "examiner_review")
    ]
    "Reports & Admin": [
        ("Report Export"          "report_export")
        ("Validation & Methods"   "validation")
        ("Settings"               "settings")
    ]
}

st.sidebar.title("SAT-SA")
st.sidebar.caption("Supervisory Analytics Tool for SOC Assessment")

with st.sidebar.expander("Quick Start Guide" expanded=True):
    st.markdown(
        """
        **How to evaluate SAT-SA:**
        
        **1. Generate Data**  
        Start at **Register & Submissions**. Click *Generate & Maintenance* to build the synthetic portfolio.
        
        **2. Review Risk Rankings**  
        Go to the **Supervisory Overview** to see the portfolio ranked by a composite risk score.
        
        **3. Adjudicate Findings**  
        Go to the **Entity Profile** and scroll down to the Finding Cards. Click inline buttons to override findings.
        
        **4. Export Reports**  
        Go to **Report Export** to generate air-gapped PDF audit reports and download full evidentiary packages.
        """
    )

if "active_page" not in st.session_state:
    st.session_state.active_page = "risk_ranking" if has_data() else "cse_input"

# ---------------------------------------------------------------------------
# Sidebar Navigation
# ---------------------------------------------------------------------------
st.sidebar.markdown("---")

for group_name items in NAV_GROUPS.items():
    st.sidebar.markdown(f"**{group_name}**")
    for label key in items:
        is_active = st.session_state.active_page == key
        button_type = "primary" if is_active else "secondary"
        if st.sidebar.button(label key=f"nav_{key}" type=button_type use_container_width=True):
            st.session_state.active_page = key
            st.rerun()
    st.sidebar.write("") # small spacer

st.sidebar.markdown("---")

st.sidebar.markdown("**Navigation**")
for label key in NAV_ITEMS:
    is_active = st.session_state.active_page == key
    btn_label = f"▸ **{label}**" if is_active else f"  {label}"
    if st.sidebar.button(btn_label key=f"nav_{key}" width="stretch"):
        st.session_state.active_page = key
        st.rerun()

st.sidebar.markdown("---")

# Global filters (for analytics pages)
active = st.session_state.active_page
analytics_pages = {"risk_ranking" "finding_cards" "evidence" "peer_comparison"
                   "activity_heatmap" "trend_analysis" "report_export"
                   "examiner_review" "entity_profile"}

# Note: the Validation page is deliberately outside analytics_pages - it should show the
# whole portfolio's detector performance regardless of the sidebar filters.

# Load data for filters
with get_db() as conn:
    metrics_rows = conn.execute("SELECT * FROM entity_metrics ORDER BY risk_rank").fetchall()
    metrics_data = [dict(r) for r in metrics_rows]
    has_entities = len(metrics_data) > 0

if active in analytics_pages:
    with get_db() as conn:
        registry = [dict(r) for r in conn.execute(
            "SELECT entity_id entity_name sector tier FROM entities").fetchall()]
else:
    registry = []

tier_by_entity = {r["entity_id"]: normalise_tier(r.get("tier")) for r in registry}

if has_entities and active in analytics_pages:
    metrics_df = pd.DataFrame(metrics_data)
    # Criticality is a register attribute so it comes from the register (an entity
    # whose analytics have not run yet still has a tier). Supervisory priority combines
    # risk score with that exposure weight: identical gaps are not equally urgent in a
    # Tier 1 operator and a Tier 4 one.
    metrics_df["criticality_tier"] = metrics_df["entity_id"].map(tier_by_entity).fillna(
        CRITICALITY_TIERS[2])
    metrics_df["supervisory_priority"] = (
        metrics_df["risk_score"] * metrics_df["criticality_tier"].map(criticality_weight)
    ).round(1)

    st.sidebar.markdown("**Global Filters**")
    sector_options = sorted(metrics_df["sector"].dropna().unique().tolist())
    sel_sectors = st.sidebar.multiselect("Sector" sector_options default=sector_options)

    tier_options = ["Critical Attention" "Elevated" "Watch" "Satisfactory"]
    sel_tiers = st.sidebar.multiselect("Risk Tier" tier_options default=tier_options)
    sel_criticality = st.sidebar.multiselect(
        "Criticality tier" CRITICALITY_TIERS default=CRITICALITY_TIERS
        help="Filter the portfolio by the entity's declared criticality - Tier 1 entities are "
             "critical national infrastructure operators.")

    filtered_metrics = metrics_df[
        metrics_df["sector"].isin(sel_sectors) & metrics_df["risk_tier"].isin(sel_tiers)
        & metrics_df["criticality_tier"].isin(sel_criticality)
    ].copy()
    filtered_entity_ids = set(filtered_metrics["entity_id"])

    st.sidebar.markdown("---")
    st.sidebar.caption(f"{len(filtered_metrics)} of {len(metrics_df)} entities in view")
else:
    metrics_df = pd.DataFrame()
    filtered_metrics = pd.DataFrame()
    filtered_entity_ids = set()

st.sidebar.markdown("---")
st.sidebar.markdown("**Register:** " - (f"{len(metrics_data)} entities" if has_entities
                                         else "empty — register an entity to begin"))
st.sidebar.markdown("**Mode:** fully offline (air-gapped)")

# ---------------------------------------------------------------------------
# Header - KPIs (analytics pages only)
# ---------------------------------------------------------------------------
if active in analytics_pages and has_entities:
    st.markdown("# Supervisory Analytics Tool (SAT-SA)")
    st.markdown("##### NCIIPC Critical Sector Entity Cyber-Resilience Supervision Prototype")
    st.divider()

    k1 k2 k3 k4 k5 = st.columns(5)
    k1.metric("Entities in view" len(filtered_metrics))
    k2.metric("Alerts analysed" f"{int(filtered_metrics['total_alerts'].sum()):}" if len(filtered_metrics) else "0")
    tier1 = int((filtered_metrics["criticality_tier"] == CRITICALITY_TIERS[0]).sum()) \
        if len(filtered_metrics) else 0

    with get_db() as conn:
        findings_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM findings WHERE entity_id IN ({})".format(
                "".join(f"'{eid}'" for eid in filtered_entity_ids)
            ) if filtered_entity_ids else "SELECT 0 as cnt"
        ).fetchone()["cnt"]
    k3.metric("Findings raised" findings_count)

    attention = int(filtered_metrics["risk_tier"].isin(["Critical Attention" "Elevated"]).sum()) \
        if len(filtered_metrics) else 0
    k4.metric("Needing attention" attention
              help="Entities in the Critical Attention or Elevated prioritisation bands"
              delta=f"{tier1} Tier 1 in view" if tier1 else None delta_color="off")
    avg_risk = round(filtered_metrics["risk_score"].mean() 1) if len(filtered_metrics) else 0
    k5.metric("Portfolio avg risk" avg_risk)

    with get_db() as conn:
        last_run = conn.execute(
            "SELECT timestamp details FROM audit_log WHERE action = 'DETECTION_RUN' "
            "ORDER BY log_id DESC LIMIT 1").fetchone()
        adjudicated = conn.execute(
            "SELECT COUNT(*) AS c FROM findings WHERE examiner_verdict <> ''").fetchone()["c"]
    if last_run:
        st.caption(f"Last analytics run: {last_run['timestamp']} · data source: ingested CSE "
                   f"submissions · fully offline (no network calls at any point)"
                   - (f" · {adjudicated} finding(s) carry an examiner verdict"
                      if adjudicated else ""))
    st.markdown("---")

elif active in analytics_pages and not has_entities:
    st.title("️ Supervisory Analytics Tool for SOC Assessment")
    st.caption("NCIIPC Critical Sector Entity cyber-resilience supervision prototype · fully offline")
    st.markdown("---")

    col1 col2 col3 = st.columns([1 2 1])
    with col2:
        st.info("### ️ The register is empty" icon="")
        st.markdown(
            """
            SAT-SA starts blank and stays blank until a Critical Sector Entity is registered and
            its submission ingested. There is nothing to analyse before then and the tool does not
            invent a portfolio for you.

            **1. Register an entity** — profile criticality tier SOC arrangements the controls
            and performance metrics it declares and its submission files.

            **2. Ingest its records** — alert metadata case management investigation workflow
            escalation records disposition/closure and asset inventory in whatever mix the CSE
            actually submits.

            **3. Run the analytics** — across every registered entity so peer comparison and
            benchmarking are meaningful.

            Bulk loading a folder of submissions and generating a synthetic portfolio for
            demonstration both live on the same page.
            """
        )
        if st.button(" Register your first entity" type="primary" width="stretch"):
            st.session_state.active_page = "cse_input"
            st.rerun()

# ---------------------------------------------------------------------------
# Page Router
# ---------------------------------------------------------------------------
if active == "cse_input":
    from views.p1_cse_input import render
    render()

elif active == "risk_ranking" and has_entities:
    from views.p2_risk_ranking import render
    render(filtered_metrics)

elif active == "examiner_review" and has_entities:
    from views.p11_examiner_review import render
    render(filtered_entity_ids)

elif active == "entity_profile" and has_entities:
    from views.p12_entity_profile import render
    render(filtered_entity_ids)

elif active == "finding_cards" and has_entities:
    from views.p3_finding_cards import render
    render(filtered_metrics filtered_entity_ids)

elif active == "evidence" and has_entities:
    from views.p4_evidence import render
    render(filtered_metrics filtered_entity_ids)

elif active == "peer_comparison" and has_entities:
    from views.p5_peer_comparison import render
    render(filtered_metrics)

elif active == "activity_heatmap" and has_entities:
    from views.p6_heatmap import render
    render(filtered_metrics)

elif active == "trend_analysis" and has_entities:
    from views.p7_trends import render
    render(filtered_metrics)

elif active == "report_export" and has_entities:
    from views.p8_report import render
    render(filtered_metrics)

elif active == "validation":
    from views.p10_validation import render
    render()

elif active == "settings":
    from views.p9_settings import render
    render()

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.markdown("---")
st.caption("SAT-SA prototype — supports supervisory judgement does not replace it. "
           "All findings should be validated via manual review before action is taken.")
