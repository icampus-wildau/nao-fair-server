import pytest
from pydantic import ValidationError

from fastapi.testclient import TestClient

from nao_fair.app import create_app
from nao_fair.test_driver import TestDriver
from nao_fair.dialog import line_ids, position_for, reconcile, step
from nao_fair.models import Applied, Dialog, InteractionsDraft
from nao_fair.touch import TouchClassifier


def _dialog(**overrides):
    body = {
        "id": "tour",
        "wrap": False,
        "lines": [
            {"id": "a", "text": "A", "delivery": "plain"},
            {"id": "b", "text": "B", "delivery": "motion", "motion": "nod"},
            {"id": "c", "text": "C", "delivery": "animated"},
        ],
    }
    body.update(overrides)
    return body


def test_next_starts_at_the_first_line_and_stops_at_the_end():
    dialog = _dialog()
    first = step(dialog, None, "dialog_next")
    assert first["id"] == "a"
    position = position_for("tour", "a", None)
    assert step(dialog, position, "dialog_next")["id"] == "b"
    landed = position_for("tour", "c", position)
    assert step(dialog, landed, "dialog_next") is None
    assert step(dialog, None, "dialog_repeat") is None
    assert step(dialog, None, "dialog_previous") is None


def test_wrap_loops_and_repeat_speaks_the_same_line():
    dialog = _dialog(wrap=True)
    position = position_for("tour", "c", None)
    assert step(dialog, position, "dialog_next")["id"] == "a"
    assert step(dialog, position, "dialog_repeat")["id"] == "c"
    start = position_for("tour", "a", None)
    assert step(dialog, start, "dialog_previous")["id"] == "c"


def test_human_lines_are_saved_but_skipped_in_both_directions():
    dialog = _dialog(lines=[
        {"id": "intro", "text": "Hallo NAO", "speaker": "human"},
        {"id": "a", "text": "Hallo", "speaker": "nao"},
        {"id": "question", "text": "Wie geht es dir?", "speaker": "human"},
        {"id": "b", "text": "Gut!"},
        {"id": "end", "text": "Danke", "speaker": "human"},
    ], wrap=True)
    assert line_ids(dialog) == ["a", "b"]
    assert step(dialog, None, "dialog_next")["id"] == "a"
    a = position_for("tour", "a", None)
    b = position_for("tour", "b", a)
    assert step(dialog, a, "dialog_next")["id"] == "b"
    assert step(dialog, b, "dialog_previous")["id"] == "a"
    assert step(dialog, b, "dialog_next")["id"] == "a"
    assert step(dialog, a, "dialog_repeat")["id"] == "a"
    assert step(_dialog(lines=[dialog["lines"][0]]), None, "dialog_next") is None
    assert Dialog.model_validate({**dialog, "name": "Tour"}).model_dump()["lines"][0]["speaker"] == "human"
    assert Dialog.model_validate({**dialog, "name": "Tour"}).model_dump()["lines"][3]["speaker"] == "nao"
    assert reconcile(["a", "b"], ["a"], b, "tour")["line_id"] == "a"


def test_reconcile_follows_the_line_and_steps_back_when_it_is_deleted():
    position = {"dialog_id": "tour", "line_id": "b", "seq": 2}
    assert reconcile(["a", "b", "c"], ["a", "c", "b"], position, "tour") == position
    moved = reconcile(["a", "b", "c"], ["a", "c"], position, "tour")
    assert moved["line_id"] == "a"
    assert reconcile(["a", "b", "c"], ["c"], {"dialog_id": "tour", "line_id": "a", "seq": 1}, "tour") is None
    assert reconcile(["a", "b"], ["a", "b"], position, "other") is None


def test_a_flat_trigger_becomes_the_tap_and_copies_its_switch():
    draft = InteractionsDraft.model_validate(
        {
            "triggers": {
                "middle_head": {"action": "speak", "phrase": "Hallo", "enabled": False},
            }
        }
    )
    gesture = draft.triggers["middle_head"]
    assert gesture.tap.action == "speak"
    assert gesture.tap.phrase == "Hallo"
    assert gesture.tap.enabled is False
    assert gesture.double_tap.action is None
    assert gesture.double_tap.enabled is False
    assert gesture.long_press.enabled is False


def test_dialog_names_are_unique_after_trimming_and_a_blank_line_is_rejected():
    InteractionsDraft.model_validate(
        {
            "triggers": {},
            "dialogs": [
                {"id": "one", "name": "Tour", "lines": [{"id": "a", "text": "Hallo"}]},
                {"id": "two", "name": "tour", "lines": []},
            ],
            "active_dialog_id": "one",
        }
    )
    with pytest.raises(ValidationError):
        Dialog.model_validate({"id": "one", "name": "  Tour  ", "lines": [{"id": "a", "text": "  "}]})
    named = Dialog.model_validate({"id": "one", "name": "  Tour  ", "lines": []})
    assert named.name == "Tour"


def test_applied_configuration_without_dialogs_still_loads():
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
        "tts_replacements": [],
        "stt_vocabulary": [],
        "triggers": {"front_head": {"action": "speak", "phrase": "Hallo", "enabled": True}},
    }
    # The seven sensors are required. Fill the rest from the flat upgrade.
    for sensor in (
        "middle_head",
        "rear_head",
        "left_hand",
        "right_hand",
        "left_bumper",
        "right_bumper",
    ):
        payload["triggers"][sensor] = {"action": None, "enabled": True}
    applied = Applied.model_validate(payload)
    assert applied.dialogs == []
    assert applied.active_dialog_id is None
    assert applied.triggers["front_head"].tap.phrase == "Hallo"


def test_a_dialog_test_speaks_the_applied_line_and_then_the_next_one(tmp_path):
    app = create_app(driver=TestDriver(), db_path=tmp_path / "fair.sqlite", secrets_path=tmp_path / "secrets.json")
    with TestClient(app) as client:
        client.post("/api/robot/connect", json={"address": "192.0.2.10"})
        applied = client.get("/api/config/applied").json()["applied"]
        dialog = {
            "id": "tour",
            "name": "Tour",
            "wrap": False,
            "idle_reset_secs": 300,
            "lines": [
                {"id": "a", "text": "Erster Satz", "delivery": "plain", "motion": None},
                {"id": "human", "text": "Eine Frage", "speaker": "human"},
                {"id": "b", "text": "Zweiter Satz", "delivery": "plain", "motion": None},
            ],
        }
        draft = {
            "triggers": applied["triggers"],
            "dialogs": [dialog],
            "active_dialog_id": "tour",
            "autostart_enabled": True,
        }
        applied_result = client.post(
            "/api/config/apply",
            json={"screen": "interactions", "draft": draft, "expected_revision": applied["revision"]},
        )
        assert applied_result.status_code == 200
        stored = client.get("/api/config/applied").json()["applied"]["dialogs"][0]["lines"]
        assert stored[1]["speaker"] == "human"
        assert stored[1]["text"] == "Eine Frage"
        first = client.post("/api/interactions/dialog-test", json={"action": "dialog_next"})
        assert first.status_code == 200
        assert app.state.runtime.transcript[-1]["text"] == "Erster Satz"
        assert app.state.runtime._dialog_position["line_id"] == "a"
        second = client.post("/api/interactions/dialog-test", json={"action": "dialog_next"})
        assert second.status_code == 200
        assert app.state.runtime.transcript[-1]["text"] == "Zweiter Satz"
        assert app.state.runtime._dialog_position["line_id"] == "b"


def test_tap_waits_double_tap_fires_on_the_second_down_and_a_hold_is_long():
    touch = TouchClassifier()
    assert touch.down("head", 0) is None
    touch.up("head", 0.1)
    assert touch.tick(0.49) == []
    assert touch.tick(0.5) == [("head", "tap")]

    touch = TouchClassifier()
    touch.down("head", 0)
    touch.up("head", 0.1)
    assert touch.down("head", 0.14) is None
    assert touch.tick(0.5) == [("head", "tap")]

    touch = TouchClassifier()
    touch.down("head", 0)
    touch.up("head", 0.1)
    assert touch.down("head", 0.2) == "double_tap"
    touch.up("head", 0.3)
    assert touch.tick(1) == []

    touch = TouchClassifier()
    touch.down("head", 0)
    assert touch.tick(0.6) == [("head", "long_press")]
    touch.up("head", 0.8)
    assert touch.tick(2) == []

    touch = TouchClassifier()
    touch.down("head", 0)
    assert touch.up("head", 0.1, wait_for_double=False) == "tap"
    assert touch.tick(1) == []
    assert touch.down("head", 0.14) is None
    assert touch.up("head", 0.145) is None
    assert touch.tick(1) == []
    assert touch.down("head", 0.16) is None
    assert touch.up("head", 0.2, wait_for_double=False) == "tap"

    touch = TouchClassifier()
    touch.down("head", 0)
    touch.up("head", 0.1)
    touch.down("hand", 0)
    touch.up("hand", 0.1)
    assert touch.flush(0.12, lambda sensor: sensor == "hand") == [("head", "tap")]
    assert touch.down("head", 0.14) is None
    assert touch.up("head", 0.145) is None
    assert touch.tick(0.5) == [("hand", "tap")]
    assert touch.tick(1) == []

    touch = TouchClassifier()
    touch.down("head", 0)
    touch.up("head", 0.1)
    assert touch.flush(0.2, lambda sensor: False) == [("head", "tap")]
    assert touch.down("head", 0.21) is None
    assert touch.up("head", 0.22, wait_for_double=False) == "tap"
