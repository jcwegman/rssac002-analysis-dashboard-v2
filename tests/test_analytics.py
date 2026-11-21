import datetime as dt

import pytest

from rssac002_dashboard.analytics import build_quality, build_series, build_size_matrix, percentile, series_csv
from rssac002_dashboard.scanner import run_scan


def test_percentile_returns_bucket_upper_edge():
    histogram = [[32, 50], [48, 40], [288, 10]]
    assert percentile(histogram, 0.50, 288) == 47
    assert percentile(histogram, 0.90, 288) == 63
    assert percentile(histogram, 0.95, 288) == 288
    assert percentile([], 0.5, 288) is None


def test_series_for_a_period(store, source):
    run_scan(store, source, "new", store.create_scan(source["id"], "new"))
    data = build_series(store, source["id"], "", dt.date(2026, 4, 30), dt.date(2026, 5, 4))
    assert data["days"] == ["2026-04-30", "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04"]
    derived = data["derived"]
    assert derived["queries"] == [None, 1000, 1000, 1000, None]  # missing days are gaps, not zeros
    assert derived["ipv6_share"][1] == pytest.approx(0.24)
    assert derived["tcp_share"][1] == pytest.approx(0.10)
    assert derived["nxdomain_share"][1] == pytest.approx(0.15)
    assert derived["noresponse_share"][1] == pytest.approx(0.015)
    assert derived["sources"][1] == 100
    assert derived["queries_per_source"][1] == pytest.approx(10)
    assert data["series"]["udp_response_p50"][1] == 111
    assert data["summary"]["days_with_data"] == 3
    assert data["summary"]["current"]["queries"] == 1000
    assert data["summary"]["previous"]["queries"] is None
    assert data["rcode_totals"][0] == {"code": 0, "name": "NOERROR", "count": 2400}
    assert {row["name"] for row in data["rcode_totals"]} >= {"No response", "NXDOMAIN"}
    histogram = data["histograms"]["tcp-response-sizes"]
    assert histogram["count"][histogram["lower"].index(4096)] == 30


def test_nameserver_series_are_separate(store, source):
    run_scan(store, source, "new", store.create_scan(source["id"], "new"))
    data = build_series(store, source["id"], "ns1", dt.date(2026, 5, 1), dt.date(2026, 5, 2), derived_only=True)
    assert data["derived"]["queries"] == [1000, None]
    assert set(data) == {"days", "derived"}


def test_unreported_value_is_a_gap_in_its_own_series_only(store, source, tree):
    from .conftest import report_text, write_report

    day = dt.date(2026, 5, 2)
    write_report(
        tree,
        "nl",
        day,
        "unique-sources",
        text=report_text("unique-sources", day, **{"num-sources-ipv6-aggregate": None}),
    )
    run_scan(store, source, "new", store.create_scan(source["id"], "new"))
    derived = build_series(store, source["id"], "", dt.date(2026, 5, 1), dt.date(2026, 5, 3), derived_only=True)[
        "derived"
    ]
    assert derived["sources_ipv6_64"] == [12, None, 12]
    assert derived["sources"] == [100, 100, 100]


def test_breakdown_and_per_code_series(store, source):
    run_scan(store, source, "new", store.create_scan(source["id"], "new"))
    data = build_series(store, source["id"], "", dt.date(2026, 4, 30), dt.date(2026, 5, 1))
    assert data["series"]["q_udp_ipv4"] == [None, 700]
    assert data["series"]["r_tcp_ipv6"] == [None, 40]
    assert data["rcodes"]["3"] == [None, 150]
    assert data["rcodes"]["-1"] == [None, 15]
    assert set(data["rcodes"]) == {"-1", "0", "2", "3", "5"}


def test_size_matrix(store, source):
    run_scan(store, source, "new", store.create_scan(source["id"], "new"))
    matrix = build_size_matrix(store, source["id"], "", dt.date(2026, 4, 30), dt.date(2026, 5, 1))
    assert matrix["days"] == ["2026-04-30", "2026-05-01"]
    udp = matrix["histograms"]["udp-request-sizes"]
    assert udp["rows"] == [None, [0, 0, 600, 300]]
    tcp = matrix["histograms"]["tcp-response-sizes"]
    assert tcp["steps"] == 257 and tcp["rows"][1][64] == 90 and tcp["rows"][1][256] == 10


def test_csv_has_a_row_per_day_and_empty_cells_for_gaps(store, source):
    run_scan(store, source, "new", store.create_scan(source["id"], "new"))
    lines = series_csv(build_series(store, source["id"], "", dt.date(2026, 4, 30), dt.date(2026, 5, 1))).splitlines()
    header = lines[0].split(",")
    assert header[0] == "day" and {"queries", "ipv6_share", "q_udp_ipv4", "rcode_3"} <= set(header)
    gap, day = (dict(zip(header, line.split(","), strict=True)) for line in lines[1:])
    assert gap["day"] == "2026-04-30" and gap["queries"] == ""
    assert day["queries"] == "1000" and day["rcode_3"] == "150" and float(day["ipv6_share"]) == pytest.approx(0.24)


def test_quality_report_finds_gaps_notes_and_mismatches(store, source, tree):
    from .conftest import report_text, write_report

    day = dt.date(2026, 5, 1)
    # ns1 is the only nameserver and reports fewer queries than the combined file that day.
    low = report_text("traffic-volume", day, **{"dns-udp-queries-received-ipv4": 600, "extra-field": 1})
    write_report(tree, "nl", day, "traffic-volume", server="ns1", text=low)
    (tree / "2026" / "05" / "unique-sources" / "nl-20260503-unique-sources.yaml").unlink()
    run_scan(store, source, "new", store.create_scan(source["id"], "new"))

    quality = build_quality(store, source["id"], ["ns1"], dt.date(2026, 5, 1), dt.date(2026, 5, 4))
    assert (quality["days"], quality["complete_days"]) == (4, 2)
    assert quality["missing"]["unique-sources"] == {"count": 2, "days": ["2026-05-03", "2026-05-04"]}
    assert quality["notes"]["count"] == 1 and "extra-field" in quality["notes"]["reports"][0]["warnings"][0]
    checks = {check["name"]: check for check in quality["checks"]}
    adds_up = checks["Nameserver query counts add up"]
    assert (adds_up["days_checked"], adds_up["days_failing"]) == (1, 1)
    assert adds_up["examples"][0] == {"day": "2026-05-01", "expected": 1000, "actual": 900, "difference": 0.1}
    assert checks["Nameserver response counts add up"]["days_failing"] == 0
    assert checks["Size histograms account for the queries"]["days_failing"] == 0
    assert checks["IPv6 /64 prefixes do not exceed IPv6 addresses"]["days_failing"] == 0


def test_change_is_only_given_against_a_well_covered_earlier_period(store, source):
    """Reports exist for 1-3 May. A period is compared with the one before it only if that one is covered."""
    run_scan(store, source, "new", store.create_scan(source["id"], "new"))
    covered = build_series(store, source["id"], "", dt.date(2026, 5, 3), dt.date(2026, 5, 3))["summary"]
    assert covered["previous"]["queries"] == 1000 and covered["previous_days_with_data"] == 1
    half = build_series(store, source["id"], "", dt.date(2026, 5, 2), dt.date(2026, 5, 3))["summary"]
    assert half["previous_days_with_data"] == 1  # 30 April has no report, 1 May has
    assert half["previous"] == dict.fromkeys(half["previous"])  # one of two days: no figures at all
    assert half["current"]["queries"] == 1000
