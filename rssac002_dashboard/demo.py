"""Write a tree of synthetic reports, so the dashboard can be tried without real data.

The numbers are made up but shaped like DNS traffic: a weekly rhythm, slow growth, a rising
IPv6 share, per-nameserver reports, one traffic spike and a few missing days.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from pathlib import Path

import yaml

from .parsing import BUCKET_WIDTH

_Dumper = getattr(yaml, "CSafeDumper", yaml.SafeDumper)

DEMO_SERVICES = [
    {"name": ".example", "short": "example", "operator": "Example Registry", "country": "NL", "scale": 1.0},
    {"name": ".test", "short": "test", "operator": "Test Network Information Centre", "country": "SE", "scale": 0.35},
    {"name": ".invalid", "short": "invalid", "operator": "Invalid Domains Ltd", "country": "", "scale": 0.08},
]
SERVERS = {"ns1": 0.5, "ns2": 0.3, "ns3": 0.2}


def _split(total: int, weights: dict) -> dict:
    scale = sum(weights.values())
    return {key: round(total * weight / scale) for key, weight in weights.items()}


def _histogram(rng: random.Random, total: int, centre: float, spread: float, open_lower: int, tail: float) -> dict:
    """Counts per 16-byte bucket: a bell around `centre` plus a thin tail of large messages."""
    lowers = range(0, open_lower + 1, BUCKET_WIDTH)
    weights = [
        math.exp(-((lower - centre) ** 2) / (2 * spread**2)) + tail * math.exp(-lower / 900) + rng.random() * 1e-4
        for lower in lowers
    ]
    scale = sum(weights)
    histogram = {}
    for lower, weight in zip(lowers, weights, strict=True):
        count = round(total * weight / scale)
        if count:
            key = f"{lower}-" if lower == open_lower else f"{lower}-{lower + BUCKET_WIDTH - 1}"
            histogram[key] = count
    return histogram


def _day_reports(rng: random.Random, queries: int, progress: float, spike: bool) -> dict[str, dict]:
    ipv6 = 0.24 + 0.10 * progress + rng.uniform(-0.01, 0.01)
    tcp = 0.035 + rng.uniform(-0.004, 0.004) + (0.05 if spike else 0)
    received = _split(
        queries,
        {
            "dns-udp-queries-received-ipv4": (1 - tcp) * (1 - ipv6),
            "dns-udp-queries-received-ipv6": (1 - tcp) * ipv6,
            "dns-tcp-queries-received-ipv4": tcp * (1 - ipv6),
            "dns-tcp-queries-received-ipv6": tcp * ipv6,
        },
    )
    answered = 0.985 - (0.03 if spike else 0)
    sent = {
        name.replace("queries-received", "responses-sent"): round(count * answered) for name, count in received.items()
    }
    responses = sum(sent.values())
    nxdomain = 0.11 + rng.uniform(-0.01, 0.01) + (0.25 if spike else 0)
    rcodes = _split(responses, {0: 1 - nxdomain - 0.012, 3: nxdomain, 2: 0.004, 5: 0.006, 1: 0.002})
    rcodes[-1] = queries - responses
    udp_q = received["dns-udp-queries-received-ipv4"] + received["dns-udp-queries-received-ipv6"]
    tcp_q = queries - udp_q
    udp_r = sent["dns-udp-responses-sent-ipv4"] + sent["dns-udp-responses-sent-ipv6"]
    sources_v4 = round(queries**0.72 * (1.6 if spike else 1) * rng.uniform(0.97, 1.03))
    sources_v6 = round(sources_v4 * (0.30 + 0.12 * progress))
    return {
        "traffic-volume": {**received, **sent},
        "rcode-volume": {code: count for code, count in rcodes.items() if count > 0},
        "unique-sources": {
            "num-sources-ipv4": sources_v4,
            "num-sources-ipv6": sources_v6,
            "num-sources-ipv6-aggregate": round(sources_v6 * 0.62),
        },
        "traffic-sizes": {
            "udp-request-sizes": _histogram(rng, udp_q, 56, 14, 288, 0.0),
            "tcp-request-sizes": _histogram(rng, tcp_q, 92, 18, 288, 0.0),
            "udp-response-sizes": _histogram(rng, udp_r, 150 + 40 * progress, 60, 4096, 0.06),
            "tcp-response-sizes": _histogram(rng, responses - udp_r, 900, 420, 4096, 0.3),
        },
    }


def write_demo_tree(root: Path, days: int = 150, end: dt.date | None = None, seed: int = 2026) -> list[dict]:
    """Write one publication tree per demo service under `root`. Returns their profiles with a 'path' each."""
    rng = random.Random(seed)
    end = end or dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
    first = end - dt.timedelta(days=days - 1)
    profiles = []
    for service in DEMO_SERVICES:
        tree = root / service["short"]
        tree.mkdir(parents=True, exist_ok=True)
        info = {"name": service["name"], "short-service": service["short"], "operator": service["operator"]}
        if service["country"]:
            info["country-iso3166"] = service["country"]
        (tree / "info.yaml").write_text(yaml.safe_dump(info, sort_keys=False))
        spike_day = first + dt.timedelta(days=int(days * 0.7))
        missing = {first + dt.timedelta(days=int(days * fraction)) for fraction in (0.31, 0.32, 0.55)}
        for offset in range(days):
            day = first + dt.timedelta(days=offset)
            if day in missing and service is DEMO_SERVICES[0]:
                continue
            progress = offset / max(days - 1, 1)
            weekly = 0.88 if day.weekday() >= 5 else 1.0
            spike = service is DEMO_SERVICES[0] and day == spike_day
            base = 1.9e9 * service["scale"] * (1 + 0.18 * progress) * weekly * rng.uniform(0.97, 1.03)
            total = round(base * (2.4 if spike else 1))
            for server, share in {"": 1.0, **SERVERS}.items():
                reports = _day_reports(
                    random.Random(f"{seed}{service['short']}{day}{server}"), round(total * share), progress, spike
                )
                for metric, values in reports.items():
                    folder = tree / f"{day:%Y}" / f"{day:%m}" / metric / ("nameservers" if server else "")
                    folder.mkdir(parents=True, exist_ok=True)
                    prefix = f"{service['short']}-{server}" if server else service["short"]
                    document = {
                        "version": "rssac002v5",
                        "service": f"{service['short']}-servers-{server}" if server else f"{service['short']}-servers",
                        "start-period": f"{day.isoformat()}T00:00:00Z",
                        "metric": metric,
                        **values,
                    }
                    path = folder / f"{prefix}-{day:%Y%m%d}-{metric}.yaml"
                    path.write_text(yaml.dump(document, Dumper=_Dumper, sort_keys=False, explicit_start=True))
        profiles.append({**service, "path": tree})
    return profiles
