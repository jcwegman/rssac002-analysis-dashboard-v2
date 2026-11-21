import datetime as dt

from rssac002_dashboard.scanner import run_scan

from .conftest import report_text, write_report


def scan(store, source, mode="new"):
    scan_id = store.create_scan(source["id"], mode)
    counters = run_scan(store, source, mode, scan_id)
    return counters, store.list_scans(source["id"], limit=1)[0]


def test_first_scan_imports_everything(store, source):
    counters, record = scan(store, source)
    assert counters == {"found": 16, "imported": 16, "updated": 0, "unchanged": 0, "failed": 0}
    assert record["status"] == "done" and record["finished_at"]
    listed = store.list_sources()[0]
    assert (listed["first_day"], listed["last_day"], listed["reports"], listed["servers"]) == (
        "2026-05-01",
        "2026-05-03",
        16,
        ["ns1"],
    )


def test_new_scan_only_fetches_what_is_missing(store, source, tree):
    scan(store, source)
    write_report(tree, "nl", dt.date(2026, 5, 4), "rcode-volume")
    counters, _ = scan(store, source)
    assert (counters["imported"], counters["unchanged"]) == (1, 16)


def test_new_scan_of_a_folder_also_finds_back_filled_months(store, source, tree):
    scan(store, source)
    write_report(tree, "nl", dt.date(2025, 11, 9), "rcode-volume")
    assert scan(store, source)[0]["imported"] == 1


def test_full_scan_replaces_changed_reports(store, source, tree):
    scan(store, source)
    day = dt.date(2026, 5, 2)
    write_report(
        tree, "nl", day, "unique-sources", text=report_text("unique-sources", day, **{"num-sources-ipv4": 999})
    )
    assert scan(store, source, "new")[0]["updated"] == 0
    counters, _ = scan(store, source, "full")
    assert (counters["updated"], counters["unchanged"]) == (1, 15)
    assert (
        store.load_metric(source["id"], "", "unique-sources", "2026-05-02", "2026-05-02")["2026-05-02"][
            "num-sources-ipv4"
        ]
        == 999
    )


def test_bad_reports_are_counted_and_explained(store, source, tree):
    day = dt.date(2026, 5, 5)
    write_report(tree, "nl", day, "rcode-volume", text="not: a report\n")
    write_report(tree, "nl", dt.date(2026, 5, 6), "rcode-volume", text=report_text("rcode-volume", day))
    counters, record = scan(store, source)
    assert (counters["imported"], counters["failed"]) == (16, 2)
    errors = {error["origin"].rsplit("/", 1)[-1]: error["error"] for error in record["errors"]}
    assert "version" in errors["nl-20260505-rcode-volume.yaml"]
    assert "file name says 2026-05-06" in errors["nl-20260506-rcode-volume.yaml"]


def test_unreadable_source_fails_the_scan(store, tmp_path):
    source_id = store.add_source(
        name=".x", short_service="x", operator="", country="", kind="dir", location=str(tmp_path / "gone")
    )
    _, record = scan(store, store.get_source(source_id))
    assert record["status"] == "failed"
    assert "Cannot read directory" in record["message"]


def test_interrupted_scans_are_closed_when_the_server_starts_not_on_every_open(store, source):
    store.create_scan(source["id"], "new")
    reopened = type(store)(store.path)  # what any other command does while a scan runs
    assert reopened.active_scan(source["id"]) is not None
    reopened.close_interrupted_scans()
    assert reopened.list_scans(source["id"])[0]["status"] == "failed"
    assert reopened.active_scan(source["id"]) is None


def test_import_from_limits_what_is_imported(store, tree):
    source_id = store.add_source(
        name=".nl",
        short_service="nl",
        operator="",
        country="",
        kind="dir",
        location=str(tree),
        import_from="2026-05-02",
    )
    counters, _ = scan(store, store.get_source(source_id))
    assert counters["imported"] == 8
    assert store.list_sources()[0]["first_day"] == "2026-05-02"


def test_database_from_before_the_rename_is_upgraded_with_its_data(tmp_path, tree):
    import sqlite3

    from rssac002_dashboard.store import SCHEMA, Store

    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE sources (id INTEGER PRIMARY KEY, tld TEXT NOT NULL, short_service TEXT NOT NULL, "
        "operator TEXT NOT NULL DEFAULT '', country TEXT NOT NULL DEFAULT '', "
        "kind TEXT NOT NULL CHECK (kind IN ('http', 'dir')), location TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);"
        + SCHEMA.split(");", 1)[1]
    )
    old.execute("INSERT INTO sources VALUES (7, '.nl', 'nl', 'SIDN', 'NL', 'dir', ?, '2026-01-01')", (str(tree),))
    old.execute(
        "INSERT INTO reports (source_id, metric, day, start_period, service, version, data, raw, sha256, origin, "
        "fetched_at) VALUES (7, 'rcode-volume', '2026-05-01', 's', 'nl', 'rssac002v5', '{}', 'raw', 'hash', 'o', 'now')"
    )
    old.commit()
    old.close()

    store = Store(path)
    source = store.list_sources()[0]
    assert (source["id"], source["name"], source["reports"], source["import_from"]) == (7, ".nl", 1, "")
    # One location can now hold several services, and Git sources are allowed.
    store.add_source(
        name="b.root-servers.net", short_service="b-root", operator="", country="", kind="git", location=str(tree)
    )
    assert len(Store(path).list_sources()) == 2
