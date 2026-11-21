import datetime as dt

import httpx
import pytest

from rssac002_dashboard.sources import (
    DirReader,
    HttpReader,
    SourceError,
    classify,
    discover,
    normalize_location,
    read_profile,
    validate_profile,
)

from .conftest import write_report


def test_classify_recognises_service_and_nameserver_files():
    day = dt.date(2026, 5, 21)
    assert classify("nl-20260521-rcode-volume.yaml", "nl", "rcode-volume") == ("", day)
    assert classify("nl-ns1.dns.nl-20260521-rcode-volume.yaml", "nl", "rcode-volume") == ("ns1.dns.nl", day)
    assert classify("be-20260521-rcode-volume.yaml", "nl", "rcode-volume") is None
    assert classify("nl-20260521-rcode-volume.yaml", "nl", "traffic-volume") is None
    assert classify("nl-20261341-rcode-volume.yaml", "nl", "rcode-volume") is None
    assert classify("notes.txt", "nl", "rcode-volume") is None


def test_discover_walks_the_layout(tree):
    write_report(tree, "be", dt.date(2026, 5, 1), "rcode-volume")
    (tree / "2026" / "05" / "zone-size").mkdir()
    found = list(discover(DirReader(str(tree)), "nl"))
    assert len(found) == 16
    assert {item.server for item in found} == {"", "ns1"}
    assert {item.day for item in found if item.server == "ns1"} == {dt.date(2026, 5, 1)}


def test_discover_since_skips_earlier_months(tree):
    write_report(tree, "nl", dt.date(2026, 3, 9), "rcode-volume")
    assert len(list(discover(DirReader(str(tree)), "nl"))) == 17
    assert len(list(discover(DirReader(str(tree)), "nl", since=dt.date(2026, 4, 20)))) == 16


def test_missing_directory_is_reported(tmp_path):
    with pytest.raises(SourceError, match="Cannot read directory"):
        list(discover(DirReader(str(tmp_path / "nope")), "nl"))


LISTING = """<html><body><h1>Index of /reports/</h1>
<a href="../">Parent</a> <a href="?C=N;O=D">Name</a> <a href="2026/">2026/</a>
<a href="/reports/2025/">2025/</a> <a href="info.yaml">info.yaml</a>
<a href="https://elsewhere.test/x/">other</a> <a href="/other/2024/">2024/</a> <a href="a/b.yaml">deep</a>
</body></html>"""


def test_http_listing_keeps_only_direct_children():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/reports/"
        return httpx.Response(200, text=LISTING)

    reader = HttpReader("https://example.test/reports", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert reader.children(()) == (["2025", "2026"], ["info.yaml"])


def test_http_errors_become_source_errors():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    with pytest.raises(SourceError, match="HTTP 404"):
        HttpReader("https://example.test/reports/", client=client).children(())


def test_normalize_location(tmp_path):
    assert normalize_location(" HTTPS://Example.test/reports/info.yaml ") == ("http", "https://Example.test/reports/")
    assert normalize_location(str(tmp_path)) == ("dir", str(tmp_path.resolve()))
    with pytest.raises(SourceError, match="not a directory"):
        normalize_location(str(tmp_path / "missing"))


def test_profile_from_info_yaml(tree):
    assert read_profile(DirReader(str(tree))) == {
        "name": ".nl",
        "short_service": "nl",
        "operator": "SIDN",
        "country": "NL",
    }
    (tree / "info.yaml").unlink()
    assert read_profile(DirReader(str(tree))) == {}


def test_validate_profile():
    profile = validate_profile({"name": "NL", "short_service": "nl", "operator": " SIDN ", "country": "nl"})
    assert profile == {"name": ".nl", "short_service": "nl", "operator": "SIDN", "country": "NL", "import_from": ""}
    named = validate_profile({"name": "B.Root-Servers.net", "short_service": "b-root", "import_from": "2025-03-01"})
    assert (named["name"], named["import_from"]) == ("b.root-servers.net", "2025-03-01")
    for bad in (
        {"name": "", "short_service": "nl"},
        {"name": ".nl", "short_service": ""},
        {"name": ".nl", "short_service": "nl", "country": "NLD"},
        {"name": ".nl", "short_service": "nl", "import_from": "March"},
    ):
        with pytest.raises(SourceError):
            validate_profile(bad)
