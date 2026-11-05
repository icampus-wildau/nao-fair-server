"""First-boot defaults. Existing operator databases are left unchanged."""

from __future__ import annotations

from uuid import uuid4

from nao_fair.booth.config import AppConfig
from nao_fair.defaults import PERSONALITY, SYSTEM_PROMPT
from nao_fair.models import Applied, default_triggers
from nao_fair.store import Store, now_iso


def seed_if_needed(store: Store) -> None:
    if store.seed_complete():
        return
    defaults = AppConfig()
    stamp = now_iso()
    personality_id = str(uuid4())
    system_id = str(uuid4())
    content = [
        _item(personality_id, "personality", "Standard", PERSONALITY, stamp),
        _item(system_id, "system_prompt", "Standard", SYSTEM_PROMPT, stamp),
    ]
    applied = Applied(
        revision=1,
        volume=defaults.output_volume,
        speech_speed=defaults.tts_speed,
        tts_language="de",
        posture=defaults.posture,
        microphone_enabled=True,
        stt_enabled=True,
        llm_enabled=True,
        listening_timeout_secs=defaults.listening_timeout_secs,
        context_reset_timeout_secs=defaults.context_reset_timeout_secs,
        thinking_timeout_secs=defaults.thinking_timeout_secs,
        unmute_delay_secs=defaults.unmute_delay_secs,
        llm_model=defaults.llm_model,
        temperature=None,
        max_tokens=None,
        gladia_region=defaults.gladia_region,
        gladia_endpointing=defaults.gladia_endpointing,
        gladia_max_utterance_secs=defaults.gladia_max_utterance_secs,
        gladia_audio_enhancer=defaults.gladia_audio_enhancer,
        gladia_language=defaults.gladia_language,
        system_prompt_id=system_id,
        knowledge_ids=[],
        local_context_id=None,
        personality_id=personality_id,
        tts_replacements=[],
        stt_vocabulary=[],
        triggers=default_triggers(),
    )
    store.insert_seed(content=content, applied=applied)


def _item(item_id: str, kind: str, name: str, markdown: str, stamp: str) -> dict:
    return {
        "id": item_id,
        "kind": kind,
        "name": name,
        "markdown": markdown,
        "updated_at": stamp,
    }
