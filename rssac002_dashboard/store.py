"""SQLite storage: sources, imported reports and scan history.

One row per report. The original YAML is kept verbatim so downloads and the republished
tree return exactly what the operator published; `data` holds the validated, normalised
values the charts are computed from.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .parsing import ParsedReport

SCHEMA_VERSION = 2

SOURCES_TABLE = """CREATE TABLE IF NOT EXISTS sources (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    short_service TEXT NOT NULL,
    operator      TEXT NOT NULL DEFAULT '',
    country       TEXT NOT NULL DEFAULT '',
    kind          TEXT NOT NULL,
    location      TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    import_from   TEXT NOT NULL DEFAULT '',
    UNIQUE (location, short_service)
);
"""

SCHEMA = (
    SOURCES_TABLE
    + """
CREATE TABLE IF NOT EXISTS reports (
    id           INTEGER PRIMARY KEY,
    source_id    INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    server       TEXT NOT NULL DEFAULT '',
    metric       TEXT NOT NULL,
    day          TEXT NOT NULL,
    start_period TEXT NOT NULL,
    service      TEXT NOT NULL,
    version      TEXT NOT NULL,
    data         TEXT NOT NULL,
    raw          TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    origin       TEXT NOT NULL,
    warnings     TEXT NOT NULL DEFAULT '[]',
    fetched_at   TEXT NOT NULL,
    UNIQUE (source_id, server, metric, day)
);
CREATE INDEX IF NOT EXISTS reports_by_day ON reports (day, metric);
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY,
    source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE,
    day       TEXT NOT NULL,
    label     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scans (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    mode        TEXT NOT NULL CHECK (mode IN ('new', 'full')),
    status      TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    found       INTEGER NOT NULL DEFAULT 0,
    imported    INTEGER NOT NULL DEFAULT 0,
    updated     INTEGER NOT NULL DEFAULT 0,
    unchanged   INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    message     TEXT NOT NULL DEFAULT '',
    errors      TEXT NOT NULL DEFAULT '[]'
);
"""
)


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def report_filename(short_service: str, server: str, day: str, metric: str) -> str:
    prefix = f"{short_service}-{server}" if server else short_service
    return f"{prefix}-{day.replace('-', '')}-{metric}.yaml"


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._upgrade()
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _upgrade(self) -> None:
        """Bring a database made by an earlier version up to the current layout, keeping its data.

        Version 2 renamed sources.tld to sources.name, allowed Git sources, and allowed several
        services to share one location. SQLite cannot change a table's constraints in place, so
        the table is rebuilt. Foreign keys are off while that happens: with them on, dropping
        the old table would delete every report that refers to it.
        """
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(sources)")]
            if "tld" not in columns:
                return
            import_from = "import_from" if "import_from" in columns else "''"
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN")
            conn.execute(SOURCES_TABLE.replace("IF NOT EXISTS sources", "sources_upgraded"))
            conn.execute(
                "INSERT INTO sources_upgraded (id, name, short_service, operator, country, kind, location, "
                f"created_at, import_from) SELECT id, tld, short_service, operator, country, kind, location, "
                f"created_at, {import_from} FROM sources"
            )
            conn.execute("DROP TABLE sources")
            conn.execute("ALTER TABLE sources_upgraded RENAME TO sources")
            conn.execute("COMMIT")
        finally:
            conn.close()

    def close_interrupted_scans(self) -> None:
        """Mark scans left 'running' by a stopped server as failed. Call once when the server starts.

        Not done on every open of the database: a command-line scan in another process is
        still running and must not be touched by, say, listing the sources.
        """
        with self.connect() as conn:
            conn.execute(
                "UPDATE scans SET status = 'failed', finished_at = ?, message = 'Interrupted by a restart' "
                "WHERE status IN ('queued', 'running')",
                (now(),),
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # --- sources ---------------------------------------------------------------------

    def add_source(
        self,
        *,
        name: str,
        short_service: str,
        operator: str,
        country: str,
        kind: str,
        location: str,
        import_from: str = "",
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO sources (name, short_service, operator, country, kind, location, import_from, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, short_service, operator, country, kind, location, import_from, now()),
            )
            return cursor.lastrowid

    def delete_source(self, source_id: int) -> bool:
        with self.connect() as conn:
            return conn.execute("DELETE FROM sources WHERE id = ?", (source_id,)).rowcount > 0

    def get_source(self, source_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
            return dict(row) if row else None

    def list_sources(self) -> list[dict]:
        """Sources with what has been imported for each: date span, report count, nameservers."""
        with self.connect() as conn:
            sources = [dict(row) for row in conn.execute("SELECT * FROM sources ORDER BY name, id")]
            for source in sources:
                stats = conn.execute(
                    "SELECT MIN(day) AS first_day, MAX(day) AS last_day, COUNT(*) AS reports "
                    "FROM reports WHERE source_id = ?",
                    (source["id"],),
                ).fetchone()
                source.update(dict(stats))
                source["servers"] = [
                    row[0]
                    for row in conn.execute(
                        "SELECT DISTINCT server FROM reports WHERE source_id = ? AND server != '' ORDER BY server",
                        (source["id"],),
                    )
                ]
                last_scan = conn.execute(
                    "SELECT status, started_at, finished_at FROM scans WHERE source_id = ? ORDER BY id DESC LIMIT 1",
                    (source["id"],),
                ).fetchone()
                source["last_scan"] = dict(last_scan) if last_scan else None
            return sources

    # --- reports ---------------------------------------------------------------------

    def stored_hashes(self, source_id: int) -> dict[tuple[str, str, str], str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT server, metric, day, sha256 FROM reports WHERE source_id = ?", (source_id,))
            return {(row["server"], row["metric"], row["day"]): row["sha256"] for row in rows}

    def save_report(self, source_id: int, server: str, report: ParsedReport, raw: str, origin: str) -> str:
        """Insert or replace one report. Returns 'imported', 'updated' or 'unchanged'."""
        digest = hashlib.sha256(raw.encode()).hexdigest()
        key = (source_id, server, report.metric, report.day.isoformat())
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT sha256 FROM reports WHERE source_id = ? AND server = ? AND metric = ? AND day = ?", key
            ).fetchone()
            if existing and existing["sha256"] == digest:
                return "unchanged"
            conn.execute(
                "INSERT INTO reports (source_id, server, metric, day, start_period, service, version, data, raw, "
                "sha256, origin, warnings, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (source_id, server, metric, day) DO UPDATE SET start_period = excluded.start_period, "
                "service = excluded.service, version = excluded.version, data = excluded.data, raw = excluded.raw, "
                "sha256 = excluded.sha256, origin = excluded.origin, warnings = excluded.warnings, "
                "fetched_at = excluded.fetched_at",
                (
                    *key,
                    report.start.isoformat(),
                    report.service,
                    report.version,
                    json.dumps(report.data, separators=(",", ":")),
                    raw,
                    digest,
                    origin,
                    json.dumps(report.warnings),
                    now(),
                ),
            )
            return "updated" if existing else "imported"

    def load_metric(self, source_id: int, server: str, metric: str, first: str, last: str) -> dict[str, dict]:
        """Normalised data per day for one metric, between two ISO dates inclusive."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT day, data FROM reports WHERE source_id = ? AND server = ? AND metric = ? "
                "AND day BETWEEN ? AND ?",
                (source_id, server, metric, first, last),
            )
            return {row["day"]: json.loads(row["data"]) for row in rows}

    def coverage(self, source_id: int, server: str) -> dict[str, list[str]]:
        """Which metrics exist for each day."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT day, metric FROM reports WHERE source_id = ? AND server = ? ORDER BY day", (source_id, server)
            )
            days: dict[str, list[str]] = {}
            for row in rows:
                days.setdefault(row["day"], []).append(row["metric"])
            return days

    def list_reports(
        self,
        *,
        source_id: int | None = None,
        server: str | None = None,
        metric: str | None = None,
        first: str | None = None,
        last: str | None = None,
        with_raw: bool = False,
        limit: int | None = None,
    ) -> list[dict]:
        clauses, params = [], []
        for column, value in (("r.source_id", source_id), ("r.server", server), ("r.metric", metric)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if first:
            clauses.append("r.day >= ?")
            params.append(first)
        if last:
            clauses.append("r.day <= ?")
            params.append(last)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        columns = "r.id, r.source_id, r.server, r.metric, r.day, r.service, r.origin, r.warnings, r.fetched_at, "
        columns += "s.short_service, s.name" + (", r.raw" if with_raw else "")
        sql = f"SELECT {columns} FROM reports r JOIN sources s ON s.id = r.source_id {where} "
        sql += "ORDER BY r.day DESC, r.metric, r.server"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self.connect() as conn:
            reports = [dict(row) for row in conn.execute(sql, params)]
        for report in reports:
            report["warnings"] = json.loads(report["warnings"])
            report["filename"] = report_filename(
                report["short_service"], report["server"], report["day"], report["metric"]
            )
        return reports

    def get_report(self, report_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT r.*, s.short_service FROM reports r JOIN sources s ON s.id = r.source_id WHERE r.id = ?",
                (report_id,),
            ).fetchone()
        if row is None:
            return None
        report = dict(row)
        report["filename"] = report_filename(report["short_service"], report["server"], report["day"], report["metric"])
        return report

    def delete_report(self, source_id: int, server: str, metric: str, day: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM reports WHERE source_id = ? AND server = ? AND metric = ? AND day = ?",
                (source_id, server, metric, day),
            )

    def load_metric_by_server(self, source_id: int, metric: str, first: str, last: str) -> dict[str, dict[str, dict]]:
        """Normalised data per day and per server ('' is the combined report), for consistency checks."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT day, server, data FROM reports WHERE source_id = ? AND metric = ? AND day BETWEEN ? AND ?",
                (source_id, metric, first, last),
            )
            days: dict[str, dict[str, dict]] = {}
            for row in rows:
                days.setdefault(row["day"], {})[row["server"]] = json.loads(row["data"])
            return days

    def reports_with_notes(self, source_id: int, first: str, last: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT day, metric, server, warnings FROM reports WHERE source_id = ? AND day BETWEEN ? AND ? "
                "AND warnings != '[]' ORDER BY day DESC, metric, server",
                (source_id, first, last),
            )
            return [{**dict(row), "warnings": json.loads(row["warnings"])} for row in rows]

    # --- events ----------------------------------------------------------------------

    def add_event(self, source_id: int | None, day: str, label: str) -> int:
        with self.connect() as conn:
            return conn.execute(
                "INSERT INTO events (source_id, day, label) VALUES (?, ?, ?)", (source_id, day, label)
            ).lastrowid

    def delete_event(self, event_id: int) -> bool:
        with self.connect() as conn:
            return conn.execute("DELETE FROM events WHERE id = ?", (event_id,)).rowcount > 0

    def list_events(self, source_id: int | None = None) -> list[dict]:
        """Every event, or with a source: its own events plus those that apply to all sources."""
        where, params = (
            ("WHERE source_id IS NULL OR source_id = ?", (source_id,)) if source_id is not None else ("", ())
        )
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(f"SELECT * FROM events {where} ORDER BY day, id", params)]

    # --- scans -----------------------------------------------------------------------

    def create_scan(self, source_id: int, mode: str, status: str = "running") -> int:
        with self.connect() as conn:
            return conn.execute(
                "INSERT INTO scans (source_id, mode, status, started_at) VALUES (?, ?, ?, ?)",
                (source_id, mode, status, now()),
            ).lastrowid

    def update_scan(self, scan_id: int, **fields) -> None:
        if "errors" in fields:
            fields["errors"] = json.dumps(fields["errors"])
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self.connect() as conn:
            conn.execute(f"UPDATE scans SET {assignments} WHERE id = ?", (*fields.values(), scan_id))

    def list_scans(self, source_id: int, limit: int = 10) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM scans WHERE source_id = ? ORDER BY id DESC LIMIT ?", (source_id, limit))
            scans = [dict(row) for row in rows]
        for scan in scans:
            scan["errors"] = json.loads(scan["errors"])
        return scans

    def active_scan(self, source_id: int) -> int | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM scans WHERE source_id = ? AND status IN ('queued', 'running') LIMIT 1", (source_id,)
            ).fetchone()
            return row["id"] if row else None
