import pytest
from fastapi.testclient import TestClient

from nao_fair.app import create_app
from nao_fair.models import LlmDraft


def test_first_start_uses_generic_defaults_and_empty_optional_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text("invalid TOML, must not be read")
    monkeypatch.setenv("NAO_FAIR_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("NAO_FAIR_PROMPTS", str(tmp_path / "missing-prompts"))
    app = create_app(db_path=tmp_path / "fair.sqlite", secrets_path=tmp_path / "secrets.json")
    runtime = app.state.runtime
    try:
        applied = runtime.applied
        assert applied.volume == 50
        assert applied.speech_speed == 80
        assert applied.tts_language == "de"
        assert applied.posture == "Sit"
        assert applied.knowledge_ids == []
        assert applied.local_context_id is None
        assert applied.tts_replacements == []
        assert applied.stt_vocabulary == []
        assert applied.dialogs == []
        assert applied.active_dialog_id is None
        assert runtime.active_setup_id is None
        assert runtime.store.list_scenarios() == []
        assert {item["kind"] for item in runtime.store.list_content()} == {"system_prompt", "personality"}
        assert len(runtime.store.list_content()) == 2
        assert all(binding.action is None for trigger in applied.triggers.values()
                   for binding in (trigger.tap, trigger.double_tap, trigger.long_press))
        sections = runtime.sections_for(applied, "Milo")
        assert len(sections) == 2
        assert "Milo" in sections[0]
        assert "{robot_name}" not in "\n".join(sections)
        assert all(runtime.applied_content.values())
    finally:
        runtime.store.close()


def test_empty_context_can_be_applied_and_saved_in_a_scenario(tmp_path):
    app = create_app(db_path=tmp_path / "fair.sqlite", secrets_path=tmp_path / "secrets.json")
    with TestClient(app) as client:
        applied = client.get("/api/config/applied").json()["applied"]
        draft = {key: applied[key] for key in LlmDraft.model_fields}
        context = client.post("/api/content", json={
            "kind": "local_context", "name": "Messe", "markdown": "Teststand",
        }).json()
        draft["local_context_id"] = context["id"]
        selected = client.post("/api/config/apply", json={
            "screen": "llm", "draft": draft, "expected_revision": applied["revision"],
        })
        assert selected.status_code == 200
        assert "Teststand" in app.state.runtime.sections_for(app.state.runtime.applied, "Milo")
        draft["local_context_id"] = None
        cleared = client.post("/api/config/apply", json={
            "screen": "llm", "draft": draft,
            "expected_revision": selected.json()["applied"]["revision"],
        })
        assert cleared.status_code == 200
        assert "Teststand" not in app.state.runtime.sections_for(app.state.runtime.applied, "Milo")
        assert context["id"] not in client.get("/api/config/applied-content").json()["items"]
        saved = client.post("/api/scenarios", json={
            **draft, "name": "Ohne Kontext", "triggers": applied["triggers"],
        })
        assert saved.status_code == 200
        assert saved.json()["local_context_id"] is None
        invalid = {**draft, "local_context_id": "missing"}
        assert client.post("/api/scenarios", json={
            **invalid, "name": "Ungültig", "triggers": applied["triggers"],
        }).status_code == 422


def test_restart_preserves_existing_content_settings_and_scenarios(tmp_path):
    options = {"db_path": tmp_path / "fair.sqlite", "secrets_path": tmp_path / "secrets.json"}
    app = create_app(**options)
    runtime = app.state.runtime
    with TestClient(app) as client:
        applied = runtime.applied.model_dump(mode="json")
        context = client.post("/api/content", json={
            "kind": "local_context", "name": "Vorhandener Ort", "markdown": "Unser Stand",
        }).json()
        prompt_id = applied["system_prompt_id"]
        client.put(f"/api/content/{prompt_id}", json={"name": "Eigener Prompt", "markdown": "Eigene Regeln"})
        draft = {key: applied[key] for key in LlmDraft.model_fields}
        draft["local_context_id"] = context["id"]
        assert client.post("/api/config/apply", json={
            "screen": "llm", "draft": draft, "expected_revision": applied["revision"],
        }).status_code == 200
        assert client.patch("/api/robot/audio", json={"volume": 37}).status_code == 200
        assert client.post("/api/scenarios", json={
            **draft, "name": "Vorhandenes Szenario", "triggers": applied["triggers"],
        }).status_code == 200
        before = runtime.applied.model_dump(mode="json")
        content = runtime.store.list_content()
        scenarios = runtime.store.list_scenarios()

    restarted = create_app(**options).state.runtime
    try:
        assert restarted.applied.model_dump(mode="json") == before
        assert restarted.store.list_content() == content
        assert restarted.store.list_scenarios() == scenarios
        assert "Eigene Regeln" in restarted.sections_for(restarted.applied, "Milo")
        assert "Unser Stand" in restarted.sections_for(restarted.applied, "Milo")
    finally:
        restarted.store.close()


def test_pipeline_fallback_has_no_location_or_file_dependency():
    pytest.importorskip("pipecat")
    from nao_fair.booth.processors.context_injector import NaoSystemInstruction

    text = NaoSystemInstruction("Milo").build_system_instruction()
    assert "Milo" in text
    assert "Zeitlicher Kontext" in text
    assert "Showroom" not in text
    assert "Vetschau" not in text
