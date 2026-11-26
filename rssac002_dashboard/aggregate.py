"""A service that is computed from other services: their reports for a day, added up.

Used for "all root servers together". The rules that make the result a proper total:

- A day is only given when every member has a report for that metric. A sum over fewer
  members would not be the total, and would look like a traffic drop.
- Such a day is kept as a partial sum instead, marked with the members it is without, when at
  least three quarters of the members have a report. The dashboard draws partial sums apart
  from the total and leaves them out of every figure, statistic, check and download. With
  fewer members the day stays a gap: the sum would say little about the total.
- Counts are added: queries and responses, responses per code, messages per size step.
- TLS and QUIC are not part of RSSAC002, and only some members report them. Their counts are
  added up over the members that do. Their size histograms are only given for a day on which
  every member that counts a transport also gives its sizes; a histogram of some of that
  traffic would not be the total's. The fields carry the aggregate's prefix, as operators do
  for fields of their own.
- Unique sources are not added. An address that queries several members would be counted
  once per member, so the total cannot be derived from the members' reports. The aggregate
  carries a unique-sources report without values, which the dashboard shows as "not reported".

The result is stored as ordinary reports, with the YAML it would have been published as,
so every chart, comparison, check and download works on it like on any other service.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math

import yaml

from .parsing import (
    BUCKET_WIDTH,
    EXTRA_SIZE_HISTOGRAMS,
    EXTRA_TRAFFIC_VOLUME_FIELDS,
    EXTRA_TRANSPORTS,
    SIZE_HISTOGRAMS,
    TRAFFIC_VOLUME_FIELDS,
    UNIQUE_SOURCES_FIELDS,
    parse_report,
)
from .store import Store, now

PREFIX = "aggregate:"
ADDITIVE = ("traffic-volume", "rcode-volume", "traffic-sizes")
# A day without every member is given as a partial sum when at least this share of them reported.
MIN_PARTIAL_SHARE = 0.75
NOT_ADDITIVE_NOTE = "unique sources cannot be added up across services, so the aggregate gives none"


def normalize(text: str) -> str:
    """Canonical location of an aggregate: 'aggregate:' and its members' file-name prefixes, sorted."""
    members = sorted({member.strip().lower() for member in text[len(PREFIX) :].split(",") if member.strip()})
    return PREFIX + ",".join(members)


def members_of(location: str) -> list[str]:
    return [member for member in location[len(PREFIX) :].split(",") if member]


def reporting(transport: str, reports: list[dict | None]) -> list[int]:
    """Positions of the members whose traffic-volume or traffic-sizes report has this transport."""
    names = (f"dns-{transport}-", f"{transport}-")
    return [position for position, report in enumerate(reports) if report and any(n.startswith(names) for n in report)]


def sized_transports(sizes: list[dict], volumes: list[dict | None]) -> list[str]:
    """The TLS and QUIC transports whose size histograms can be added up into a total for this day."""
    return [
        transport
        for transport in EXTRA_TRANSPORTS
        if reporting(transport, sizes) and set(reporting(transport, volumes)) <= set(reporting(transport, sizes))
    ]


def combine(metric: str, reports: list[dict], prefix: str = "", sized: list[str] | None = None) -> dict:
    """The body of the aggregate report for one day, in RSSAC002 field names.

    `prefix` goes in front of the TLS and QUIC fields. `sized` names the transports whose size
    histograms are included, see sized_transports.
    """
    if metric == "traffic-volume":
        body = {name: sum(report[name] for report in reports) for name in TRAFFIC_VOLUME_FIELDS}
        for name in EXTRA_TRAFFIC_VOLUME_FIELDS:
            if any(name in report for report in reports):
                body[f"{prefix}-{name}"] = sum(report.get(name, 0) for report in reports)
        return body
    if metric == "rcode-volume":
        codes: dict[int, int] = {}
        for report in reports:
            for code, count in report["rcodes"].items():
                codes[int(code)] = codes.get(int(code), 0) + count
        return dict(sorted(codes.items()))
    if metric == "traffic-sizes":
        wanted = {name: (name, open_lower) for name, open_lower in SIZE_HISTOGRAMS.items()}
        for name, open_lower in EXTRA_SIZE_HISTOGRAMS.items():
            if name.split("-")[0] in (sized or ()):
                wanted[f"{prefix}-{name}"] = (name, open_lower)
        body = {}
        for field, (name, open_lower) in wanted.items():
            steps: dict[int, int] = {}
            for report in reports:
                for lower, count in report.get(name, ()):
                    steps[lower] = steps.get(lower, 0) + count
            body[field] = {
                (f"{lower}-" if lower >= open_lower else f"{lower}-{lower + BUCKET_WIDTH - 1}"): steps[lower]
                for lower in sorted(steps)
            }
        return body
    return dict.fromkeys(UNIQUE_SOURCES_FIELDS)


def render(
    service: str,
    metric: str,
    day: str,
    body: dict,
    members: list[str],
    extra: dict | None = None,
    missing: tuple[str, ...] = (),
) -> str:
    """The aggregate report as YAML, marked as computed.

    `members` are the services added up. `extra` names, per TLS or QUIC transport in the body,
    the members its counts come from. `missing` names the members without a report that day,
    which makes this a partial sum.
    """
    document = {
        "version": "rssac002v5",
        "service": service,
        "start-period": f"{day}T00:00:00Z",
        "metric": metric,
        **body,
    }
    header = "# Computed by the RSSAC002 Analysis Dashboard, not published by an operator.\n"
    if metric == "unique-sources":
        header += (
            f"# Unique sources cannot be added up across services, so no values are given for the total of\n"
            f"# these {len(members)} services: {', '.join(members)}.\n"
        )
    else:
        header += f"# The sum of the {metric} reports of {len(members)} services for this day: {', '.join(members)}.\n"
    if missing:
        header += (
            f"# A partial sum, not the total of {service}: {len(missing)} of its {len(members) + len(missing)} "
            f"services had no {metric} report for this day: {', '.join(missing)}.\n"
        )
    for transport, reporters in (extra or {}).items():
        if not reporters:
            continue
        header += (
            f"# {transport.upper()} is not part of RSSAC002. Its fields here add up what these services report "
            f"in fields of their own: {', '.join(reporters)}.\n"
        )
    return header + yaml.safe_dump(document, sort_keys=False, explicit_start=True)


def run(store: Store, source: dict, scan_id: int) -> dict:
    """Recompute the aggregate from its members' stored reports. Returns the scan counters."""
    counters = {"found": 0, "imported": 0, "updated": 0, "unchanged": 0, "failed": 0}
    store.update_scan(scan_id, status="running")
    members = members_of(source["location"])
    by_prefix = {one["short_service"]: one for one in store.list_sources() if one["kind"] != "aggregate"}
    absent = [member for member in members if member not in by_prefix]
    if absent or not members:
        message = f"Cannot compute: not a source yet: {', '.join(absent)}" if absent else "The aggregate has no members"
        store.update_scan(scan_id, status="failed", finished_at=now(), message=message)
        return counters

    first = source["import_from"] or "0000-01-01"
    last = dt.date.max.isoformat()
    stored = {(metric, day) for server, metric, day in store.stored_hashes(source["id"]) if server == ""}
    produced: set[tuple[str, str]] = set()
    incomplete = dict.fromkeys(ADDITIVE, 0)
    loaded = {
        metric: [store.load_metric(by_prefix[member]["id"], "", metric, first, last) for member in members]
        for metric in ADDITIVE
    }
    partial = dict.fromkeys(ADDITIVE, 0)
    needed = math.ceil(MIN_PARTIAL_SHARE * len(members))
    for metric in ADDITIVE:
        per_member = loaded[metric]
        for day in sorted(set().union(*per_member)):
            counters["found"] += 1
            have = [position for position, by_day in enumerate(per_member) if day in by_day]
            missing = tuple(member for position, member in enumerate(members) if position not in have)
            if missing:
                incomplete[metric] += 1
                if len(have) < needed:
                    continue
                partial[metric] += 1
            present = [members[position] for position in have]
            reports = [per_member[position][day] for position in have]
            sized = []
            if metric == "traffic-sizes":
                sized = sized_transports(reports, [loaded["traffic-volume"][position].get(day) for position in have])
            # The members that the TLS and QUIC numbers of this report come from, for its header.
            shown = EXTRA_TRANSPORTS if metric == "traffic-volume" else sized
            extra = {one: [present[position] for position in reporting(one, reports)] for one in shown}
            keys = [(metric, day)]
            # A day with a total also gets the empty unique-sources report.
            if metric == "traffic-volume" and not missing:
                keys.append(("unique-sources", day))
                counters["found"] += 1
            for one_metric, one_day in keys:
                body = combine(one_metric, reports, source["short_service"], sized)
                own = extra if one_metric == metric else {}
                raw = render(source["name"], one_metric, one_day, body, present, own, missing)
                report = parse_report(raw, expected_metric=one_metric)
                if one_metric == "unique-sources":
                    report = dataclasses.replace(report, warnings=[NOT_ADDITIVE_NOTE])
                counters[store.save_report(source["id"], "", report, raw, source["location"], missing)] += 1
                produced.add((one_metric, one_day))
    # A day that no longer has every member (a member's report was withdrawn) loses its total.
    for metric, day in stored - produced:
        store.delete_report(source["id"], "", metric, day)

    left_out = [f"{metric} on {incomplete[metric] - partial[metric]} days" for metric in ADDITIVE]
    left_out = [text for text in left_out if " on 0 days" not in text]
    kept = [f"{metric} on {count} days" for metric, count in partial.items() if count]
    message = f"Added up {len(members)} services."
    if left_out:
        message += f" Left out, because not every service had a report: {'; '.join(left_out)}."
    if kept:
        message += f" Given as a partial sum, because some services had no report: {'; '.join(kept)}."
    store.update_scan(scan_id, status="done", finished_at=now(), message=message, **counters)
    return counters
