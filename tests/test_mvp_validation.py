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


def test_invalid_preset_and_setup_rejected_before_saving(tmp_path):
    app = create_app(db_path=tmp_path / "db", secrets_path=tmp_path / "secrets")
    with TestClient(app) as client:
        preset = client.post("/api/presets", json={"kind": "personality", "name": "Fehler", "payload": {"content_id": "missing"}})
        assert preset.status_code == 422
        assert all(item["name"] != "Fehler" for item in client.get("/api/presets").json())
        missing_terms = client.post("/api/presets", json={"kind": "stt_vocabulary", "name": "Leer", "payload": {}})
        assert missing_terms.status_code == 422
        available = client.get("/api/presets", params={"kind": "personality"}).json()[0]
        setup = client.post("/api/setups", json={"name": "Fehler", "preset_ids": {"system_prompt": available["id"]}})
        assert setup.status_code == 422
        assert all(item["name"] != "Fehler" for item in client.get("/api/setups").json())
