from __future__ import annotations

from fastapi.testclient import TestClient

from nao_fair.app import create_app
from nao_fair.test_driver import TestDriver


def test_clean_end_from_speaking_clears_transcript(tmp_path) -> None:
    driver = TestDriver(name="Milo")
    app = create_app(
        driver=driver,
        db_path=tmp_path / "fair.sqlite",
        secrets_path=tmp_path / "secrets.json",
    )
    with TestClient(app) as client:
        connected = client.post("/api/robot/connect", json={"address": "192.0.2.10"})
        assert connected.status_code == 200
        driver.set_conversation_state("speaking")
        driver.append_transcript("visitor", "Frage")
        driver.append_transcript("nao", "Antwort")
        driver.end_connection()

        with client.websocket_connect("/api/events") as ws:
            picture = ws.receive_json()
        assert picture["connection"] == {
            "state": "disconnected",
            "address": "192.0.2.10",
            "name": None,
            "failure": None,
        }
        assert picture["conversation_state"] == "idle"
        assert picture["transcript"] == []
        status = client.get("/api/robot/status").json()
        assert status["conversation_state"] == "idle"
        assert status["failure"] is None


def test_refused_connect_keeps_the_failure(tmp_path) -> None:
    driver = TestDriver(name="Milo", refuse="connection refused")
    app = create_app(
        driver=driver,
        db_path=tmp_path / "fair.sqlite",
        secrets_path=tmp_path / "secrets.json",
    )
    with TestClient(app) as client:
        response = client.post("/api/robot/connect", json={"address": "192.0.2.10"})
        assert response.status_code == 200
        assert response.json() == {
            "state": "disconnected",
            "address": "192.0.2.10",
            "name": None,
            "failure": "connection refused",
        }
        with client.websocket_connect("/api/events") as ws:
            picture = ws.receive_json()
        assert picture["connection"]["failure"] == "connection refused"
        assert picture["connection"]["state"] == "disconnected"
        assert picture["connection"]["name"] is None
        assert picture["conversation_state"] == "idle"
        assert picture["transcript"] == []


def test_unreachable_robot_is_a_failed_connect(tmp_path) -> None:
    app = create_app(
        db_path=tmp_path / "fair.sqlite",
        secrets_path=tmp_path / "secrets.json",
        bridge_port=1,
    )
    with TestClient(app) as client:
        response = client.post("/api/robot/connect", json={"address": "127.0.0.1"})
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "disconnected"
        assert body["address"] == "127.0.0.1"
        assert body["name"] is None
        assert body["failure"].startswith("NAO unter 127.0.0.1 ist nicht erreichbar.")
        refused = client.post("/api/speech/speak", json={"text": "Hallo"})
        assert refused.status_code == 409
        assert refused.json() == {"error": "no robot connected"}
