import datetime as dt
from pathlib import Path

import pytest
import yaml

from rssac002_dashboard.store import Store

BODIES = {
    "traffic-volume": {
        "dns-udp-queries-received-ipv4": 700,
        "dns-udp-queries-received-ipv6": 200,
        "dns-tcp-queries-received-ipv4": 60,
        "dns-tcp-queries-received-ipv6": 40,
        "dns-udp-responses-sent-ipv4": 690,
        "dns-udp-responses-sent-ipv6": 195,
        "dns-tcp-responses-sent-ipv4": 60,
        "dns-tcp-responses-sent-ipv6": 40,
    },
    "rcode-volume": {0: 800, 3: 150, 2: 20, 5: 15, -1: 15},
    "unique-sources": {"num-sources-ipv4": 80, "num-sources-ipv6": 20, "num-sources-ipv6-aggregate": 12},
    "traffic-sizes": {
        "udp-request-sizes": {"32-47": 600, "48-63": 300},
        "tcp-request-sizes": {"48-63": 100},
        "udp-response-sizes": {"96-111": 500, "112-127": 300, "1232-1247": 85},
        "tcp-response-sizes": {"1024-1039": 90, "4096-": 10},
    },
}


def report_text(metric: str, day: dt.date, service: str = "nl-servers", **overrides) -> str:
    document = {"version": "rssac002v5", "service": service, "start-period": f"{day}T00:00:00Z", "metric": metric}
    document.update(BODIES[metric])
    document.update(overrides)
    return yaml.safe_dump(document, sort_keys=False, explicit_start=True)


def write_report(root: Path, short: str, day: dt.date, metric: str, server: str = "", text: str | None = None) -> Path:
    folder = root / f"{day:%Y}" / f"{day:%m}" / metric / ("nameservers" if server else "")
    folder.mkdir(parents=True, exist_ok=True)
    prefix = f"{short}-{server}" if server else short
    path = folder / f"{prefix}-{day:%Y%m%d}-{metric}.yaml"
    path.write_text(text if text is not None else report_text(metric, day))
    return path


@pytest.fixture
def tree(tmp_path) -> Path:
    """Reports for 'nl' on 1-3 May 2026, all four metrics, plus nameserver ns1 on 1 May."""
    root = tmp_path / "reports"
    for offset in range(3):
        day = dt.date(2026, 5, 1) + dt.timedelta(days=offset)
        for metric in BODIES:
            write_report(root, "nl", day, metric)
    for metric in BODIES:
        write_report(root, "nl", dt.date(2026, 5, 1), metric, server="ns1")
    (root / "info.yaml").write_text("tld: .nl\nshort-service: nl\ntld-operator: SIDN\ncountry-iso3166: NL\n")
    return root


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "db" / "dashboard.sqlite3")


@pytest.fixture
def source(store, tree) -> dict:
    source_id = store.add_source(
        name=".nl", short_service="nl", operator="SIDN", country="NL", kind="dir", location=str(tree)
    )
    return store.get_source(source_id)
