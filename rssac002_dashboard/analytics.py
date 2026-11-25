"""Turn stored reports into the series and summaries the dashboard shows.

Everything is computed per UTC day. A day without a report is `None`, never zero, so gaps
stay visible instead of looking like a traffic drop.
"""

from __future__ import annotations

import datetime as dt
import math

from .parsing import BUCKET_WIDTH, EXTRA_SIZE_HISTOGRAMS, EXTRA_TRANSPORTS, SIZE_HISTOGRAMS
from .store import Store

SIZE_METRICS = ("traffic-volume", "rcode-volume", "unique-sources", "traffic-sizes")

RCODE_NAMES = {
    -1: "No response",
    0: "NOERROR",
    1: "FORMERR",
    2: "SERVFAIL",
    3: "NXDOMAIN",
    4: "NOTIMP",
    5: "REFUSED",
    6: "YXDOMAIN",
    7: "YXRRSET",
    8: "NXRRSET",
    9: "NOTAUTH",
    10: "NOTZONE",
    11: "DSOTYPENI",
    16: "BADVERS",
    17: "BADKEY",
    18: "BADTIME",
    19: "BADMODE",
    20: "BADNAME",
    21: "BADALG",
    22: "BADTRUNC",
    23: "BADCOOKIE",
}
# Rcodes with their own share series; everything else is folded into "other".
NAMED_RCODE_SHARES = {"noerror": "0", "nxdomain": "3", "servfail": "2", "refused": "5", "noresponse": "-1"}

# Measures that can be compared between services: key -> (label, unit).
MEASURES = {
    "queries": ("Queries per day", "count"),
    "queries_udp_tcp": ("UDP and TCP queries per day", "count"),
    "responses": ("Responses per day", "count"),
    "answer_ratio": ("Responses per query", "ratio"),
    "ipv6_share": ("IPv6 share of queries", "share"),
    "tcp_share": ("TCP share of queries", "share"),
    "encrypted_share": ("TLS and QUIC share of queries", "share"),
    "noerror_share": ("NOERROR share", "share"),
    "nxdomain_share": ("NXDOMAIN share", "share"),
    "servfail_share": ("SERVFAIL share", "share"),
    "refused_share": ("REFUSED share", "share"),
    "noresponse_share": ("Unanswered share", "share"),
    "other_rcode_share": ("Other response codes share", "share"),
    "sources": ("Unique sources per day", "count"),
    "sources_ipv4": ("Unique IPv4 sources", "count"),
    "sources_ipv6": ("Unique IPv6 sources", "count"),
    "sources_ipv6_64": ("Unique IPv6 /64 networks", "count"),
    "queries_per_source": ("Queries per source", "ratio"),
    "udp_response_p50": ("Median UDP response size", "bytes"),
}


def _div(numerator, denominator):
    return numerator / denominator if denominator else None


def percentile(histogram: list[list[int]], fraction: float, open_lower: int) -> int | None:
    """Upper edge of the bucket holding the given fraction of observations (the open bucket's lower edge)."""
    total = sum(count for _, count in histogram)
    if not total:
        return None
    running = 0
    for lower, count in histogram:
        running += count
        if running >= fraction * total:
            return lower if lower >= open_lower else lower + BUCKET_WIDTH - 1
    return None


IP_VERSIONS = ("ipv4", "ipv6")
TRANSPORTS = ("udp", "tcp", *EXTRA_TRANSPORTS)


def _traffic(report: dict) -> dict:
    """Queries and responses over every transport the report has.

    TLS and QUIC are operators' own fields, see parsing.py. A report without them adds nothing
    to the totals, and its TLS and QUIC values are None ("not reported"), not zero.
    """
    q = {(p, i): report.get(f"dns-{p}-queries-received-{i}") for p in TRANSPORTS for i in IP_VERSIONS}
    r = {(p, i): report.get(f"dns-{p}-responses-sent-{i}") for p in TRANSPORTS for i in IP_VERSIONS}

    def queries_over(*wanted):
        return sum(count or 0 for key, count in q.items() if set(key) & set(wanted))

    queries, responses = queries_over(*TRANSPORTS), sum(count or 0 for count in r.values())
    udp_tcp = queries_over("udp", "tcp")
    reports_extra = any(q[p, i] is not None for p in EXTRA_TRANSPORTS for i in IP_VERSIONS)
    encrypted = queries_over(*EXTRA_TRANSPORTS) if reports_extra else None
    return {
        "queries": queries,
        "queries_udp_tcp": udp_tcp,
        "queries_encrypted": encrypted,
        "responses": responses,
        "queries_udp": queries_over("udp"),
        "queries_tcp": queries_over("tcp"),
        "queries_ipv4": queries_over("ipv4"),
        "queries_ipv6": queries_over("ipv6"),
        "answer_ratio": _div(responses, queries),
        "ipv6_share": _div(queries_over("ipv6"), queries),
        # Of UDP and TCP only: the share is read as a sign of truncated UDP responses.
        "tcp_share": _div(queries_over("tcp"), udp_tcp),
        "encrypted_share": _div(encrypted, queries) if reports_extra else None,
        **{f"q_{proto}_{ip}": count for (proto, ip), count in q.items()},
        **{f"r_{proto}_{ip}": count for (proto, ip), count in r.items()},
    }


def _rcodes(report: dict) -> dict:
    counts = report["rcodes"]
    total = sum(counts.values())
    named = {name: counts.get(code, 0) for name, code in NAMED_RCODE_SHARES.items()}
    values = {f"{name}_share": _div(count, total) for name, count in named.items()}
    values["other_rcode_share"] = _div(total - sum(named.values()), total)
    values["rcode_total"] = total
    values["nxdomain"] = named["nxdomain"]
    return values


def _sources(report: dict) -> dict:
    ipv4, ipv6 = report["num-sources-ipv4"], report["num-sources-ipv6"]
    return {
        "sources": ipv4 + ipv6 if ipv4 is not None and ipv6 is not None else None,
        "sources_ipv4": ipv4,
        "sources_ipv6": ipv6,
        "sources_ipv6_64": report["num-sources-ipv6-aggregate"],
    }


def _sizes(report: dict) -> dict:
    values = {}
    for name, open_lower in SIZE_HISTOGRAMS.items():
        key = name.removesuffix("-sizes").replace("-", "_")
        values[f"{key}_p50"] = percentile(report[name], 0.50, open_lower)
        values[f"{key}_p95"] = percentile(report[name], 0.95, open_lower)
    return values


# The traffic-volume counters, as series names: q = queries received, r = responses sent.
BREAKDOWN = tuple(f"{side}_{proto}_{ip}" for side in ("q", "r") for proto in TRANSPORTS for ip in IP_VERSIONS)


def _mean(values: list) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def _ratio_of_sums(numerators: list, denominators: list) -> float | None:
    pairs = [(n, d) for n, d in zip(numerators, denominators, strict=True) if n is not None and d is not None]
    return _div(sum(n for n, _ in pairs), sum(d for _, d in pairs))


# A period is compared with the one before it only when that earlier period has reports for at
# least this share of its days. A year set against five months of an earlier year is not a change.
MIN_PREVIOUS_COVERAGE = 0.9


def _summarise(columns: dict[str, list], window: slice, minimum_days: int = 1) -> dict:
    """Period figures. A figure whose reports cover fewer than `minimum_days` days is None."""

    def col(name):
        return columns[name][window]

    def covered(name):
        return sum(1 for value in col(name) if value is not None) >= minimum_days

    return {
        "queries": _mean(col("queries")) if covered("queries") else None,
        "ipv6_share": _ratio_of_sums(col("queries_ipv6"), col("queries")) if covered("queries") else None,
        "tcp_share": _ratio_of_sums(col("queries_tcp"), col("queries_udp_tcp")) if covered("queries") else None,
        "nxdomain_share": _ratio_of_sums(col("nxdomain"), col("rcode_total")) if covered("rcode_total") else None,
        "sources": _mean(col("sources")) if covered("sources") else None,
    }


def build_series(
    store: Store, source_id: int, server: str, first: dt.date, last: dt.date, derived_only: bool = False
) -> dict:
    """Everything the dashboard needs for one source (and optionally one nameserver) over a date range."""
    span = (last - first).days + 1
    history = span  # the same number of earlier days, for the previous-period comparison
    load_first = first - dt.timedelta(days=history)
    dates = [load_first + dt.timedelta(days=offset) for offset in range(history + span)]
    days = [day.isoformat() for day in dates]

    def load(metric):
        return store.load_metric(source_id, server, metric, days[0], days[-1])

    per_metric = [
        (load("traffic-volume"), _traffic),
        (load("rcode-volume"), _rcodes),
        (load("unique-sources"), _sources),
        (load("traffic-sizes"), _sizes),
    ]
    columns: dict[str, list] = {}
    for reports, compute in per_metric:
        for index, day in enumerate(days):
            if day not in reports:
                continue
            for name, value in compute(reports[day]).items():
                columns.setdefault(name, [None] * len(days))[index] = value
    for name in ("queries", "queries_ipv6", "queries_tcp", "nxdomain", "rcode_total", "sources", *MEASURES):
        columns.setdefault(name, [None] * len(days))
    columns["queries_per_source"] = [
        _div(q, s) if q is not None and s is not None else None
        for q, s in zip(columns["queries"], columns["sources"], strict=True)
    ]

    current = slice(history, None)
    result = {
        "days": days[current],
        "derived": {name: columns[name][current] for name in MEASURES},
    }
    if derived_only:
        return result

    extra = ("queries_udp", "queries_tcp", "queries_encrypted", "queries_ipv4", "queries_ipv6", *BREAKDOWN)
    percentiles = [
        f"{p}_{side}_{q}" for p in ("udp", "tcp") for side in ("request", "response") for q in ("p50", "p95")
    ]
    result["series"] = {name: columns.get(name, [None] * len(days))[current] for name in (*extra, *percentiles)}
    result["summary"] = {
        "current": _summarise(columns, current),
        "previous": _summarise(columns, slice(history - span, history), math.ceil(MIN_PREVIOUS_COVERAGE * span)),
        "previous_days_with_data": sum(1 for v in columns["queries"][history - span : history] if v is not None),
        "days_with_data": sum(1 for v in columns["queries"][current] if v is not None),
        "days": span,
    }

    # Totals over the selected range, for the distribution charts.
    sizes, rcodes = per_metric[3][0], per_metric[1][0]
    selected = set(days[current])
    histograms = {}
    for name, open_lower in SIZE_HISTOGRAMS.items():
        lowers = list(range(0, open_lower + 1, BUCKET_WIDTH))
        totals = dict.fromkeys(lowers, 0)
        for day, report in sizes.items():
            if day in selected:
                for lower, count in report[name]:
                    totals[lower] += count
        histograms[name] = {"lower": lowers, "count": [totals[lower] for lower in lowers], "open_lower": open_lower}
    result["histograms"] = histograms

    rcode_totals: dict[int, int] = {}
    for day, report in rcodes.items():
        if day in selected:
            for code, count in report["rcodes"].items():
                rcode_totals[int(code)] = rcode_totals.get(int(code), 0) + count
    result["rcode_totals"] = [
        {"code": code, "name": RCODE_NAMES.get(code, f"RCODE {code}"), "count": count}
        for code, count in sorted(rcode_totals.items(), key=lambda item: -item[1])
    ]
    # Responses per code per day. A code absent from a day's report is zero that day.
    result["rcodes"] = {
        str(code): [rcodes[day]["rcodes"].get(str(code), 0) if day in rcodes else None for day in days[current]]
        for code in sorted(rcode_totals)
    }
    return result


def build_size_matrix(store: Store, source_id: int, server: str, first: dt.date, last: dt.date) -> dict:
    """Message counts per day and 16-byte size step, for the size heatmaps.

    Each histogram is a row per day (None for a day without a report) and a column per size
    step, cut off after the largest step that is ever non-zero. The TLS and QUIC histograms
    are given when a report in the period has them, with None for the days it does not.
    """
    days = [(first + dt.timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)]
    reports = store.load_metric(source_id, server, "traffic-sizes", days[0], days[-1])
    histograms = {}
    for name, open_lower in {**SIZE_HISTOGRAMS, **EXTRA_SIZE_HISTOGRAMS}.items():
        if name in EXTRA_SIZE_HISTOGRAMS and not any(name in report for report in reports.values()):
            continue
        top = max((lower for report in reports.values() for lower, _ in report.get(name, ())), default=0)
        width = top // BUCKET_WIDTH + 1
        rows = []
        for day in days:
            if name not in reports.get(day, ()):
                rows.append(None)
                continue
            row = [0] * width
            for lower, count in reports[day][name]:
                row[lower // BUCKET_WIDTH] = count
            rows.append(row)
        histograms[name] = {"steps": width, "open_lower": open_lower, "rows": rows}
    return {"days": days, "step_bytes": BUCKET_WIDTH, "histograms": histograms}


def series_csv(data: dict) -> str:
    """The daily series as CSV: one row per day, one column per measure. Missing values are empty."""
    columns = {
        **data["derived"],
        **data["series"],
        **{f"rcode_{code}": values for code, values in data["rcodes"].items()},
    }
    lines = [",".join(["day", *columns])]
    for index, day in enumerate(data["days"]):
        cells = ("" if values[index] is None else repr(values[index]) for values in columns.values())
        lines.append(",".join([day, *cells]))
    return "\n".join(lines) + "\n"


# --- data quality ------------------------------------------------------------------------


def _total(report: dict, side: str) -> int:
    return sum(count for name, count in report.items() if side in name)


def _sized(volume: dict, sizes: dict, side: str) -> tuple[int, int]:
    """Messages counted by traffic-volume and by the size histograms, over the same transports.

    UDP and TCP always. TLS and QUIC only when the sizes report has a histogram for them: an
    operator can count such traffic without giving its sizes.
    """
    counter = "queries-received" if side == "request" else "responses-sent"
    transports = ["udp", "tcp", *(t for t in EXTRA_TRANSPORTS if f"{t}-{side}-sizes" in sizes)]
    counted = sum(volume.get(f"dns-{t}-{counter}-{ip}", 0) for t in transports for ip in IP_VERSIONS)
    return counted, sum(count for t in transports for _, count in sizes[f"{t}-{side}-sizes"])


def _check(
    name: str, description: str, tolerance: float, rows: list[tuple[str, float, float]], limit: bool = False
) -> dict:
    """Compare pairs of numbers that should agree. With `limit`, the second must not exceed the first."""
    failing = []
    for day, expected, actual in rows:
        gap = actual - expected if limit else abs(actual - expected)
        difference = gap / expected if expected else float(gap > 0)
        if difference > tolerance:
            failing.append({"day": day, "expected": expected, "actual": actual, "difference": difference})
    failing.sort(key=lambda row: -row["difference"])
    return {
        "name": name,
        "description": description,
        "tolerance": tolerance,
        "days_checked": len({day for day, _, _ in rows}),
        "days_failing": len({row["day"] for row in failing}),
        "largest": failing[0]["difference"] if failing else 0.0,
        "examples": failing[:10],
    }


def build_quality(store: Store, source_id: int, servers: list[str], first: dt.date, last: dt.date) -> dict:
    """Gaps, import notes and internal consistency of one source's reports over a period.

    The checks compare numbers that the RSSAC002 metrics define as the same quantity counted in
    two ways. A failing check points at the reports, or at the tool that produced them.
    """
    days = [(first + dt.timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)]
    by_metric = {metric: store.load_metric_by_server(source_id, metric, days[0], days[-1]) for metric in SIZE_METRICS}
    volume, rcodes, sources, sizes = (by_metric[metric] for metric in SIZE_METRICS)

    def combined(reports):
        return [(day, per_server[""]) for day, per_server in sorted(reports.items()) if "" in per_server]

    def complete(reports):
        """Days on which the combined report and every nameserver's report exist."""
        return [(day, s) for day, s in sorted(reports.items()) if servers and "" in s and all(n in s for n in servers)]

    def rcode_sum(report, with_unanswered=True):
        return sum(count for code, count in report["rcodes"].items() if with_unanswered or code != "-1")

    checks = [
        _check(
            "Nameserver query counts add up",
            "Queries received, summed over the nameserver reports, equal the combined report.",
            0.001,
            [
                (d, _total(s[""], "queries-received"), sum(_total(s[n], "queries-received") for n in servers))
                for d, s in complete(volume)
            ],
        ),
        _check(
            "Nameserver response counts add up",
            "Responses sent, summed over the nameserver reports, equal the combined report.",
            0.001,
            [
                (d, _total(s[""], "responses-sent"), sum(_total(s[n], "responses-sent") for n in servers))
                for d, s in complete(volume)
            ],
        ),
        _check(
            "Nameserver response-code counts add up",
            "Response-code counts, summed over the nameserver reports, equal the combined report.",
            0.001,
            [(d, rcode_sum(s[""]), sum(rcode_sum(s[n]) for n in servers)) for d, s in complete(rcodes)],
        ),
        _check(
            "Response codes account for the responses",
            "The response-code counts (without code −1) add up to the responses sent over all transports.",
            0.01,
            [
                (d, _total(volume[d][""], "responses-sent"), rcode_sum(r, False))
                for d, r in combined(rcodes)
                if "" in volume.get(d, {})
            ],
        ),
        _check(
            "Size histograms account for the queries",
            "The query-size histograms add up to the queries received over the same transports.",
            0.01,
            [(d, *_sized(volume[d][""], r, "request")) for d, r in combined(sizes) if "" in volume.get(d, {})],
        ),
        _check(
            "Size histograms account for the responses",
            "The response-size histograms add up to the responses sent over the same transports.",
            0.01,
            [(d, *_sized(volume[d][""], r, "response")) for d, r in combined(sizes) if "" in volume.get(d, {})],
        ),
        _check(
            "No nameserver sees more sources than all together",
            "Each nameserver's unique IPv4 and IPv6 source counts are at most the combined counts.",
            0.0,
            [
                (d, s[""][field], s[n][field])
                for d, s in complete(sources)
                for n in servers
                for field in ("num-sources-ipv4", "num-sources-ipv6")
                if s[""][field] is not None and s[n][field] is not None
            ],
            limit=True,
        ),
        _check(
            "IPv6 /64 prefixes do not exceed IPv6 addresses",
            "The number of distinct /64 prefixes is at most the number of distinct IPv6 addresses.",
            0.0,
            [
                (d, r["num-sources-ipv6"], r["num-sources-ipv6-aggregate"])
                for d, r in combined(sources)
                if r["num-sources-ipv6"] is not None and r["num-sources-ipv6-aggregate"] is not None
            ],
            limit=True,
        ),
    ]
    missing = {metric: [day for day in days if "" not in reports.get(day, {})] for metric, reports in by_metric.items()}
    notes = store.reports_with_notes(source_id, days[0], days[-1])
    return {
        "days": len(days),
        "complete_days": sum(1 for day in days if all("" in reports.get(day, {}) for reports in by_metric.values())),
        "missing": {metric: {"count": len(found), "days": found[:60]} for metric, found in missing.items()},
        "notes": {"count": len(notes), "reports": notes[:60]},
        "checks": [check for check in checks if check["days_checked"]],
    }
