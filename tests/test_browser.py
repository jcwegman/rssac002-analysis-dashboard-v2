"""Runs the interface in headless Chrome against a live server with demo data.

Skipped when Chrome or Node.js (22 or newer, for its built-in WebSocket) is not installed.
"""

import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from rssac002_dashboard.app import create_app
from rssac002_dashboard.auth import hash_password
from rssac002_dashboard.config import Settings
from rssac002_dashboard.demo import write_demo_tree
from rssac002_dashboard.scanner import run_scan

CHROME = next(filter(None, map(shutil.which, ("google-chrome", "chromium", "chromium-browser", "chrome"))), None)
NODE = shutil.which("node")
PASSWORD = "browser-test"


def node_is_recent() -> bool:
    try:
        version = subprocess.run([NODE, "--version"], capture_output=True, text=True, timeout=10).stdout
        return int(version.lstrip("v").split(".")[0]) >= 22
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not CHROME or not NODE or not node_is_recent(), reason="needs Chrome and Node.js 22 or newer")
def test_interface_in_a_browser(tmp_path):
    settings = Settings(db_path=tmp_path / "db.sqlite3", scan_interval_hours=0, session_secret="browser-test")
    settings.admin_password_hash = hash_password(PASSWORD)
    app = create_app(settings)
    store = app.state.store
    for profile in write_demo_tree(tmp_path / "reports", days=45):
        source_id = store.add_source(
            name=profile["name"], short_service=profile["short"], operator=profile["operator"],
            country=profile["country"], kind="dir", location=str(profile["path"]),
        )  # fmt: skip
        run_scan(store, store.get_source(source_id), "new", store.create_scan(source_id, "new"))

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        script = Path(__file__).parent / "browser" / "check.mjs"
        result = subprocess.run(
            [NODE, str(script), f"http://127.0.0.1:{port}", CHROME, PASSWORD],
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
