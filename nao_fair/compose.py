"""Prompt text used when a pipeline is built. The clock paragraph is not included."""

from __future__ import annotations

import re

from nao_fair.models import Applied
from nao_fair.store import Store

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def strip_comments(text: str) -> str:
    return _COMMENT.sub("", text)


def prompt_sections(store: Store, applied: Applied, robot_name: str) -> list[str]:
    """Personality, system prompt, knowledge, then local context.

    ``{robot_name}`` is filled here. The booth injector appends the clock.
    """
    name = robot_name.strip() or "NAO"

    def fill(markdown: str) -> str:
        return strip_comments(markdown).replace("{robot_name}", name).strip()

    personality = _markdown(store, applied.personality_id, "personality")
    system = _markdown(store, applied.system_prompt_id, "system_prompt")
    local = _markdown(store, applied.local_context_id, "local_context")
    sections = [fill(personality), fill(system)]
    for knowledge_id in applied.knowledge_ids:
        sections.append(fill(_markdown(store, knowledge_id, "knowledge")))
    sections.append(fill(local))
    return [section for section in sections if section]


def enabled_replacements(applied: Applied) -> list[tuple[str, str]]:
    return [
        (item.source, item.replacement)
        for item in applied.tts_replacements
        if item.enabled and item.source and item.replacement
    ]


def enabled_vocabulary(applied: Applied) -> list[str]:
    return [item.term for item in applied.stt_vocabulary if item.enabled and item.term.strip()]


def _markdown(store: Store, item_id: str, kind: str) -> str:
    item = store.get_content(item_id)
    if item is None or item["kind"] != kind:
        raise ValueError(f"missing {kind}")
    return str(item["markdown"])
