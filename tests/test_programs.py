"""Decisions for the system-tab program rows."""

import threading
import time

from fastapi.testclient import TestClient

from nao_fair.app import create_app
from nao_fair.programs import autostart_overview, install_action, release_tuple


def test_release_tags_compare_only_as_versions():
    assert release_tuple("v1.2.3") == (1, 2, 3)
    assert release_tuple("main") is None
    assert install_action(None, "v0.2.0", False) == "install"
    assert install_action("main", "v0.2.0", True) == "install"
    assert install_action("v0.1.0", "v0.2.0", True) == "update"
    assert install_action("v0.2.0", "v0.2.0", True) == "reinstall"
    assert install_action("v0.3.0", "v0.2.0", True) is None
    assert install_action("v0.1.0", None, True) is None


def test_autostart_overview_follows_the_connection():
    assert autostart_overview(connected=False, reachable=None, answering=None) == "unknown"
    assert autostart_overview(connected=False, reachable=False, answering=None) == "blocked"
    assert autostart_overview(connected=False, reachable=True, answering=None) == "unknown"
    assert autostart_overview(connected=True, reachable=True, answering=True) == "running"
    assert autostart_overview(connected=True, reachable=True, answering=False) == "stopped"


def test_system_lists_autostart_without_blocking_conversation(tmp_path):
    app = create_app(db_path=tmp_path / "fair.sqlite", secrets_path=tmp_path / "secrets.json")
    with TestClient(app) as client:
        body = client.get("/api/system").json()
        assert body["checks"]["autostart"]["state"] == "unknown"
        assert "autostart" not in str(body["conversation_ready"])


def test_programs_offer_install_and_refuse_a_second_one(tmp_path, monkeypatch):
    app = create_app(db_path=tmp_path / "fair.sqlite", secrets_path=tmp_path / "secrets.json")
    service = app.state.programs
    service._releases = {
        "bridge": {"tag": "v0.2.0", "tarball": "https://example.invalid/bridge"},
        "autostart": {"tag": "v0.2.0", "tarball": "https://example.invalid/autostart"},
    }
    service._releases_at = time.monotonic()
    service._releases_error = None
    started = threading.Event()

    def prepare_tree(repo, tarball_url, marker, work):
        root = work / "release"
        (root / "packaging").mkdir(parents=True)
        (root / marker).write_text("marker", encoding="utf-8")
        return root

    def run_install(root, host, password, version, known_hosts):
        started.set()
        time.sleep(0.4)
        completed = type("Completed", (), {})()
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""
        return completed

    def scan_host_key(host):
        return f"{host} ssh-ed25519 AAAA"

    monkeypatch.setattr("nao_fair.programs.prepare_tree", prepare_tree)
    monkeypatch.setattr("nao_fair.programs.run_install", run_install)
    monkeypatch.setattr("nao_fair.programs.scan_host_key", scan_host_key)
    monkeypatch.setattr("nao_fair.programs.fingerprint_of", lambda line: "SHA256:test")
    with TestClient(app) as client:
        client.post("/api/robot/connect", json={"address": "192.0.2.20"})
        listed = client.get("/api/programs")
        assert listed.status_code == 200
        bridge = next(item for item in listed.json()["programs"] if item["id"] == "bridge")
        assert bridge["action"] == "install"
        assert client.post("/api/programs/bridge/install", json={"password": "secret", "confirm": False}).status_code == 400
        first = client.post("/api/programs/bridge/install", json={"password": "secret", "confirm": True})
        assert first.status_code == 200
        assert client.post("/api/programs/autostart/install", json={"password": "secret", "confirm": True}).status_code == 409
        deadline = time.time() + 3
        state = first.json()["state"]
        while time.time() < deadline and state not in ("done", "failed"):
            if state == "hostkey":
                assert client.post("/api/programs/install/hostkey", json={"accept": True}).status_code == 200
            time.sleep(0.05)
            current = client.get("/api/programs").json()["install"]
            assert current is not None
            state = current["state"]
        assert started.wait(3)
        assert state == "done"
        assert "secret" not in client.get("/api/programs").text
