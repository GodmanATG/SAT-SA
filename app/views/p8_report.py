"""
Page 8: Report Export — PDF report generation
"""

import os
import tempfile
import json
import streamlit as st
import pandas as pd
from database import get_db
from detection.snapshots import cycle_diff, list_cycles, portfolio_changes
from reports.pdf_gen import build_entity_report, build_portfolio_report


def _run_meta(conn) -> dict:
    """Latest analytics run, for the report header."""
    row = conn.execute(
        "SELECT action, details, timestamp FROM audit_log WHERE action = 'DETECTION_RUN' "
        "ORDER BY log_id DESC LIMIT 1").fetchone()
    if not row:
        return {}
    details = str(row["details"] or "")
    run_id = details.split("(", 1)[1].split(")", 1)[0] if "(" in details else ""
    return dict(cycle_label=details.split(":", 1)[0].strip(), run_id=run_id,
                taken_at=str(row["timestamp"]))


def render(filtered_metrics: pd.DataFrame):
    st.subheader("One-Click Auditor Report (PDF)")
    st.caption("Generate fully offline PDF reports for individual entities or the full portfolio.")

    if filtered_metrics.empty:
        st.info("No entities match current filters.")
        return

    rc1, rc2 = st.columns(2)

    # ── Single entity report ─────────────────────────────────────────────
    with rc1:
        st.markdown("**Single-Entity Report**")
        st.caption("Detailed supervisory assessment for one entity with all findings and evidence summary.")

        entity_names = sorted(filtered_metrics["entity_name"].tolist())
        rep_entity = st.selectbox("Select entity", entity_names, key="rep_entity")

        if st.button(" Generate Entity Report", type="primary"):
            row = filtered_metrics[filtered_metrics["entity_name"] == rep_entity].iloc[0]
            entity_id = row["entity_id"]

            # Load findings, the entity's declarations and its cycle history
            with get_db() as conn:
                findings_rows = conn.execute(
                    "SELECT * FROM findings WHERE entity_id = ? ORDER BY severity_score DESC",
                    (entity_id,)
                ).fetchall()
                verdicts = {r["finding_id"]: r for r in conn.execute(
                    "SELECT finding_id, verdict, rationale FROM adjudications a WHERE a.rowid IN "
                    "(SELECT MAX(rowid) FROM adjudications GROUP BY finding_id)").fetchall()}
                entity_row = conn.execute("SELECT * FROM entities WHERE entity_id = ?",
                                          (entity_id,)).fetchone()
                cycle = cycle_diff(conn, entity_id)
                run_meta = _run_meta(conn)

            findings = []
            for r in findings_rows:
                record = dict(r)
                verdict = verdicts.get(record["finding_id"])
                record["examiner_verdict"] = (record.get("examiner_verdict")
                                             or (verdict["verdict"] if verdict else ""))
                record["examiner_rationale"] = verdict["rationale"] if verdict else ""
                findings.append(record)

            profile = dict(entity_row) if entity_row else {}
            try:
                declarations = json.loads(row.get("declared_kpi_details") or "[]")
            except (TypeError, ValueError):
                declarations = []
            try:
                controls = json.loads((profile.get("declared_controls") or "[]"))
            except (TypeError, ValueError):
                controls = []

            tmp_path = os.path.join(tempfile.gettempdir(), f"{entity_id}_report.pdf")

            try:
                build_entity_report(dict(row), findings, tmp_path,
                                    declarations=declarations, controls=controls,
                                    cycle=cycle, profile=profile, run_meta=run_meta)
                with open(tmp_path, "rb") as f:
                    st.session_state["entity_pdf_bytes"] = f.read()
                    st.session_state["entity_pdf_name"] = f"{rep_entity.replace(' ', '_')}_SAT-SA_Report.pdf"
                st.success("Report generated locally — no data left this machine.")
            except Exception as e:
                st.error(f"Report generation failed: {e}")

        if "entity_pdf_bytes" in st.session_state:
            st.download_button(
                "⬇️ Download Entity Report (PDF)",
                st.session_state["entity_pdf_bytes"],
                file_name=st.session_state["entity_pdf_name"],
                mime="application/pdf",
            )

    # ── Portfolio report ─────────────────────────────────────────────────
    with rc2:
        st.markdown("**Full Portfolio Report**")
        st.caption(f"Summary report covering all {len(filtered_metrics)} entities.")

        if st.button(" Generate Portfolio Report", type="primary"):
            # Load all findings, the cycle movement and the register's criticality tiers
            with get_db() as conn:
                findings_rows = conn.execute(
                    "SELECT * FROM findings ORDER BY severity_score DESC"
                ).fetchall()
                tiers = {r["entity_id"]: r["tier"] for r in conn.execute(
                    "SELECT entity_id, tier FROM entities")}
                changes = portfolio_changes(conn).to_dict("records") \
                    if list_cycles(conn) else []
                run_meta = _run_meta(conn)

            findings = [dict(r) for r in findings_rows]

            tmp_path = os.path.join(tempfile.gettempdir(), "portfolio_report.pdf")

            try:
                build_portfolio_report(
                    filtered_metrics.to_dict("records"),
                    findings,
                    tmp_path,
                    changes=changes,
                    run_meta=run_meta,
                    tier_by_entity=tiers,
                )
                with open(tmp_path, "rb") as f:
                    st.session_state["portfolio_pdf_bytes"] = f.read()
                st.success("Report generated locally — no data left this machine.")
            except Exception as e:
                st.error(f"Report generation failed: {e}")

        if "portfolio_pdf_bytes" in st.session_state:
            st.download_button(
                "⬇️ Download Portfolio Report (PDF)",
                st.session_state["portfolio_pdf_bytes"],
                file_name="SAT-SA_Portfolio_Report.pdf",
                mime="application/pdf",
            )

    # ── Machine-readable exports ─────────────────────────────────────────
    st.markdown("---")
    st.subheader("Machine-readable exports")
    st.caption("For an audit file, a peer review, or re-analysis elsewhere. Generated locally.")

    x1, x2, x3 = st.columns(3)
    with get_db() as conn:
        findings_rows = [dict(r) for r in conn.execute(
            "SELECT f.*, e.entity_name, e.sector FROM findings f "
            "JOIN entities e ON f.entity_id = e.entity_id "
            "ORDER BY f.severity_score DESC").fetchall()]
        metrics_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM entity_metrics ORDER BY risk_rank").fetchall()]
        monthly_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM monthly_metrics ORDER BY entity_name, month").fetchall()]

    with x1:
        st.download_button("⬇️ Findings register (CSV)",
                           pd.DataFrame(findings_rows).to_csv(index=False).encode("utf-8"),
                           "sat_sa_findings.csv", "text/csv")
    with x2:
        st.download_button("⬇️ Entity metrics (CSV)",
                           pd.DataFrame(metrics_rows).to_csv(index=False).encode("utf-8"),
                           "sat_sa_entity_metrics.csv", "text/csv")
    with x3:
        st.download_button("⬇️ Monthly trend metrics (CSV)",
                           pd.DataFrame(monthly_rows).to_csv(index=False).encode("utf-8"),
                           "sat_sa_monthly_metrics.csv", "text/csv")

    y1, y2 = st.columns(2)
    with get_db() as conn:
        cycle_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM metric_snapshots ORDER BY snapshot_id").fetchall()]
        adjudication_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM adjudications ORDER BY decided_at").fetchall()]
    with y1:
        st.download_button("⬇️ Cycle snapshots (CSV)",
                           pd.DataFrame(cycle_rows).to_csv(index=False).encode("utf-8"),
                           "sat_sa_metric_snapshots.csv", "text/csv",
                           help="One row per entity per analytics run — the cycle-over-cycle audit "
                                "surface.")
    with y2:
        st.download_button("⬇️ Examiner adjudications (CSV)",
                           pd.DataFrame(adjudication_rows).to_csv(index=False).encode("utf-8"),
                           "sat_sa_adjudications.csv", "text/csv",
                           help="Append-only record of every examiner verdict, with rationale.")

    st.download_button("⬇️ Full supervisory dataset (JSON)",
                       json.dumps(dict(findings=findings_rows, entity_metrics=metrics_rows,
                                       monthly_metrics=monthly_rows,
                                       metric_snapshots=cycle_rows,
                                       adjudications=adjudication_rows), indent=2,
                                  default=str).encode("utf-8"),
                       "sat_sa_dataset.json", "application/json")

    # ── Audit Log ────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader(" Audit Log")
    st.caption("Append-only record of uploads, ingestion, detection runs and config changes. "
               "Each detection run logs the exact thresholds used, so any result can be reproduced "
               "and defended.")

    with get_db() as conn:
        log_rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 50"
        ).fetchall()

    if log_rows:
        log_df = pd.DataFrame([dict(r) for r in log_rows])
        st.dataframe(log_df, width="stretch", hide_index=True)
    else:
        st.info("No audit log entries yet.")
