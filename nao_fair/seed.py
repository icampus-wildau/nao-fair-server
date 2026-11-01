"""First-boot seed. After the flag is stored, the database is the source."""

from __future__ import annotations

import tomllib
from pathlib import Path
from uuid import uuid4

from nao_fair.models import Applied, LlmDraft, default_triggers
from nao_fair.store import Store, now_iso

_DEFAULT_VOCAB = (
    "NAO",
    "Telematik",
    "TH Wildau",
    "Wildau",
    "RoboticLab",
    "EDIH",
    "Stop",
    "Stopp",
)


def seed_if_needed(store: Store, *, config_path: Path, prompts_dir: Path) -> None:
    if store.seed_complete():
        return
    sample = _load_sample(config_path)
    stamp = now_iso()
    personality_id = str(uuid4())
    system_id = str(uuid4())
    local_id = str(uuid4())
    knowledge_id = str(uuid4())
    answering = _read(prompts_dir / "answering_hints.md")
    technical = _read(prompts_dir / "technical_context.md")
    content = [
        _item(personality_id, "personality", "Standard", _read(prompts_dir / "personality.md"), stamp),
        _item(system_id, "system_prompt", "Standard", f"{answering.rstrip()}\n\n{technical.lstrip()}", stamp),
        _item(local_id, "local_context", "Standard", _read(prompts_dir / "local_context.md"), stamp),
        _item(knowledge_id, "knowledge", "Standard", _read(prompts_dir / "event_context.md"), stamp),
    ]
    replacements = _replacements(prompts_dir / "tts_replacements.txt")
    vocabulary = [{"term": term, "enabled": True} for term in _DEFAULT_VOCAB]
    triggers = default_triggers()
    voice_payload = {
        "volume": int(sample["output_volume"]),
        "speech_speed": int(sample["tts_speed"]),
        "tts_language": "de",
        "posture": sample["posture"],
        "microphone_enabled": True,
        "stt_enabled": True,
        "llm_enabled": True,
        "listening_timeout_secs": float(sample["listening_timeout_secs"]),
        "context_reset_timeout_secs": float(sample["context_reset_timeout_secs"]),
        "thinking_timeout_secs": float(sample["thinking_timeout_secs"]),
        "unmute_delay_secs": float(sample["unmute_delay_secs"]),
    }
    llm_payload = {
        "llm_model": str(sample["llm_model"]),
        "temperature": None,
        "max_tokens": None,
        "gladia_region": str(sample["gladia_region"]),
        "gladia_endpointing": float(sample["gladia_endpointing"]),
        "gladia_max_utterance_secs": float(sample["gladia_max_utterance_secs"]),
        "gladia_audio_enhancer": bool(sample["gladia_audio_enhancer"]),
        "gladia_language": str(sample["gladia_language"]),
    }
    scenario_id = str(uuid4())
    applied = Applied.model_validate(
        {
            "revision": 1,
            **voice_payload,
            **llm_payload,
            "system_prompt_id": system_id,
            "knowledge_ids": [knowledge_id],
            "local_context_id": local_id,
            "personality_id": personality_id,
            "tts_replacements": replacements,
            "stt_vocabulary": vocabulary,
            "triggers": triggers,
        }
    )
    document = {key: applied.model_dump(mode="json")[key] for key in (*LlmDraft.model_fields, "triggers")}
    store.insert_seed(
        content=content,
        scenario={
            "id": scenario_id,
            "name": "Standard",
            "description": "Seeded fair scenario",
            "document": document,
            "updated_at": stamp,
        },
        applied=applied,
        active_setup_id=scenario_id,
    )


def _item(item_id: str, kind: str, name: str, markdown: str, stamp: str) -> dict:
    return {
        "id": item_id,
        "kind": kind,
        "name": name,
        "markdown": markdown,
        "updated_at": stamp,
    }


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_sample(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _replacements(path: Path) -> list[dict]:
    pairs: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or " -> " not in stripped:
            continue
        source, replacement = stripped.split(" -> ", 1)
        source = source.strip()
        replacement = replacement.strip()
        if source and replacement:
            pairs.append({"source": source, "replacement": replacement, "enabled": True})
    return pairs
