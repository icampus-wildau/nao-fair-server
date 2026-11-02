import asyncio
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from nao_fair.app import create_app
from nao_fair.models import Applied
from nao_fair.robot.autostart import (
    ACK_SEQ,
    ASSIGNMENT,
    ASSIGNMENT_SEQ,
    KEPT_NOTICE,
    LEASE_ACK_SEQ,
    assignment_document,
    push_assignment,
    wait_lease_ack,
    write_lease,
)
from nao_fair.test_driver import TestDriver


def test_a_stored_configuration_without_the_switch_is_on():
    payload = {
        "revision": 1,
        "volume": 50,
        "speech_speed": 100,
        "tts_language": "de",
        "posture": "Sit",
        "microphone_enabled": True,
        "stt_enabled": True,
        "llm_enabled": True,
        "listening_timeout_secs": 8,
        "context_reset_timeout_secs": 60,
        "thinking_timeout_secs": 12,
        "unmute_delay_secs": 0.4,
        "llm_model": "gpt-test",
        "temperature": None,
        "max_tokens": None,
        "gladia_region": "eu-west",
        "gladia_endpointing": 0.6,
        "gladia_max_utterance_secs": 30,
        "gladia_audio_enhancer": False,
        "gladia_language": "de",
        "system_prompt_id": "system",
        "knowledge_ids": [],
        "local_context_id": "local",
        "personality_id": "personality",
        "tts_replacements": [{"source": "TH Wildau", "replacement": "Te Ha Wildau", "enabled": True}],
        "stt_vocabulary": [],
        "triggers": {
            sensor: {"action": None, "phrase": "", "voice": None, "motion": None, "enabled": True}
            for sensor in (
                "front_head",
                "middle_head",
                "rear_head",
                "left_hand",
                "right_hand",
                "left_bumper",
                "right_bumper",
            )
        },
    }
    applied = Applied.model_validate(payload)
    assert applied.autostart_enabled is True
    document = assignment_document(applied)
    assert document["armed"] is True
    assert document["tts_language"] == "German"
    assert document["replacements"] == [["TH Wildau", "Te Ha Wildau"]]
    assert document["triggers"]["front_head"]["action"] is None


def test_applying_interactions_can_turn_autostart_off_and_a_scenario_keeps_it(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "fair.sqlite", secrets_path=tmp_path / "secrets.json")) as client:
        applied = client.get("/api/config/applied").json()["applied"]
        assert applied["autostart_enabled"] is True
        draft = {"triggers": applied["triggers"], "autostart_enabled": False}
        result = client.post(
            "/api/config/apply",
            json={"screen": "interactions", "draft": draft, "expected_revision": applied["revision"]},
        )
        assert result.status_code == 200
        assert result.json()["applied"]["autostart_enabled"] is False
        scenario = client.get("/api/scenarios").json()[0]
        saved = client.put(
            f"/api/scenarios/{scenario['id']}",
            json={"name": scenario["name"], "description": scenario["description"], **{key: scenario[key] for key in scenario if key not in ("id", "name", "description")}, "autostart_enabled": False},
        )
        assert saved.status_code == 200
        assert saved.json()["autostart_enabled"] is False


def test_a_driver_connection_reports_autostart_in_sync(tmp_path):
    driver = TestDriver(name="Milo")
    app = create_app(driver=driver, db_path=tmp_path / "fair.sqlite", secrets_path=tmp_path / "secrets.json")
    with TestClient(app) as client:
        with client.websocket_connect("/api/events") as ws:
            assert ws.receive_json()["autostart_synced"] is None
        client.post("/api/robot/connect", json={"address": "192.0.2.10"})
        with client.websocket_connect("/api/events") as ws:
            picture = ws.receive_json()
        assert picture["autostart_synced"] is True
        client.post("/api/robot/disconnect")
        with client.websocket_connect("/api/events") as ws:
            assert ws.receive_json()["autostart_synced"] is None


def test_a_failed_push_keeps_the_previous_assignment_visible(tmp_path):
    driver = TestDriver()
    app = create_app(driver=driver, db_path=tmp_path / "fair.sqlite", secrets_path=tmp_path / "secrets.json")

    class Link:
        async def sync_autostart(self, applied):
            del applied
            return False

        async def on_audio_changed(self, applied):
            del applied

        async def close(self):
            return None

    with TestClient(app) as client:
        client.post("/api/robot/connect", json={"address": "192.0.2.10"})
        runtime = client.app.state.runtime
        runtime._link = Link()
        applied = client.get("/api/config/applied").json()["applied"]
        with client.websocket_connect("/api/events") as ws:
            ws.receive_json()
            result = client.patch("/api/robot/audio", json={"volume": 40})
            assert result.status_code == 200
            found = [ws.receive_json()["type"] for _ in range(2)]
            assert "autostart.changed" in found
            assert "autostart.kept" not in found
        runtime._link = Link()
        with client.websocket_connect("/api/events") as ws:
            ws.receive_json()
            result = client.post(
                "/api/config/apply",
                json={"screen": "interactions", "draft": {"triggers": applied["triggers"], "autostart_enabled": True}, "expected_revision": result.json()["applied"]["revision"]},
            )
            assert result.status_code == 200
            types = [ws.receive_json()["type"] for _ in range(3)]
            assert "autostart.kept" in types
        assert KEPT_NOTICE == "Übernommen. Der Roboter hat den bisherigen Autostart behalten."


def test_push_waits_for_the_robot_acknowledgement():
    asyncio.run(_push_waits_for_the_robot_acknowledgement())


async def _push_waits_for_the_robot_acknowledgement():
    class Memory:
        def __init__(self):
            self.data = {}

        def insertData(self, key, value):
            self.data[key] = value

        def raiseEvent(self, key, value):
            self.data[key] = value
            if key == ASSIGNMENT_SEQ:
                self.data[ACK_SEQ] = value

        def getData(self, key):
            if key not in self.data:
                raise RuntimeError(key)
            return self.data[key]

    memory = Memory()

    async def _call(fn, *args, **kwargs):
        return fn(*args)

    nao = SimpleNamespace(session=SimpleNamespace(service=lambda name: memory), call_qi=_call)
    ok = await push_assignment(nao, {"armed": True}, 3)
    assert ok is True
    assert json.loads(memory.data[ASSIGNMENT])["armed"] is True

    # A previous connection can leave an acknowledgement with the same sequence.
    memory.data[ACK_SEQ] = 4

    async def silent(fn, *args, **kwargs):
        return fn(*args)

    nao.call_qi = silent
    memory.raiseEvent = lambda key, value: memory.data.__setitem__(key, value)
    assert await push_assignment(nao, {"armed": True}, 4, timeout=0.05) is False
    memory.data[LEASE_ACK_SEQ] = 1
    await write_lease(nao, "hold", 1)
    assert await wait_lease_ack(nao, 1, timeout=0.05) is False


def test_lease_renewal_retries_after_a_transient_error(monkeypatch):
    from nao_fair.robot import autostart
    from nao_fair.robot.live import LiveLink

    async def run():
        link = LiveLink(None, object(), "NAO")
        attempts = []

        async def write(nao, command, seq):
            attempts.append(seq)
            if len(attempts) == 1:
                raise RuntimeError("temporary failure")
            link._closing = True

        monkeypatch.setattr(autostart, "LEASE_PERIOD_SECS", 0)
        monkeypatch.setattr(autostart, "write_lease", write)
        async def ack(*args, **kwargs):
            return True
        monkeypatch.setattr(autostart, "wait_lease_ack", ack)
        await link._renew_autostart()
        assert attempts == [1, 2]

    asyncio.run(run())
