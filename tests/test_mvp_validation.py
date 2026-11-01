from fastapi.testclient import TestClient

from nao_fair.app import create_app
from nao_fair.test_driver import TestDriver


def test_stop_and_clear_action_clears_transcript(tmp_path):
    driver = TestDriver()
    app = create_app(driver=driver, db_path=tmp_path / "db", secrets_path=tmp_path / "secrets")
    with TestClient(app) as client:
        assert client.post("/api/conversation/stop-and-clear").status_code == 409
        client.post("/api/robot/connect", json={"address": "192.0.2.1"})
        driver.set_conversation_state("speaking")
        driver.append_transcript("visitor", "Hallo")
        response = client.post("/api/conversation/stop-and-clear")
        assert response.status_code == 200
        with client.websocket_connect("/api/events") as ws:
            picture = ws.receive_json()
        assert picture["conversation_state"] == "idle"
        assert picture["transcript"] == []


def test_invalid_scenario_rejected_before_saving(tmp_path):
    app = create_app(db_path=tmp_path / "db", secrets_path=tmp_path / "secrets")
    with TestClient(app) as client:
        applied = client.get("/api/config/applied").json()["applied"]
        body = {key: applied[key] for key in ("llm_model", "temperature", "max_tokens", "gladia_region", "gladia_endpointing", "gladia_max_utterance_secs", "gladia_audio_enhancer", "gladia_language", "system_prompt_id", "knowledge_ids", "local_context_id", "personality_id", "tts_replacements", "stt_vocabulary", "triggers")}
        body.update({"name": "Fehler", "description": "", "personality_id": "missing"})
        rejected = client.post("/api/scenarios", json=body)
        assert rejected.status_code == 422
        assert all(item["name"] != "Fehler" for item in client.get("/api/scenarios").json())
        body["personality_id"] = applied["personality_id"]
        body["name"] = ""
        assert client.post("/api/scenarios", json=body).status_code == 422
