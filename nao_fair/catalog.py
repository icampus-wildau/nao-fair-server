"""Sensors, motions, and timing ranges the console is allowed to use."""

from __future__ import annotations

SENSORS: tuple[tuple[str, str, str], ...] = (
    ("front_head", "FrontTactilTouched", "Vorderkopf"),
    ("middle_head", "MiddleTactilTouched", "Kopfmitte"),
    ("rear_head", "RearTactilTouched", "Hinterkopf"),
    ("left_hand", "HandLeftBackTouched", "Linke Hand"),
    ("right_hand", "HandRightBackTouched", "Rechte Hand"),
    ("left_bumper", "LeftBumperPressed", "Linker Bumper"),
    ("right_bumper", "RightBumperPressed", "Rechter Bumper"),
)

SENSOR_IDS: tuple[str, ...] = tuple(item[0] for item in SENSORS)

# ``stop`` stops motion. It is not a pose stored on a trigger binding.
MOTIONS: tuple[str, ...] = (
    "wave_left",
    "wave_right",
    "nod",
    "shake",
    "sit",
    "stand",
    "stop",
)
BINDING_MOTIONS: tuple[str, ...] = (
    "wave_left",
    "wave_right",
    "nod",
    "shake",
    "sit",
    "stand",
)

TIMING_RANGES: dict[str, dict[str, float]] = {
    "listening_timeout_secs": {"min": 0.5, "max": 120.0, "step": 0.1},
    "context_reset_timeout_secs": {"min": 1.0, "max": 3600.0, "step": 0.5},
    "thinking_timeout_secs": {"min": 1.0, "max": 180.0, "step": 0.5},
    "unmute_delay_secs": {"min": 0.0, "max": 10.0, "step": 0.1},
}

SETUP_SLOTS: tuple[str, ...] = (
    "system_prompt",
    "knowledge_bundle",
    "local_context",
    "personality",
    "llm",
    "voice",
    "stt_vocabulary",
    "tts_replacements",
    "interaction_mappings",
)

CONTENT_KINDS: tuple[str, ...] = (
    "system_prompt",
    "knowledge",
    "local_context",
    "personality",
)

PRESET_KINDS: tuple[str, ...] = SETUP_SLOTS


def capabilities() -> dict:
    return {
        "sensors": [
            {"id": sensor_id, "subscriber": subscriber, "label_de": label}
            for sensor_id, subscriber, label in SENSORS
        ],
        "motions": list(MOTIONS),
        "voices": [],
        "battery": False,
        "timing_ranges": TIMING_RANGES,
    }
