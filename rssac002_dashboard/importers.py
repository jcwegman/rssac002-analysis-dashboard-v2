"""Known publishers of RSSAC002-style reports, ready to add with one command or click.

An importer is a named list of sources: where an operator's reports live and which services
they cover. To add a publisher, add an entry to IMPORTERS below. Each source needs:

    kind           "git", "http" or "dir" (see READERS in sources.py), or "aggregate" for a service
                   that is the sum of others: its location is "aggregate:" and the members'
                   file-name prefixes, and it must come after its members (see aggregate.py)
    location       the repository or listing address; for Git, "#folder" names the folder
                   inside the repository that holds the year folders
    name           how the service is shown, such as ".nl" or "b.root-servers.net"
    short_service  the prefix of its report file names, such as "nl" or "b-root"
    operator       who runs it (optional)
    country        two-letter ISO 3166 code (optional)

If a publisher needs a way of fetching that does not exist yet, write a reader for it in
sources.py first; the importer then only names that reader's kind.
"""

from __future__ import annotations

from dataclasses import dataclass

from .sources import validate_profile
from .store import Store

ROOT_REPOSITORY = "https://github.com/rssac-caucus/RSSAC002-data"
ROOT_OPERATORS = {
    "a": ("Verisign", "US"),
    "b": ("USC Information Sciences Institute", "US"),
    "c": ("Cogent Communications", "US"),
    "d": ("University of Maryland", "US"),
    "e": ("NASA Ames Research Center", "US"),
    "f": ("Internet Systems Consortium", "US"),
    "g": ("US Department of Defense (NIC)", "US"),
    "h": ("US Army Research Lab", "US"),
    "i": ("Netnod", "SE"),
    "j": ("Verisign", "US"),
    "k": ("RIPE NCC", "NL"),
    "l": ("ICANN", "US"),
    "m": ("WIDE Project", "JP"),
}


@dataclass(frozen=True)
class Importer:
    key: str
    title: str
    description: str
    sources: tuple[dict, ...]


IMPORTERS = {
    importer.key: importer
    for importer in (
        Importer(
            key="sidn-nl",
            title=".nl (SIDN)",
            description="Daily reports for the .nl authoritative servers, combined and per nameserver, "
            "from SIDN's repository github.com/SIDN/nl-rssac002.",
            sources=(
                {
                    "kind": "git",
                    "location": "https://github.com/SIDN/nl-rssac002#nl-rssac002",
                    "name": ".nl",
                    "short_service": "nl",
                    "operator": "SIDN",
                    "country": "NL",
                },
            ),
        ),
        Importer(
            key="root-servers",
            title="Root servers (RSSAC Caucus collection)",
            description="The thirteen root servers, a to m, and their total, from the collection the RSSAC Caucus "
            "keeps at github.com/rssac-caucus/RSSAC002-data. It goes back to 2013; give a start day to import less.",
            sources=tuple(
                {
                    "kind": "git",
                    "location": ROOT_REPOSITORY,
                    "name": f"{letter}.root-servers.net",
                    "short_service": f"{letter}-root",
                    "operator": operator,
                    "country": country,
                }
                for letter, (operator, country) in ROOT_OPERATORS.items()
            )
            # Last, so that it is computed after the thirteen it adds up have been imported.
            + (
                {
                    "kind": "aggregate",
                    "location": "aggregate:" + ",".join(f"{letter}-root" for letter in ROOT_OPERATORS),
                    "name": "root-servers.net",
                    "short_service": "root-servers",
                    "operator": "All 13 root servers, added up by the dashboard",
                    "country": "",
                },
            ),
        ),
    )
}


def add_sources(store: Store, importer: Importer, import_from: str = "") -> list[int]:
    """Add the importer's sources that are not there yet. Returns the ids of all of them, old and new."""
    existing = {(source["location"], source["short_service"]): source["id"] for source in store.list_sources()}
    ids = []
    for spec in importer.sources:
        key = (spec["location"], spec["short_service"])
        if key not in existing:
            profile = validate_profile({**spec, "import_from": import_from})
            existing[key] = store.add_source(kind=spec["kind"], location=spec["location"], **profile)
        ids.append(existing[key])
    return ids
