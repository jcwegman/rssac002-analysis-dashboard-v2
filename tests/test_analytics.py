import datetime as dt

import pytest

from rssac002_dashboard.analytics import (
    build_quality,
    build_series,
    build_size_matrix,
    percentile,
    series_csv,
    with_unnamed_rcodes_summed,
)
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


def with_tls(store, source, tree, sizes=True):
    """The 'nl' reports of the tree, with DNS over TLS and QUIC on 2 May as an operator reports them."""
    from .conftest import report_text, write_report

    day = dt.date(2026, 5, 2)
    volume = {
        "nl-dns-tls-queries-received-ipv4": 150,
        "nl-dns-tls-queries-received-ipv6": 90,
        "nl-dns-quic-queries-received-ipv4": 10,
        "nl-dns-tls-responses-sent-ipv4": 150,
        "nl-dns-tls-responses-sent-ipv6": 90,
        "nl-dns-quic-responses-sent-ipv4": 10,
    }
    write_report(tree, "nl", day, "traffic-volume", text=report_text("traffic-volume", day, **volume))
    # Like b and h root, the response codes count the responses over every transport.
    write_report(tree, "nl", day, "rcode-volume", text=report_text("rcode-volume", day, **{"1": 250}))
    if sizes:
        histograms = {
            "nl-tls-request-sizes": {"48-63": 240},
            "nl-tls-response-sizes": {"96-111": 200, "4096-": 40},
            "nl-quic-request-sizes": {"16-31": 10},
            "nl-quic-response-sizes": {"848-863": 10},
        }
        write_report(tree, "nl", day, "traffic-sizes", text=report_text("traffic-sizes", day, **histograms))
    run_scan(store, source, "new", store.create_scan(source["id"], "new"))


def test_tls_and_quic_count_in_the_totals_and_have_series_of_their_own(store, source, tree):
    with_tls(store, source, tree)
    data = build_series(store, source["id"], "", dt.date(2026, 5, 1), dt.date(2026, 5, 2))
    derived, series = data["derived"], data["series"]
    assert derived["queries"] == [1000, 1250]
    assert derived["queries_udp_tcp"] == [1000, 1000]
    assert derived["responses"] == [985, 1235]
    assert derived["ipv6_share"][1] == pytest.approx(330 / 1250)
    assert derived["tcp_share"] == [pytest.approx(0.10), pytest.approx(0.08)]  # of all queries
    assert derived["tls_share"] == [None, pytest.approx(0.192)]  # not reported is a gap, not 0%
    assert derived["quic_share"] == [None, pytest.approx(0.008)]
    assert series["q_tls_ipv6"] == [None, 90] and series["r_quic_ipv4"] == [None, 10]
    assert series["q_quic_ipv6"] == [None, None]  # the operator left this one field out
    assert data["summary"]["current"]["tcp_share"] == pytest.approx(200 / 2250)
    header = series_csv(data).splitlines()[0].split(",")
    assert {"queries_udp_tcp", "tls_share", "quic_share", "q_tls_ipv4", "r_quic_ipv6"} <= set(header)


def test_tls_size_histograms_are_in_the_matrix_only_when_reported(store, source, tree):
    run_scan(store, source, "new", store.create_scan(source["id"], "new"))
    plain = build_size_matrix(store, source["id"], "", dt.date(2026, 5, 1), dt.date(2026, 5, 2))
    assert set(plain["histograms"]) == {
        "udp-request-sizes",
        "tcp-request-sizes",
        "udp-response-sizes",
        "tcp-response-sizes",
    }
    with_tls(store, source, tree)
    store.delete_report(source["id"], "", "traffic-sizes", "2026-05-02")
    run_scan(store, source, "new", store.create_scan(source["id"], "new"))
    matrix = build_size_matrix(store, source["id"], "", dt.date(2026, 5, 1), dt.date(2026, 5, 2))
    assert matrix["histograms"]["tls-request-sizes"]["rows"] == [None, [0, 0, 0, 240]]
    assert matrix["histograms"]["tls-response-sizes"]["rows"][1][256] == 40


@pytest.mark.parametrize("sizes", [True, False])
def test_checks_hold_when_tls_is_counted_with_or_without_its_sizes(store, source, tree, sizes):
    """h root gives sizes for its TLS and QUIC messages, b root does not; neither is a mismatch."""
    with_tls(store, source, tree, sizes=sizes)
    quality = build_quality(store, source["id"], [], dt.date(2026, 5, 2), dt.date(2026, 5, 2))
    checks = {check["name"]: check for check in quality["checks"]}
    assert checks["Response codes account for the responses"]["days_failing"] == 0
    assert checks["Size histograms account for the queries"]["days_failing"] == 0
    assert checks["Size histograms account for the responses"]["days_failing"] == 0
    assert quality["notes"]["count"] == 0


def test_tls_sizes_that_do_not_add_up_are_found(store, source, tree):
    from .conftest import report_text, write_report

    with_tls(store, source, tree)
    day = dt.date(2026, 5, 2)
    short = report_text("traffic-sizes", day, **{"nl-tls-request-sizes": {"48-63": 100}})
    write_report(tree, "nl", day, "traffic-sizes", text=short)
    run_scan(store, source, "full", store.create_scan(source["id"], "full"))
    quality = build_quality(store, source["id"], [], day, day)
    check = next(check for check in quality["checks"] if check["name"] == "Size histograms account for the queries")
    assert check["examples"][0]["expected"] == 1240 and check["examples"][0]["actual"] == 1100


def test_unnamed_response_codes_are_summed_for_the_partial_sums_of_a_total_too():
    data = {
        "rcodes": {"0": [5, None], "300": [1, None], "4000": [2, None]},
        "partial": {"missing": {}, "rcodes": {"0": [None, 4], "300": [None, 1], "4000": [None, 0]}},
    }
    page = with_unnamed_rcodes_summed(data)
    assert page["rcodes"] == {"0": [5, None]}
    assert page["rcodes_unnamed"] == {"codes": [300, 4000], "values": [3, None]}
    assert page["partial"]["rcodes"] == {"0": [None, 4]}
    assert page["partial"]["rcodes_unnamed"] == [None, 1]
    assert data["partial"]["rcodes"].keys() == {"0", "300", "4000"}  # the CSV still gets a column per code
