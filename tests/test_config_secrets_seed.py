from __future__ import annotations

import json

from fastapi.testclient import TestClient

from nao_fair.app import create_app
from nao_fair.test_driver import TestDriver


def _paths(tmp_path):
    return tmp_path / "fair.sqlite", tmp_path / "secrets.json"


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


def test_apply_and_activate_without_a_robot(tmp_path) -> None:
    driver = TestDriver(name="Milo")
    db_path, secrets_path = _paths(tmp_path)
    app = create_app(driver=driver, db_path=db_path, secrets_path=secrets_path)
    with TestClient(app) as client:
        assert client.get("/api/robot/status").json()["state"] == "disconnected"
        current = client.get("/api/config/applied").json()
        revision = current["applied"]["revision"]
        applied = current["applied"]
        response = client.post(
            "/api/config/apply",
            json={
                "screen": "robot",
                "draft": _robot_draft(applied, volume=40),
                "expected_revision": revision,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["applied"]["volume"] == 40
        assert body["applied"]["revision"] == revision + 1
        assert body["applied"]["llm_model"] == applied["llm_model"]
        assert client.get("/api/robot/status").json()["state"] == "disconnected"

        stale = client.post(
            "/api/config/apply",
            json={
                "screen": "robot",
                "draft": _robot_draft(body["applied"], volume=30),
                "expected_revision": revision,
            },
        )
        assert stale.status_code == 409
        assert client.get("/api/config/applied").json()["applied"]["volume"] == 40

        invalid = client.post(
            "/api/config/apply",
            json={
                "screen": "robot",
                "draft": _robot_draft(body["applied"], volume=140),
                "expected_revision": revision + 1,
            },
        )
        assert invalid.status_code == 422
        assert client.get("/api/config/applied").json()["applied"]["volume"] == 40

        client.post("/api/robot/connect", json={"address": "192.0.2.10"})
        driver.append_transcript("visitor", "bleibt stehen")
        kept = client.post(
            "/api/config/apply",
            json={
                "screen": "robot",
                "draft": _robot_draft(body["applied"], volume=41),
                "expected_revision": revision + 1,
            },
        )
        assert kept.status_code == 200
        with client.websocket_connect("/api/events") as ws:
            picture = ws.receive_json()
        assert picture["conversation_state"] == "idle"
        assert [line["text"] for line in picture["transcript"]] == ["bleibt stehen"]
        assert picture["connection"]["state"] == "connected"
        assert picture["applied"]["volume"] == 41

        setup_id = client.get("/api/setups").json()[0]["id"]
        activated = client.post(f"/api/setups/{setup_id}/activate")
        assert activated.status_code == 200
        assert activated.json()["applied"]["volume"] == 50
        assert activated.json()["active_setup_id"] == setup_id
        assert client.get("/api/robot/status").json()["state"] == "connected"


def test_secrets_never_echo_and_blank_put_does_not_clear(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-secret-value")
    monkeypatch.delenv("GLADIA_API_KEY", raising=False)
    app = create_app(
        db_path=tmp_path / "fair.sqlite",
        secrets_path=tmp_path / "secrets.json",
    )
    secret = "console-secret-value"
    with TestClient(app) as client:
        before = client.get("/api/secrets").json()
        assert before == {
            "slots": [
                {"slot": "openai", "status": "environment"},
                {"slot": "gladia", "status": "missing"},
            ]
        }
        written = client.put("/api/secrets/openai", json={"value": secret})
        assert written.status_code == 200
        assert written.json() == {"slot": "openai", "status": "console"}
        assert secret not in written.text
        listed = client.get("/api/secrets")
        assert secret not in listed.text
        assert "env-secret-value" not in listed.text
        assert listed.json()["slots"][0]["status"] == "console"
        applied = client.get("/api/config/applied")
        assert secret not in applied.text
        with client.websocket_connect("/api/events") as ws:
            picture = ws.receive_json()
        assert secret not in json.dumps(picture)
        blank = client.put("/api/secrets/openai", json={"value": "   "})
        assert blank.status_code == 400
        assert client.get("/api/secrets").json()["slots"][0]["status"] == "console"
        removed = client.delete("/api/secrets/openai")
        assert removed.status_code == 200
        assert removed.json() == {"slot": "openai", "status": "environment"}
        assert secret not in removed.text


def test_seed_runs_once_and_a_preset_survives_restart(tmp_path) -> None:
    db_path, secrets_path = _paths(tmp_path)
    app = create_app(db_path=db_path, secrets_path=secrets_path)
    with TestClient(app) as client:
        setups = client.get("/api/setups").json()
        assert len(setups) == 1
        assert setups[0]["name"] == "Standard"
        content_id = client.get("/api/content", params={"kind": "personality"}).json()[0]["id"]
        created = client.post(
            "/api/presets",
            json={
                "kind": "personality",
                "name": "Warm",
                "description": "kept",
                "payload": {"content_id": content_id},
            },
        )
        assert created.status_code == 200
        preset_id = created.json()["id"]

    restarted = create_app(db_path=db_path, secrets_path=secrets_path)
    with TestClient(restarted) as client:
        setups = client.get("/api/setups").json()
        assert len(setups) == 1
        assert setups[0]["name"] == "Standard"
        loaded = client.get(f"/api/presets/{preset_id}")
        assert loaded.status_code == 200
        assert loaded.json()["name"] == "Warm"
        assert loaded.json()["payload"] == {"content_id": content_id}
        prompts = client.get("/api/content", params={"kind": "system_prompt"}).json()
        assert len(prompts) == 1
