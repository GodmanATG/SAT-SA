"""
API ingestion poller
====================
The problem statement requires ingestion of "common formats such as CSV, JSON,
database exports **and APIs where available**". Batch file submission is the normal
case for a supervisory review, but a CSE whose SOC runs on ServiceNow or Jira can
expose the same six field groups over REST. This module polls such an endpoint and
feeds the result through *exactly the same* canonicalisation path as a file upload,
so nothing about the analysis depends on how the data arrived.

Two halves:

* ``fetch_records`` / ``ingest_from_api`` — pull JSON from an endpoint (with
  pagination) and load it into the canonical tables, idempotently.
* ``MockEndpointServer`` — a tiny local HTTP server that serves an already
  generated submission folder in ServiceNow- or Jira-shaped JSON, so the API path
  can be demonstrated **and tested on a fully air-gapped machine** with no
  internet and no third-party service.

Because the tool is required to run air-gapped, remote hosts are refused unless the
caller passes ``allow_remote=True`` explicitly; the default is loopback-only.
"""

from __future__ import annotations

import json
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd

APP_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from database import (  # noqa: E402
    ensure_schema, entity_id_for, get_connection, log_action, upsert_entity,
)
from ingestion.normalizer import (  # noqa: E402
    build_records, guess_column_mapping, insert_records,
)

# ---------------------------------------------------------------------------
# Field-name translation used by the mock server.
#
# Real SOC platforms do not use our canonical names, and that mismatch is the whole
# point of a normalisation layer. The mock emits platform-style names so the poller
# exercises the same synonym mapping a genuine ServiceNow export would.
# ---------------------------------------------------------------------------

SERVICENOW_FIELDS = {
    "alert_id": "sys_id", "created_ts": "sys_created_on", "updated_ts": "sys_updated_on",
    "closed_ts": "resolved_at", "acknowledged_ts": "acknowledged_at",
    "severity": "priority", "alert_category": "category", "status": "state",
    "assigned_analyst_id": "assigned_to", "assigned_team": "assignment_group",
    "asset_id": "cmdb_ci", "hostname": "node", "investigator_notes": "work_notes",
    "alert_name": "short_description", "source_system": "source",
    "risk_score": "risk_score", "detection_rule_id": "rule_id",
    "case_id": "parent_incident", "sla_target_minutes": "sla_target_minutes",
    "disposition": "close_code", "ip_address": "ip_address",
    "user_id": "caller_id", "alert_type": "incident_type",
}

# A few platform vocabulary values, so severity normalisation is exercised too.
SERVICENOW_SEVERITY = {"1": "critical", "2": "high", "3": "medium", "4": "low", "5": "low"}

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

# ServiceNow API path segment -> canonical SAT-SA table. A real poller addresses the
# platform's own table names; the canonical name is what the analytics layer knows.
PLATFORM_TABLES = {
    "incident": "alerts",
    "sn_si_task": "dispositions",
    "sn_customerservice_case": "cases",
    "task": "escalations",
    "problem": "cases",
    "cmdb_ci": "asset_inventory",
}


@dataclass
class ApiEndpoint:
    """One pollable REST resource that populates one canonical table."""

    name: str                       # e.g. "incidents"
    url: str                        # full URL, page params appended
    target_table: str               # alerts | cases | escalations | ...
    records_path: tuple = ()        # dotted path to the records array in the payload
    page_param: str = "sysparm_offset"
    limit_param: str = "sysparm_limit"
    page_size: int = 2000
    extra_params: dict = field(default_factory=dict)


@dataclass
class ApiProfile:
    """The profile an entity is registered with when it is created by polling."""

    entity_name: str
    sector: str = "Strategic & Public Enterprises"
    criticality_tier: str = "Tier 3 - Medium"
    soc_name: str = ""
    notes: str = "Registered from an API poll"


def _is_loopback(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return host in LOOPBACK_HOSTS


def fetch_records(url: str, records_path: tuple = (), params: dict | None = None,
                  timeout: float = 15.0, allow_remote: bool = False,
                  page_param: str | None = None, limit_param: str | None = None,
                  page_size: int | None = None, max_pages: int = 200,
                  headers: dict | None = None) -> list[dict]:
    """Fetch JSON records from ``url``, following pagination until exhausted.

    ``records_path`` locates the array inside the payload (e.g. ``("result",)`` for
    ServiceNow, ``("issues",)`` for Jira). With no path, the payload itself must be
    an array. Pagination stops when a page returns fewer than ``page_size`` rows or
    when the payload is no longer a list.
    """
    if not allow_remote and not _is_loopback(url):
        raise ValueError(
            f"refusing to call non-loopback host {url!r}: SAT-SA is an air-gapped tool. "
            f"Pass allow_remote=True only for a CNAPP/allow-listed host inside the same "
            f"enclave network.")

    collected: list[dict] = []
    offset = 0
    for _ in range(max_pages):
        query = dict(params or {})
        if page_param:
            query[page_param] = offset
        if limit_param and page_size:
            query[limit_param] = page_size
        full = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
        request = urllib.request.Request(full, headers={"Accept": "application/json",
                                                       **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
            raise ConnectionError(f"API poll failed for {full}: {exc}") from exc

        rows = payload
        for key in records_path:
            if isinstance(rows, dict):
                rows = rows.get(key)
            else:
                rows = None
            if rows is None:
                break
        if not isinstance(rows, list):
            break
        collected.extend(rows)
        if not page_param or len(rows) < (page_size or len(rows)):
            break
        offset += page_size or len(rows)
    return collected


def ingest_from_api(entity: ApiProfile, endpoints: list[ApiEndpoint], *,
                    allow_remote: bool = False, replace: bool = True,
                    timeout: float = 15.0, progress=None) -> dict:
    """Register/refresh an entity and load every endpoint into the canonical tables.

    Runs through the same ``build_records`` path as file ingestion, so API data is
    canonicalised identically (dates to ISO-8601, severities to four levels, column
    names mapped with the same scoped synonym table) and is idempotent: re-polling
    replaces that entity's rows per table in a single transaction.
    """
    entity_id = entity_id_for(entity.entity_name)
    summary = dict(entity_id=entity_id, entity_name=entity.entity_name, tables={},
                   files=[], warnings=[], source="api")

    ensure_schema()
    conn = get_connection()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        upsert_entity(conn, entity_id, entity_name=entity.entity_name, sector=entity.sector,
                      tier=entity.criticality_tier, soc_name=entity.soc_name,
                      notes=entity.notes, source="api")
        for endpoint in endpoints:
            if progress:
                progress(f"Polling {endpoint.name}")
            try:
                rows = fetch_records(
                    endpoint.url, endpoint.records_path,
                    params=endpoint.extra_params, timeout=timeout,
                    allow_remote=allow_remote,
                    page_param=endpoint.page_param or None,
                    limit_param=endpoint.limit_param if endpoint.page_param else None,
                    page_size=endpoint.page_size if endpoint.page_param else None)
            except (ConnectionError, ValueError) as exc:
                summary["warnings"].append(f"{endpoint.name}: {exc}")
                continue
            if not rows:
                summary["warnings"].append(f"{endpoint.name}: no records returned")
                continue

            frame = pd.json_normalize(rows)
            mapping = guess_column_mapping(frame, endpoint.target_table)
            records, report = build_records(frame, entity_id, endpoint.target_table, mapping)
            if not records:
                summary["warnings"].append(
                    f"{endpoint.name}: response carried no recognisable "
                    f"{endpoint.target_table} columns")
                continue
            insert_records(conn, endpoint.target_table, records, replace=replace,
                           entity_id=entity_id)
            summary["tables"][endpoint.target_table] = len(records)
            entry = dict(report)
            entry.update(name=endpoint.name, table=endpoint.target_table,
                         rows=len(records))
            summary["files"].append(entry)

        log_action(conn, "SUBMISSION_INGESTED", entity_id,
                   "API poll: " + ", ".join(f"{k}={v}" for k, v in summary["tables"].items()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return summary


def default_endpoints(base_url: str, entity_name: str, flavour: str = "servicenow") -> list[ApiEndpoint]:
    """Standard endpoint set for a platform flavour, pointing at a mock/real server."""
    if flavour == "jira":
        return [
            ApiEndpoint("issues", f"{base_url}/rest/api/3/search", "alerts",
                        records_path=("issues",), page_param="startAt",
                        limit_param="maxResults", page_size=1000),
            ApiEndpoint("worklogs", f"{base_url}/rest/api/3/worklog", "investigations",
                        records_path=("worklogs",), page_param="startAt",
                        limit_param="maxResults", page_size=1000),
        ]
    return [
        ApiEndpoint("incident", f"{base_url}/api/now/table/incident", "alerts",
                    records_path=("result",), page_size=2000,
                    extra_params={"sysparm_query": f"company={entity_name}"}),
        ApiEndpoint("case", f"{base_url}/api/now/table/sn_customerservice_case", "cases",
                    records_path=("result",), page_size=2000,
                    extra_params={"sysparm_query": f"company={entity_name}"}),
        ApiEndpoint("task", f"{base_url}/api/now/table/sn_si_task", "dispositions",
                    records_path=("result",), page_size=2000,
                    extra_params={"sysparm_query": f"company={entity_name}"}),
        ApiEndpoint("escalation", f"{base_url}/api/now/table/task", "escalations",
                    records_path=("result",), page_size=2000,
                    extra_params={"sysparm_query": f"company={entity_name}"}),
        ApiEndpoint("inventory", f"{base_url}/api/now/table/cmdb_ci", "asset_inventory",
                    records_path=("result",), page_size=2000,
                    extra_params={"sysparm_query": f"company={entity_name}"}),
    ]


# ---------------------------------------------------------------------------
# Mock endpoint server (offline demonstration / test harness)
# ---------------------------------------------------------------------------

def _translate(frame: pd.DataFrame, translation: dict[str, str], flavour: str) -> list[dict]:
    """Rename canonical columns to platform-style names for the mock payload.

    Built by explicit assignment rather than ``DataFrame.rename`` because a real
    export can easily carry both the canonical name and the platform name (a CSE's
    ``alerts_export.csv`` has a ``priority`` column *and* we want to emit ``priority``
    for ``severity``). A plain rename then produces two identically named columns and
    every subsequent ``out[name]`` silently becomes a DataFrame, which is exactly how
    the mock server used to crash with ``'DataFrame' object has no attribute 'str'``.
    Here the canonical column always wins over a same-named pass-through column.
    """
    data: dict[str, pd.Series] = {}
    for col in frame.columns:
        if col not in translation:
            data[col] = frame[col]
    for canonical, platform in translation.items():
        if canonical in frame.columns:
            data[platform] = frame[canonical]
    out = pd.DataFrame(data, index=frame.index)

    severity_col = translation.get("severity")
    if severity_col and severity_col in out.columns:
        out[severity_col] = (out[severity_col].astype(str).str.lower()
                             .map({v: k for k, v in SERVICENOW_SEVERITY.items()})
                             .fillna("3"))
    if flavour == "jira" and "alert_id" in out.columns:
        out["key"] = out["alert_id"]
    return out.where(pd.notna(out), None).to_dict("records")


def _load_folder_tables(folder: pathlib.Path) -> dict[str, list[dict]]:
    """Read a submission folder into platform-shaped record lists, keyed by table."""
    from ingestion.bulk import COVER_SHEETS, DATA_EXTENSIONS, _read_table
    from ingestion.normalizer import auto_detect_table_type

    tables: dict[str, list[dict]] = {}
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in DATA_EXTENSIONS or path.name in COVER_SHEETS:
            continue
        frame, _err = _read_table(path)
        if frame is None or frame.empty:
            continue
        table = auto_detect_table_type(frame)
        frame = frame.astype(object)
        tables.setdefault(table, []).extend(
            _translate(frame, SERVICENOW_FIELDS, "servicenow"))
    return tables


class MockEndpointServer:
    """Serve one submission folder as a paginated, platform-shaped JSON API.

    Endpoints (mirroring ServiceNow's table API):

        GET /api/health
        GET /api/entities
        GET /api/now/table/<table>?sysparm_offset=&sysparm_limit=&sysparm_query=
        GET /rest/api/3/search?startAt=&maxResults=          (Jira flavour)
        GET /rest/api/3/worklog?startAt=&maxResults=
    """

    JIRA_TABLES = {"alerts": "issues", "investigations": "worklogs"}

    def __init__(self, submissions_dir, flavour: str = "servicenow"):
        self.submissions_dir = pathlib.Path(submissions_dir)
        self.flavour = flavour
        self._cache: dict[str, dict[str, list[dict]]] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _entities(self) -> list[str]:
        if not self.submissions_dir.exists():
            return []
        return sorted(p.name for p in self.submissions_dir.iterdir()
                      if p.is_dir() and (p / "entity_profile.json").exists())

    def _tables_for(self, entity: str) -> dict[str, list[dict]]:
        if entity not in self._cache:
            folder = self.submissions_dir / entity
            self._cache[entity] = _load_folder_tables(folder) if folder.exists() else {}
        return self._cache[entity]

    def handle(self, path: str, query: dict) -> tuple[int, dict]:
        if path.rstrip("/") in ("/api/health", "/health"):
            return 200, dict(status="ok", flavour=self.flavour,
                             entities=len(self._entities()))
        if path.rstrip("/") in ("/api/entities", "/entities"):
            return 200, dict(entities=self._entities())

        parts = [p for p in path.split("/") if p]
        entity = (query.get("company") or query.get("entity") or [None])[0]
        # a sysparm_query like "company=Deccan PowerGrid Corporation" carries the entity
        raw_query = (query.get("sysparm_query") or [""])[0]
        if not entity and "company=" in raw_query:
            entity = raw_query.split("company=", 1)[1]
        if not entity:
            entities = self._entities()
            if not entities:
                return 404, dict(error="no submissions available to serve")
            entity = entities[0]

        table = ""
        if len(parts) >= 4 and parts[:3] == ["api", "now", "table"]:
            table = PLATFORM_TABLES.get(parts[3], parts[3])
        elif parts[:4] == ["rest", "api", "3", "search"]:
            table = "alerts"
        elif parts[:4] == ["rest", "api", "3", "worklog"]:
            table = "investigations"
        else:
            return 404, dict(error=f"unknown endpoint {path}")

        rows = self._tables_for(entity).get(table, [])
        offset = int((query.get("sysparm_offset") or query.get("startAt") or [0])[0])
        limit = int((query.get("sysparm_limit") or query.get("maxResults") or [2000])[0])
        page = rows[offset:offset + limit]

        if self.flavour == "jira":
            if table == "investigations":
                return 200, dict(worklogs=page, total=len(rows))
            return 200, dict(issues=page, total=len(rows), startAt=offset,
                             maxResults=limit)
        return 200, dict(result=page, total_count=len(rows))

    def start(self, port: int = 8787, host: str = "127.0.0.1") -> ThreadingHTTPServer:
        server_self = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (http.server API)
                parsed = urllib.parse.urlparse(self.path)
                status, payload = server_self.handle(parsed.path,
                                                     urllib.parse.parse_qs(parsed.query))
                body = json.dumps(payload, default=str).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # keep the console quiet
                return

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self._server

    def serve_forever(self, port: int = 8787, host: str = "127.0.0.1"):
        print(f"Mock {self.flavour} endpoint serving {self.submissions_dir} "
              f"on http://{host}:{port}")
        print(f"  try: curl 'http://{host}:{port}/api/now/table/incident?sysparm_limit=2'")
        self.start(port=port, host=host)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Poll a SOC REST API into SAT-SA")
    ap.add_argument("--serve", action="store_true",
                    help="run the offline mock endpoint server instead of polling")
    ap.add_argument("--flavour", default="servicenow", choices=["servicenow", "jira"])
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--submissions", default=str(APP_DIR.parent / "submissions"))
    ap.add_argument("--base-url", default="http://127.0.0.1:8787",
                    help="base URL to poll (loopback only unless --allow-remote)")
    ap.add_argument("--entity", default="", help="entity_name from the mock/real endpoint")
    ap.add_argument("--sector", default="Strategic & Public Enterprises")
    ap.add_argument("--tier", default="Tier 3 - Medium")
    ap.add_argument("--allow-remote", action="store_true")
    args = ap.parse_args(argv)

    if args.serve:
        MockEndpointServer(args.submissions, args.flavour).serve_forever(
            port=args.port, host=args.host)
        return

    entity_name = args.entity
    if not entity_name:
        server = MockEndpointServer(args.submissions, args.flavour)
        entities = server._entities()
        if not entities:
            raise SystemExit("No submissions found and no --entity given.")
        entity_name = entities[0]
    profile = ApiProfile(entity_name=entity_name, sector=args.sector,
                         criticality_tier=args.tier)
    result = ingest_from_api(profile, default_endpoints(args.base_url, entity_name, args.flavour),
                            allow_remote=args.allow_remote,
                            progress=lambda m: print(f"  · {m}"))
    print(json.dumps({k: v for k, v in result.items() if k != "files"}, indent=2))
    for warning in result["warnings"]:
        print(f"  ! {warning}")


if __name__ == "__main__":
    main()
