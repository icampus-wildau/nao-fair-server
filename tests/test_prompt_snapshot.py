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


def test_scenario_keeps_the_content_id_and_apply_reads_the_latest_wording(tmp_path):
    app = create_app(db_path=tmp_path / "fair.sqlite", secrets_path=tmp_path / "secrets.json")
    with TestClient(app) as client:
        applied = client.get("/api/config/applied").json()["applied"]
        content = client.post("/api/content", json={"kind": "personality", "name": "Messe", "markdown": "Freundlich"}).json()
        scenario = {key: applied[key] for key in LlmDraft.model_fields}
        scenario.update({"name": "Messe", "description": "", "personality_id": content["id"], "triggers": applied["triggers"]})
        saved = client.post("/api/scenarios", json=scenario)
        assert saved.status_code == 200
        assert saved.json()["personality_id"] == content["id"]
        draft = {key: scenario[key] for key in LlmDraft.model_fields}
        applied_now = client.post("/api/config/apply", json={"screen": "llm", "draft": draft, "expected_revision": applied["revision"]})
        assert applied_now.status_code == 200
        runtime = app.state.runtime
        assert "Freundlich" in runtime.sections_for(runtime.applied, "Milo")
        client.put(f"/api/content/{content['id']}", json={"name": "Messe", "markdown": "Sachlich"})
        assert "Freundlich" in runtime.sections_for(runtime.applied, "Milo")
        assert saved.json()["personality_id"] == content["id"]
        again = client.post("/api/config/apply", json={"screen": "llm", "draft": draft, "expected_revision": applied_now.json()["applied"]["revision"]})
        assert again.status_code == 200
        assert "Sachlich" in runtime.sections_for(runtime.applied, "Milo")
        assert client.get("/api/robot/status").json()["state"] == "disconnected"
