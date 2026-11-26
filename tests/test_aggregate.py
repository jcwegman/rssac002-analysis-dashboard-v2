import datetime as dt

import yaml

from rssac002_dashboard import aggregate
from rssac002_dashboard.analytics import build_quality, build_series, series_csv
from rssac002_dashboard.parsing import parse_report
from rssac002_dashboard.scanner import run_scan
from rssac002_dashboard.sources import normalize_location

from .conftest import BODIES, report_text, write_report

DAYS = [dt.date(2026, 5, 1), dt.date(2026, 5, 2)]


def scan(store, source_id, mode="new"):
    scan_id = store.create_scan(source_id, mode)
    return run_scan(store, store.get_source(source_id), mode, scan_id), store.list_scans(source_id, limit=1)[0]


def make(store, tmp_path):
    """Two services, 'aa' and 'bb', with reports on both days, and their aggregate."""
    ids = {}
    for short in ("aa", "bb"):
        root = tmp_path / short
        for day in DAYS:
            for metric in BODIES:
                write_report(root, short, day, metric)
        ids[short] = store.add_source(
            name=f".{short}", short_service=short, operator="", country="", kind="dir", location=str(root)
        )
        scan(store, ids[short])
    ids["total"] = store.add_source(
        name="both", short_service="both", operator="", country="", kind="aggregate", location="aggregate:aa,bb"
    )
    return ids


def test_counts_are_added_and_unique_sources_are_not(store, tmp_path):
    ids = make(store, tmp_path)
    counters, record = scan(store, ids["total"])
    assert (counters["imported"], counters["failed"]) == (8, 0)
    assert record["status"] == "done" and record["message"] == "Added up 2 services."

    data = build_series(store, ids["total"], "", DAYS[0], DAYS[1])
    assert data["derived"]["queries"] == [2000, 2000]
    assert data["derived"]["nxdomain_share"][0] == 0.15  # shares are unchanged by adding equal members
    assert data["rcodes"]["3"] == [300, 300]
    assert data["series"]["udp_response_p50"][0] == 111
    assert data["histograms"]["tcp-response-sizes"]["count"][-1] == 40
    assert data["derived"]["sources"] == [None, None]
    assert data["derived"]["queries_per_source"] == [None, None]


def test_the_stored_file_is_valid_and_marked_as_computed(store, tmp_path):
    ids = make(store, tmp_path)
    scan(store, ids["total"])
    reports = {
        row["metric"]: row
        for row in store.list_reports(source_id=ids["total"], first="2026-05-01", last="2026-05-01", with_raw=True)
    }
    volume = reports["traffic-volume"]
    assert volume["filename"] == "both-20260501-traffic-volume.yaml"
    assert volume["raw"].startswith("# Computed by the RSSAC002 Analysis Dashboard, not published by an operator.")
    assert "2 services for this day: aa, bb" in volume["raw"]
    assert yaml.safe_load(volume["raw"])["dns-udp-queries-received-ipv4"] == 1400
    assert parse_report(volume["raw"]).data["dns-tcp-responses-sent-ipv6"] == 80
    assert reports["unique-sources"]["warnings"] == [aggregate.NOT_ADDITIVE_NOTE]
    assert yaml.safe_load(reports["unique-sources"]["raw"])["num-sources-ipv4"] is None


def test_a_day_without_every_member_is_a_gap_not_a_smaller_total(store, tmp_path):
    ids = make(store, tmp_path)
    scan(store, ids["total"])
    # 'bb' withdraws its traffic-volume report for the second day and publishes a third day alone.
    store.delete_report(ids["bb"], "", "traffic-volume", "2026-05-02")
    write_report(tmp_path / "bb", "bb", dt.date(2026, 5, 3), "rcode-volume")
    scan(store, ids["bb"])
    store.delete_report(ids["bb"], "", "traffic-volume", "2026-05-02")

    _, record = scan(store, ids["total"])
    assert (
        "Left out, because not every service had a report: traffic-volume on 1 days; rcode-volume on 1 days."
        in record["message"]
    )
    data = build_series(store, ids["total"], "", DAYS[0], dt.date(2026, 5, 3))
    assert data["derived"]["queries"] == [2000, None, None]
    assert data["rcodes"]["0"] == [1600, 1600, None]
    coverage = store.coverage(ids["total"], "")
    assert sorted(coverage["2026-05-02"]) == [
        "rcode-volume",
        "traffic-sizes",
    ]  # the stale total and its companion are gone


def test_changed_member_reports_change_the_total(store, tmp_path):
    ids = make(store, tmp_path)
    scan(store, ids["total"])
    write_report(tmp_path / "aa", "aa", DAYS[0], "rcode-volume", text=report_text("rcode-volume", DAYS[0], **{"7": 40}))
    scan(store, ids["aa"], "full")
    counters, _ = scan(store, ids["total"])
    assert (counters["updated"], counters["unchanged"]) == (1, 7)
    assert build_series(store, ids["total"], "", DAYS[0], DAYS[0])["rcodes"]["7"] == [40]


def test_missing_member_is_explained(store):
    source_id = store.add_source(
        name="x", short_service="x", operator="", country="", kind="aggregate", location="aggregate:aa,zz"
    )
    _, record = scan(store, source_id)
    assert record["status"] == "failed" and record["message"] == "Cannot compute: not a source yet: aa, zz"


def test_aggregate_locations_are_recognised():
    assert normalize_location(" Aggregate: b-root , a-root,a-root ") == ("aggregate", "aggregate:a-root,b-root")


def with_tls(store, tmp_path, sizes_too=True):
    """The two services of make(), where only 'aa' reports TLS on the first day, and their total."""
    ids = make(store, tmp_path)
    volume = {"aa-dns-tls-queries-received-ipv4": 50, "aa-dns-tls-responses-sent-ipv4": 48}
    write_report(
        tmp_path / "aa", "aa", DAYS[0], "traffic-volume", text=report_text("traffic-volume", DAYS[0], **volume)
    )
    if sizes_too:
        sizes = {"aa-tls-request-sizes": {"48-63": 50}, "aa-tls-response-sizes": {"96-111": 48}}
        write_report(
            tmp_path / "aa", "aa", DAYS[0], "traffic-sizes", text=report_text("traffic-sizes", DAYS[0], **sizes)
        )
    scan(store, ids["aa"], "full")
    scan(store, ids["total"])
    return ids


def raw_of(store, source_id, metric, day):
    return store.list_reports(source_id=source_id, metric=metric, first=day, last=day, with_raw=True)[0]["raw"]


def test_tls_of_the_members_that_report_it_is_added_to_the_total(store, tmp_path):
    ids = with_tls(store, tmp_path)
    data = build_series(store, ids["total"], "", DAYS[0], DAYS[1])
    assert data["derived"]["queries"] == [2050, 2000]
    assert data["derived"]["queries_udp_tcp"] == [2000, 2000]
    assert data["series"]["q_tls_ipv4"] == [50, None]  # nobody reports TLS on the second day
    assert data["series"]["r_tls_ipv4"] == [48, None]

    raw = raw_of(store, ids["total"], "traffic-volume", "2026-05-01")
    # In the published file the fields carry the total's own prefix, like an operator's own fields.
    assert yaml.safe_load(raw)["both-dns-tls-queries-received-ipv4"] == 50
    assert "# TLS is not part of RSSAC002." in raw and raw.split("# TLS")[1].splitlines()[0].endswith(": aa.")
    assert "QUIC" not in raw
    assert "TLS" not in raw_of(store, ids["total"], "traffic-volume", "2026-05-02")


def test_tls_sizes_are_in_the_total_only_when_every_member_with_tls_gives_them(store, tmp_path):
    ids = with_tls(store, tmp_path)
    document = yaml.safe_load(raw_of(store, ids["total"], "traffic-sizes", "2026-05-01"))
    assert document["both-tls-request-sizes"] == {"48-63": 50}
    assert "both-tls-request-sizes" not in yaml.safe_load(raw_of(store, ids["total"], "traffic-sizes", "2026-05-02"))

    # Now 'bb' counts TLS as well, without sizes: a histogram of only 'aa' would not be the total's.
    volume = report_text("traffic-volume", DAYS[0], **{"bb-dns-tls-queries-received-ipv6": 9})
    write_report(tmp_path / "bb", "bb", DAYS[0], "traffic-volume", text=volume)
    scan(store, ids["bb"], "full")
    scan(store, ids["total"])
    raw = raw_of(store, ids["total"], "traffic-sizes", "2026-05-01")
    assert "tls" not in raw.lower()
    assert build_series(store, ids["total"], "", DAYS[0], DAYS[0])["derived"]["queries"] == [2059]


def make_four(store, tmp_path, without=()):
    """Services 'aa' to 'dd' and their total. `without` lists (service, day, metric) reports that do not exist."""
    ids = {}
    for short in ("aa", "bb", "cc", "dd"):
        root = tmp_path / short
        for day in DAYS:
            for metric in BODIES:
                if (short, day, metric) not in without:
                    write_report(root, short, day, metric)
        ids[short] = store.add_source(
            name=f"{short}.example", short_service=short, operator="", country="", kind="dir", location=str(root)
        )
        scan(store, ids[short])
    ids["total"] = store.add_source(
        name="all", short_service="all", operator="", country="", kind="aggregate", location="aggregate:aa,bb,cc,dd"
    )
    return ids


def test_a_day_without_one_member_of_four_is_kept_as_a_partial_sum_apart_from_the_total(store, tmp_path):
    ids = make_four(store, tmp_path, without=[("dd", DAYS[1], "traffic-volume")])
    _, record = scan(store, ids["total"])
    assert record["message"] == (
        "Added up 4 services. Given as a partial sum, because some services had no report: traffic-volume on 1 days."
    )

    data = build_series(store, ids["total"], "", DAYS[0], DAYS[1])
    assert data["derived"]["queries"] == [4000, None]  # still a gap in the series
    assert data["partial"]["derived"]["queries"] == [None, 3000]
    assert data["partial"]["derived"]["ipv6_share"][1] == 0.24
    assert data["partial"]["series"]["q_udp_ipv4"] == [None, 2100]
    assert data["partial"]["missing"] == {"traffic-volume": [None, ["dd"]]}
    assert data["partial"]["metric"]["queries"] == "traffic-volume"
    assert data["rcodes"]["3"] == [600, 600]  # the other metrics are complete
    assert data["partial"]["rcodes"]["3"] == [None, None]
    # Nothing is computed from the partial sum.
    assert data["summary"]["days_with_data"] == 1 and data["summary"]["current"]["queries"] == 4000
    assert "partial" not in build_series(store, ids["total"], "", DAYS[0], DAYS[0])
    assert "partial" not in build_series(store, ids["aa"], "", DAYS[0], DAYS[1])


def test_a_partial_sum_is_not_a_report_of_the_service(store, tmp_path):
    ids = make_four(store, tmp_path, without=[("dd", DAYS[1], "traffic-volume")])
    scan(store, ids["total"])
    listed = store.list_reports(source_id=ids["total"], metric="traffic-volume")
    assert [row["day"] for row in listed] == ["2026-05-01"]
    assert sorted(store.coverage(ids["total"], "")["2026-05-02"]) == ["rcode-volume", "traffic-sizes"]
    assert next(one for one in store.list_sources() if one["id"] == ids["total"])["reports"] == 6
    assert "2026-05-02" not in store.load_metric(ids["total"], "", "traffic-volume", "2026-05-01", "2026-05-02")

    data, missing = store.load_partial(ids["total"], "traffic-volume", "2026-05-01", "2026-05-02")["2026-05-02"]
    assert missing == ["dd"] and data["dns-udp-queries-received-ipv4"] == 2100
    with store.connect() as conn:
        raw = conn.execute("SELECT raw FROM reports WHERE missing != ''").fetchone()["raw"]
    assert "# The sum of the traffic-volume reports of 3 services for this day: aa, bb, cc." in raw
    assert "# A partial sum, not the total of all: 1 of its 4 services had no traffic-volume report" in raw

    quality = build_quality(store, ids["total"], [], DAYS[0], DAYS[1])
    assert quality["missing"]["traffic-volume"] == {"count": 1, "days": ["2026-05-02"]}
    assert quality["partial"] == {
        "days": 1,
        "runs": [{"first": "2026-05-02", "last": "2026-05-02", "metrics": {"traffic-volume": ["dd"]}}],
    }
    assert all(check["days_failing"] == 0 for check in quality["checks"])

    header, first, second = (
        line.split(",") for line in series_csv(build_series(store, ids["total"], "", *DAYS)).splitlines()
    )
    row = dict(zip(header, second, strict=True))
    assert (row["queries"], row["partial_queries"], row["missing_traffic_volume"]) == ("", "3000", "dd")
    assert dict(zip(header, first, strict=True))["partial_queries"] == ""


def test_a_partial_sum_becomes_the_total_when_the_last_member_reports(store, tmp_path):
    ids = make_four(store, tmp_path, without=[("dd", DAYS[1], "traffic-volume")])
    scan(store, ids["total"])
    write_report(tmp_path / "dd", "dd", DAYS[1], "traffic-volume")
    scan(store, ids["dd"])
    scan(store, ids["total"])
    data = build_series(store, ids["total"], "", DAYS[0], DAYS[1])
    assert data["derived"]["queries"] == [4000, 4000] and "partial" not in data
    assert len(store.list_reports(source_id=ids["total"], metric="unique-sources")) == 2


def test_with_fewer_than_three_quarters_of_the_members_a_day_stays_a_gap(store, tmp_path):
    gone = [(short, DAYS[1], "rcode-volume") for short in ("cc", "dd")]
    ids = make_four(store, tmp_path, without=gone)
    _, record = scan(store, ids["total"])
    assert "Left out, because not every service had a report: rcode-volume on 1 days." in record["message"]
    assert "partial sum" not in record["message"]
    data = build_series(store, ids["total"], "", DAYS[0], DAYS[1])
    assert data["rcodes"]["0"] == [3200, None] and "partial" not in data
