"""
Page 1: Register & Submissions
==============================
The audit trail starts here. Three jobs:

1. **Register** a Critical Sector Entity by hand - name, sector, criticality tier,
   SOC arrangements, contact, and what the entity *declares* about its controls and
   performance metrics. The register is permanent (stored in the database) and every
   field is editable afterwards.
2. **Load that entity's submission** - upload the alert / case / investigation /
   escalation / disposition / inventory files, or point the tool at a folder that
   already contains them.
3. **Run the analytics** across all registered entities.

The page deliberately starts empty: nothing is pre-loaded, nothing is analysed until
an entity has been registered *and* a submission ingested. Files are read through the
same canonical ingestion path the bulk loader uses, so a single company and a foreign
submission folder behave identically.
"""

import json
import pathlib
import sys

import pandas as pd
import streamlit as st

APP_DIR = pathlib.Path(__file__).parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config import CRITICALITY_TIERS, SECTORS, normalise_tier  # noqa: E402
from database import (  # noqa: E402
    ENTITY_PROFILE_FIELDS, db_stats, delete_entity, entity_id_for, get_db, log_action,
    upsert_entity, vacuum_db,
)
from ingestion.bulk import (  # noqa: E402
    bulk_ingest, discover_submission_folders, find_submission_folder, ingest_submission_folder,
    load_ground_truth, reingest_entity, validate_submission_folder,
)
from views.components import (  # noqa: E402
    ingest_summary_card, run_detection_with_progress, validate_folder_card,
)

# Where a folder of CSE submissions is read from. Files are read in place and never
# copied, so a submission is ingested from the same path the CSE delivered it to.
SUBMISSIONS_DIR = APP_DIR.parent / "submissions"

DATA_EXTENSIONS = {"csv", "json", "xlsx", "xls"}
DOC_EXTENSIONS = {"pdf", "docx", "txt", "log", "xml"}

KPI_FIELDS = [
    ("sla_compliance_pct", "Declared SLA compliance (%)", 0.0, 100.0),
    ("critical_alert_ack_minutes_median", "Declared critical alert acknowledgement (minutes)", 0.0, 600.0),
    ("critical_incident_containment_minutes_median",
     "Declared critical incident containment (minutes)", 0.0, 5000.0),
    ("escalation_compliance_pct", "Declared escalation compliance (%)", 0.0, 100.0),
    ("investigation_records_completeness_pct",
     "Declared investigation record completeness (%)", 0.0, 100.0),
    ("monitoring_coverage_production_pct",
     "Declared production monitoring coverage (%)", 0.0, 100.0),
]


def _run_detection_with_progress():
    """Thin alias so the page keeps one name for the shared progress wrapper."""
    return run_detection_with_progress("Running analytics")


def _safe_slug(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " _-").strip().replace(" ", "_")


def _profile_from_form(values: dict, kpis: dict, controls: list[str], folder: pathlib.Path) -> dict:
    """Build a cover-sheet dict from the form, in the same shape a submission uses."""
    return dict(
        entity_name=values.get("entity_name", ""),
        sector=values.get("sector", ""),
        sub_sector=values.get("sub_sector", ""),
        criticality_tier=values.get("tier", CRITICALITY_TIERS[2]),
        cin=values.get("cin", ""),
        hq=values.get("hq", ""),
        state=values.get("state", ""),
        website=values.get("website", ""),
        nciipc_id=values.get("nciipc_id", ""),
        soc=dict(name=values.get("soc_name", ""), model=values.get("soc_model", ""),
                 coverage=values.get("soc_coverage", ""),
                 analyst_count=int(values.get("analyst_count") or 0)),
        contact_person=values.get("contact", ""),
        contact_email=values.get("contact_email", ""),
        contact_phone=values.get("contact_phone", ""),
        review_period_start=values.get("review_period_start", ""),
        review_period_end=values.get("review_period_end", ""),
        declared_controls=controls,
        declared_kpis=kpis,
        notes=values.get("notes", ""),
        submission_note=values.get("notes", ""),
        upload_folder=str(folder),
    )


# ---------------------------------------------------------------------------
# Shared form (used for both "add" and "edit", so the two cannot drift apart)
# ---------------------------------------------------------------------------

def _entity_form(prefix: str, existing: dict | None = None):
    """Render the entity profile form and return the collected values."""
    e = existing or {}
    declared_kpis = {}
    if e.get("declared_kpis"):
        try:
            declared_kpis = json.loads(e["declared_kpis"]) or {}
        except (TypeError, ValueError, json.JSONDecodeError):
            declared_kpis = {}
    controls_text = ""
    if e.get("declared_controls"):
        try:
            controls_text = "\n".join(json.loads(e["declared_controls"]) or [])
        except (TypeError, ValueError, json.JSONDecodeError):
            controls_text = ""

    st.markdown("##### Entity identity")
    c1, c2, c3 = st.columns(3)
    with c1:
        name = st.text_input("Entity name *", value=e.get("entity_name", ""), key=f"{prefix}_name")
        sector = st.selectbox(
            "CII sector *", SECTORS,
            index=SECTORS.index(e["sector"]) if e.get("sector") in SECTORS else 0,
            key=f"{prefix}_sector")
        sub_sector = st.text_input("Sub-sector", value=e.get("sub_sector", ""), key=f"{prefix}_subsector")
    with c2:
        tier = st.selectbox(
            "Criticality tier *", CRITICALITY_TIERS,
            index=CRITICALITY_TIERS.index(normalise_tier(e.get("tier")))
            if e.get("tier") else 2,
            key=f"{prefix}_tier",
            help="Drives supervisory priority: two entities with identical gaps are not equally "
                 "urgent if one is a Tier 1 critical national infrastructure operator.")
        hq = st.text_input("Headquarters / city", value=e.get("hq", ""), key=f"{prefix}_hq")
        state = st.text_input("State", value=e.get("state", ""), key=f"{prefix}_state")
    with c3:
        cin = st.text_input("CIN / registration", value=e.get("cin", ""), key=f"{prefix}_cin")
        nciipc_id = st.text_input("NCIIPC CSE ID", value=e.get("nciipc_id", ""), key=f"{prefix}_nciipc")
        website = st.text_input("Website", value=e.get("website", ""), key=f"{prefix}_website")

    st.markdown("##### SOC arrangements")
    s1, s2, s3, s4 = st.columns(4)
    soc_name = s1.text_input("SOC / CERT name", value=e.get("soc_name", ""), key=f"{prefix}_socname")
    soc_model = s2.selectbox(
        "Delivery model", ["", "In-house", "Hybrid (MSSP + in-house Tier 3)", "Managed (MSSP)",
                           "Managed (shared services)", "Not stated"],
        index=["", "In-house", "Hybrid (MSSP + in-house Tier 3)", "Managed (MSSP)",
               "Managed (shared services)", "Not stated"].index(e.get("soc_model", ""))
        if e.get("soc_model") in ["", "In-house", "Hybrid (MSSP + in-house Tier 3)", "Managed (MSSP)",
                                  "Managed (shared services)", "Not stated"] else 0,
        key=f"{prefix}_socmodel")
    soc_coverage = s3.selectbox(
        "Monitoring coverage window",
        ["", "24x7", "Extended hours (06:00-24:00)", "Extended hours (06:00-22:00)",
         "Business hours (08:00-20:00)", "Business hours (09:00-18:00)",
         "Business hours (07:00-19:00)", "Business hours with on-call", "Not stated"],
        index=0, key=f"{prefix}_soccoverage")
    analyst_count = s4.number_input("Analyst headcount", min_value=0, max_value=1000,
                                    value=int(e.get("analyst_count") or 0), step=1,
                                    key=f"{prefix}_analysts")

    st.markdown("##### Contact & review period")
    p1, p2, p3, p4 = st.columns(4)
    contact = p1.text_input("Contact person", value=e.get("contact", ""), key=f"{prefix}_contact")
    contact_email = p2.text_input("Contact email", value=e.get("contact_email", ""), key=f"{prefix}_email")
    contact_phone = p3.text_input("Contact phone", value=e.get("contact_phone", ""), key=f"{prefix}_phone")
    period = p4.text_input("Review period (start → end)",
                           value=(f"{e.get('review_period_start', '')} → {e.get('review_period_end', '')}"
                                  if e.get("review_period_start") else ""),
                           key=f"{prefix}_period",
                           help="Optional. Leave blank to use the period implied by the submitted records.")

    st.markdown("##### What the entity declares about itself")
    st.caption(
        "These are the controls and performance metrics the entity reports in its cover sheet. "
        "The tool recomputes each one from the entity's own submitted records and flags any "
        "declaration its evidence does not support (rule IM-008). Leaving a metric at 0 means "
        "'not declared'."
    )
    controls_text = st.text_area(
        "Declared controls (one per line)", value=controls_text, height=110, key=f"{prefix}_controls",
        placeholder="Documented incident management procedure approved by CISO\n"
                    "Root cause analysis mandatory for P1/P2 incidents")
    kpi_values = {}
    kcols = st.columns(3)
    for i, (key, label, lo, hi) in enumerate(KPI_FIELDS):
        current = declared_kpis.get(key, 0)
        try:
            current = float(current)
        except (TypeError, ValueError):
            current = 0.0
        kpi_values[key] = kcols[i % 3].number_input(
            label, min_value=lo, max_value=hi, value=min(hi, max(lo, current)),
            step=0.1 if hi <= 100 else 1.0, key=f"{prefix}_kpi_{key}")

    notes = st.text_area("Notes", value=e.get("notes", ""), height=70, key=f"{prefix}_notes")

    values = dict(entity_name=name.strip(), sector=sector, sub_sector=sub_sector, tier=tier,
                  cin=cin, hq=hq, state=state, website=website, nciipc_id=nciipc_id,
                  soc_name=soc_name, soc_model=soc_model, soc_coverage=soc_coverage,
                  analyst_count=analyst_count, contact=contact, contact_email=contact_email,
                  contact_phone=contact_phone,
                  review_period_start=(period.split("→")[0].strip() if "→" in period else ""),
                  review_period_end=(period.split("→")[1].strip() if "→" in period else ""),
                  notes=notes)
    kpis = {k: v for k, v in kpi_values.items() if v not in (0, 0.0)}
    controls = [line.strip() for line in controls_text.splitlines() if line.strip()]
    return values, kpis, controls


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render():
    st.title("📁 Register & Submissions")
    st.caption("A blank register by design. Add a Critical Sector Entity, load its submitted records, "
               "then run the analytics. Nothing is pre-loaded and nothing is analysed until you do.")

    tab_add, tab_manage, tab_bulk, tab_api, tab_demo = st.tabs([
        "➕ Register a CSE", "🏢 Entity Register", "📦 Bulk Ingest", "🔌 API Polling",
        "🧪 Generate & Maintenance"])

    # ── Tab 1: register one entity ───────────────────────────────────────
    with tab_add:
        with get_db() as conn:
            entity_count = conn.execute("SELECT COUNT(*) AS c FROM entities").fetchone()["c"]
        if entity_count == 0:
            st.info("**Step 1 of 3 — register your first entity.** Fill in the profile below, attach its "
                    "submission files (optional; you can also point at a folder later), and save. The "
                    "profile is stored permanently and can be edited at any time.", icon="1️⃣")
        else:
            st.caption(f"{entity_count} entities currently registered. Analytics run across all of "
                       f"them together, so peer comparison stays meaningful.")

        values, kpis, controls = _entity_form("add")

        st.markdown("---")
        st.markdown("##### Submission files (optional at registration)")
        st.caption("Alert metadata, case-management records and investigation workflow as CSV/JSON/XLSX; "
                   "PDF/DOCX reports are stored as supporting documents and text-searched.")
        uploaded = st.file_uploader(
            "Select this entity's files", accept_multiple_files=True, key="add_files",
            type=sorted(DATA_EXTENSIONS | DOC_EXTENSIONS))

        c_save, c_save_run = st.columns([1, 1])
        save_clicked = c_save.button("💾 Save entity profile", type="primary", width="stretch")
        save_run_clicked = c_save_run.button("💾 Save, ingest files and run analytics",
                                             width="stretch",
                                             disabled=not uploaded)

        if save_clicked or save_run_clicked:
            if not values["entity_name"]:
                st.error("Entity name is required.")
            else:
                entity_id = entity_id_for(values["entity_name"])
                folder = SUBMISSIONS_DIR / _safe_slug(values["entity_name"])
                folder.mkdir(parents=True, exist_ok=True)
                profile = _profile_from_form(values, kpis, controls, folder)
                (folder / "entity_profile.json").write_text(json.dumps(profile, indent=2),
                                                            encoding="utf-8")

                structured, documents, saved = [], [], []
                for uf in uploaded or []:
                    suffix = uf.name.rsplit(".", 1)[-1].lower()
                    dest = folder / uf.name
                    dest.write_bytes(uf.getvalue())
                    saved.append(uf.name)
                    (structured if suffix in DATA_EXTENSIONS else documents).append(uf.name)

                with get_db() as conn:
                    action = upsert_entity(
                        conn, entity_id, source="manual", upload_folder=str(folder),
                        **{k: v for k, v in values.items() if k in ENTITY_PROFILE_FIELDS})
                    conn.execute(
                        "UPDATE entities SET declared_controls = ?, declared_kpis = ?, "
                        "updated_at = CURRENT_TIMESTAMP WHERE entity_id = ?",
                        (json.dumps(controls), json.dumps(kpis), entity_id))
                    log_action(conn, "ENTITY_REGISTERED", entity_id,
                               f"{action.title()} '{values['entity_name']}' "
                               f"(tier {values['tier']}, sector {values['sector']})")

                st.success(f"✅ **{values['entity_name']}** saved to the register as `{entity_id}` "
                           f"({action}).")

                # Canonical ingestion path: identical to the bulk loader's
                if structured:
                    summary = ingest_submission_folder(folder, profile_override=profile)
                    st.success(f"✅ Submission ingested for **{values['entity_name']}**.")
                    # The validation card is what tells the supervisor whether the tool
                    # understood the file - a row count alone cannot.
                    ingest_summary_card(summary)
                    for warning in summary.get("warnings", []):
                        st.warning(warning)

                for doc in documents:
                    try:
                        from ingestion.normalizer import ingest_document
                        from ingestion.parsers import detect_file_type, parse_file
                        with open(folder / doc, "rb") as handle:
                            parsed = parse_file(handle, doc)
                        if isinstance(parsed, dict):
                            ingest_document(parsed, entity_id, doc, detect_file_type(doc))
                            st.info(f"📄 `{doc}` stored as a supporting document "
                                    f"({len(parsed.get('referenced_ids', []))} referenced IDs extracted)")
                    except Exception as exc:
                        st.warning(f"Could not read `{doc}`: {exc}")

                if save_run_clicked:
                    summary = _run_detection_with_progress()
                    st.success(f"✅ Analysed {summary['entities']} entities across "
                               f"{summary['alerts']:,} alerts → {summary['findings']} findings "
                               f"({summary['duration_s']}s). Open **Risk Ranking**.")
                    st.rerun()
                else:
                    st.caption("Next: **Entity Register** tab → *Run analytics*, or ingest more "
                               "entities first.")

    # ── Tab 2: manage / edit the register ────────────────────────────────
    with tab_manage:
        with get_db() as conn:
            entities = [dict(r) for r in conn.execute(
                "SELECT * FROM entities ORDER BY tier, entity_name").fetchall()]

        if not entities:
            st.info("The register is empty. Use **➕ Register a CSE** to add the first entity, or "
                    "**📦 Bulk Ingest** to load a folder of submissions.")
        else:
            st.markdown("##### Filter")
            f1, f2, f3 = st.columns(3)
            tier_filter = f1.multiselect("Criticality tier", CRITICALITY_TIERS,
                                        default=CRITICALITY_TIERS, key="reg_tier_filter")
            sector_filter = f2.multiselect("Sector", sorted({e["sector"] for e in entities}),
                                          default=sorted({e["sector"] for e in entities}),
                                          key="reg_sector_filter")
            search = f3.text_input("Search name / ID", key="reg_search").strip().lower()

            visible = [e for e in entities
                       if normalise_tier(e.get("tier")) in tier_filter
                       and e["sector"] in sector_filter
                       and (not search or search in e["entity_name"].lower()
                            or search in (e.get("entity_id") or "").lower())]
            st.caption(f"{len(visible)} of {len(entities)} entities match. "
                       f"Criticality is a register attribute, so it filters the whole register, not "
                       f"just the analytics view.")

            with get_db() as conn:
                counts = {}
                for table in ("alerts", "cases", "escalations", "asset_inventory",
                              "investigations", "dispositions"):
                    for row in conn.execute(
                            f"SELECT entity_id, COUNT(*) AS c FROM {table} GROUP BY entity_id"):
                        counts.setdefault(row["entity_id"], {})[table] = row["c"]
                metrics = {r["entity_id"]: dict(r) for r in
                           conn.execute("SELECT entity_id, risk_score, risk_tier, risk_rank "
                                        "FROM entity_metrics")}

            if visible:
                st.dataframe(pd.DataFrame([{
                    "Entity": e["entity_name"],
                    "Tier": e["tier"],
                    "Sector": e["sector"],
                    "SOC coverage": e.get("soc_coverage", ""),
                    "Alerts": counts.get(e["entity_id"], {}).get("alerts", 0),
                    "Cases": counts.get(e["entity_id"], {}).get("cases", 0),
                    "Investigations": counts.get(e["entity_id"], {}).get("investigations", 0),
                    "Dispositions": counts.get(e["entity_id"], {}).get("dispositions", 0),
                    "Assets": counts.get(e["entity_id"], {}).get("asset_inventory", 0),
                    "Risk": metrics.get(e["entity_id"], {}).get("risk_score", ""),
                    "Risk tier": metrics.get(e["entity_id"], {}).get("risk_tier", "not yet analysed"),
                } for e in visible]), width="stretch", hide_index=True)

            st.markdown("---")
            st.markdown("##### Edit, re-ingest or withdraw an entity")
            names = [f"{e['entity_name']}  ({e['tier']})" for e in visible]
            if not names:
                st.info("No entity matches the filter.")
            else:
                choice = st.selectbox("Select an entity", names, key="reg_choice")
                selected = visible[names.index(choice)]
                entity_id = selected["entity_id"]

                tab_edit, tab_files, tab_remove = st.tabs(["✏️ Edit profile", "📥 Submission files",
                                                          "🗑️ Withdraw"])

                with tab_edit:
                    st.caption("Every field is editable. Saving updates the register entry in place — "
                               "the entity id, and therefore all its ingested records, does not change.")
                    values, kpis, controls = _entity_form("edit", selected)
                    if st.button("💾 Save changes", type="primary", key="reg_edit_save"):
                        if not values["entity_name"]:
                            st.error("Entity name cannot be empty.")
                        else:
                            with get_db() as conn:
                                upsert_entity(
                                    conn, entity_id, source=selected.get("source", "manual"),
                                    **{k: v for k, v in values.items()
                                       if k in ENTITY_PROFILE_FIELDS})
                                conn.execute(
                                    "UPDATE entities SET declared_controls = ?, declared_kpis = ?, "
                                    "updated_at = CURRENT_TIMESTAMP WHERE entity_id = ?",
                                    (json.dumps(controls), json.dumps(kpis), entity_id))
                                log_action(conn, "ENTITY_UPDATED", entity_id,
                                           f"Profile updated: tier={values['tier']}, "
                                           f"sector={values['sector']}, "
                                           f"{len(controls)} declared controls, "
                                           f"{len(kpis)} declared KPIs")
                            st.success("Profile updated.")

                with tab_files:
                    folder = find_submission_folder(entity_id)
                    if folder:
                        files = sorted(p.name for p in pathlib.Path(folder).iterdir()
                                       if p.is_file())
                        st.caption(f"Submission folder: `{folder}` — {len(files)} file(s).")
                        st.write(", ".join(f"`{f}`" for f in files) or "—")
                    else:
                        st.warning("No submission folder found on disk for this entity.")

                    more = st.file_uploader("Add or replace files for this entity",
                                           accept_multiple_files=True, key=f"up_{entity_id}",
                                           type=sorted(DATA_EXTENSIONS | DOC_EXTENSIONS))
                    c1, c2 = st.columns(2)
                    if c1.button("📥 Save & ingest these files", disabled=not more, type="primary"):
                        target = pathlib.Path(folder) if folder else \
                            SUBMISSIONS_DIR / _safe_slug(selected["entity_name"])
                        target.mkdir(parents=True, exist_ok=True)
                        for uf in more:
                            (target / uf.name).write_bytes(uf.getvalue())
                        # a cover sheet must exist for the ingest to bind records to this entity
                        if not (target / "entity_profile.json").exists():
                            (target / "entity_profile.json").write_text(json.dumps(dict(
                                entity_name=selected["entity_name"], sector=selected["sector"],
                                criticality_tier=selected.get("tier"),
                                cse_id=selected.get("nciipc_id") or None,
                            ) | {k: selected.get(k, "") for k in ENTITY_PROFILE_FIELDS}, indent=2),
                                encoding="utf-8")
                        summary = ingest_submission_folder(target)
                        st.success("Ingested. Re-submitting a corrected export replaces the "
                                   "previous upload for each table it contains — records are "
                                   "updated in place, never duplicated.")
                        ingest_summary_card(summary)
                        for warning in summary.get("warnings", []):
                            st.warning(warning)
                    if c2.button("🔄 Re-ingest all files on disk"):
                        summary = reingest_entity(entity_id, root=SUBMISSIONS_DIR)
                        st.success("Re-ingested from disk.")
                        ingest_summary_card(summary)
                    if folder and st.button("🔍 Validate the folder without loading it",
                                            key=f"val_{entity_id}"):
                        report = validate_submission_folder(folder)
                        st.caption("Dry run — nothing was written to the database.")
                        validate_folder_card(report)
                    st.caption("After changing data, run the analytics again so findings and scores "
                               "reflect the new submission.")

                with tab_remove:
                    st.caption(f"Withdrawing **{selected['entity_name']}** deletes its submitted "
                               f"records, findings and scores from the register. The files on disk "
                               f"are left untouched.")
                    confirm = st.checkbox("I understand this cannot be undone",
                                          key=f"del_confirm_{entity_id}")
                    if st.button("🗑️ Withdraw entity", disabled=not confirm,
                                 key=f"del_{entity_id}"):
                        with get_db() as conn:
                            removed = delete_entity(conn, entity_id)
                            log_action(conn, "ENTITY_WITHDRAWN", entity_id,
                                       f"Withdrawn '{selected['entity_name']}' "
                                       f"({removed.get('alerts', 0)} alerts removed)")
                        st.success(f"Withdrawn. Removed {removed.get('alerts', 0):,} alerts, "
                                   f"{removed.get('cases', 0):,} cases.")
                        st.rerun()

            st.markdown("---")
            c_run, c_vac = st.columns([2, 1])
            if c_run.button("🔍 Run analytics across all registered entities", type="primary"):
                summary = _run_detection_with_progress()
                st.success(f"✅ {summary['entities']} entities · {summary['alerts']:,} alerts · "
                           f"{summary['findings']} findings in {summary['duration_s']}s.")
                st.rerun()
            if c_vac.button("🧹 Compact database"):
                result = vacuum_db()
                st.success(f"Reclaimed {result['saved'] / 1024 / 1024:.1f} MB — now "
                           f"{result['size_mb']} MB.")

    # ── Tab 3: bulk ingest ───────────────────────────────────────────────
    with tab_bulk:
        st.markdown("### 📦 Bulk ingest a folder of CSE submissions")
        st.caption(
            "A submission is a folder: an entity cover sheet (`entity_profile.json`) plus alert, "
            "case, investigation-workflow, escalation, disposition and inventory exports. Point the "
            "tool at a directory of such folders — each entity is registered permanently and ingested "
            "in one pass. Re-ingesting a corrected submission updates records in place."
        )
        root = st.text_input("Directory containing submission folders", value=str(SUBMISSIONS_DIR),
                             key="bulk_root")
        found = discover_submission_folders(root) if root else []
        st.caption(f"Found **{len(found)}** submission folder(s)" +
                   (f": {', '.join(f.name for f in found[:6])}" + (" …" if len(found) > 6 else "")
                    if found else "."))

        limit = st.number_input("Limit to first N folders (0 = all)", min_value=0, value=0, step=1)
        cval, cing = st.columns(2)
        if cval.button("🔍 Validate all folders (dry run)", disabled=not found):
            reports = [validate_submission_folder(f) for f in
                       (found[:int(limit)] if limit else found)]
            total_rows = sum(r["total_rows"] for r in reports)
            partial = [r for r in reports if any(f.get("unmatched_required") for f in r["files"])]
            st.success(f"Validated {len(reports)} folder(s) · {total_rows:,} rows would load · "
                       f"nothing written.")
            if partial:
                st.warning(f"{len(partial)} folder(s) carry a file that is missing an "
                           f"analysis-critical column. Those are listed below — the ingest will "
                           "still succeed, but the affected detectors will read as absent for that "
                           "entity rather than silently suggesting there was nothing to find.")
            for report in reports:
                if report.get("warnings"):
                    with st.expander(f"⚠️ {report['name']}"):
                        validate_folder_card(report)
            st.caption("Expand a folder above for the per-file column detail. If a submission "
                       "cannot be parsed, the ingest reports it per file rather than failing the "
                       "whole batch.")

        if cing.button("📥 Register & ingest submissions", type="primary", disabled=not found):
            bar = st.progress(0.0, text="Ingesting...")
            results = bulk_ingest(
                root, limit=int(limit) or None,
                progress=lambda i, n, nm: bar.progress(i / max(1, n), text=f"[{i}/{n}] {nm}"))
            bar.empty()
            ok = [r for r in results if r["entity_id"]]
            rows = sum(sum(r.get("rows", {}).values()) for r in ok)
            st.success(f"✅ Registered {len(ok)} entities and ingested {rows:,} records.")
            failed = [r for r in results if not r["entity_id"]]
            for r in failed:
                st.error(f"{r['entity_name']}: " + "; ".join(r.get("warnings", ["failed"])))
            st.dataframe(pd.DataFrame([{
                "Entity": r["entity_name"],
                "New?": "new" if r.get("created") else "updated",
                **{k: v for k, v in r.get("rows", {}).items()},
            } for r in ok]), width="stretch", hide_index=True)

            # File-level validation for the folders that need attention. A clean folder's
            # detail is available on demand through the per-entity Validate button, so the
            # report here stays targeted at what a supervisor has to act on.
            flagged = [r for r in ok if r.get("warnings")
                       or any(f.get("unmatched_required") or f.get("status") == "skipped"
                              for f in r.get("files", []))]
            if flagged:
                st.warning(f"{len(flagged)} submission(s) need attention — see the file-level "
                           f"detail below.")
                for r in flagged:
                    with st.expander(f"⚠️ {r['entity_name']}"):
                        ingest_summary_card(r)

        st.markdown("---")
        st.markdown("### 🧾 Coverage checklist")
        st.caption("Which of the six submission field groups the register actually holds, per entity. "
                   "An absent group is itself supervisory-relevant: a case-management file that was "
                   "never submitted is not the same as one that shows nothing wrong.")
        with get_db() as conn:
            rows = [dict(r) for r in conn.execute("""
                SELECT e.entity_name, e.tier,
                  (SELECT COUNT(*) FROM alerts a WHERE a.entity_id=e.entity_id) AS alerts,
                  (SELECT COUNT(*) FROM cases c WHERE c.entity_id=e.entity_id) AS cases,
                  (SELECT COUNT(*) FROM investigations i WHERE i.entity_id=e.entity_id) AS investigations,
                  (SELECT COUNT(*) FROM escalations x WHERE x.entity_id=e.entity_id) AS escalations,
                  (SELECT COUNT(*) FROM dispositions d WHERE d.entity_id=e.entity_id) AS dispositions,
                  (SELECT COUNT(*) FROM asset_inventory n WHERE n.entity_id=e.entity_id) AS assets
                FROM entities e ORDER BY e.entity_name""").fetchall()]
        if rows:
            frame = pd.DataFrame(rows)
            for col in ("alerts", "cases", "investigations", "escalations", "dispositions", "assets"):
                frame[col] = frame[col].apply(lambda v: f"{v:,}" if v else "— not submitted")
            st.dataframe(frame, width="stretch", hide_index=True)
        else:
            st.info("No entities registered yet.")

    # ── Tab 4: API polling ───────────────────────────────────────────────
    with tab_api:
        st.markdown("### 🔌 Ingest from a SOC platform API")
        st.caption(
            "The problem statement asks for ingestion of \"CSV, JSON, database exports and APIs "
            "where available\". This pulls the same six field groups over REST and feeds them "
            "through **exactly the same canonicalisation path** as a file upload — dates to "
            "ISO-8601, severities normalised, column names mapped with the same scoped synonym "
            "table — so nothing about the analysis depends on how the data arrived. Polling is "
            "idempotent: re-polling replaces that entity's rows per table."
        )
        from ingestion.api_poller import ApiProfile, MockEndpointServer, default_endpoints, ingest_from_api

        a1, a2, a3 = st.columns([2, 1, 1])
        base_url = a1.text_input("Endpoint base URL", value="http://127.0.0.1:8787", key="api_base")
        flavour = a2.selectbox("Platform flavour", ["servicenow", "jira"], key="api_flavour")
        remote = a3.checkbox("Allow non-loopback host", value=False, key="api_remote",
                             help="Off by default. SAT-SA is an air-gapped tool, so a remote host "
                                  "must be allow-listed deliberately.")

        with get_db() as conn:
            known = [r["entity_name"] for r in conn.execute(
                "SELECT entity_name FROM entities ORDER BY entity_name")]
        mode = st.radio("Poll for", ["An entity already in the register", "Register a new entity "
                                     "from the endpoint"], horizontal=True, key="api_mode")

        if mode.startswith("An entity already"):
            if not known:
                st.info("The register is empty. Register an entity first, or choose the second "
                        "option to create one from the poll.")
                entity_name = ""
            else:
                entity_name = st.selectbox("Entity", known, key="api_entity")
            sector = tier = ""
        else:
            e1, e2, e3 = st.columns(3)
            entity_name = e1.text_input("Entity name", key="api_new_name")
            sector = e2.selectbox("CII sector", SECTORS, key="api_new_sector")
            tier = e3.selectbox("Criticality tier", CRITICALITY_TIERS, index=2, key="api_new_tier")

        if st.button("🔌 Poll the endpoint and ingest", type="primary",
                     disabled=not entity_name):
            profile = ApiProfile(entity_name=entity_name,
                                 sector=sector or "Strategic & Public Enterprises",
                                 criticality_tier=tier or CRITICALITY_TIERS[2],
                                 notes="Registered from an API poll")
            bar = st.progress(0.0, text="Polling...")
            try:
                result = ingest_from_api(
                    profile, default_endpoints(base_url, entity_name, flavour),
                    allow_remote=remote,
                    progress=lambda msg: bar.progress(0.5, text=f"{msg}..."))
                bar.progress(1.0, text="Done")
                bar.empty()
                if result["tables"]:
                    st.success(f"✅ Polled {sum(result['tables'].values()):,} record(s) for "
                               f"**{entity_name}**.")
                    ingest_summary_card(result)
                else:
                    st.warning("The endpoint returned nothing usable. Check that it is running "
                               "and that the entity name matches.")
                for warning in result.get("warnings", []):
                    st.warning(warning)
            except Exception as exc:
                bar.empty()
                st.error(f"API poll failed: {exc}")

        st.markdown("---")
        st.markdown("#### Offline mock endpoint (demonstration & test harness)")
        st.caption(
            "Serves an already generated submission folder as paginated, platform-shaped JSON "
            "(ServiceNow table API or Jira search shape). This exists so the API path can be "
            "demonstrated and regression-tested on a fully air-gapped machine — no internet, no "
            "third-party service, no cloud. Point the poller above at it to watch the same "
            "synonym mapping and date normalisation do real work on platform field names."
        )
        m1, m2, m3 = st.columns(3)
        mock_dir = m1.text_input("Submission folder to serve", value=str(SUBMISSIONS_DIR),
                                 key="mock_dir")
        mock_port = int(m2.number_input("Port", min_value=1024, max_value=65535, value=8787,
                                        step=1, key="mock_port"))
        mock_flavour = m3.selectbox("Payload shape", ["servicenow", "jira"], key="mock_flavour")
        s1, s2, s3 = st.columns(3)
        if s1.button("▶️ Start mock endpoint"):
            server = MockEndpointServer(mock_dir, mock_flavour)
            try:
                server.start(port=mock_port)
                st.session_state["mock_server"] = server
                st.success(f"Mock {mock_flavour} endpoint listening on "
                           f"http://127.0.0.1:{mock_port} — serving "
                           f"{len(server._entities())} entity folder(s).")
            except OSError as exc:
                st.error(f"Could not start the mock endpoint on port {mock_port}: {exc}. Another "
                         f"process is probably already listening there.")
        if s2.button("⏹ Stop mock endpoint"):
            server = st.session_state.pop("mock_server", None)
            if server:
                server.stop()
                st.info("Mock endpoint stopped.")
            else:
                st.caption("No mock endpoint started by this session.")
        if s3.button("🩺 Check health"):
            import urllib.request
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{mock_port}/api/health",
                                            timeout=5) as response:
                    st.success(f"Endpoint healthy: {response.read().decode('utf-8')}")
            except Exception as exc:
                st.warning(f"No endpoint answering on port {mock_port} ({exc}).")

        st.caption("From a terminal, the same harness runs head-lessly: "
                   "`python -m ingestion.api_poller --serve --flavour servicenow` in one shell, "
                   "then `python -m ingestion.api_poller --entity \"<name>\"` in another. It is "
                   "also a fixture for the regression tests.")

    # ── Tab 5: generation + maintenance ──────────────────────────────────
    with tab_demo:
        st.markdown("### 🧪 Generate a synthetic CSE portfolio (offline, seeded)")
        from synthetic.submissions import ROSTER
        st.caption(
            f"Writes one submission folder per Critical Sector Entity — {len(ROSTER)} fictional CSEs "
            f"across all 10 CII sectors, six months of alert metadata, case-management records, "
            f"investigation workflow events, escalation records, disposition/closure records and asset "
            f"inventory — plus `ground_truth.json` recording exactly which weakness was injected where "
            f"(that is what the Validation page scores the detectors against). Names are fictional on "
            f"purpose: a synthetic dataset must never imply that a real organisation is underperforming."
        )

        d1, d2, d3, d4 = st.columns(4)
        n_cse = d1.number_input("Entities", min_value=1, max_value=len(ROSTER), value=len(ROSTER),
                                step=1)
        d_days = d2.number_input("Review window (days)", min_value=60, max_value=730, value=180,
                                 step=30)
        rate = d3.number_input("Alert rate (alerts per monitored asset per day)", min_value=0.05,
                               max_value=2.0, value=0.34, step=0.05,
                               help="Volume scales with each entity's own monitored estate, which is "
                                    "what makes 'unexpectedly low activity' measurable rather than an "
                                    "artefact of report size.")
        gen_cycle = d4.number_input(
            "Submission cycle", min_value=1, max_value=2, value=1, step=1,
            help="Cycle 1 is the baseline. Cycle 2 re-submits the same entities with a "
                 "deliberate drift (three deteriorate, two improve, one unchanged), which is "
                 "what makes the cycle-over-cycle comparison demonstrable rather than "
                 "asserted. Re-ingest and re-run to see the movement on the Entity Profile.")
        out_dir = st.text_input("Output directory", value=str(SUBMISSIONS_DIR))
        if int(gen_cycle) > 1:
            from synthetic.submissions import CYCLE_DRIFT
            drift = CYCLE_DRIFT.get(int(gen_cycle), {})
            if drift:
                st.info("**Cycle 2 drift:** " + " · ".join(
                    f"{name}: " + (", ".join(drift[name].get("add", []))
                                   or "no new weakness") +
                    (" (improved: " + ", ".join(drift[name]["remove"]) + ")"
                     if drift[name].get("remove") else "")
                    for name in drift))

        c_gen, c_all = st.columns(2)
        if c_gen.button("🏭 Generate submission folders"):
            from synthetic.submissions import write_submissions
            with st.spinner("Simulating six months of SOC records..."):
                res = write_submissions(out_dir, companies=int(n_cse), days=int(d_days),
                                        alerts_per_asset_day=float(rate), cycle=int(gen_cycle))
            st.success(f"✅ {res['companies']} folders (cycle {res['cycle']}) written to "
                       f"`{res['out_dir']}` · {res['total_alerts']:,} alerts · "
                       f"{res['total_cases']:,} cases · {res['total_investigations']:,} "
                       f"investigation events · {res['total_dispositions']:,} dispositions.")
            st.info("Then use **Bulk Ingest**, or the one-click button beside this.")
        if c_all.button("⚡ Generate, ingest and analyse", type="primary"):
            from synthetic.submissions import write_submissions
            with st.spinner("Generating submissions..."):
                res = write_submissions(out_dir, companies=int(n_cse), days=int(d_days),
                                        alerts_per_asset_day=float(rate), cycle=int(gen_cycle))
            with st.spinner("Registering and ingesting..."):
                results = bulk_ingest(out_dir)
            summary = _run_detection_with_progress()
            st.success(f"✅ {res['companies']} CSEs (cycle {res['cycle']}) · "
                       f"{sum(sum(r.get('rows', {}).values()) for r in results):,} records · "
                       f"{summary['findings']} findings · {summary['cycle_label']} "
                       f"({summary['run_id']}) recorded.")
            if int(gen_cycle) > 1:
                st.info("Open **🏢 Entity Profile** and expand *Changes since the last cycle* on any "
                        "drifted entity to see the movement, or use the portfolio movement table on "
                        "the Risk Ranking page.")
            st.rerun()

        st.markdown("---")
        st.markdown("### 💾 Storage & maintenance")
        stats = db_stats()
        s1, s2, s3, s4, s5 = st.columns(5)
        s1.metric("Database size", f"{stats['size_mb']} MB")
        s2.metric("Alerts", f"{stats['counts'].get('alerts', 0):,}")
        s3.metric("Cases", f"{stats['counts'].get('cases', 0):,}")
        s4.metric("Investigation events", f"{stats['counts'].get('investigations', 0):,}")
        s5.metric("Free pages", f"{stats['free_pages']:,}")
        st.caption("A finding stores a capped sample of evidence ids (the true count is kept as a "
                   "number) and the database auto-vacuums on delete, so a ~47,000-alert, "
                   "~83,000-event portfolio stays in the tens of megabytes.")

        ground = load_ground_truth(out_dir)
        if ground:
            st.caption(f"Ground truth present from `{ground.get('generated_at', 'unknown')}` "
                       f"({len(ground.get('entities', []))} entities) — "
                       f"the Validation page will score detectors against it.")

        st.markdown("---")
        st.markdown("**⚠️ Danger zone**")
        confirm = st.checkbox("I understand this deletes all ingested data and findings")
        if st.button("🗑️ Clear all data", disabled=not confirm):
            with get_db() as conn:
                for table in ("audit_log", "adjudications", "monthly_metrics", "findings", "metric_snapshots", "entity_metrics",
                              "documents", "investigations", "dispositions", "escalations",
                              "cases", "alerts", "asset_inventory", "entities"):
                    conn.execute(f"DELETE FROM {table}")
                log_action(conn, "DATA_CLEARED", "", "All data cleared by supervisor")
            vacuum_db()
            st.success("All data cleared and storage reclaimed. The app is back to a blank register.")
            st.rerun()
