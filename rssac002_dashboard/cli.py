"""Command line: run the dashboard, manage sources without the web admin, create demo data."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from .auth import hash_password
from .config import Settings, load_env_file
from .importers import IMPORTERS, add_sources
from .scanner import run_scan
from .sources import SourceError, make_reader, normalize_location, read_profile, validate_profile
from .store import Store


def _scan(store: Store, source: dict, mode: str) -> bool:
    scan_id = store.create_scan(source["id"], mode)
    counters = run_scan(store, source, mode, scan_id)
    scan = store.list_scans(source["id"], limit=1)[0]
    line = "{name}: {found} found, {imported} imported, {updated} updated, {failed} failed".format(
        name=source["name"], **counters
    )
    print(line + (f" ({scan['message']})" if scan["message"] else ""))
    for error in scan["errors"][:10]:
        print(f"  {error['origin']}: {error['error']}")
    return scan["status"] == "done" and not counters["failed"]


def _add(store: Store, args) -> int:
    try:
        kind, location = normalize_location(args.location)
        reader = make_reader(kind, location, store.path.parent / "repos")
        try:
            profile = read_profile(reader)
        finally:
            reader.close()
        typed = {
            "name": args.name,
            "short_service": args.short_service,
            "operator": args.operator,
            "country": args.country,
            "import_from": getattr(args, "since", None),
        }
        profile = validate_profile({**profile, **{key: value for key, value in typed.items() if value}})
    except SourceError as exc:
        print(f"Cannot add source: {exc}", file=sys.stderr)
        return 2
    existing = next((s for s in store.list_sources() if s["location"] == location), None)
    source_id = existing["id"] if existing else store.add_source(kind=kind, location=location, **profile)
    return 0 if _scan(store, store.get_source(source_id), "new") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rssac002-dashboard", description=__doc__)
    parser.add_argument("--db", type=Path, help="database file (default: $RSSAC002_DB or data/dashboard.sqlite3)")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the web dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    add = commands.add_parser("add", help="add a report endpoint URL or local directory, and import it")
    add.add_argument("location")
    add.add_argument("--name", help="for example .nl (read from info.yaml when published)")
    add.add_argument("--short-service", help="prefix of the report file names, for example nl")
    add.add_argument("--operator")
    add.add_argument("--country", help="two-letter ISO 3166 code")
    add.add_argument("--since", help="import reports from this day on (YYYY-MM-DD), for sources with a long history")

    scan = commands.add_parser("scan", help="import new reports from every source")
    scan.add_argument("--full", action="store_true", help="re-fetch every report and replace changed ones")

    commands.add_parser("sources", help="list sources")
    commands.add_parser("importers", help="list the known publishers that can be imported by name")
    known = commands.add_parser("import", help="add a known publisher's services and import their reports")
    known.add_argument("key", choices=sorted(IMPORTERS), help="name shown by the importers command")
    known.add_argument("--since", default="", help="import reports from this day on (YYYY-MM-DD)")
    remove = commands.add_parser("remove", help="remove a source and its imported reports from the dashboard")
    remove.add_argument("id", type=int, help="number shown by the sources command")
    commands.add_parser("hash-password", help="create the value for RSSAC002_ADMIN_PASSWORD_HASH")

    demo = commands.add_parser("demo", help="generate synthetic reports for three made-up services and import them")
    demo.add_argument("--dir", type=Path, default=Path("demo-reports"))
    demo.add_argument("--days", type=int, default=150)

    args = parser.parse_args(argv)
    load_env_file()  # settings from a .env file in the current folder, if there is one
    settings = Settings()
    if args.db:
        settings.db_path = args.db

    if args.command == "hash-password":
        password = getpass.getpass("Admin password: ")
        if not password or password != getpass.getpass("Repeat: "):
            print("Passwords are empty or do not match.", file=sys.stderr)
            return 2
        print(hash_password(password))
        return 0

    if args.command == "serve":
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port)
        return 0

    if args.command == "importers":
        for importer in IMPORTERS.values():
            print(f"{importer.key:<14} {importer.title}: {len(importer.sources)} service(s)")
            print(f"{'':<15}{importer.description}")
        return 0

    store = Store(settings.db_path)
    if args.command == "import":
        try:
            ids = add_sources(store, IMPORTERS[args.key], args.since)
        except SourceError as exc:
            print(f"Cannot import: {exc}", file=sys.stderr)
            return 2
        results = [_scan(store, store.get_source(source_id), "new") for source_id in ids]
        return 0 if all(results) else 1
    if args.command == "add":
        return _add(store, args)
    if args.command == "scan":
        ordered = sorted(store.list_sources(), key=lambda source: source["kind"] == "aggregate")  # totals last
        results = [_scan(store, source, "full" if args.full else "new") for source in ordered]
        return 0 if all(results) else 1
    if args.command == "remove":
        source = store.get_source(args.id)
        if source is None or not store.delete_source(args.id):
            print(f"There is no source {args.id}.", file=sys.stderr)
            return 2
        print(f"Removed {source['name']} and its imported reports. The files at {source['location']} are untouched.")
        return 0
    if args.command == "sources":
        for source in store.list_sources():
            span = f"{source['first_day']} to {source['last_day']}" if source["reports"] else "no reports"
            print(
                f"{source['id']:>3}  {source['name']:<12} {source['reports']:>7} reports  {span}  {source['location']}"
            )
        return 0
    if args.command == "demo":
        from .demo import write_demo_tree

        ok = True
        for profile in write_demo_tree(args.dir, days=args.days):
            args.location, args.name, args.short_service = str(profile["path"]), None, None
            args.operator, args.country, args.since = None, None, None
            ok = _add(store, args) == 0 and ok
        print(f"Demo reports are in {args.dir}/. Start the dashboard with: rssac002-dashboard serve")
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
