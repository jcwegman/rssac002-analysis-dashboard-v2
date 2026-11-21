"""Import reports from a source into the store, in the foreground (CLI) or the background (web)."""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

from . import aggregate
from .parsing import ReportError, parse_report
from .sources import SourceError, discover, make_reader
from .store import Store, now

logger = logging.getLogger(__name__)
MAX_STORED_ERRORS = 200
PROGRESS_EVERY = 25
HTTP_WORKERS = 6


class ScanInProgress(Exception):
    pass


def run_scan(store: Store, source: dict, mode: str, scan_id: int, reader=None) -> dict:
    """Scan one source and record the outcome on the scan row. Returns the final counters.

    mode 'new'  fetches only reports that are not stored yet, looking at recent months only.
    mode 'full' re-fetches everything and replaces reports whose content changed.
    """
    if source["kind"] == "aggregate":  # computed from other sources' stored reports, nothing to fetch
        return aggregate.run(store, source, scan_id)
    counters = {"found": 0, "imported": 0, "updated": 0, "unchanged": 0, "failed": 0}
    errors: list[dict] = []
    store.update_scan(scan_id, status="running")
    try:
        reader = reader or make_reader(source["kind"], source["location"], store.path.parent / "repos")
    except SourceError as exc:
        store.update_scan(scan_id, status="failed", finished_at=now(), message=str(exc))
        return counters
    try:
        stored = store.stored_hashes(source["id"])
        since = None
        if mode == "new" and stored and source["kind"] == "http":
            # Listing a web endpoint costs a request per directory, so only recent months are
            # visited: from a month before the newest stored report, to catch late or corrected
            # files. A local folder is cheap to list and is always walked completely, which also
            # picks up back-filled months and files that failed to import earlier.
            since = dt.date.fromisoformat(max(day for _, _, day in stored)) - dt.timedelta(days=31)
        # A source can be limited to reports from a given day on, for endpoints with a long history.
        floor = dt.date.fromisoformat(source["import_from"]) if source.get("import_from") else None
        if floor and (since is None or since < floor):
            since = floor
        todo = []
        for found in discover(reader, source["short_service"], since):
            if floor and found.day < floor:
                continue
            counters["found"] += 1
            if mode == "new" and (found.server, found.metric, found.day.isoformat()) in stored:
                counters["unchanged"] += 1
            else:
                todo.append(found)
        store.update_scan(scan_id, **counters)

        def fetch(found):
            origin = reader.origin(found.parts)
            try:
                return found, origin, reader.read(found.parts), None
            except SourceError as exc:
                return found, origin, None, exc

        # Web endpoints are fetched a few files at a time: each file is small and most of the
        # wait is the connection, so this is several times faster without loading the server much.
        with ThreadPoolExecutor(max_workers=HTTP_WORKERS if source["kind"] == "http" else 1) as pool:
            for done, (found, origin, raw, error) in enumerate(pool.map(fetch, todo), 1):
                try:
                    if error is not None:
                        raise error
                    report = parse_report(raw, expected_metric=found.metric)
                    if report.day != found.day:
                        raise ReportError(f"file name says {found.day} but start-period is {report.start.isoformat()}")
                    counters[store.save_report(source["id"], found.server, report, raw, origin)] += 1
                except (ReportError, SourceError) as exc:
                    counters["failed"] += 1
                    if len(errors) < MAX_STORED_ERRORS:
                        errors.append({"origin": origin, "error": str(exc)})
                if done % PROGRESS_EVERY == 0:
                    store.update_scan(scan_id, **counters)
        message = "" if counters["found"] else "No report files found for this short service name"
        store.update_scan(scan_id, status="done", finished_at=now(), message=message, errors=errors, **counters)
    except SourceError as exc:
        store.update_scan(scan_id, status="failed", finished_at=now(), message=str(exc), errors=errors, **counters)
    except Exception as exc:  # noqa: BLE001 - a scan must never leave its row stuck on 'running'
        logger.exception("Scan %s failed", scan_id)
        store.update_scan(
            scan_id, status="failed", finished_at=now(), message=f"Unexpected error: {exc}", errors=errors, **counters
        )
    finally:
        reader.close()
    return counters


class Scanner:
    """Runs scans on worker threads so the web request that starts one returns immediately."""

    def __init__(self, store: Store):
        self.store = store
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="scan")
        self._lock = threading.Lock()

    def start(self, source_id: int, mode: str) -> int:
        with self._lock:
            source = self.store.get_source(source_id)
            if source is None:
                raise KeyError(source_id)
            if self.store.active_scan(source_id) is not None:
                raise ScanInProgress
            scan_id = self.store.create_scan(source_id, mode, status="queued")
        self._pool.submit(run_scan, self.store, source, mode, scan_id)
        return scan_id

    def start_all(self, mode: str = "new") -> None:
        """Scan every source. Aggregates are recomputed once the sources they add up have finished."""
        fetched, computed = [], []
        for source in self.store.list_sources():
            (computed if source["kind"] == "aggregate" else fetched).append(source)
        waiting = []
        for source in fetched:
            with suppress(ScanInProgress, KeyError):
                self.start(source["id"], mode)
                waiting.append(source["id"])
        if computed:
            threading.Thread(target=self._compute_after, args=(waiting, computed, mode), daemon=True).start()

    def _compute_after(self, source_ids: list[int], aggregates: list[dict], mode: str) -> None:
        while any(self.store.active_scan(source_id) is not None for source_id in source_ids):
            time.sleep(1)
        for source in aggregates:
            with suppress(ScanInProgress, KeyError):
                self.start(source["id"], mode)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
