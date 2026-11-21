"""Where reports come from: a Git repository, an HTTP directory listing or a local directory.

Each kind is a "reader": a small class that can list a directory, read a file and name where a
file came from. To support another way of publishing reports, write a class with the same four
methods as DirReader and add it to READERS at the bottom of this file. Known publishers are
listed in importers.py.

All follow the RSSAC002 publication layout, with the per-nameserver extension used by the
ENTRADA-based generator:

    <root>/info.yaml                                             (optional profile)
    <root>/<yyyy>/<mm>/<metric>/<short-service>-<yyyymmdd>-<metric>.yaml
    <root>/<yyyy>/<mm>/<metric>/nameservers/<short-service>-<server>-<yyyymmdd>-<metric>.yaml
"""

from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx
import yaml

from .parsing import MAX_REPORT_BYTES, METRICS

NAMESERVER_DIR = "nameservers"
NAME_RE = re.compile(r"^\.?[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")
SHORT_SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_FILENAME_RE = re.compile(rf"^(?P<prefix>.+)-(?P<date>\d{{8}})-(?P<metric>{'|'.join(METRICS)})\.ya?ml$")


class SourceError(Exception):
    """The source could not be read. The message is shown to the admin."""


@dataclass(frozen=True)
class ReportFile:
    parts: tuple[str, ...]
    server: str
    metric: str
    day: dt.date


def classify(name: str, short_service: str, metric: str) -> tuple[str, dt.date] | None:
    """Return (server, day) when the file belongs to this service, else None. server '' = all nameservers."""
    match = _FILENAME_RE.match(name)
    if match is None or match["metric"] != metric:
        return None
    prefix = match["prefix"]
    if prefix == short_service:
        server = ""
    elif prefix.startswith(short_service + "-"):
        server = prefix[len(short_service) + 1 :]
    else:
        return None
    try:
        day = dt.datetime.strptime(match["date"], "%Y%m%d").date()
    except ValueError:
        return None
    return server, day


class DirReader:
    kind = "dir"

    def __init__(self, location: str):
        self.root = Path(location)

    def origin(self, parts: tuple[str, ...]) -> str:
        return str(self.root.joinpath(*parts))

    def children(self, parts: tuple[str, ...]) -> tuple[list[str], list[str]]:
        directory = self.root.joinpath(*parts)
        try:
            entries = sorted(directory.iterdir())
        except OSError as exc:
            raise SourceError(f"Cannot read directory {directory}: {exc.strerror or exc}") from exc
        return [e.name for e in entries if e.is_dir()], [e.name for e in entries if e.is_file()]

    def read(self, parts: tuple[str, ...]) -> str:
        path = self.root.joinpath(*parts)
        try:
            if path.stat().st_size > MAX_REPORT_BYTES:
                raise SourceError(f"{path} is larger than 2 MB")
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SourceError(f"Cannot read {path}: {exc}") from exc

    def close(self) -> None:
        pass


class _LinkCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.extend(value for name, value in attrs if name == "href" and value)


class HttpReader:
    kind = "http"

    def __init__(self, location: str, client: httpx.Client | None = None):
        self.root = location.rstrip("/") + "/"
        self.client = client or httpx.Client(
            timeout=20, follow_redirects=True, headers={"User-Agent": "rssac002-dashboard"}
        )

    def origin(self, parts: tuple[str, ...]) -> str:
        return self.root + "/".join(parts)

    def _get(self, url: str, accept: str) -> httpx.Response:
        try:
            response = self.client.get(url, headers={"Accept": accept})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SourceError(f"{url} answered HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise SourceError(f"Cannot reach {url}: {exc}") from exc
        if len(response.content) > MAX_REPORT_BYTES:
            raise SourceError(f"{url} is larger than 2 MB")
        return response

    def children(self, parts: tuple[str, ...]) -> tuple[list[str], list[str]]:
        """Directory and file names linked from a listing page, one level down only."""
        base = self.origin(parts).rstrip("/") + "/"
        collector = _LinkCollector()
        collector.feed(self._get(base, "text/html").text)
        dirs, files = set(), set()
        for href in collector.hrefs:
            target = urljoin(base, href).split("#")[0].split("?")[0]
            if not target.startswith(base):
                continue
            name = unquote(target[len(base) :])
            if not name or name.startswith("."):
                continue
            if name.endswith("/") and "/" not in name[:-1]:
                dirs.add(name[:-1])
            elif "/" not in name:
                files.add(name)
        return sorted(dirs), sorted(files)

    def read(self, parts: tuple[str, ...]) -> str:
        return self._get(self.origin(parts), "application/yaml, text/yaml, text/plain, */*").text

    def close(self) -> None:
        self.client.close()


class NoReader:
    """For sources that are computed rather than fetched: there is nothing to list or read."""

    kind = "aggregate"

    def read(self, parts: tuple[str, ...]) -> str:
        raise SourceError("A computed source has no files")

    def close(self) -> None:
        pass


GIT_HOSTS = ("github.com", "gitlab.com", "codeberg.org")
GIT_REF = "refs/dashboard/latest"
GIT_REFRESH_SECONDS = 600
_git_locks: dict[Path, threading.Lock] = {}
_git_locks_guard = threading.Lock()


class GitReader:
    """Reports kept in a Git repository, such as the ones SIDN and the RSSAC Caucus publish on GitHub.

    The location is the repository's https address, optionally followed by "#" and the folder
    inside it that holds the year folders. The dashboard keeps a bare, shallow copy of the
    repository next to its database and reads files straight from Git's object store, so a
    repository of hundreds of thousands of small files costs one pack file on disk, not a
    checkout. The copy is refreshed at most every ten minutes, and only the default branch is read.
    """

    kind = "git"

    def __init__(self, location: str, cache_dir: Path | str):
        self.url, _, folder = location.partition("#")
        self.prefix = tuple(part for part in folder.split("/") if part)
        slug = re.sub(r"[^a-z0-9.]+", "-", self.url.split("://", 1)[-1].lower()).strip("-")
        self.repo = Path(cache_dir) / slug
        self._batch: subprocess.Popen | None = None
        self._batch_lock = threading.Lock()
        self._refresh()
        if not self.prefix:
            self.prefix = self._find_reports_folder()

    def _git(self, *args: str, cwd: Path | None = None, timeout: int = 900) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except FileNotFoundError as exc:
            raise SourceError("Git is not installed on the dashboard server") from exc
        except subprocess.TimeoutExpired as exc:
            raise SourceError(f"Git did not finish in time for {self.url}") from exc
        if result.returncode:
            reason = (result.stderr.strip().splitlines() or ["unknown error"])[-1]
            raise SourceError(f"Git could not read {self.url}: {reason}")
        return result.stdout

    def _refresh(self) -> None:
        """Clone the repository on first use, and fetch its newest commit when the copy is stale."""
        with _git_locks_guard:
            lock = _git_locks.setdefault(self.repo, threading.Lock())
        with lock:  # several services can share one repository; only one of them fetches
            marker = self.repo / "dashboard-fetched"
            if not (self.repo / "HEAD").exists():
                self.repo.parent.mkdir(parents=True, exist_ok=True)
                self._git("clone", "--quiet", "--bare", "--depth", "1", "--", self.url, str(self.repo))
                self._git("update-ref", GIT_REF, "HEAD", cwd=self.repo)
            elif not marker.exists() or time.time() - marker.stat().st_mtime > GIT_REFRESH_SECONDS:
                self._git("fetch", "--quiet", "--depth", "1", "origin", f"+HEAD:{GIT_REF}", cwd=self.repo)
            else:
                return
            marker.touch()

    def _find_reports_folder(self) -> tuple[str, ...]:
        """Without a folder in the location: the root if it has year folders, else the one subfolder that has."""
        folders, _ = self.children(())
        if any(re.fullmatch(r"\d{4}", name) for name in folders):
            return ()
        holding = [name for name in folders if any(re.fullmatch(r"\d{4}", year) for year in self.children((name,))[0])]
        return (holding[0],) if len(holding) == 1 else ()

    def _path(self, parts: tuple[str, ...]) -> str:
        return "/".join((*self.prefix, *parts))

    def origin(self, parts: tuple[str, ...]) -> str:
        path = self._path(parts)
        if urlsplit(self.url).netloc.lower() == "github.com":
            return f"{self.url}/blob/HEAD/{path}"
        return f"{self.url}#{path}"

    def children(self, parts: tuple[str, ...]) -> tuple[list[str], list[str]]:
        path = self._path(parts)
        try:
            listing = self._git("ls-tree", f"{GIT_REF}:{path}" if path else GIT_REF, cwd=self.repo, timeout=120)
        except SourceError as exc:
            if "Not a valid object name" in str(exc) or "not a tree object" in str(exc):
                raise SourceError(f"There is no folder '{path}' in {self.url}") from exc
            raise
        dirs, files = [], []
        for line in listing.splitlines():
            meta, _, name = line.partition("\t")
            (dirs if meta.split()[1] == "tree" else files).append(name)
        return sorted(dirs), sorted(files)

    def read(self, parts: tuple[str, ...]) -> str:
        path = self._path(parts)
        with self._batch_lock:
            if self._batch is None:
                self._batch = subprocess.Popen(
                    ["git", "cat-file", "--batch"], cwd=self.repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE
                )
            self._batch.stdin.write(f"{GIT_REF}:{path}\n".encode())
            self._batch.stdin.flush()
            header = self._batch.stdout.readline().decode().split()
            if len(header) != 3 or header[1] != "blob":
                raise SourceError(f"There is no file '{path}' in {self.url}")
            content = self._batch.stdout.read(int(header[2]) + 1)[:-1]
        if len(content) > MAX_REPORT_BYTES:
            raise SourceError(f"{path} is larger than 2 MB")
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SourceError(f"{path} is not UTF-8 text") from exc

    def close(self) -> None:
        if self._batch is not None:
            self._batch.stdin.close()
            self._batch.wait(timeout=10)
            self._batch = None


def _git_location(text: str) -> str | None:
    """The canonical location when the text names a Git repository, else None.

    Accepts a repository address, or the address of a folder as copied from the browser on
    GitHub, GitLab or Codeberg (".../tree/main/reports"), which becomes "<repository>#reports".
    """
    explicit = text.lower().startswith("git+")
    parts = urlsplit(text[4:] if explicit else text)
    host = parts.netloc.lower()
    if not (explicit or host in GIT_HOSTS or parts.path.endswith(".git")):
        return None
    if parts.scheme.lower() != "https":
        raise SourceError("A Git repository must be given as an https:// address")
    segments = [segment for segment in parts.path.split("/") if segment]
    folder = parts.fragment.strip("/")
    if host in GIT_HOSTS and len(segments) >= 2:
        rest = segments[2:]
        skip = 2 if rest[:1] == ["tree"] else 3 if rest[:2] in (["src", "branch"], ["-", "tree"]) else None
        if skip and not folder:
            folder = "/".join(rest[skip:])
        segments = segments[:2]
    if not segments:
        raise SourceError("The address does not name a repository")
    url = f"https://{host}/{'/'.join(segments).removesuffix('.git')}"
    return f"{url}#{folder}" if folder else url


def normalize_location(text: str) -> tuple[str, str]:
    """Return (kind, canonical location) for a repository, a URL or a directory path. Raises SourceError."""
    text = text.strip()
    if text.lower().startswith("aggregate:"):
        from .aggregate import members_of, normalize

        location = normalize(text)
        if len(members_of(location)) < 2:
            raise SourceError("An aggregate needs at least two members, such as aggregate:a-root,b-root")
        return "aggregate", location
    if re.match(r"^(git\+)?https?://", text, re.IGNORECASE):
        git = _git_location(text)
        if git:
            return "git", git
        parts = urlsplit(text)
        if not parts.netloc:
            raise SourceError("The URL has no host name")
        path = parts.path
        if path.endswith("/info.yaml"):
            path = path[: -len("info.yaml")]
        return "http", f"{parts.scheme.lower()}://{parts.netloc}{path.rstrip('/')}/"
    if not text:
        raise SourceError("Enter a repository address, a URL or a directory path")
    path = Path(text).expanduser()
    if not path.is_dir():
        raise SourceError(f"{path} is not a directory on the dashboard server")
    return "dir", str(path.resolve())


def make_reader(kind: str, location: str, cache_dir: Path | str | None = None):
    """The reader for a source. `cache_dir` is where Git sources keep their copy of the repository."""
    if kind == "aggregate":
        return NoReader()
    if kind == "git":
        if cache_dir is None:
            raise SourceError("Git sources need a folder to keep the repository in")
        return GitReader(location, cache_dir)
    return READERS[kind](location)


def read_profile(reader) -> dict:
    """Profile fields from info.yaml, or {} when there is none."""
    try:
        info = yaml.safe_load(reader.read(("info.yaml",)))
    except (SourceError, yaml.YAMLError):
        return {}
    if not isinstance(info, dict):
        return {}
    # "name" and "operator" are this dashboard's keys; "tld" and "tld-operator" are the older spellings.
    profile = {
        "name": info.get("name") or info.get("tld"),
        "short_service": info.get("short-service"),
        "operator": info.get("operator") or info.get("tld-operator"),
        "country": info.get("country-iso3166"),
    }
    return {key: str(value).strip() for key, value in profile.items() if value not in (None, "")}


def validate_profile(profile: dict) -> dict:
    """Normalise and check profile fields. Raises SourceError naming the first problem."""
    name = str(profile.get("name") or "").strip().lower()
    # "nl" becomes ".nl"; a full service name such as "b.root-servers.net" is kept as it is.
    if name and "." not in name:
        name = "." + name
    if not NAME_RE.match(name):
        raise SourceError("A name is required, such as '.nl' or 'b.root-servers.net'")
    import_from = str(profile.get("import_from") or "").strip()
    if import_from:
        try:
            import_from = dt.date.fromisoformat(import_from).isoformat()
        except ValueError as exc:
            raise SourceError("'Import from' must be a date like 2025-03-01") from exc
    short_service = str(profile.get("short_service") or "").strip().lower()
    if not SHORT_SERVICE_RE.match(short_service):
        raise SourceError("Short service name is required: the prefix of the report file names, such as 'nl'")
    country = str(profile.get("country") or "").strip().upper()
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        raise SourceError("Country must be a two-letter ISO 3166 code, such as NL")
    operator = str(profile.get("operator") or "").strip()[:200]
    return {
        "name": name,
        "short_service": short_service,
        "operator": operator,
        "country": country,
        "import_from": import_from,
    }


def discover(reader, short_service: str, since: dt.date | None = None) -> Iterator[ReportFile]:
    """Walk the publication layout and yield every report file of this service.

    With `since`, months before that date's month are not visited at all.
    """
    years, _ = reader.children(())
    for year in years:
        if not re.fullmatch(r"\d{4}", year) or (since and int(year) < since.year):
            continue
        months, _ = reader.children((year,))
        for month in months:
            if not re.fullmatch(r"\d{2}", month):
                continue
            if since and (int(year), int(month)) < (since.year, since.month):
                continue
            metric_dirs, _ = reader.children((year, month))
            for metric in metric_dirs:
                if metric not in METRICS:
                    continue
                base = (year, month, metric)
                subdirs, files = reader.children(base)
                listings = [(base, files)]
                if NAMESERVER_DIR in subdirs:
                    listings.append(((*base, NAMESERVER_DIR), reader.children((*base, NAMESERVER_DIR))[1]))
                for directory, names in listings:
                    for name in names:
                        match = classify(name, short_service, metric)
                        if match is not None:
                            yield ReportFile((*directory, name), match[0], metric, match[1])


# The ways reports can be fetched. A new reader class goes here under a name of its own, which is
# then the `kind` of the sources that use it.
READERS = {"dir": DirReader, "http": HttpReader, "git": GitReader}
