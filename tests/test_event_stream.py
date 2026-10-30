from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from nao_fair.app import create_app
from nao_fair.catalog import MOTIONS, SENSORS
from nao_fair.test_driver import TestDriver


def _robot_draft(applied: dict, **changes) -> dict:
    fields = (
        "volume",
        "speech_speed",
        "tts_language",
        "posture",
        "microphone_enabled",
        "stt_enabled",
        "llm_enabled",
        "listening_timeout_secs",
        "context_reset_timeout_secs",
        "thinking_timeout_secs",
        "unmute_delay_secs",
    )
    draft = {name: applied[name] for name in fields}
    draft.update(changes)
    return draft


def test_event_stream_picture_then_live_reports(tmp_path) -> None:
    driver = TestDriver(name="Milo")
    app = create_app(
        driver=driver,
        db_path=tmp_path / "fair.sqlite",
        secrets_path=tmp_path / "secrets.json",
    )
    with TestClient(app) as client:
        with client.websocket_connect("/api/events") as ws:
            picture = ws.receive_json()
            assert picture["type"] == "picture"
            assert picture["sequence"] == 1
            assert picture["connection"] == {
                "state": "disconnected",
                "address": None,
                "name": None,
                "failure": None,
            }
            assert picture["conversation_state"] == "idle"
            assert picture["transcript"] == []
            assert picture["active_setup_id"]
            assert "battery" not in picture
            assert "battery" not in picture["connection"]

            response = client.post("/api/robot/connect", json={"address": "192.0.2.10"})
            assert response.status_code == 200
            assert response.json()["state"] == "connected"
            assert response.json()["name"] == "Milo"
            assert response.json()["failure"] is None

            driver.set_conversation_state("listening")
            driver.append_transcript("visitor", "Guten Tag")
            driver.append_transcript("nao", "Hallo")
            driver.press("front_head")

            events = [ws.receive_json() for _ in range(6)]

        types = [picture["type"], *[event["type"] for event in events]]
        assert types == [
            "picture",
            "connection.changed",
            "connection.changed",
            "conversation.state_changed",
            "transcript.message",
            "transcript.message",
            "sensor.pressed",
        ]
        sequences = [picture["sequence"], *[event["sequence"] for event in events]]
        assert sequences == list(range(1, 8))
        assert events[0]["connection"]["state"] == "disconnected"
        assert events[0]["connection"]["address"] == "192.0.2.10"
        assert events[0]["connection"]["failure"] is None
        assert events[1]["connection"]["state"] == "connected"
        assert events[1]["connection"]["name"] == "Milo"
        assert events[2] == {
            "type": "conversation.state_changed",
            "sequence": 4,
            "state": "listening",
        }
        assert events[3]["speaker"] == "visitor"
        assert events[3]["text"] == "Guten Tag"
        assert events[4]["speaker"] == "nao"
        assert events[4]["text"] == "Hallo"
        assert events[5]["sensor_id"] == "front_head"
        assert "action.completed" not in types

        status = client.get("/api/robot/status").json()
        assert status["state"] == "connected"
        assert status["name"] == "Milo"
        assert status["address"] == "192.0.2.10"
        assert status["conversation_state"] == "listening"
        caps = client.get("/api/robot/capabilities").json()
        assert [(item["id"], item["subscriber"]) for item in caps["sensors"]] == [
            (sensor_id, subscriber) for sensor_id, subscriber, _label in SENSORS
        ]
        assert caps["motions"] == list(MOTIONS)
        assert caps["voices"] == []
        assert caps["battery"] is False
        assert set(caps["timing_ranges"]) == {
            "listening_timeout_secs",
            "context_reset_timeout_secs",
            "thinking_timeout_secs",
            "unmute_delay_secs",
        }


def test_disconnect_emits_transcript_replaced(tmp_path) -> None:
    driver = TestDriver(name="Milo")
    app = create_app(
        driver=driver,
        db_path=tmp_path / "fair.sqlite",
        secrets_path=tmp_path / "secrets.json",
    )
    with TestClient(app) as client:
        with client.websocket_connect("/api/events") as ws:
            ws.receive_json()
            response = client.post("/api/robot/connect", json={"address": "192.0.2.10"})
            assert response.status_code == 200
            ws.receive_json()
            ws.receive_json()
            driver.set_conversation_state("speaking")
            ws.receive_json()
            driver.append_transcript("visitor", "Frage")
            ws.receive_json()
            driver.end_connection()
            replaced = ws.receive_json()
            state = ws.receive_json()
            connection = ws.receive_json()

        assert replaced == {"type": "transcript.replaced", "sequence": 6, "lines": []}
        assert state == {"type": "conversation.state_changed", "sequence": 7, "state": "idle"}
        assert connection["type"] == "connection.changed"
        assert connection["sequence"] == 8
        assert connection["connection"]["state"] == "disconnected"


def test_stop_and_clear_emits_transcript_replaced(tmp_path) -> None:
    driver = TestDriver(name="Milo")
    app = create_app(
        driver=driver,
        db_path=tmp_path / "fair.sqlite",
        secrets_path=tmp_path / "secrets.json",
    )
    runtime = app.state.runtime
    with TestClient(app) as client:
        with client.websocket_connect("/api/events") as ws:
            ws.receive_json()
            response = client.post("/api/robot/connect", json={"address": "192.0.2.10"})
            assert response.status_code == 200
            ws.receive_json()
            ws.receive_json()
            driver.append_transcript("visitor", "bleibt kurz")
            ws.receive_json()
            future = asyncio.run_coroutine_threadsafe(
                runtime._stop_conversation(clear=True),
                runtime._loop,
            )
            future.result(timeout=5)
            replaced = ws.receive_json()

        assert replaced == {"type": "transcript.replaced", "sequence": 5, "lines": []}
