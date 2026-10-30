from fastapi.testclient import TestClient

from nao_fair.app import create_app
from nao_fair.models import LlmDraft, RobotDraft


def test_content_edits_take_effect_only_after_llm_apply_and_survive_restart(tmp_path):
    options = {"db_path": tmp_path / "fair.sqlite", "secrets_path": tmp_path / "secrets.json"}
    app = create_app(**options)
    with TestClient(app) as client:
        runtime = app.state.runtime
        applied = client.get("/api/config/applied").json()["applied"]
        prompt_id = applied["system_prompt_id"]
        original = runtime.sections_for(runtime.applied, "Milo")
        original_markdown = client.get("/api/config/applied-content").json()["items"][prompt_id]
        response = client.put(f"/api/content/{prompt_id}", json={"name": "Test", "markdown": "Neuer Text für {robot_name}"})
        assert response.status_code == 200
        assert runtime.sections_for(runtime.applied, "Milo") == original
        assert client.get("/api/config/applied-content").json()["items"][prompt_id] == original_markdown

        robot = {key: applied[key] for key in RobotDraft.model_fields}
        result = client.post("/api/config/apply", json={"screen": "robot", "draft": robot, "expected_revision": applied["revision"]})
        assert result.status_code == 200
        assert runtime.sections_for(runtime.applied, "Milo") == original
        applied = result.json()["applied"]

    app = create_app(**options)
    with TestClient(app) as client:
        runtime = app.state.runtime
        assert runtime.sections_for(runtime.applied, "Milo") == original
        draft = {key: applied[key] for key in LlmDraft.model_fields}
        result = client.post("/api/config/apply", json={"screen": "llm", "draft": draft, "expected_revision": applied["revision"]})
        assert result.status_code == 200
        assert "Neuer Text für Milo" in runtime.sections_for(runtime.applied, "Milo")
        assert client.get("/api/config/applied-content").json()["items"][prompt_id] == "Neuer Text für {robot_name}"

    app = create_app(**options)
    with TestClient(app):
        assert "Neuer Text für Milo" in app.state.runtime.sections_for(app.state.runtime.applied, "Milo")


def test_setup_activation_snapshots_latest_preset_content_offline(tmp_path):
    app = create_app(db_path=tmp_path / "fair.sqlite", secrets_path=tmp_path / "secrets.json")
    with TestClient(app) as client:
        content = client.post("/api/content", json={"kind": "personality", "name": "Messe", "markdown": "Freundlich"}).json()
        preset = client.post("/api/presets", json={"kind": "personality", "name": "Messe", "payload": {"content_id": content["id"]}}).json()
        setup = client.post("/api/setups", json={"name": "Messe", "preset_ids": {"personality": preset["id"]}}).json()
        activated = client.post(f"/api/setups/{setup['id']}/activate")
        assert activated.status_code == 200
        assert activated.json()["active_setup_id"] == setup["id"]
        runtime = app.state.runtime
        assert "Freundlich" in runtime.sections_for(runtime.applied, "Milo")
        client.put(f"/api/content/{content['id']}", json={"name": "Messe", "markdown": "Sachlich"})
        assert "Freundlich" in runtime.sections_for(runtime.applied, "Milo")
        assert client.post(f"/api/setups/{setup['id']}/activate").status_code == 200
        assert "Sachlich" in runtime.sections_for(runtime.applied, "Milo")
        assert client.get("/api/robot/status").json()["state"] == "disconnected"
