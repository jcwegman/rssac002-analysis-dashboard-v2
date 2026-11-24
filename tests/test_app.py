import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from rssac002_dashboard.app import create_app
from rssac002_dashboard.auth import Sessions, hash_password, verify_password
from rssac002_dashboard.config import Settings
from rssac002_dashboard.scanner import run_scan

HEADERS = {"X-Dashboard-Request": "1"}


def make_client(tmp_path, password: str | None = "correct horse", **overrides) -> TestClient:
    settings = Settings(
        db_path=tmp_path / "db.sqlite3", scan_interval_hours=0, session_secret="test-secret", **overrides
    )
    settings.admin_password_hash = hash_password(password) if password else ""
    return TestClient(create_app(settings))


@pytest.fixture
def client(tmp_path, tree):
    with make_client(tmp_path) as client:
        store = client.app.state.store
        source_id = store.add_source(
            name=".nl", short_service="nl", operator="SIDN", country="NL", kind="dir", location=str(tree)
        )
        run_scan(store, store.get_source(source_id), "new", store.create_scan(source_id, "new"))
        yield client


def sign_in(client):
    assert client.post("/api/session", json={"password": "correct horse"}, headers=HEADERS).status_code == 200


def wait_for_scans(client, source_id):
    for _ in range(100):
        scans = client.get(f"/api/sources/{source_id}/scans").json()["scans"]
        if scans and scans[0]["status"] in ("done", "failed"):
            return scans[0]
        time.sleep(0.05)
    raise AssertionError("scan did not finish")


def test_password_hash_roundtrip():
    stored = hash_password("s3cret")
    assert verify_password("s3cret", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("s3cret", "not-a-hash")


def test_session_tokens_expire_and_cannot_be_forged():
    sessions = Sessions("secret", 60)
    token = sessions.issue()
    assert sessions.valid(token)
    assert not sessions.valid(token[:-1] + ("0" if token[-1] != "0" else "1"))
    assert not Sessions("other", 60).valid(token)
    assert not Sessions("secret", -1).valid(Sessions("secret", -1).issue())


def test_meta_hides_locations_from_visitors(client):
    meta = client.get("/api/meta").json()
    assert meta["sources"][0]["name"] == ".nl"
    assert meta["sources"][0]["reports"] == 16
    assert "location" not in meta["sources"][0]
    assert meta["admin"] == {"configured": True, "signed_in": False}
    sign_in(client)
    assert "location" in client.get("/api/meta").json()["sources"][0]


def test_series_and_coverage(client):
    data = client.get("/api/series", params={"source": 1, "from": "2026-05-01", "to": "2026-05-03"}).json()
    assert data["derived"]["queries"] == [1000, 1000, 1000]
    assert client.get("/api/coverage", params={"source": 1}).json() == {
        "first": "2026-05-01",
        "last": "2026-05-03",
        "counts": "444",
    }
    assert client.get("/api/series", params={"source": 99}).status_code == 404
    assert client.get("/api/series", params={"source": 1, "from": "yesterday"}).status_code == 422
    assert client.get("/api/series", params={"source": 1, "from": "2026-05-03", "to": "2026-05-01"}).status_code == 422


def test_report_files_are_returned_verbatim(client, tree):
    listing = client.get("/api/reports", params={"source": 1, "metric": "rcode-volume"}).json()["reports"]
    assert [row["filename"] for row in listing] == [f"nl-2026050{day}-rcode-volume.yaml" for day in (3, 2, 1)]
    original = (tree / "2026" / "05" / "rcode-volume" / "nl-20260503-rcode-volume.yaml").read_text()
    assert client.get(f"/api/reports/{listing[0]['id']}").text == original
    download = client.get(f"/api/reports/{listing[0]['id']}", params={"download": 1})
    assert "nl-20260503-rcode-volume.yaml" in download.headers["content-disposition"]


def test_archive_uses_the_publication_layout(client):
    response = client.get("/api/archive", params={"source": 1, "from": "2026-05-01", "to": "2026-05-01"})
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert "2026/05/rcode-volume/nl-20260501-rcode-volume.yaml" in names
    assert "2026/05/rcode-volume/nameservers/nl-ns1-20260501-rcode-volume.yaml" in names
    assert len(names) == 8
    assert client.get("/api/archive", params={"source": 1, "from": "2030-01-01"}).status_code == 404


def test_republished_tree(client, tree):
    assert 'href="2026/"' in client.get("/rssac002/").text
    assert 'href="05/"' in client.get("/rssac002/2026/").text
    assert 'href="rcode-volume/"' in client.get("/rssac002/2026/05/").text
    listing = client.get("/rssac002/2026/05/rcode-volume/").text
    assert 'href="nameservers/"' in listing and 'href="nl-20260501-rcode-volume.yaml"' in listing
    assert 'href="nl-ns1-20260501-rcode-volume.yaml"' in client.get("/rssac002/2026/05/rcode-volume/nameservers/").text
    original = (tree / "2026" / "05" / "rcode-volume" / "nl-20260501-rcode-volume.yaml").read_text()
    assert client.get("/rssac002/2026/05/rcode-volume/nl-20260501-rcode-volume.yaml").text == original
    assert client.get("/rssac002/2026/05/rcode-volume/nl-20260509-rcode-volume.yaml").status_code == 404
    assert client.get("/rssac002/2031/").status_code == 404


def test_the_dashboard_can_import_from_another_dashboard(client, tmp_path):
    """The republished tree is itself a valid source: listing pages and files round-trip."""
    from rssac002_dashboard.sources import HttpReader, discover

    reader = HttpReader("http://testserver/rssac002/", client=client)
    found = list(discover(reader, "nl"))
    assert len(found) == 16
    assert reader.read(found[0].parts).startswith("---")


def test_admin_actions_need_a_session_and_the_request_header(client, tree):
    body = {"location": str(tree)}
    assert client.post("/api/sources/preview", json=body, headers=HEADERS).status_code == 401
    assert client.post("/api/session", json={"password": "wrong"}, headers=HEADERS).status_code == 401
    assert client.post("/api/session", json={"password": "correct horse"}).status_code == 403
    sign_in(client)
    assert client.post("/api/sources/preview", json=body).status_code == 403
    preview = client.post("/api/sources/preview", json=body, headers=HEADERS).json()
    assert preview["has_info"] and preview["profile"]["name"] == ".nl"


def test_repeated_wrong_passwords_are_throttled(client):
    for _ in range(5):
        assert client.post("/api/session", json={"password": "wrong"}, headers=HEADERS).status_code == 401
    assert client.post("/api/session", json={"password": "correct horse"}, headers=HEADERS).status_code == 429


def test_add_scan_and_remove_a_source(client, tree, tmp_path):
    sign_in(client)
    assert client.post("/api/sources", json={"location": str(tree)}, headers=HEADERS).status_code == 409

    other = tmp_path / "other"
    other.mkdir()
    assert client.post("/api/sources", json={"location": str(other)}, headers=HEADERS).status_code == 422
    created = client.post(
        "/api/sources", json={"location": str(other), "name": "be", "short_service": "be"}, headers=HEADERS
    )
    assert created.status_code == 201
    source_id = created.json()["id"]
    scan = wait_for_scans(client, source_id)
    assert scan["status"] == "failed" or scan["found"] == 0

    assert client.post(f"/api/sources/{source_id}/scan", json={"mode": "sideways"}, headers=HEADERS).status_code == 422
    assert client.post(f"/api/sources/{source_id}/scan", json={"mode": "full"}, headers=HEADERS).status_code == 202
    wait_for_scans(client, source_id)
    assert client.delete(f"/api/sources/{source_id}", headers=HEADERS).status_code == 200
    assert client.delete(f"/api/sources/{source_id}", headers=HEADERS).status_code == 404

    assert client.delete("/api/session", headers=HEADERS).json() == {"signed_in": False}
    assert client.get("/api/sources/1/scans").status_code == 401


def test_without_a_password_there_is_no_admin(tmp_path):
    with make_client(tmp_path, password=None) as client:
        assert client.get("/api/meta").json()["admin"] == {"configured": False, "signed_in": False}
        assert client.post("/api/session", json={"password": ""}, headers=HEADERS).status_code == 503


def test_interface_and_security_headers(client):
    page = client.get("/")
    assert "RSSAC002 Analysis Dashboard" in page.text
    # Visitors can see who made it, find the source and open an issue.
    assert "Jorrit Wegman, TU Delft" in page.text
    assert 'href="https://github.com/jcwegman/rssac002-analysis-dashboard-v2/issues"' in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    script = client.get("/static/js/main.js")
    assert script.status_code == 200 and script.headers["cache-control"] == "no-cache"


def test_csv_and_size_matrix_endpoints(client):
    params = {"source": 1, "from": "2026-05-01", "to": "2026-05-03"}
    csv = client.get("/api/series.csv", params=params)
    assert csv.headers["content-type"].startswith("text/csv")
    assert "nl-rssac002-20260501-20260503.csv" in csv.headers["content-disposition"]
    assert len(csv.text.splitlines()) == 4
    matrix = client.get("/api/sizes", params=params).json()
    assert len(matrix["days"]) == 3 and matrix["histograms"]["udp-response-sizes"]["rows"][0][77] == 85


def test_events_are_public_to_read_and_admin_to_change(client):
    body = {"source_id": 1, "day": "2026-05-02", "label": "Capture outage"}
    assert client.post("/api/events", json=body, headers=HEADERS).status_code == 401
    sign_in(client)
    assert client.post("/api/events", json={**body, "label": " "}, headers=HEADERS).status_code == 422
    event_id = client.post("/api/events", json=body, headers=HEADERS).json()["id"]
    client.post("/api/events", json={"day": "2026-05-03", "label": "Applies everywhere"}, headers=HEADERS)
    assert [event["label"] for event in client.get("/api/events", params={"source": 1}).json()["events"]] == [
        "Capture outage",
        "Applies everywhere",
    ]
    assert client.delete(f"/api/events/{event_id}", headers=HEADERS).status_code == 200
    assert len(client.get("/api/events").json()["events"]) == 1


def test_quality_endpoint(client):
    quality = client.get("/api/quality", params={"source": 1, "from": "2026-05-01", "to": "2026-05-03"}).json()
    assert quality["complete_days"] == 3 and quality["notes"]["count"] == 0
    assert all(check["days_failing"] == 0 for check in quality["checks"])


def test_env_file_is_read_without_overriding(tmp_path, monkeypatch):
    from rssac002_dashboard.config import load_env_file

    env = tmp_path / ".env"
    env.write_text("# comment\nRSSAC002_TEST_A='quoted value'\nRSSAC002_TEST_B=from-file\n\nnot a setting\n")
    monkeypatch.delenv("RSSAC002_TEST_A", raising=False)
    monkeypatch.setenv("RSSAC002_TEST_B", "already-set")
    load_env_file(env)
    import os

    assert os.environ["RSSAC002_TEST_A"] == "quoted value"
    assert os.environ["RSSAC002_TEST_B"] == "already-set"
    monkeypatch.delenv("RSSAC002_TEST_A")


def test_importers_are_listed_and_need_admin_to_run(client):
    listed = {one["key"]: one for one in client.get("/api/importers").json()["importers"]}
    assert listed["root-servers"]["services"][0] == "a.root-servers.net" and listed["root-servers"]["added"] == 0
    assert client.post("/api/importers/sidn-nl", json={}, headers=HEADERS).status_code == 401
    sign_in(client)
    assert client.post("/api/importers/nope", json={}, headers=HEADERS).status_code == 404
    assert client.post("/api/importers/sidn-nl", json={"import_from": "soon"}, headers=HEADERS).status_code == 422


def add_reports(client, tree):
    store = client.app.state.store
    source_id = store.add_source(
        name=".nl", short_service="nl", operator="", country="", kind="dir", location=str(tree)
    )
    run_scan(store, store.get_source(source_id), "new", store.create_scan(source_id, "new"))


def test_a_client_that_floods_the_api_is_told_to_wait(tmp_path, tree):
    with make_client(tmp_path, rate_limit_per_minute=5) as client:
        add_reports(client, tree)
        assert [client.get("/api/meta").status_code for _ in range(5)] == [200] * 5
        refused = client.get("/api/meta")
        assert refused.status_code == 429 and int(refused.headers["retry-after"]) >= 1
        assert "Too many requests" in refused.json()["detail"]
        assert client.get("/rssac002/").status_code == 429  # the published tree shares the budget
        assert (
            client.get("/").status_code == 200 and client.get("/healthz").status_code == 200
        )  # the page itself does not


def test_zip_downloads_use_up_the_budget_quickly(tmp_path, tree):
    with make_client(tmp_path, rate_limit_per_minute=100) as client:
        add_reports(client, tree)
        params = {"source": 1, "from": "2026-05-01", "to": "2026-05-01"}
        assert [client.get("/api/archive", params=params).status_code for _ in range(3)] == [200, 200, 429]


def test_behind_a_proxy_each_visitor_has_a_budget_of_their_own(tmp_path):
    with make_client(tmp_path, rate_limit_per_minute=2, trust_proxy=True) as client:
        one, two = {"X-Forwarded-For": "198.51.100.7"}, {"X-Forwarded-For": "203.0.113.9"}
        assert [client.get("/api/meta", headers=one).status_code for _ in range(3)] == [200, 200, 429]
        assert client.get("/api/meta", headers=two).status_code == 200
    with make_client(tmp_path, rate_limit_per_minute=2) as client:  # header ignored unless the proxy is trusted
        codes = [client.get("/api/meta", headers={"X-Forwarded-For": f"198.51.100.{n}"}).status_code for n in range(3)]
        assert codes == [200, 200, 429]


def test_zip_downloads_are_capped(tmp_path, tree):
    with make_client(tmp_path, max_archive_reports=4) as client:
        add_reports(client, tree)
        assert client.get("/api/meta").json()["limits"] == {"archive_reports": 4}
        assert (
            client.get("/api/archive", params={"source": 1, "from": "2026-05-01", "to": "2026-05-01"}).status_code
            == 413
        )
        small = client.get("/api/archive", params={"source": 1, "server": "", "from": "2026-05-01", "to": "2026-05-01"})
        assert small.status_code == 200
