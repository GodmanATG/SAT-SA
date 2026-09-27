"""
Shared UI components
====================
Small pieces of behaviour that several pages need and that must behave *identically*
wherever they appear: recalculating the scores, reporting what an ingestion actually
matched, showing cycle-over-cycle movement, and exporting the full evidence set
behind a finding.

Keeping them here rather than copying them into each page is deliberate: the
examiner's verdict has to have the same effect whether it was recorded on the
Finding Card or on the Examiner Review page, and the recalculate action must re-run
exactly the same engine in both places.
"""

from __future__ import annotations

import pathlib
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

APP_DIR = pathlib.Path(__file__).parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config import (  # noqa: E402
    ADJUDICATION_VERDICTS, CAPABILITY_COLUMNS, CAPABILITY_NAMES, VERDICT_ICON, load_config,
)
from database import get_db  # noqa: E402


# ---------------------------------------------------------------------------
# Examiner verdicts
# ---------------------------------------------------------------------------

def canonical_verdict(text: str | None) -> str:
    """Match a stored verdict string onto one of the configured verdict labels.

    Adjudications are stored as free text so an examiner can qualify a verdict ("False
    positive - duplicate alert"); every table that displays one needs the canonical
    label to pick the right indicator, so the match lives here rather than being
    re-implemented per page.
    """
    verdict = (text or "").strip()
    if not verdict:
        return ""
    return next((v for v in ADJUDICATION_VERDICTS if v.lower() in verdict.lower()), verdict)


def verdict_display(text: str | None, *, when: str = "", outstanding: str = "— outstanding") -> str:
    """Verdict with its indicator and decision date, for a table cell."""
    verdict = (text or "").strip()
    if not verdict:
        return outstanding
    label = canonical_verdict(verdict)
    return f"{VERDICT_ICON.get(label, '•')} {verdict}" + (f"  ·  {when}" if when else "")


# ---------------------------------------------------------------------------
# Recalculate
# ---------------------------------------------------------------------------

def run_detection_with_progress(caption: str = "Recalculating scores"):
    """Run the detection engine with the app's standard progress feedback."""
    from detection.engine import run_detection
    bar = st.progress(0, text=f"{caption}...")
    steps: list[str] = []

    def progress(msg):
        steps.append(msg)
        bar.progress(min(0.95, len(steps) / 8), text=f"{msg}...")

    summary = run_detection(progress=progress)
    bar.progress(1.0, text="Done")
    bar.empty()
    return summary


def recalculate_button(key: str, *, label: str = "🔄 Recalculate scores now",
                       caption: str | None = None, columns=(1, 3)):
    """A one-click re-run, for the moment right after an examiner records a verdict.

    A verdict changes how a finding counts towards the entity's score, but the score
    is only recomputed when the engine runs. Without this the examiner has to
    navigate away and remember to re-run, which is exactly the kind of friction that
    makes a human-in-the-loop feature go unused.
    """
    if caption:
        st.caption(caption)
    left, _right = st.columns(columns)
    if left.button(label, key=f"recalc_{key}", type="primary"):
        summary = run_detection_with_progress()
        st.success(f"✅ Recalculated — {summary['entities']} entities · "
                   f"{summary['findings']} findings · {summary['cycle_label']} "
                   f"({summary['run_id']}) · {summary['duration_s']}s.")
        st.rerun()


# ---------------------------------------------------------------------------
# Ingestion reporting
# ---------------------------------------------------------------------------

STATUS_ICON = {"ok": "✅", "note": "🟡", "partial": "⚠️", "skipped": "⛔"}


def ingest_summary_card(summary: dict, *, expanded: bool = False):
    """Render the per-file validation summary of one submission ingest.

    This answers the question an ingest report has to answer and a bare row count does
    not: *did the tool understand this file?* A file can load 12,486 rows successfully
    while missing the one column the analysis depends on, and the only place that is
    visible is here.
    """
    rows = summary.get("rows", {})
    files = summary.get("files", []) or []
    total = sum(rows.values()) if rows else 0

    if not files:
        st.info("No structured files were found in this submission.")
        return

    st.markdown(f"**{total:,} records** ingested across {len(rows)} table(s): "
                + ", ".join(f"`{k}`={v:,}" for k, v in rows.items()))
    if summary.get("duplicate_rows"):
        st.caption(f"{summary['duplicate_rows']:,} duplicate identifier(s) inside the submission "
                   f"were collapsed (last row wins).")
    for table, count in (summary.get("shared_ids") or {}).items():
        st.caption(f"{count:,} identifier(s) in `{table}` are also used by another entity. Records "
                   f"are keyed on (entity, identifier) and kept verbatim, so both are retained.")

    table_rows = []
    for entry in files:
        status = entry.get("status", "ok")
        table_rows.append({
            "": STATUS_ICON.get(status, "•"),
            "File": entry.get("name", ""),
            "Loaded as": entry.get("table", ""),
            "Rows": entry.get("rows", 0),
            "Columns matched": (f"{entry.get('columns_matched', 0)}/"
                                f"{entry.get('columns_expected', 0)}"),
            "Note": entry.get("message", "") or "",
        })
    st.dataframe(pd.DataFrame(table_rows), width="stretch", hide_index=True)

    with st.expander("Column-level detail", expanded=expanded):
        for entry in files:
            st.markdown(f"**`{entry.get('name','')}`** → `{entry.get('table','')}` "
                        f"({entry.get('rows', 0):,} rows, "
                        f"{entry.get('columns_matched', 0)}/{entry.get('columns_expected', 0)} "
                        f"canonical columns matched)")
            cols = st.columns(2)
            matched = entry.get("matched_columns") or []
            cols[0].caption("Matched: " + (", ".join(matched) if matched else "—"))
            missing = entry.get("missing_columns") or []
            cols[1].caption("Not in this file: " + (", ".join(missing[:18]) +
                                                    (" …" if len(missing) > 18 else "")
                                                    if missing else "—"))
            if entry.get("unmatched_required"):
                st.warning("Analysis-critical column(s) missing: "
                           + ", ".join(entry["unmatched_required"])
                           + " — detectors that depend on them will read as absent for this entity.")
            if entry.get("unmapped_date_columns"):
                st.caption("Date-looking columns not part of the canonical schema (stored as "
                           "documents only): " + ", ".join(entry["unmapped_date_columns"]))
            if entry.get("extra_columns"):
                st.caption(f"{len(entry['extra_columns'])} further column(s) in the file are not "
                           f"part of the canonical schema and are ignored: "
                           + ", ".join(entry["extra_columns"][:12]))
            st.markdown("---")


def validate_folder_card(report: dict):
    """Render a dry-run validation report (nothing written to the database)."""
    st.markdown(f"**`{report['name']}`** — {report['total_rows']:,} rows across "
                f"{len(report['files'])} file(s)")
    frame = pd.DataFrame([{
        "": STATUS_ICON.get(f.get("status", "ok"), "•"),
        "File": f.get("name", ""),
        "Would load as": f.get("table", ""),
        "Rows": f.get("rows", 0),
        "Columns matched": f"{f.get('columns_matched', 0)}/{f.get('columns_expected', 0)}",
        "Note": f.get("message", "") or "",
    } for f in report["files"]])
    st.dataframe(frame, width="stretch", hide_index=True)
    for warning in report.get("warnings", []):
        st.warning(warning)


# ---------------------------------------------------------------------------
# Cycle over cycle
# ---------------------------------------------------------------------------

# Direction indicators for the cycle-over-cycle panel. Distinct from ``config.VERDICT_ICON``,
# which is about examiner verdicts; the two were previously both called VERDICT_ICON and
# imported into different pages, which invited the wrong one being picked up.
DIRECTION_ICON = {"better": "🟢", "worse": "🔴", "flat": "⚪", "up": "▲", "down": "▼"}


def cycle_diff_table(diff: dict, *, only_movements: bool = True) -> pd.DataFrame:
    """Presentation frame for a cycle-over-cycle diff."""
    changes = diff.get("changes") or []
    if only_movements:
        shown = [c for c in changes if c["verdict"] in ("better", "worse")]
        changes = shown or changes
    return pd.DataFrame([{
        "": DIRECTION_ICON.get(c["verdict"], "•"),
        "Measure": c["label"],
        "Previous cycle": c["previous_display"],
        "Latest cycle": c["latest_display"],
        "Change": c["delta_display"],
        "Verdict": {"better": "improved", "worse": "deteriorated",
                    "flat": "unchanged", "up": "higher", "down": "lower"}[c["verdict"]],
    } for c in changes])


def cycle_panel(entity_id: str, *, title: str = "Changes since the last cycle"):
    """Render the cycle-over-cycle panel for one entity (or a hint if there is one run)."""
    from detection.snapshots import cycle_diff, list_cycles
    with get_db() as conn:
        cycles = list_cycles(conn, entity_id=entity_id)
        diff = cycle_diff(conn, entity_id)

    st.subheader(title)
    if not cycles:
        st.info("No analytics run has been recorded for this entity yet.")
        return
    if diff.get("previous") is None:
        latest = diff.get("latest") or {}
        st.info(
            f"**{latest.get('cycle_label', 'Cycle 1')}** is the first recorded run for this "
            f"entity (captured {latest.get('taken_at', 'n/a')}). A comparison appears once a "
            f"second run has been recorded — re-run the analytics after the next submission "
            f"cycle and the movement will be shown here.")
        return

    latest, previous = diff["latest"], diff["previous"]
    summary = diff.get("summary", {})
    k1, k2, k3, k4 = st.columns(4)
    risk_before = float(previous.get("risk_score") or 0)
    risk_now = float(latest.get("risk_score") or 0)
    k1.metric("Risk score", f"{risk_now:.1f}", delta=f"{risk_now - risk_before:+.1f}",
              delta_color="inverse",
              help="Higher risk score = more supervisory concern. The delta is against the "
                   "previous recorded cycle.")
    k2.metric("Measures improved", summary.get("better", 0))
    k3.metric("Measures deteriorated", summary.get("worse", 0))
    k4.metric("Comparison", f"{previous.get('cycle_label', '')} → {latest.get('cycle_label', '')}")
    st.caption(f"Comparing the run recorded {latest.get('taken_at', '')} with "
               f"{previous.get('taken_at', '')}. Only movements larger than a per-measure noise "
               f"floor are called, so a rounding artefact is never reported as a deterioration.")

    frame = cycle_diff_table(diff)
    if frame.empty:
        st.caption("No measure moved beyond its noise floor between the two cycles.")
    else:
        st.dataframe(frame, width="stretch", hide_index=True)

    with st.expander("Full measure-by-measure comparison"):
        st.dataframe(cycle_diff_table(diff, only_movements=False),
                     width="stretch", hide_index=True)
        st.caption("Direction is declared per measure: for fast closure rates a rise is the "
                   "finding, for escalation and root-cause rates a fall is.")


# ---------------------------------------------------------------------------
# Evidence export
# ---------------------------------------------------------------------------

def full_evidence_export(finding: dict, *, key: str, label: str | None = None):
    """Download every record behind a finding (not just the stored 25-id sample)."""
    from detection.evidence import build_evidence_export
    try:
        with get_db() as conn:
            frame = build_evidence_export(conn, finding, thresholds=load_config())
    except Exception as exc:  # never let an export break the page
        st.caption(f"Full-evidence export unavailable for this finding: {exc}")
        return

    resolved = int(frame.get("resolved_records", pd.Series([0])).iloc[0]) if not frame.empty else 0
    declared = int(finding.get("evidence_count", 0) or 0)
    if resolved:
        note = (f"Reconstructed **{resolved:,}** record(s) at full population "
                f"(the stored sample holds {min(declared, 25):,}).")
    else:
        note = "This finding has no per-record evidence list (see the export's note column)."
    default_label = (f"⬇️ Export all evidence records (CSV) — {resolved:,} row(s)" if resolved
                     else "⬇️ Export evidence basis (CSV)")
    st.download_button(
        label or default_label,
        data=frame.to_csv(index=False).encode("utf-8"),
        file_name=f"sat-sa-evidence-{finding.get('rule_id','rule')}-"
                  f"{(finding.get('entity_id') or '')[:8]}.csv",
        mime="text/csv", key=f"ev_{key}")
    st.caption(note)


# ---------------------------------------------------------------------------
# Capability radar
# ---------------------------------------------------------------------------

def capability_radar(row: dict, *, name: str, key: str, compare_row: dict | None = None,
                     compare_name: str | None = None, height: int = 360):
    """Radar chart of *capability strength*.

    The capability table stores **weakness** (0 = strong, 100 = weakest), so plotting
    it raw produced a chart where a bigger polygon meant a worse entity - the opposite
    of every reader's instinct. The scale is therefore inverted here and labelled
    explicitly: a larger polygon means more capability, and the axis title says so.
    Both series are on the inverted scale so an overlay stays honest.
    """
    caps = {c: row.get(col, 0) for c, col in CAPABILITY_COLUMNS.items()}
    labels = [CAPABILITY_NAMES[c] for c in caps]
    values = [max(0.0, 100.0 - float(caps[c] or 0)) for c in caps]

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(r=values + [values[0]], theta=labels + [labels[0]],
                                  fill="toself", name=f"{name} (capability strength)",
                                  line_color="#1f77b4"))
    if compare_row is not None:
        compare_values = [max(0.0, 100.0 - float(compare_row.get(col, 0) or 0))
                          for col in CAPABILITY_COLUMNS.values()]
        fig.add_trace(go.Scatterpolar(r=compare_values + [compare_values[0]],
                                      theta=labels + [labels[0]], name=str(compare_name),
                                      opacity=0.55, line_color="#ff7f0e"))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100],
                                   title="Capability strength (100 = no finding bears on it)")),
        showlegend=True, height=height,
        margin=dict(l=60, r=60, t=40, b=40),
        title=dict(text="Higher is better on every axis — 100 = no weakness finding for that "
                        "capability, 0 = the weakest scorecard value in the portfolio",
                   font=dict(size=11)),
    )
    st.plotly_chart(fig, width="stretch", key=key)


def weakness_bar(row: dict, *, key: str, height: int = 320):
    """Horizontal bar of the same scores, shown as weakness so the direction is unambiguous."""
    pairs = [(f"{c} {CAPABILITY_NAMES[c]}", col) for c, col in CAPABILITY_COLUMNS.items()]
    frame = pd.DataFrame([{"Capability": label, "Score": float(row.get(col, 0) or 0)}
                          for label, col in pairs]).sort_values("Score")
    fig = go.Figure(go.Bar(x=frame["Score"], y=frame["Capability"], orientation="h",
                           marker_color="#c0392b"))
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10),
                      xaxis=dict(range=[0, 100], title="Weakness score (0 = strong, 100 = weakest)"),
                      title=dict(text="Same data as a bar chart: shorter bars are stronger "
                                      "capabilities", font=dict(size=11)))
    st.plotly_chart(fig, width="stretch", key=key)


def capability_source_note():
    st.caption("A capability score is the summed severity of the findings tagged to it "
               "(scaled by the capability weights in Settings), so **0 means no finding bears on "
               "that capability** — it is not a measurement that the capability is perfect. A "
               "dimension with no coverage in the submission at all will read 0.")
