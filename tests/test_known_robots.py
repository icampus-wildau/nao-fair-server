from __future__ import annotations

from fastapi.testclient import TestClient

from nao_fair.app import create_app
from nao_fair.test_driver import TestDriver


def _app(tmp_path, driver: TestDriver | None = None):
    return create_app(
        driver=driver or TestDriver(name="Milo"),
        db_path=tmp_path / "fair.sqlite",
        secrets_path=tmp_path / "secrets.json",
    )


def test_successful_connect_records_newest_first_and_restart_keeps_them(tmp_path) -> None:
    driver = TestDriver(name="Milo")
    app = _app(tmp_path, driver)
    with TestClient(app) as client:
        first = client.post("/api/robot/connect", json={"address": "192.0.2.10"})
        assert first.status_code == 200
        assert first.json()["known_robots"] == [{"address": "192.0.2.10", "name": "Milo"}]
        client.post("/api/robot/disconnect")
        driver.name = "Ada"
        second = client.post("/api/robot/connect", json={"address": "192.0.2.20"})
        assert second.json()["known_robots"] == [
            {"address": "192.0.2.20", "name": "Ada"},
            {"address": "192.0.2.10", "name": "Milo"},
        ]

    restored = _app(tmp_path, TestDriver(name="Ada"))
    with TestClient(restored) as client:
        with client.websocket_connect("/api/events") as ws:
            picture = ws.receive_json()
        assert picture["connection"]["address"] is None
        assert picture["known_robots"] == [
            {"address": "192.0.2.20", "name": "Ada"},
            {"address": "192.0.2.10", "name": "Milo"},
        ]
        client.post("/api/robot/connect", json={"address": "192.0.2.10"})
        with client.websocket_connect("/api/events") as ws:
            picture = ws.receive_json()
        assert picture["known_robots"][0] == {"address": "192.0.2.10", "name": "Ada"}
        removed = client.post("/api/robot/known-robots/remove", json={"address": "192.0.2.20"})
        assert removed.status_code == 200
        assert removed.json()["known_robots"] == [{"address": "192.0.2.10", "name": "Ada"}]
        status = client.get("/api/robot/status").json()
        assert status["state"] == "connected"
        assert status["address"] == "192.0.2.10"


def test_same_address_text_is_one_known_robot_and_a_different_spelling_is_another(tmp_path) -> None:
    driver = TestDriver(name="Ada")
    app = _app(tmp_path, driver)
    with TestClient(app) as client:
        client.post("/api/robot/connect", json={"address": "10.0.0.8"})
        client.post("/api/robot/disconnect")
        driver.name = "Otto"
        client.post("/api/robot/connect", json={"address": "10.0.0.08"})
        client.post("/api/robot/disconnect")
        driver.name = "Ida"
        body = client.post("/api/robot/connect", json={"address": "10.0.0.8"}).json()
        assert body["known_robots"] == [
            {"address": "10.0.0.8", "name": "Ida"},
            {"address": "10.0.0.08", "name": "Otto"},
        ]


def test_failed_connect_adds_nothing_and_remove_of_an_unknown_address_is_quiet(tmp_path) -> None:
    driver = TestDriver(name="Milo", refuse="connection refused")
    app = _app(tmp_path, driver)
    with TestClient(app) as client:
        refused = client.post("/api/robot/connect", json={"address": "192.0.2.10"})
        assert refused.status_code == 200
        assert "known_robots" not in refused.json()
        with client.websocket_connect("/api/events") as ws:
            picture = ws.receive_json()
        assert picture["known_robots"] == []
        runtime = client.app.state.runtime
        sequence = runtime.hub.sequence
        missing = client.post("/api/robot/known-robots/remove", json={"address": "192.0.2.10"})
        assert missing.status_code == 200
        assert missing.json()["known_robots"] == []
        assert runtime.hub.sequence == sequence
        empty = client.post("/api/robot/known-robots/remove", json={"address": "  "})
        assert empty.status_code == 400


def test_the_list_stops_at_fifteen(tmp_path) -> None:
    driver = TestDriver(name="NAO")
    app = _app(tmp_path, driver)
    with TestClient(app) as client:
        for number in range(16):
            driver.name = f"N{number}"
            client.post("/api/robot/connect", json={"address": f"192.0.2.{number}"})
            client.post("/api/robot/disconnect")
        with client.websocket_connect("/api/events") as ws:
            robots = ws.receive_json()["known_robots"]
        assert len(robots) == 15
        assert robots[0] == {"address": "192.0.2.15", "name": "N15"}
        assert all(item["address"] != "192.0.2.0" for item in robots)


def test_remove_reaches_other_windows(tmp_path) -> None:
    app = _app(tmp_path, TestDriver(name="Milo"))
    with TestClient(app) as client:
        client.post("/api/robot/connect", json={"address": "192.0.2.10"})
        with client.websocket_connect("/api/events") as ws:
            assert ws.receive_json()["known_robots"] == [{"address": "192.0.2.10", "name": "Milo"}]
            client.post("/api/robot/known-robots/remove", json={"address": "192.0.2.10"})
            event = ws.receive_json()
        assert event["type"] == "known_robots.changed"
        assert event["known_robots"] == []
