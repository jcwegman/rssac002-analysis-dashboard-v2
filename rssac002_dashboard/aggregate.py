"""A service that is computed from other services: their reports for a day, added up.

Used for "all root servers together". The rules that make the result a proper total:

- A day is only given when every member has a report for that metric. A sum over fewer
  members would not be the total, and would look like a traffic drop. Such days stay gaps.
- Counts are added: queries and responses, responses per code, messages per size step.
- Unique sources are not added. An address that queries several members would be counted
  once per member, so the total cannot be derived from the members' reports. The aggregate
  carries a unique-sources report without values, which the dashboard shows as "not reported".

The result is stored as ordinary reports, with the YAML it would have been published as,
so every chart, comparison, check and download works on it like on any other service.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import yaml

from .parsing import BUCKET_WIDTH, SIZE_HISTOGRAMS, UNIQUE_SOURCES_FIELDS, parse_report
from .store import Store, now

PREFIX = "aggregate:"
ADDITIVE = ("traffic-volume", "rcode-volume", "traffic-sizes")
NOT_ADDITIVE_NOTE = "unique sources cannot be added up across services, so the aggregate gives none"


def normalize(text: str) -> str:
    """Canonical location of an aggregate: 'aggregate:' and its members' file-name prefixes, sorted."""
    members = sorted({member.strip().lower() for member in text[len(PREFIX) :].split(",") if member.strip()})
    return PREFIX + ",".join(members)


def members_of(location: str) -> list[str]:
    return [member for member in location[len(PREFIX) :].split(",") if member]


def combine(metric: str, reports: list[dict]) -> dict:
    """The body of the aggregate report for one day, in RSSAC002 field names."""
    if metric == "traffic-volume":
        return {name: sum(report[name] for report in reports) for name in reports[0]}
    if metric == "rcode-volume":
        codes: dict[int, int] = {}
        for report in reports:
            for code, count in report["rcodes"].items():
                codes[int(code)] = codes.get(int(code), 0) + count
        return dict(sorted(codes.items()))
    if metric == "traffic-sizes":
        body = {}
        for name, open_lower in SIZE_HISTOGRAMS.items():
            steps: dict[int, int] = {}
            for report in reports:
                for lower, count in report[name]:
                    steps[lower] = steps.get(lower, 0) + count
            body[name] = {
                (f"{lower}-" if lower >= open_lower else f"{lower}-{lower + BUCKET_WIDTH - 1}"): steps[lower]
                for lower in sorted(steps)
            }
        return body
    return dict.fromkeys(UNIQUE_SOURCES_FIELDS)


def render(service: str, metric: str, day: str, body: dict, members: list[str]) -> str:
    """The aggregate report as YAML, marked as computed."""
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
    for metric in ADDITIVE:
        per_member = [store.load_metric(by_prefix[member]["id"], "", metric, first, last) for member in members]
        for day in sorted(set().union(*per_member)):
            counters["found"] += 1
            reports = [reports_by_day.get(day) for reports_by_day in per_member]
            if any(report is None for report in reports):
                incomplete[metric] += 1
                continue
            keys = [(metric, day)]
            if metric == "traffic-volume":  # a day with a total also gets the empty unique-sources report
                keys.append(("unique-sources", day))
                counters["found"] += 1
            for one_metric, one_day in keys:
                body = combine(one_metric, reports)
                raw = render(source["name"], one_metric, one_day, body, members)
                report = parse_report(raw, expected_metric=one_metric)
                if one_metric == "unique-sources":
                    report = dataclasses.replace(report, warnings=[NOT_ADDITIVE_NOTE])
                counters[store.save_report(source["id"], "", report, raw, source["location"])] += 1
                produced.add((one_metric, one_day))
    # A day that no longer has every member (a member's report was withdrawn) loses its total.
    for metric, day in stored - produced:
        store.delete_report(source["id"], "", metric, day)

    left_out = [f"{metric} on {count} days" for metric, count in incomplete.items() if count]
    message = f"Added up {len(members)} services."
    if left_out:
        message += f" Left out, because not every service had a report: {'; '.join(left_out)}."
    store.update_scan(scan_id, status="done", finished_at=now(), message=message, **counters)
    return counters
