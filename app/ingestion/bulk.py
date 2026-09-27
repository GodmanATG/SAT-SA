"""
Bulk submission-folder ingestion
================================
A CSE submission is a *folder*, not a single file: an entity cover sheet plus
alert, case, investigation-workflow, escalation, disposition and inventory
exports.  This module discovers those folders, registers each entity permanently
in the registry, normalises every structured file into the canonical schema, and
reports exactly what happened — file by file.

Design notes that matter for a supervisory tool:

* Ingestion is **idempotent and corrective**.  For every table a folder supplies,
  the entity's previous rows for that table are deleted and the new rows inserted
  inside a **single transaction**.  Re-submitting a corrected export therefore
  replaces the earlier version rather than being silently ignored (``INSERT OR
  IGNORE`` would keep the stale row and look successful), and a failure part-way
  through rolls back to the previously ingested state.
* Only tables the submission actually provides are replaced, so a partial
  re-upload (say, just the case-management file) never blanks the other tables.
* Identifiers that collide *across* entities are retained verbatim. The natural key
  of a submission record is ``(entity_id, record id)`` — two CSEs legitimately both
  submit an ``ALT-0001``, and silently rewriting one of them would break the
  examiner's ability to cross-check the record against the CSE's own export. A count
  of such shared identifiers is reported as a data-hygiene note.
* Foreign keys are intentionally not enforced while ingesting: real submissions
  contain escalation records pointing at cases that do not exist.  That dangling
  reference is itself supervisory evidence (see rule NS-005) and must not abort
  an upload.
* ``ground_truth.json`` and ``entity_profile.json`` are cover sheets, never
  treated as data tables.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

import pandas as pd

APP_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config import normalise_tier  # noqa: E402
from database import (  # noqa: E402
    ensure_schema, entity_id_for, get_connection, log_action, upsert_entity,
)
from ingestion.normalizer import (  # noqa: E402
    REQUIRED_FOR_ANALYSIS, TABLE_COLUMNS, TABLE_ID, auto_detect_table_type,
    build_records, guess_column_mapping, insert_records, mapping_report,
)

# Canonical columns per table live in the normaliser (single source of truth) and
# are re-exported here for backwards compatibility with older imports.
__all__ = [
    "TABLE_COLUMNS", "TABLE_ID", "discover_submission_folders", "ingest_submission_folder",
    "bulk_ingest", "reingest_entity", "find_submission_folder", "load_ground_truth",
    "validate_submission_folder", "main",
]

COVER_SHEETS = {"ground_truth.json", "entity_profile.json", "manifest.json",
                "submissions_manifest.json"}

DATA_EXTENSIONS = {".csv", ".json", ".xlsx", ".xls"}

# Kept as a module-level alias: older code (and the docs) referred to this.
REQUIRED_FIELDS = REQUIRED_FOR_ANALYSIS


# ---------------------------------------------------------------------------
# Discovery / parsing
# ---------------------------------------------------------------------------

def discover_submission_folders(root) -> list[pathlib.Path]:
    """Return folders under ``root`` that look like CSE submissions."""
    root = pathlib.Path(root)
    if not root.exists():
        return []
    found = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and any(
            f.suffix.lower() in DATA_EXTENSIONS and f.name not in COVER_SHEETS
            for f in child.iterdir()
        ):
            found.append(child)
    return found


def _read_table(path: pathlib.Path) -> tuple[pd.DataFrame | None, str]:
    """Read one structured file into a flat DataFrame. Returns (frame, error)."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            return pd.read_csv(path), ""
        if suffix in (".xlsx", ".xls"):
            return pd.read_excel(path, engine="openpyxl"), ""
        if suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return pd.json_normalize(data), ""
            if isinstance(data, dict):
                for value in data.values():
                    if isinstance(value, list):
                        return pd.json_normalize(value), ""
                return pd.json_normalize([data]), ""
            return None, "JSON is neither a list nor an object"
    except Exception as exc:  # a corrupt export must be reported, not fatal
        return None, f"{type(exc).__name__}: {exc}"
    return None, f"unsupported extension {suffix}"


# ---------------------------------------------------------------------------
# Entity registry
# ---------------------------------------------------------------------------

def register_entity(conn, profile: dict, folder: pathlib.Path) -> tuple[str, bool]:
    """Register (or refresh) an entity from a submission cover sheet.

    The entity id is derived deterministically from the entity name, so
    re-ingesting the same submission always maps onto the same entity and never
    creates a duplicate register entry — and a manually registered entity with the
    same name is the *same* entity, not a second empty one.
    """
    name = str(profile.get("entity_name") or folder.name).strip()
    entity_id = entity_id_for(name)
    soc = profile.get("soc") if isinstance(profile.get("soc"), dict) else {}
    profile_kpis = profile.get("declared_kpis")
    profile_controls = profile.get("declared_controls")
    result = upsert_entity(
        conn, entity_id,
        entity_name=name,
        sector=profile.get("sector") or "Strategic & Public Enterprises",
        sub_sector=profile.get("sub_sector", ""),
        tier=normalise_tier(profile.get("criticality_tier") or profile.get("tier")),
        cin=profile.get("cin", ""),
        hq=profile.get("hq", ""),
        state=profile.get("state", ""),
        website=profile.get("website", ""),
        nciipc_id=profile.get("nciipc_id", ""),
        soc_name=profile.get("soc_name") or soc.get("name", ""),
        soc_model=profile.get("soc_model") or soc.get("model", ""),
        soc_coverage=profile.get("soc_coverage") or soc.get("coverage", ""),
        analyst_count=int(profile.get("analyst_count") or soc.get("analyst_count") or 0),
        contact=profile.get("contact") or profile.get("contact_person", ""),
        contact_email=profile.get("contact_email", ""),
        contact_phone=profile.get("contact_phone", ""),
        review_period_start=str(profile.get("review_period_start", ""))[:19],
        review_period_end=str(profile.get("review_period_end", ""))[:19],
        declared_controls=json.dumps(profile_controls if profile_controls is not None else []),
        declared_kpis=json.dumps(profile_kpis if profile_kpis is not None else {}),
        notes=profile.get("notes") or profile.get("submission_note", ""),
        source="bulk",
        upload_folder=str(folder),
    )
    # A cover sheet is the entity's own declaration, so a re-submission refreshes
    # the declared controls/KPIs even for an entity that was registered by hand.
    if profile_controls is not None or profile_kpis is not None:
        conn.execute(
            "UPDATE entities SET declared_controls = COALESCE(?, declared_controls), "
            "declared_kpis = COALESCE(?, declared_kpis) WHERE entity_id = ?",
            (json.dumps(profile_controls) if profile_controls is not None else None,
             json.dumps(profile_kpis) if profile_kpis is not None else None, entity_id))
    return entity_id, result == "created"


# ---------------------------------------------------------------------------
# Derived fields
#
# Several things a detector needs are not columns in any single submitted file:
# whether a case documented a root cause lives in the disposition record, and a
# case's reopen count is recorded against the closure. Deriving them here keeps
# the detectors reading one schema, and keeps the derivation in one auditable
# place instead of scattered across rule functions.
# ---------------------------------------------------------------------------

def derive_linked_fields(conn, entity_id: str) -> dict:
    """Fill case/alert convenience columns from the disposition records."""
    out = {}
    has_disp = conn.execute(
        "SELECT 1 FROM dispositions WHERE entity_id = ? LIMIT 1", (entity_id,)).fetchone()
    if not has_disp:
        return out

    # cases: root cause / remediation documented, reopen count.
    # Every correlated subquery is scoped by entity_id as well as case_id: a case id is
    # unique only within one CSE, so without the second predicate another entity's
    # disposition could decide whether this entity documented a root cause.
    cur = conn.execute("""
        UPDATE cases SET
            root_cause_documented = COALESCE((
                SELECT CASE WHEN TRIM(COALESCE(d.root_cause,'')) <> '' THEN 1 ELSE 0 END
                FROM dispositions d
                WHERE d.case_id = cases.case_id AND d.entity_id = cases.entity_id LIMIT 1), 0),
            remediation_documented = COALESCE((
                SELECT CASE WHEN TRIM(COALESCE(d.remediation_reference,'')) <> ''
                             OR d.remediation_status IN ('completed','in_progress')
                        THEN 1 ELSE 0 END
                FROM dispositions d
                WHERE d.case_id = cases.case_id AND d.entity_id = cases.entity_id LIMIT 1), 0),
            reopened_count = COALESCE((
                SELECT MAX(COALESCE(d.reopen_count,0))
                FROM dispositions d
                WHERE d.case_id = cases.case_id AND d.entity_id = cases.entity_id), 0)
        WHERE entity_id = ? AND case_id IN (
            SELECT case_id FROM dispositions WHERE entity_id = ? AND TRIM(COALESCE(case_id,'')) <> '')
    """, (entity_id, entity_id))
    out["cases_derived"] = cur.rowcount

    # alerts: fill a missing disposition / close time from the closure record
    cur = conn.execute("""
        UPDATE alerts SET
            disposition = CASE WHEN TRIM(COALESCE(alerts.disposition,'')) = ''
                               THEN COALESCE((SELECT d.disposition FROM dispositions d
                                              WHERE d.alert_id = alerts.alert_id
                                                AND d.entity_id = alerts.entity_id LIMIT 1), '')
                               ELSE alerts.disposition END,
            closed_ts = COALESCE(alerts.closed_ts, (SELECT d.closure_ts FROM dispositions d
                                                     WHERE d.alert_id = alerts.alert_id
                                                       AND d.entity_id = alerts.entity_id LIMIT 1)),
            sla_target_minutes = CASE WHEN COALESCE(alerts.sla_target_minutes,0) = 0
                                 THEN COALESCE((SELECT d.sla_target_minutes FROM dispositions d
                                                WHERE d.alert_id = alerts.alert_id
                                                  AND d.entity_id = alerts.entity_id LIMIT 1), 0)
                                 ELSE alerts.sla_target_minutes END
        WHERE entity_id = ? AND (alerts.closed_ts IS NULL OR TRIM(COALESCE(alerts.disposition,'')) = '')
    """, (entity_id,))
    out["alerts_derived"] = cur.rowcount

    # a missing case status should read from whether the case was ever closed
    cur = conn.execute(
        "UPDATE cases SET status = CASE WHEN closed_ts IS NOT NULL THEN 'closed' ELSE 'open' END "
        "WHERE entity_id = ? AND TRIM(COALESCE(status,'')) = ''", (entity_id,))
    out["case_status_filled"] = cur.rowcount
    return out


# ---------------------------------------------------------------------------
# Identifier hygiene
# ---------------------------------------------------------------------------

def _chunked(seq, size=900):
    for start in range(0, len(seq), size):
        yield seq[start:start + size]


def _cross_entity_collisions(conn, table: str, records: list[dict],
                             id_col: str, entity_id: str) -> list[str]:
    """Identifiers in this submission that a *different* entity already uses.

    Purely informational: the primary key is ``(entity_id, id)``, so a shared id is
    legal and **both** records are kept exactly as submitted. The count is reported
    because it is occasionally interesting in itself (two CSEs quoting the same
    vendor ticket, or a copy-pasted export), never because anything was changed.
    """
    if not records:
        return []
    ids = [str(r.get(id_col)) for r in records]
    foreign: set[str] = set()
    for chunk in _chunked(ids):
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT {id_col}, entity_id FROM {table} WHERE {id_col} IN ({marks})",
            chunk).fetchall()
        foreign |= {str(r[0]) for r in rows if str(r[1]) != entity_id}
    return sorted(foreign)


def _dedupe(records: list[dict], id_col: str) -> tuple[list[dict], int]:
    """Drop duplicate identifiers inside one submission (last row wins)."""
    seen: dict[str, dict] = {}
    for rec in records:
        seen[str(rec.get(id_col))] = rec
    dropped = len(records) - len(seen)
    return list(seen.values()), dropped


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def ingest_submission_folder(folder, profile_override: dict | None = None,
                             run_in_one_connection: bool = True) -> dict:
    """Ingest every structured file in one submission folder.

    Returns a summary dict: rows per table, a per-file validation report, the
    entity id, and any warnings. The whole folder is ingested in **one
    transaction**, and each table the folder supplies replaces that entity's
    previous rows for the table.
    """
    folder = pathlib.Path(folder)
    profile = {}
    prof_file = folder / "entity_profile.json"
    if prof_file.exists():
        try:
            profile = json.loads(prof_file.read_text(encoding="utf-8"))
        except Exception:
            profile = {}
    if profile_override:
        profile = {**profile, **profile_override}

    # Headless ingestion can be the very first thing run on a fresh checkout, so the
    # schema is created here rather than assumed to exist from a prior app start.
    ensure_schema()
    conn = get_connection()
    # Set before any statement starts a transaction: dangling references in real
    # submissions are data to analyse, not ingest errors.
    conn.execute("PRAGMA foreign_keys=OFF")
    summary = dict(folder=str(folder), entity_name=profile.get("entity_name", folder.name),
                   entity_id="", created=False, rows={}, files=[], warnings=[],
                   shared_ids={}, duplicate_rows=0)
    try:
        entity_id, created = register_entity(conn, profile, folder)
        summary["entity_id"], summary["created"] = entity_id, created

        per_table: dict[str, list[dict]] = {}
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in DATA_EXTENSIONS or path.name in COVER_SHEETS:
                continue
            df, error = _read_table(path)
            entry = dict(name=path.name, table="", rows=0, columns_matched=0,
                         columns_expected=0, missing_columns=[], unmatched_required=[],
                         unmapped_date_columns=[], extra_columns=[], status="ok",
                         message="")
            if df is None or df.empty:
                entry.update(status="skipped",
                             message=error or "file was empty")
                summary["files"].append(entry)
                summary["warnings"].append(f"{path.name}: {entry['message']}")
                continue

            table = auto_detect_table_type(df)
            mapping = guess_column_mapping(df, table)
            records, report = build_records(df, entity_id, table, mapping)
            entry.update(report)
            entry["table"] = table
            entry["rows"] = len(records)
            if not records:
                entry.update(status="skipped",
                             message=f"no recognisable columns for {table}")
                summary["warnings"].append(f"{path.name}: {entry['message']}")
                summary["files"].append(entry)
                continue
            if report["unmatched_required"]:
                entry["status"] = "partial"
                entry["message"] = ("missing analysis-critical column(s): "
                                    + ", ".join(report["unmatched_required"]))
            elif report["unmapped_date_columns"]:
                entry["status"] = "note"
                entry["message"] = ("date column(s) not part of the canonical schema: "
                                    + ", ".join(report["unmapped_date_columns"]))
            per_table.setdefault(table, []).extend(records)
            summary["files"].append(entry)

        for table, records in per_table.items():
            id_col = TABLE_ID[table]
            shared = _cross_entity_collisions(conn, table, records, id_col, entity_id)
            if shared:
                summary["shared_ids"][table] = len(shared)
            records, dropped = _dedupe(records, id_col)
            summary["duplicate_rows"] += dropped
            # Delete-then-insert: a corrected re-submission replaces the previous
            # upload for this table instead of being ignored as a duplicate.
            insert_records(conn, table, records, replace=True, entity_id=entity_id)
            summary["rows"][table] = len(records)

        # Some fields a detector needs live across two files; derive them once here.
        try:
            summary["derived"] = derive_linked_fields(conn, entity_id)
        except sqlite3.Error as exc:
            summary["warnings"].append(f"derived fields: {exc}")

        log_action(conn, "SUBMISSION_INGESTED", entity_id,
                   f"Ingested '{folder.name}': " +
                   ", ".join(f"{k}={v}" for k, v in summary["rows"].items()) +
                   (f"; {summary['duplicate_rows']} duplicate row(s) collapsed"
                    if summary["duplicate_rows"] else ""))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return summary


def bulk_ingest(root, *, limit: int | None = None, progress=None) -> list[dict]:
    """Ingest every submission folder under ``root``.

    ``progress`` may be a callable ``(index, total, name)`` for UI feedback.
    """
    folders = discover_submission_folders(root)
    if limit:
        folders = folders[:limit]
    results = []
    for i, folder in enumerate(folders, start=1):
        if progress:
            progress(i, len(folders), folder.name)
        try:
            results.append(ingest_submission_folder(folder))
        except Exception as exc:  # never let one bad submission stop the batch
            results.append(dict(folder=str(folder), entity_name=folder.name,
                                entity_id="", created=False, rows={}, files=[],
                                shared_ids={}, duplicate_rows=0,
                                warnings=[f"failed: {exc}"]))
    return results


def validate_submission_folder(folder) -> dict:
    """Dry-run a submission folder: report mapping quality without writing anything.

    This is what backs the *Validate only* button — a supervisor can see that a
    file's ``investigation_note_text`` column was not recognised **before** the
    records are loaded and the detectors quietly read empty text.
    """
    folder = pathlib.Path(folder)
    files, warnings = [], []
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in DATA_EXTENSIONS or path.name in COVER_SHEETS:
            continue
        df, error = _read_table(path)
        if df is None or df.empty:
            files.append(dict(name=path.name, table="", rows=0, status="skipped",
                              message=error or "file was empty", unmatched_required=[],
                              unmapped_date_columns=[], columns_matched=0,
                              columns_expected=0))
            warnings.append(f"{path.name}: {error or 'empty'}")
            continue
        table = auto_detect_table_type(df)
        mapping = guess_column_mapping(df, table)
        report = mapping_report(df, table, mapping)
        from ingestion.normalizer import find_unmapped_date_columns
        report["unmapped_date_columns"] = find_unmapped_date_columns(df, mapping)
        report["name"] = path.name
        report["status"] = "partial" if report["unmatched_required"] else "ok"
        report["message"] = ("missing analysis-critical column(s): "
                             + ", ".join(report["unmatched_required"])
                             ) if report["unmatched_required"] else ""
        files.append(report)
        if report["unmatched_required"]:
            warnings.append(f"{path.name}: " + report["message"])
    has_profile = (folder / "entity_profile.json").exists()
    if not has_profile:
        warnings.append("no entity_profile.json cover sheet — the entity will be registered "
                        "from the folder name")
    return dict(folder=str(folder), name=folder.name, files=files, warnings=warnings,
                total_rows=sum(f.get("rows", 0) for f in files))


def find_submission_folder(entity_id: str, root=None) -> pathlib.Path | None:
    """Locate the on-disk submission folder that belongs to a registered entity.

    Matches first on the folder recorded at ingest time, then on the cover sheet's
    entity name — so a folder that was moved or re-generated is still recognised.
    """
    root = pathlib.Path(root) if root else APP_DIR.parent / "submissions"
    conn = get_connection()
    try:
        row = conn.execute("SELECT entity_name, upload_folder FROM entities WHERE entity_id = ?",
                           (entity_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None

    recorded = pathlib.Path(str(row["upload_folder"] or ""))
    if recorded.name and recorded.exists() and recorded.is_dir():
        return recorded

    for candidate in discover_submission_folders(root):
        prof = candidate / "entity_profile.json"
        if prof.exists():
            try:
                data = json.loads(prof.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            if str(data.get("entity_name", "")).strip() == str(row["entity_name"]).strip():
                return candidate
        if candidate.name.strip() == str(row["entity_name"]).strip():
            return candidate
    return None


def reingest_entity(entity_id: str, root=None) -> dict:
    """Re-ingest the submission folder of a registered entity.

    Ingestion replaces this entity's rows per table, so this refreshes a corrected
    re-submission in place without duplicating anything and without touching the
    entity's register entry or its examiner verdicts.
    """
    folder = find_submission_folder(entity_id, root)
    if folder is None:
        return dict(entity_id=entity_id, rows={}, files=[],
                    warnings=["No submission folder found on disk for this entity"],
                    entity_name="", created=False, shared_ids={}, duplicate_rows=0)
    return ingest_submission_folder(folder)


def load_ground_truth(root) -> dict:
    """Read the generator's injected-weakness ground truth (for validation)."""
    path = pathlib.Path(root) / "ground_truth.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Ingest CSE submission folders into the SAT-SA database")
    ap.add_argument("--path", default=None, help="folder containing submission sub-folders")
    ap.add_argument("--validate-only", action="store_true",
                    help="report mapping quality without writing to the database")
    args = ap.parse_args(argv)

    ensure_schema()
    root = pathlib.Path(args.path) if args.path else APP_DIR.parent / "submissions"
    if args.validate_only:
        for folder in discover_submission_folders(root):
            report = validate_submission_folder(folder)
            print(f"\n{report['name']}  ({report['total_rows']:,} rows)")
            for entry in report["files"]:
                print(f"  {entry['name']:<32} {entry.get('table', ''):<16} "
                      f"rows={entry.get('rows', 0):>6} "
                      f"cols={entry.get('columns_matched', 0)}/{entry.get('columns_expected', 0)}"
                      + (f"  ! {entry['message']}" if entry.get("message") else ""))
        return

    results = bulk_ingest(root, progress=lambda i, n, name: print(f"[{i}/{n}] {name}"))
    ok = [r for r in results if r["entity_id"]]
    print(f"\nIngested {len(ok)} of {len(results)} submission folders from {root}")
    for r in results:
        print(f"  {r['entity_name']:<42} {r['rows']}")
        for w in r["warnings"]:
            print(f"      ! {w}")
    return results


if __name__ == "__main__":
    main()
