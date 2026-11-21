import datetime as dt
import shutil
import subprocess

import pytest

from rssac002_dashboard import sources
from rssac002_dashboard.importers import IMPORTERS, add_sources
from rssac002_dashboard.scanner import run_scan
from rssac002_dashboard.sources import GitReader, SourceError, discover, normalize_location

from .conftest import write_report

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def git(repo, *args):
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.org", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", *identity, *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path, tree):
    """A Git repository whose 'reports' folder holds the standard test tree."""
    repo = tmp_path / "upstream"
    shutil.copytree(tree, repo / "reports")
    (repo / "README.md").write_text("reports live in reports/\n")
    git(repo, "init", "--quiet", "--initial-branch", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "reports")
    return repo


def test_reads_listings_and_files_without_a_checkout(repository, tmp_path):
    reader = GitReader(str(repository), tmp_path / "cache")
    try:
        assert reader.prefix == ("reports",)  # found without being told
        assert reader.children(()) == (["2026"], ["info.yaml"])
        found = list(discover(reader, "nl"))
        assert len(found) == 16 and {item.server for item in found} == {"", "ns1"}
        assert reader.read(found[0].parts).startswith("---\nversion: rssac002v5")
        assert reader.origin(found[0].parts).endswith("#reports/" + "/".join(found[0].parts))
        with pytest.raises(SourceError, match="no folder"):
            reader.children(("2031",))
        with pytest.raises(SourceError, match="no file"):
            reader.read(("2026", "nothing.yaml"))
        assert reader.read(("info.yaml",)).startswith("tld: .nl")  # still usable after a miss
    finally:
        reader.close()
    assert not list((tmp_path / "cache").glob("*/reports"))  # bare copy: no working tree


def test_new_commits_are_picked_up_on_refresh(repository, tmp_path, monkeypatch):
    GitReader(str(repository), tmp_path / "cache").close()
    write_report(repository / "reports", "nl", dt.date(2026, 5, 9), "rcode-volume")
    git(repository, "add", "-A")
    git(repository, "commit", "--quiet", "-m", "one more day")

    still_cached = GitReader(str(repository), tmp_path / "cache")
    assert len(list(discover(still_cached, "nl"))) == 16  # refreshed at most every ten minutes
    still_cached.close()
    monkeypatch.setattr(sources, "GIT_REFRESH_SECONDS", 0)
    refreshed = GitReader(str(repository), tmp_path / "cache")
    assert len(list(discover(refreshed, "nl"))) == 17
    refreshed.close()


def test_scan_imports_from_a_git_source(store, repository):
    source_id = store.add_source(
        name=".nl", short_service="nl", operator="SIDN", country="NL", kind="git", location=f"{repository}#reports"
    )
    scan_id = store.create_scan(source_id, "new")
    counters = run_scan(store, store.get_source(source_id), "new", scan_id)
    assert (counters["imported"], counters["failed"]) == (16, 0)
    assert (store.path.parent / "repos").is_dir()


def test_unreachable_repository_fails_the_scan_with_a_reason(store, tmp_path):
    source_id = store.add_source(
        name=".x", short_service="x", operator="", country="", kind="git", location=str(tmp_path / "missing")
    )
    run_scan(store, store.get_source(source_id), "new", store.create_scan(source_id, "new"))
    scan = store.list_scans(source_id)[0]
    assert scan["status"] == "failed" and "Git could not read" in scan["message"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "https://github.com/SIDN/nl-rssac002/tree/main/nl-rssac002",
            "https://github.com/SIDN/nl-rssac002#nl-rssac002",
        ),
        ("https://github.com/rssac-caucus/RSSAC002-data", "https://github.com/rssac-caucus/RSSAC002-data"),
        ("https://github.com/SIDN/nl-rssac002.git#nl-rssac002", "https://github.com/SIDN/nl-rssac002#nl-rssac002"),
        (
            "https://codeberg.org/owner/repo/src/branch/main/data/reports",
            "https://codeberg.org/owner/repo#data/reports",
        ),
        ("https://gitlab.com/owner/repo/-/tree/main/reports", "https://gitlab.com/owner/repo#reports"),
        ("git+https://git.example.org/team/reports", "https://git.example.org/team/reports"),
    ],
)
def test_repository_addresses_are_recognised(text, expected):
    assert normalize_location(text) == ("git", expected)


def test_other_addresses_are_not_mistaken_for_repositories():
    assert normalize_location("https://b.root-servers.org/rssac/")[0] == "http"
    with pytest.raises(SourceError, match="https://"):
        normalize_location("git+http://git.example.org/team/reports")


def test_importers_add_their_services_once(store):
    root = IMPORTERS["root-servers"]
    ids = add_sources(store, root, "2025-03-01")
    assert len(ids) == 14 and add_sources(store, root) == ids
    listed = {source["name"]: source for source in store.list_sources()}
    assert listed["k.root-servers.net"]["short_service"] == "k-root"
    assert listed["k.root-servers.net"]["import_from"] == "2025-03-01"
    assert listed["k.root-servers.net"]["kind"] == "git"
    assert len({source["location"] for source in listed.values() if source["kind"] == "git"}) == 1  # one repository
    total = listed["root-servers.net"]
    assert total["kind"] == "aggregate" and total["location"].count(",") == 12 and ids[-1] == total["id"]
    assert len(add_sources(store, IMPORTERS["sidn-nl"])) == 1
