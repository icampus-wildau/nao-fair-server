"""Push the applied autostart assignment and renew its lease over NAOqi.

Key names match nao-fair-autostart. A missing acknowledgement means the
on-robot process did not store the document. The connection stays up.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from nao_fair.compose import enabled_replacements
from nao_fair.models import Applied

logger = logging.getLogger(__name__)

ASSIGNMENT = "NAOFair/Autostart/Assignment"
ASSIGNMENT_SEQ = "NAOFair/Autostart/AssignmentSeq"
POSITION = "NAOFair/Dialog/Position"
POSITION_SEQ = "NAOFair/Dialog/PositionSeq"
ACK_SEQ = "NAOFair/Autostart/AckSeq"
LEASE_COMMAND = "NAOFair/Autostart/LeaseCommand"
LEASE_SEQ = "NAOFair/Autostart/LeaseSeq"
LEASE_ACK_SEQ = "NAOFair/Autostart/LeaseAckSeq"
VERSION = "NAOFair/Autostart/Version"
ARMED_STATE = "NAOFair/Autostart/ArmedState"

ACK_TIMEOUT_SECS = 2.0
LEASE_PERIOD_SECS = 1.0
KEPT_NOTICE = "Übernommen. Der Roboter hat den bisherigen Autostart behalten."


def assignment_document(applied: Applied) -> dict[str, Any]:
    from nao_fair.robot.live import nao_language

    return {
        "armed": bool(applied.autostart_enabled),
        "volume": int(applied.volume),
        "speech_speed": int(applied.speech_speed),
        "tts_language": nao_language(applied.tts_language),
        "replacements": [[source, replacement] for source, replacement in enabled_replacements(applied)],
        "triggers": {
            sensor_id: {
                gesture: {
                    "action": slot.action,
                    "phrase": slot.phrase,
                    "motion": slot.motion,
                    "enabled": slot.enabled,
                    "delivery": slot.delivery,
                    "yield_next": slot.yield_next,
                }
                for gesture, slot in (
                    ("tap", binding.tap),
                    ("double_tap", binding.double_tap),
                    ("long_press", binding.long_press),
                )
            }
            for sensor_id, binding in applied.triggers.items()
        },
        "dialogs": [dialog.model_dump(mode="json") for dialog in applied.dialogs],
        "active_dialog_id": applied.active_dialog_id,
    }


async def push_assignment(nao: Any, document: dict[str, Any], seq: int, *, timeout: float = ACK_TIMEOUT_SECS) -> bool:
    payload = json.dumps(document, ensure_ascii=False)

    def write() -> None:
        memory = nao.session.service("ALMemory")
        memory.insertData(ASSIGNMENT, payload)
        memory.insertData(ACK_SEQ, -1)
        memory.raiseEvent(ASSIGNMENT_SEQ, int(seq))

    await nao.call_qi(write)
    return await _wait_equal(nao, ACK_SEQ, seq, timeout)


async def write_lease(nao: Any, command: str, seq: int) -> None:
    def write() -> None:
        memory = nao.session.service("ALMemory")
        memory.insertData(LEASE_COMMAND, command)
        memory.insertData(LEASE_ACK_SEQ, -1)
        memory.raiseEvent(LEASE_SEQ, int(seq))

    await nao.call_qi(write)


async def wait_lease_ack(nao: Any, seq: int, *, timeout: float = ACK_TIMEOUT_SECS) -> bool:
    return await _wait_equal(nao, LEASE_ACK_SEQ, seq, timeout)


async def read_armed_state(nao: Any) -> dict | None:
    def read():
        try:
            value = nao.session.service("ALMemory").getData(ARMED_STATE)
            data = json.loads(value)
        except (ValueError, TypeError, RuntimeError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("armed"), bool):
            return None
        if not isinstance(data.get("change"), str) or not data["change"]:
            return None
        return data
    try:
        return await nao.call_qi(read, timeout=2.0)
    except Exception:
        logger.exception("Could not read robot autostart switch")
        return None


async def read_reported_version(nao: Any) -> str | None:
    def read() -> str | None:
        try:
            value = nao.session.service("ALMemory").getData(VERSION)
        except Exception:
            return None
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    try:
        return await nao.call_qi(read)
    except Exception:
        logger.exception("Could not read the autostart version")
        return None


async def _wait_equal(nao: Any, key: str, expected: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        def read() -> int | None:
            try:
                return int(nao.session.service("ALMemory").getData(key))
            except Exception:
                return None

        try:
            seen = await nao.call_qi(read)
        except Exception:
            logger.exception("Could not read %s", key)
            return False
        if seen == expected:
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.05)
