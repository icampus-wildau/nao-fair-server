"""Scenario documents and the one-time read of legacy preset setups."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from nao_fair.models import InteractionsDraft, LlmDraft

SCENARIO_FIELDS = tuple(LlmDraft.model_fields) + ("triggers",)


def combination(payload: dict) -> dict:
    """The stored part of a scenario, checked as a language draft plus triggers."""
    llm = LlmDraft.model_validate({key: payload[key] for key in LlmDraft.model_fields})
    interactions = InteractionsDraft.model_validate({"triggers": payload["triggers"]})
    return {
        **llm.model_dump(mode="json"),
        "triggers": {key: value.model_dump(mode="json") for key, value in interactions.triggers.items()},
    }


def content_ids(document: dict) -> set[str]:
    ids = {document[key] for key in ("system_prompt_id", "local_context_id", "personality_id") if isinstance(document.get(key), str)}
    ids.update(value for value in document.get("knowledge_ids", []) if isinstance(value, str))
    return ids


def replace_content_id(document: dict, item_id: str, replacement_id: str) -> dict:
    updated = dict(document)
    for key in ("system_prompt_id", "local_context_id", "personality_id"):
        if updated.get(key) == item_id:
            updated[key] = replacement_id
    if isinstance(updated.get("knowledge_ids"), list):
        updated["knowledge_ids"] = list(dict.fromkeys(replacement_id if value == item_id else value for value in updated["knowledge_ids"]))
    return updated


def legacy_scenario_document(setup: dict, presets: dict[str, dict], applied: dict) -> dict | None:
    """Fold one old setup into a scenario, skipping its voice preset."""
    data = {key: applied[key] for key in SCENARIO_FIELDS if key in applied}
    preset_ids = setup.get("preset_ids")
    if not isinstance(preset_ids, dict):
        return None
    for slot, preset_id in preset_ids.items():
        if slot == "voice":
            continue
        preset = presets.get(str(preset_id))
        if preset is None:
            return None
        try:
            _apply_preset(data, str(slot), preset["payload"])
        except ValueError:
            return None
    try:
        return combination(data)
    except (ValidationError, KeyError, ValueError):
        return None


def _apply_preset(data: dict, slot: str, payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ValueError(f"{slot} payload must be an object")
    if slot == "system_prompt":
        data["system_prompt_id"] = _required_str(payload, "content_id")
    elif slot == "knowledge_bundle":
        ids = payload.get("content_ids")
        if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
            raise ValueError("knowledge bundle content_ids must be a list of strings")
        data["knowledge_ids"] = list(ids)
    elif slot == "local_context":
        data["local_context_id"] = _required_str(payload, "content_id")
    elif slot == "personality":
        data["personality_id"] = _required_str(payload, "content_id")
    elif slot == "llm":
        for field in (
            "llm_model",
            "temperature",
            "max_tokens",
            "gladia_region",
            "gladia_endpointing",
            "gladia_max_utterance_secs",
            "gladia_audio_enhancer",
            "gladia_language",
        ):
            if field not in payload:
                raise ValueError(f"llm preset missing {field}")
            data[field] = payload[field]
    elif slot == "stt_vocabulary":
        if "terms" not in payload:
            raise ValueError("stt vocabulary preset missing terms")
        data["stt_vocabulary"] = payload["terms"]
    elif slot == "tts_replacements":
        if "replacements" not in payload:
            raise ValueError("tts replacements preset missing replacements")
        data["tts_replacements"] = payload["replacements"]
    elif slot == "interaction_mappings":
        if "triggers" not in payload:
            raise ValueError("interaction mappings preset missing triggers")
        data["triggers"] = payload["triggers"]
    elif slot != "voice":
        raise ValueError(f"unknown preset slot {slot}")


def _required_str(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing {key}")
    return value


def public_scenario(row: dict[str, Any]) -> dict[str, Any]:
    document = row["document"]
    return {"id": row["id"], "name": row["name"], "description": row["description"], **document}
