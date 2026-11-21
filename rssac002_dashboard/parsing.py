"""Parse and validate RSSAC002-style YAML reports into a normalised form.

The parser is strict about anything that would make a number wrong (types, ranges, bucket
boundaries) and lenient about things that do not (unknown extra keys, omitted zero fields).
Leniencies are recorded as warnings so they stay visible.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

import yaml

METRICS = ("traffic-volume", "traffic-sizes", "rcode-volume", "unique-sources")

TRAFFIC_VOLUME_FIELDS = tuple(
    f"dns-{proto}-{side}-{ip}"
    for side in ("queries-received", "responses-sent")
    for proto in ("udp", "tcp")
    for ip in ("ipv4", "ipv6")
)
UNIQUE_SOURCES_FIELDS = ("num-sources-ipv4", "num-sources-ipv6", "num-sources-ipv6-aggregate")

# Histogram name -> lower bound of its open-ended last bucket.
SIZE_HISTOGRAMS = {
    "udp-request-sizes": 288,
    "tcp-request-sizes": 288,
    "udp-response-sizes": 4096,
    "tcp-response-sizes": 4096,
}
BUCKET_WIDTH = 16

# -1 is not an RSSAC002 rcode. The ENTRADA-based generator uses it for queries without a
# matched response, so it is accepted and shown as "No response".
# The upper end is the 16-bit RCODE space of TSIG and TKEY (RFC 6895), which some root
# servers report, not only the 12-bit space of ordinary extended RCODEs.
RCODE_MIN, RCODE_MAX = -1, 65535

COMMON_FIELDS = ("version", "service", "start-period", "end-period", "metric")
MAX_REPORT_BYTES = 2 * 1024 * 1024

# libyaml parses about ten times faster; it is used when PyYAML was built with it.
_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

_VERSION_RE = re.compile(r"^rssac002v\d+$")
_BUCKET_RE = re.compile(r"^(\d+)-(\d*)$")


class ReportError(ValueError):
    """The report cannot be used. The message says what is wrong."""


@dataclass(frozen=True)
class ParsedReport:
    metric: str
    service: str
    version: str
    start: dt.datetime
    data: dict
    warnings: list[str] = field(default_factory=list)

    @property
    def day(self) -> dt.date:
        return self.start.date()


def _count(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReportError(f"{what} must be an integer, got {value!r}")
    if value < 0:
        raise ReportError(f"{what} must not be negative, got {value}")
    return value


def _start_period(value) -> dt.datetime:
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, dt.date):
        parsed = dt.datetime.combine(value, dt.time())
    elif isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ReportError(f"start-period is not a valid timestamp: {value!r}") from exc
    else:
        raise ReportError(f"start-period is not a valid timestamp: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _traffic_volume(doc: dict, warnings: list[str]) -> dict:
    data = {name: _count(doc.get(name, 0), name) for name in TRAFFIC_VOLUME_FIELDS}
    _warn_unknown(doc, TRAFFIC_VOLUME_FIELDS, warnings)
    return data


def _unique_sources(doc: dict, warnings: list[str]) -> dict:
    """A field that is left out counts as zero; a field explicitly set to null is "not reported".

    Operators blank out a value they know to be wrong (SIDN did for early IPv6 /64 counts).
    Keeping it as None shows a gap for that one value instead of rejecting the whole report.
    """
    data = {}
    for name in UNIQUE_SOURCES_FIELDS:
        if name in doc and doc[name] is None:
            data[name] = None
            warnings.append(f"{name} has no value and is shown as not reported")
        else:
            data[name] = _count(doc.get(name, 0), name)
    _warn_unknown(doc, UNIQUE_SOURCES_FIELDS, warnings)
    return data


def _rcode_volume(doc: dict, warnings: list[str]) -> dict:
    rcodes: dict[str, int] = {}
    for key, value in doc.items():
        if key in COMMON_FIELDS:
            continue
        if isinstance(key, bool) or not isinstance(key, int):
            if isinstance(key, str) and re.fullmatch(r"-?\d+", key):
                key = int(key)
            else:
                warnings.append(f"ignored unknown field {key!r}")
                continue
        if not RCODE_MIN <= key <= RCODE_MAX:
            raise ReportError(f"rcode must be between {RCODE_MIN} and {RCODE_MAX}, got {key}")
        count = _count(value, f"rcode {key}")
        if count:
            rcodes[str(key)] = rcodes.get(str(key), 0) + count
    return {"rcodes": dict(sorted(rcodes.items(), key=lambda item: int(item[0])))}


def _histogram(name: str, raw, open_lower: int, warnings: list[str]) -> list[list[int]]:
    """Counts per 16-byte step, as [lower edge, count] pairs.

    RSSAC002 ends each histogram with an open-ended step (288 bytes and up for queries, 4,096
    for responses). Some operators keep reporting 16-byte steps beyond that point. Those counts
    are added to the open-ended step, so that every operator's histogram has the same shape.
    """
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise ReportError(f"{name} must be a mapping of size buckets to counts")
    buckets: dict[int, int] = {}
    seen: set[str] = set()
    folded = False
    for key, value in raw.items():
        match = _BUCKET_RE.match(key) if isinstance(key, str) else None
        if match is None:
            raise ReportError(f"{name}: {key!r} is not a size bucket")
        lower = int(match.group(1))
        upper = int(match.group(2)) if match.group(2) else None
        if lower % BUCKET_WIDTH:
            raise ReportError(f"{name}: bucket {key!r} does not start on a {BUCKET_WIDTH}-byte boundary")
        if upper is None and lower < open_lower:
            raise ReportError(f"{name}: only a bucket from {open_lower} bytes up may be open-ended, got {key!r}")
        if upper is not None and upper != lower + BUCKET_WIDTH - 1:
            raise ReportError(f"{name}: bucket {key!r} must span exactly {BUCKET_WIDTH} bytes")
        if key in seen:
            raise ReportError(f"{name}: bucket {key!r} appears twice")
        seen.add(key)
        count = _count(value, f"{name} bucket {key}")
        if lower >= open_lower:
            folded = folded or upper is not None or lower > open_lower
            lower = open_lower
        if count:
            buckets[lower] = buckets.get(lower, 0) + count
    if folded:
        warnings.append(f"{name} has steps beyond {open_lower} bytes; they are counted in the open-ended step")
    return [[lower, buckets[lower]] for lower in sorted(buckets)]


def _traffic_sizes(doc: dict, warnings: list[str]) -> dict:
    data = {name: _histogram(name, doc.get(name), open_lower, warnings) for name, open_lower in SIZE_HISTOGRAMS.items()}
    _warn_unknown(doc, SIZE_HISTOGRAMS, warnings)
    return data


def _warn_unknown(doc: dict, known, warnings: list[str]) -> None:
    for key in doc:
        if key not in known and key not in COMMON_FIELDS:
            warnings.append(f"ignored unknown field {key!r}")


_PARSERS = {
    "traffic-volume": _traffic_volume,
    "traffic-sizes": _traffic_sizes,
    "rcode-volume": _rcode_volume,
    "unique-sources": _unique_sources,
}


def parse_report(text: str, expected_metric: str | None = None) -> ParsedReport:
    """Parse one report. Raises ReportError when the report cannot be trusted."""
    if len(text.encode("utf-8", "replace")) > MAX_REPORT_BYTES:
        raise ReportError("report is larger than 2 MB")
    try:
        documents = [doc for doc in yaml.load_all(text, Loader=_Loader) if doc is not None]
    except yaml.YAMLError as exc:
        raise ReportError(f"not valid YAML: {str(exc).splitlines()[0]}") from exc
    if len(documents) != 1 or not isinstance(documents[0], dict):
        raise ReportError("expected exactly one YAML mapping")
    doc = documents[0]

    version = doc.get("version")
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        raise ReportError(f"version must look like 'rssac002v5', got {version!r}")
    service = doc.get("service")
    if not isinstance(service, str) or not service.strip():
        raise ReportError("service is missing")
    metric = doc.get("metric")
    if metric not in _PARSERS:
        raise ReportError(f"unsupported metric {metric!r}")
    if expected_metric is not None and metric != expected_metric:
        raise ReportError(f"file is named as {expected_metric} but contains {metric}")
    if "start-period" not in doc:
        raise ReportError("start-period is missing")
    start = _start_period(doc["start-period"])

    warnings: list[str] = []
    if start.time() != dt.time():
        warnings.append(f"measurement window starts at {start:%H:%M} UTC instead of 00:00")
    data = _PARSERS[metric](doc, warnings)
    return ParsedReport(metric, service.strip(), version, start, data, warnings)
