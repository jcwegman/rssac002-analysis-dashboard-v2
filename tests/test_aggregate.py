import datetime as dt

import yaml

from rssac002_dashboard import aggregate
from rssac002_dashboard.analytics import build_series
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
