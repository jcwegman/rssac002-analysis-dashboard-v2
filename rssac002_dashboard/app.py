"""The web application: JSON API, republished report tree, and the single-page interface."""

from __future__ import annotations

import asyncio
import datetime as dt
import html
import sqlite3
import tempfile
import threading
import zipfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .analytics import (
    MEASURES,
    build_quality,
    build_series,
    build_size_matrix,
    series_csv,
    with_unnamed_rcodes_summed,
)
from .auth import COOKIE_NAME, REQUEST_HEADER, LoginThrottle, Sessions, verify_password
from .config import Settings
from .importers import IMPORTERS, add_sources, reports_pages
from .limits import RateLimiter
from .parsing import METRICS
from .scanner import ScanInProgress, Scanner
from .sources import NAMESERVER_DIR, SourceError, make_reader, normalize_location, read_profile, validate_profile
from .store import Store

STATIC_DIR = Path(__file__).parent / "static"
MAX_RANGE_DAYS = 3660
ARCHIVE_COST = 40  # a zip download, in ordinary requests
MAX_ARCHIVES_AT_ONCE = 2
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


class LoginBody(BaseModel):
    password: str


class SourceBody(BaseModel):
    location: str
    name: str | None = None
    short_service: str | None = None
    operator: str | None = None
    country: str | None = None
    import_from: str | None = None


class ImportBody(BaseModel):
    import_from: str | None = None


class EventBody(BaseModel):
    source_id: int | None = None
    day: str
    label: str


class ScanBody(BaseModel):
    mode: str = "new"


def _date(value: str | None, name: str) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, f"'{name}' must be a date like 2026-05-21") from exc


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    store = Store(settings.db_path)
    store.close_interrupted_scans()
    scanner = Scanner(store)
    sessions = Sessions(settings.session_secret, settings.session_hours * 3600)
    throttle = LoginThrottle()

    async def scan_periodically():
        while True:
            await asyncio.to_thread(scanner.start_all, "new")
            await asyncio.sleep(settings.scan_interval_hours * 3600)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(scan_periodically()) if settings.scan_interval_hours > 0 else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            scanner.shutdown()

    app = FastAPI(
        title="RSSAC002 Analysis Dashboard", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.state.store, app.state.scanner = store, scanner

    app.add_middleware(GZipMiddleware, minimum_size=2000)

    limiter = RateLimiter(settings.rate_limit_per_minute)
    archive_slots = threading.BoundedSemaphore(MAX_ARCHIVES_AT_ONCE)

    def client_of(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "") if settings.trust_proxy else ""
        return forwarded.split(",")[-1].strip() or (request.client.host if request.client else "unknown")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        path = request.url.path
        if path.startswith(("/api/", "/rssac002/")):
            wait = limiter.wait_for(client_of(request), ARCHIVE_COST if path == "/api/archive" else 1)
            if wait:
                seconds = int(wait) + 1
                detail = f"Too many requests from your address. Try again in {seconds} seconds."
                return JSONResponse({"detail": detail}, status_code=429, headers={"Retry-After": str(seconds)})
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith("/static/"):
            # Always revalidate, so an upgraded dashboard never runs with stale scripts.
            response.headers["Cache-Control"] = "no-cache"
        return response

    def is_admin(request: Request) -> bool:
        return bool(settings.admin_password_hash) and sessions.valid(request.cookies.get(COOKIE_NAME))

    def require_admin(request: Request) -> None:
        if not is_admin(request):
            raise HTTPException(401, "Sign in as admin first")
        if request.method not in ("GET", "HEAD") and request.headers.get(REQUEST_HEADER) != "1":
            raise HTTPException(403, "Request did not come from the dashboard")

    def source_or_404(source_id: int) -> dict:
        source = store.get_source(source_id)
        if source is None:
            raise HTTPException(404, "Unknown source")
        return source

    # --- public API --------------------------------------------------------------------

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/api/meta")
    def meta(request: Request):
        admin = is_admin(request)
        sources = store.list_sources()
        pages = reports_pages()
        for source in sources:
            source["reports_page"] = pages.get((source["location"], source["short_service"]), "")
            if not admin:
                del source["location"]
        return {
            "version": __version__,
            "sources": sources,
            "metrics": METRICS,
            "measures": {key: {"label": label, "unit": unit} for key, (label, unit) in MEASURES.items()},
            "admin": {"configured": bool(settings.admin_password_hash), "signed_in": admin},
            "limits": {"archive_reports": settings.max_archive_reports},
        }

    def period(first: str | None, last: str | None) -> tuple[dt.date, dt.date]:
        last_day = _date(last, "to") or dt.datetime.now(dt.UTC).date()
        first_day = _date(first, "from") or last_day - dt.timedelta(days=29)
        if first_day > last_day:
            raise HTTPException(422, "'from' must not be after 'to'")
        if (last_day - first_day).days >= MAX_RANGE_DAYS:
            raise HTTPException(422, "The date range is limited to ten years")
        return first_day, last_day

    @app.get("/api/series")
    def series(
        source: int,
        server: str = "",
        first: str | None = Query(None, alias="from"),
        last: str | None = Query(None, alias="to"),
        parts: str = "all",
    ):
        source_or_404(source)
        data = build_series(store, source, server, *period(first, last), derived_only=parts == "derived")
        return with_unnamed_rcodes_summed(data)

    @app.get("/api/series.csv")
    def series_as_csv(
        source: int,
        server: str = "",
        first: str | None = Query(None, alias="from"),
        last: str | None = Query(None, alias="to"),
    ):
        """The same daily series as a CSV file, for analysis in other tools."""
        profile = source_or_404(source)
        first_day, last_day = period(first, last)
        scope = f"{profile['short_service']}-{server}" if server else profile["short_service"]
        name = f"{scope}-rssac002-{first_day:%Y%m%d}-{last_day:%Y%m%d}.csv"
        return Response(
            series_csv(build_series(store, source, server, first_day, last_day)),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/api/sizes")
    def sizes(
        source: int,
        server: str = "",
        first: str | None = Query(None, alias="from"),
        last: str | None = Query(None, alias="to"),
    ):
        source_or_404(source)
        return build_size_matrix(store, source, server, *period(first, last))

    @app.get("/api/quality")
    def quality(source: int, first: str | None = Query(None, alias="from"), last: str | None = Query(None, alias="to")):
        source_or_404(source)
        servers = next(one["servers"] for one in store.list_sources() if one["id"] == source)
        return build_quality(store, source, servers, *period(first, last))

    @app.get("/api/events")
    def events(source: int | None = None):
        return {"events": store.list_events(source)}

    @app.post("/api/events", dependencies=[Depends(require_admin)], status_code=201)
    def add_event(body: EventBody):
        day, label = _date(body.day, "day"), body.label.strip()
        if day is None or not label or len(label) > 200:
            raise HTTPException(422, "An event needs a day and a description of at most 200 characters")
        if body.source_id is not None:
            source_or_404(body.source_id)
        return {"id": store.add_event(body.source_id, day.isoformat(), label)}

    @app.delete("/api/events/{event_id}", dependencies=[Depends(require_admin)])
    def delete_event(event_id: int):
        if not store.delete_event(event_id):
            raise HTTPException(404, "Unknown event")
        return {"deleted": event_id}

    @app.get("/api/coverage")
    def coverage(source: int, server: str = ""):
        """Number of metrics present per day, as one digit per day from the first report to the last."""
        source_or_404(source)
        days = store.coverage(source, server)
        if not days:
            return {"first": None, "last": None, "counts": ""}
        first, last = dt.date.fromisoformat(min(days)), dt.date.fromisoformat(max(days))
        counts = "".join(
            str(len(days.get((first + dt.timedelta(days=offset)).isoformat(), ())))
            for offset in range((last - first).days + 1)
        )
        return {"first": first.isoformat(), "last": last.isoformat(), "counts": counts}

    @app.get("/api/reports")
    def reports(
        source: int,
        server: str = "",
        metric: str | None = None,
        first: str | None = Query(None, alias="from"),
        last: str | None = Query(None, alias="to"),
    ):
        source_or_404(source)
        first_day, last_day = _date(first, "from"), _date(last, "to")
        rows = store.list_reports(
            source_id=source,
            server=server,
            metric=metric or None,
            first=first_day and first_day.isoformat(),
            last=last_day and last_day.isoformat(),
            limit=settings.max_archive_reports + 1,
        )
        return {"reports": rows}

    @app.get("/api/reports/{report_id}")
    def report_file(report_id: int, download: bool = False):
        report = store.get_report(report_id)
        if report is None:
            raise HTTPException(404, "Unknown report")
        headers = {"Content-Disposition": f'attachment; filename="{report["filename"]}"'} if download else {}
        return Response(report["raw"], media_type="text/plain; charset=utf-8", headers=headers)

    @app.get("/api/archive")
    def archive(
        source: int,
        server: str | None = None,
        metric: str | None = None,
        first: str | None = Query(None, alias="from"),
        last: str | None = Query(None, alias="to"),
    ):
        """A zip of the stored reports, laid out like the RSSAC002 publication tree."""
        profile = source_or_404(source)
        first_day, last_day = _date(first, "from"), _date(last, "to")
        wanted = {
            "source_id": source,
            "server": server,
            "metric": metric or None,
            "first": first_day and first_day.isoformat(),
            "last": last_day and last_day.isoformat(),
        }
        # Count first, without the files' contents, so an oversized request costs next to nothing.
        matching = len(store.list_reports(**wanted, limit=settings.max_archive_reports + 1))
        if not matching:
            raise HTTPException(404, "No reports match")
        if matching > settings.max_archive_reports:
            raise HTTPException(413, f"More than {settings.max_archive_reports:,} reports match. Narrow the period.")
        # Building a zip holds every file in memory for a moment, so only a few run side by side.
        if not archive_slots.acquire(blocking=False):
            raise HTTPException(429, "Other downloads are being prepared. Try again in a moment.")
        try:
            rows = store.list_reports(**wanted, with_raw=True)
            buffer = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive_file:
                for row in rows:
                    folder = f"{row['day'][:4]}/{row['day'][5:7]}/{row['metric']}"
                    if row["server"]:
                        folder += f"/{NAMESERVER_DIR}"
                    archive_file.writestr(f"{folder}/{row['filename']}", row["raw"])
            buffer.seek(0)
        finally:
            archive_slots.release()
        name = f"{profile['short_service']}-rssac002-reports.zip"
        return StreamingResponse(
            buffer, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'}
        )

    # --- admin API ---------------------------------------------------------------------

    @app.post("/api/session")
    def sign_in(body: LoginBody, request: Request, response: Response):
        if not settings.admin_password_hash:
            raise HTTPException(503, "No admin password is configured on the server")
        if request.headers.get(REQUEST_HEADER) != "1":
            raise HTTPException(403, "Request did not come from the dashboard")
        if throttle.blocked():
            raise HTTPException(429, "Too many wrong passwords. Try again in a minute.")
        if not verify_password(body.password, settings.admin_password_hash):
            throttle.record_failure()
            raise HTTPException(401, "Wrong password")
        secure = settings.cookie_secure or request.url.scheme == "https"
        response.set_cookie(
            COOKIE_NAME, sessions.issue(), max_age=sessions.lifetime, httponly=True, samesite="strict", secure=secure
        )
        return {"signed_in": True}

    @app.delete("/api/session")
    def sign_out(response: Response):
        response.delete_cookie(COOKIE_NAME)
        return {"signed_in": False}

    def resolve_source(body: SourceBody) -> tuple[str, str, dict, bool]:
        """Location and profile for a source: info.yaml values, overridden by anything the admin typed."""
        kind, location = normalize_location(body.location)
        reader = make_reader(kind, location, store.path.parent / "repos")
        try:
            published = read_profile(reader)
        finally:
            reader.close()
        typed = {key: value for key, value in body.model_dump(exclude={"location"}).items() if value}
        return kind, location, {**published, **typed}, bool(published)

    @app.post("/api/sources/preview", dependencies=[Depends(require_admin)])
    def preview_source(body: SourceBody):
        try:
            kind, location, profile, has_info = resolve_source(body)
        except SourceError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"kind": kind, "location": location, "profile": profile, "has_info": has_info}

    @app.post("/api/sources", dependencies=[Depends(require_admin)], status_code=201)
    def add_source(body: SourceBody):
        try:
            kind, location, profile, _ = resolve_source(body)
            profile = validate_profile(profile)
        except SourceError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            source_id = store.add_source(kind=kind, location=location, **profile)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "This location is already a source") from exc
        return {"id": source_id, "scan_id": scanner.start(source_id, "new")}

    @app.get("/api/importers")
    def importers():
        """Known publishers, and how many of each one's services are already sources."""
        existing = {(source["location"], source["short_service"]) for source in store.list_sources()}
        return {
            "importers": [
                {
                    "key": importer.key,
                    "title": importer.title,
                    "description": importer.description,
                    "services": [spec["name"] for spec in importer.sources],
                    "added": sum((spec["location"], spec["short_service"]) in existing for spec in importer.sources),
                }
                for importer in IMPORTERS.values()
            ]
        }

    @app.post("/api/importers/{key}", dependencies=[Depends(require_admin)], status_code=202)
    def run_importer(key: str, body: ImportBody):
        """Add a known publisher's services as sources and start importing them."""
        if key not in IMPORTERS:
            raise HTTPException(404, "Unknown importer")
        try:
            ids = add_sources(store, IMPORTERS[key], body.import_from or "")
        except SourceError as exc:
            raise HTTPException(422, str(exc)) from exc
        for source_id in ids:
            with suppress(ScanInProgress):
                scanner.start(source_id, "new")
        return {"sources": ids}

    @app.delete("/api/sources/{source_id}", dependencies=[Depends(require_admin)])
    def delete_source(source_id: int):
        if not store.delete_source(source_id):
            raise HTTPException(404, "Unknown source")
        return {"deleted": source_id}

    @app.get("/api/sources/{source_id}/scans", dependencies=[Depends(require_admin)])
    def scans(source_id: int):
        source_or_404(source_id)
        return {"scans": store.list_scans(source_id)}

    @app.post("/api/sources/{source_id}/scan", dependencies=[Depends(require_admin)], status_code=202)
    def start_scan(source_id: int, body: ScanBody):
        if body.mode not in ("new", "full"):
            raise HTTPException(422, "mode must be 'new' or 'full'")
        try:
            return {"scan_id": scanner.start(source_id, body.mode)}
        except KeyError as exc:
            raise HTTPException(404, "Unknown source") from exc
        except ScanInProgress as exc:
            raise HTTPException(409, "A scan of this source is already running") from exc

    # --- republished report tree ---------------------------------------------------------

    def listing(title: str, names: list[str]) -> HTMLResponse:
        items = "".join(f'<li><a href="{html.escape(name, quote=True)}">{html.escape(name)}</a></li>' for name in names)
        page = f"<!doctype html><title>{html.escape(title)}</title><h1>{html.escape(title)}</h1><ul>{items}</ul>"
        return HTMLResponse(page)

    @app.get("/rssac002/{path:path}")
    def republished(path: str, request: Request):
        """Stored reports under the RSSAC002 publication layout, so other tools can fetch them from here."""
        if path and not path.endswith("/") and not path.endswith((".yaml", ".yml")):
            return Response(status_code=307, headers={"Location": str(request.url.path) + "/"})
        parts = [part for part in path.split("/") if part]
        title = "/rssac002/" + "".join(f"{part}/" for part in parts if not part.endswith((".yaml", ".yml")))
        with store.connect() as conn:

            def distinct(expression: str, where: str = "1", params: tuple = ()) -> list[str]:
                sql = f"SELECT DISTINCT {expression} FROM reports WHERE {where} ORDER BY 1"
                return [row[0] for row in conn.execute(sql, params)]

            if len(parts) == 0:
                return listing(title, [f"{year}/" for year in distinct("substr(day, 1, 4)")])
            if len(parts) == 1:
                months = distinct("substr(day, 6, 2)", "substr(day, 1, 4) = ?", (parts[0],))
                return listing(title, [f"{month}/" for month in months]) if months else Response(status_code=404)
            month = f"{parts[0]}-{parts[1]}-%"
            if len(parts) == 2:
                metrics = distinct("metric", "day LIKE ?", (month,))
                return listing(title, [f"{metric}/" for metric in metrics]) if metrics else Response(status_code=404)
        in_month = store.list_reports(metric=parts[2], first=month[:-1] + "01", last=month[:-1] + "31")
        per_server = len(parts) > 3 and parts[3] == NAMESERVER_DIR
        rows = [row for row in in_month if bool(row["server"]) == per_server]
        tail = parts[4:] if per_server else parts[3:]
        if not tail:
            names = sorted(row["filename"] for row in rows)
            if not per_server and any(row["server"] for row in in_month):
                names.insert(0, f"{NAMESERVER_DIR}/")
            return listing(title, names) if names else Response(status_code=404)
        for row in rows:
            if len(tail) == 1 and row["filename"] == tail[0]:
                return Response(store.get_report(row["id"])["raw"], media_type="text/yaml; charset=utf-8")
        raise HTTPException(404, "No such report")

    # --- interface -----------------------------------------------------------------------

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app
