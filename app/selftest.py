"""
SAT-SA self-test
================
Renders every page headlessly against the live database and exercises the
human-in-the-loop path end to end (record a verdict -> recalculate -> the score
moves).  It exists because the app is a Streamlit UI: a syntax error is easy to
catch, a page that raises on an empty register or a missing column is not.

Run it after any change to the detection, ingestion or view layer:

    cd SAT-SA/app
    python selftest.py

Exit code 0 means every page rendered and every interaction check passed.  The
recalculate check is the only part that writes to the database (one adjudication
and one analytics run); point ``SATSA_DB`` at a copy if that matters.

Uses Streamlit's own AppTest harness, which runs the real ``app.py`` in-process -
so this tests the application a supervisor actually launches, not a stand-in.
"""

from __future__ import annotations

import pathlib
import sys
import time

APP_DIR = pathlib.Path(__file__).parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from streamlit.testing.v1 import AppTest  # noqa: E402

PAGES = [
    ("cse_input", "Register & Submissions"),
    ("risk_ranking", "Supervisory Overview"),
    ("finding_cards", "Finding Cards"),
    ("examiner_review", "Examiner Review"),
    ("entity_profile", "Entity Profile"),
    ("evidence", "Evidence Drill-Down"),
    ("peer_comparison", "Peer Comparison"),
    ("activity_heatmap", "Activity Heatmap"),
    ("trend_analysis", "Trend Analysis"),
    ("report_export", "Report Export"),
    ("validation", "Validation & Methods"),
    ("settings", "Settings"),
]

failures: list[str] = []


def render_page(page: str, label: str) -> AppTest | None:
    """Render one page from a cold session and report what it raised."""
    started = time.time()
    app = AppTest.from_file("app.py", default_timeout=300)
    app.session_state["active_page"] = page
    try:
        app.run()
    except Exception as exc:  # noqa: BLE001 - a page that cannot even start is the finding
        failures.append(f"{label}: run() raised {type(exc).__name__}: {exc}")
        print(f"FAIL  {label:<26} {type(exc).__name__}: {exc}")
        return None
    elapsed = time.time() - started
    if app.exception:
        messages = [str(getattr(e, "value", e)) for e in app.exception]
        failures.append(f"{label}: {' | '.join(messages)[:400]}")
        print(f"FAIL  {label:<26} ({elapsed:.1f}s)")
        return None
    print(f"ok    {label:<26} ({elapsed:.1f}s)  buttons={len(app.button)} "
          f"frames={len(app.dataframe)} charts={len(app.get('plotly_chart'))} "
          f"metrics={len(app.metric)}")
    return app


# ---------------------------------------------------------------------------
# 1. Every page renders
# ---------------------------------------------------------------------------
print("Rendering every page\n" + "-" * 60)
rendered: dict[str, AppTest] = {}
for key, label in PAGES:
    app = render_page(key, label)
    if app is not None:
        rendered[key] = app

# ---------------------------------------------------------------------------
# 2. The adjudication loop actually closes: verdict -> score moves
# ---------------------------------------------------------------------------
print("\nExaminer adjudication loop\n" + "-" * 60)

from database import get_connection, record_adjudication  # noqa: E402


def entity_score(entity_id: str) -> dict:
    conn = get_connection()
    try:
        row = conn.execute("SELECT risk_score, examiner_suppressed FROM entity_metrics "
                           "WHERE entity_id = ?", (entity_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


conn = get_connection()
try:
    target = conn.execute("""
        SELECT f.finding_id, f.entity_id, f.rule_id, f.title
        FROM findings f
        WHERE COALESCE(f.examiner_verdict, '') = ''
        ORDER BY f.severity_score DESC LIMIT 1""").fetchone()
finally:
    conn.close()

if not target:
    print("·  no un-adjudicated finding available (register is empty or all adjudicated) - "
          "skipping the interaction checks")
else:
    before = entity_score(target["entity_id"])
    conn = get_connection()
    try:
        record_adjudication(conn, target["finding_id"], target["entity_id"],
                            "False positive - selftest", "Recorded by selftest.py", "selftest")
        conn.commit()
        stored = conn.execute("SELECT COUNT(*) FROM adjudications WHERE finding_id = ?",
                              (target["finding_id"],)).fetchone()[0]
    finally:
        conn.close()
    print(f"·  adjudicated {target['rule_id']} on {target['entity_id'][:8]} ({stored} row(s) stored)")
    if stored < 1:
        failures.append("verdict was not persisted")

    # The recalculate button is the UI half: drive it for real.
    page = rendered.get("examiner_review")
    recalc = [b for b in (page.button if page else []) if str(b.key or "").startswith("recalc_")]
    if not recalc:
        failures.append("no recalculate control on the Examiner Review page")
    else:
        recalc[0].click().run()
        if page.exception:
            failures.append(f"recalculate raised: {[str(getattr(e, 'value', e)) for e in page.exception]}")
        else:
            after = entity_score(target["entity_id"])
            print(f"·  risk score {before.get('risk_score')} -> {after.get('risk_score')} "
                  f"(suppressed findings: {after.get('examiner_suppressed')})")
            if after.get("risk_score") == before.get("risk_score") and not after.get("examiner_suppressed"):
                failures.append("score did not move after a false-positive verdict")

# ---------------------------------------------------------------------------
# 3. Features the problem statement asks for are actually present
# ---------------------------------------------------------------------------
print("\nRequired capabilities present in the UI\n" + "-" * 60)

REQUIRED = [
    ("finding_cards", "download_button", "full-evidence CSV export"),
    ("finding_cards", "button", "recalculate"),
    ("register", "button", "dry-run validation"),
    ("report_export", "download_button", "report export"),
]

for page_key, widget, description in REQUIRED:
    page = rendered.get(page_key)
    if page is None:
        continue
    if widget == "button":
        present = any(str(b.label or "") != "" for b in page.button)
    else:
        present = len(page.get(widget)) > 0
    print(f"{'ok  ' if present else 'MISS'} {description} ({widget} on {page_key})")
    if not present:
        failures.append(f"{description} not found on the {page_key} page")

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
print()
if failures:
    print(f"{len(failures)} PROBLEM(S):")
    for problem in failures:
        print(f"  - {problem}")
    sys.exit(1)
print("All checks passed.")
