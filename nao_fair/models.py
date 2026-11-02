"""JSON shapes shared by the picture, apply, and stored configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nao_fair.catalog import BINDING_MOTIONS, SENSOR_IDS, TIMING_RANGES

ActionName = Literal[
    "speak",
    "start_conversation",
    "stop_conversation",
    "stop_speech",
    "toggle_conversation",
    "stop_and_clear",
    "motion",
    "stop_motion",
    "dialog_next",
    "dialog_repeat",
    "dialog_previous",
]
GestureName = Literal["tap", "double_tap", "long_press"]
DeliveryName = Literal["plain", "motion", "animated"]
LineMotion = Literal["wave_left", "wave_right", "nod", "shake"]
GESTURE_NAMES = ("tap", "double_tap", "long_press")
LINE_MOTIONS = ("wave_left", "wave_right", "nod", "shake")
PostureName = Literal["Sit", "Stand"]
ConnectionState = Literal["disconnected", "connecting", "connected"]
ConversationState = Literal["idle", "listening", "thinking", "speaking"]
SpeakerName = Literal["visitor", "nao"]
ContentKind = Literal["system_prompt", "knowledge", "local_context", "personality"]
ScreenName = Literal["robot", "llm", "interactions"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Replacement(ApiModel):
    source: str
    replacement: str
    enabled: bool


class VocabularyTerm(ApiModel):
    term: str
    enabled: bool


class GestureBinding(ApiModel):
    action: ActionName | None = None
    phrase: str = ""
    voice: str | None = None
    motion: str | None = None
    enabled: bool = True

    @field_validator("voice")
    @classmethod
    def voice_stays_null(cls, value: str | None) -> None:
        if value is not None:
            raise ValueError("voice is not supported")
        return None

    @field_validator("motion")
    @classmethod
    def motion_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in BINDING_MOTIONS:
            raise ValueError("unsupported motion")
        return value

    @model_validator(mode="after")
    def action_matches_payload(self) -> GestureBinding:
        if self.action is None:
            if self.motion is not None:
                raise ValueError("empty trigger cannot store a motion")
            return self
        if self.action == "motion":
            if self.motion is None:
                raise ValueError("motion action requires a motion")
        elif self.motion is not None:
            raise ValueError("motion is only set for a motion action")
        if self.action == "speak" and self.enabled and not self.phrase.strip():
            raise ValueError("speak action requires a phrase")
        return self


def empty_gesture(enabled: bool = True) -> dict:
    return {
        "action": None,
        "phrase": "",
        "voice": None,
        "motion": None,
        "enabled": enabled,
    }


def trigger_from_flat(value: dict) -> dict:
    """A saved trigger with one action becomes the tap. The other gestures copy its switch."""
    enabled = value.get("enabled", True)
    tap = {
        "action": value.get("action"),
        "phrase": value.get("phrase", ""),
        "voice": value.get("voice"),
        "motion": value.get("motion"),
        "enabled": enabled,
    }
    return {
        "tap": tap,
        "double_tap": empty_gesture(enabled),
        "long_press": empty_gesture(enabled),
    }


class TriggerBinding(ApiModel):
    tap: GestureBinding
    double_tap: GestureBinding
    long_press: GestureBinding

    @model_validator(mode="before")
    @classmethod
    def upgrade_flat_trigger(cls, value: object) -> object:
        if isinstance(value, dict) and "tap" not in value and "action" in value:
            return trigger_from_flat(value)
        return value

    def gesture(self, name: str) -> GestureBinding:
        return getattr(self, name)


class DialogLine(ApiModel):
    speaker: Literal["nao", "human"] = "nao"
    id: str = Field(min_length=1)
    text: str
    delivery: DeliveryName = "plain"
    motion: LineMotion | None = None

    @model_validator(mode="after")
    def delivery_matches_motion(self) -> DialogLine:
        if not self.text.strip():
            raise ValueError("line text is empty")
        if self.delivery == "motion":
            if self.motion is None:
                raise ValueError("motion delivery requires a motion")
        elif self.motion is not None:
            raise ValueError("motion is only set for a motion delivery")
        return self


class Dialog(ApiModel):
    id: str = Field(min_length=1)
    name: str
    wrap: bool = False
    idle_reset_secs: float | None = 300
    lines: list[DialogLine] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def trimmed_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("dialog name is empty")
        return name

    @field_validator("idle_reset_secs")
    @classmethod
    def idle_reset_range(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value <= 0 or value > 86400:
            raise ValueError("idle reset is outside 0..86400")
        return value


def check_dialogs(dialogs: list[Dialog], active_dialog_id: str | None) -> None:
    ids = [item.id for item in dialogs]
    if len(ids) != len(set(ids)):
        raise ValueError("dialog id is duplicated")
    names = [item.name for item in dialogs]
    if len(names) != len(set(names)):
        raise ValueError("dialog name is duplicated")
    if active_dialog_id is not None and active_dialog_id not in set(ids):
        raise ValueError("active dialog is missing")
    for dialog in dialogs:
        line_ids = [line.id for line in dialog.lines]
        if len(line_ids) != len(set(line_ids)):
            raise ValueError("line id is duplicated")


def empty_binding() -> dict:
    return trigger_from_flat(empty_gesture())


def default_triggers() -> dict[str, dict]:
    triggers = {sensor_id: empty_binding() for sensor_id in SENSOR_IDS}
    triggers["front_head"] = trigger_from_flat({**empty_gesture(), "action": "toggle_conversation"})
    triggers["rear_head"] = trigger_from_flat({**empty_gesture(), "action": "stop_and_clear"})
    triggers["left_bumper"] = trigger_from_flat({**empty_gesture(), "action": "motion", "motion": "wave_left"})
    triggers["right_bumper"] = trigger_from_flat({**empty_gesture(), "action": "motion", "motion": "wave_right"})
    return triggers


class Applied(ApiModel):
    revision: int = Field(ge=1)
    volume: int = Field(ge=0, le=100)
    speech_speed: int = Field(ge=50, le=400)
    tts_language: str = Field(min_length=1)
    posture: PostureName
    microphone_enabled: bool
    stt_enabled: bool
    llm_enabled: bool
    listening_timeout_secs: float
    context_reset_timeout_secs: float
    thinking_timeout_secs: float
    unmute_delay_secs: float
    llm_model: str = Field(min_length=1)
    temperature: float | None = None
    max_tokens: int | None = None
    gladia_region: str = Field(min_length=1)
    gladia_endpointing: float
    gladia_max_utterance_secs: float
    gladia_audio_enhancer: bool
    gladia_language: str = Field(min_length=1)
    system_prompt_id: str = Field(min_length=1)
    knowledge_ids: list[str]
    local_context_id: str = Field(min_length=1)
    personality_id: str = Field(min_length=1)
    tts_replacements: list[Replacement]
    stt_vocabulary: list[VocabularyTerm]
    triggers: dict[str, TriggerBinding]
    dialogs: list[Dialog] = Field(default_factory=list)
    active_dialog_id: str | None = None
    autostart_enabled: bool = True

    @model_validator(mode="after")
    def check_ranges(self) -> Applied:
        for name, bounds in TIMING_RANGES.items():
            value = getattr(self, name)
            if value < bounds["min"] or value > bounds["max"]:
                raise ValueError(f"{name} is outside {bounds['min']}..{bounds['max']}")
        if self.gladia_endpointing <= 0 or self.gladia_endpointing > 10:
            raise ValueError("gladia_endpointing is outside 0..10")
        if not 1 <= self.gladia_max_utterance_secs <= 120:
            raise ValueError("gladia_max_utterance_secs is outside 1..120")
        if self.temperature is not None and not 0 <= self.temperature <= 2:
            raise ValueError("temperature is outside 0..2")
        if self.max_tokens is not None and not 1 <= self.max_tokens <= 128000:
            raise ValueError("max_tokens is outside 1..128000")
        if set(self.triggers) != set(SENSOR_IDS):
            raise ValueError("triggers must contain the seven sensors")
        check_dialogs(self.dialogs, self.active_dialog_id)
        sources = [item.source for item in self.tts_replacements]
        if any(not item.source.strip() for item in self.tts_replacements):
            raise ValueError("tts replacement source is empty")
        if len(sources) != len(set(sources)):
            raise ValueError("tts replacement source is duplicated")
        terms = [item.term for item in self.stt_vocabulary]
        if any(not item.term.strip() for item in self.stt_vocabulary):
            raise ValueError("stt vocabulary term is empty")
        if len(terms) != len(set(terms)):
            raise ValueError("stt vocabulary term is duplicated")
        return self


class Connection(ApiModel):
    state: ConnectionState
    address: str | None
    name: str | None
    failure: str | None


class AudioUpdate(ApiModel):
    volume: int | None = Field(default=None, ge=0, le=100)
    speech_speed: int | None = Field(default=None, ge=50, le=200)

    @model_validator(mode="after")
    def has_update(self) -> AudioUpdate:
        if self.volume is None and self.speech_speed is None:
            raise ValueError("an audio value is required")
        return self


class RobotDraft(ApiModel):
    volume: int = Field(ge=0, le=100)
    speech_speed: int = Field(ge=50, le=400)
    tts_language: str = Field(min_length=1)
    posture: PostureName
    microphone_enabled: bool
    stt_enabled: bool
    llm_enabled: bool
    listening_timeout_secs: float
    context_reset_timeout_secs: float
    thinking_timeout_secs: float
    unmute_delay_secs: float


class LlmDraft(ApiModel):
    llm_model: str = Field(min_length=1)
    temperature: float | None = None
    max_tokens: int | None = None
    gladia_region: str = Field(min_length=1)
    gladia_endpointing: float
    gladia_max_utterance_secs: float
    gladia_audio_enhancer: bool
    gladia_language: str = Field(min_length=1)
    system_prompt_id: str = Field(min_length=1)
    knowledge_ids: list[str]
    local_context_id: str = Field(min_length=1)
    personality_id: str = Field(min_length=1)
    tts_replacements: list[Replacement]
    stt_vocabulary: list[VocabularyTerm]


class InteractionsDraft(ApiModel):
    triggers: dict[str, TriggerBinding]
    dialogs: list[Dialog] = Field(default_factory=list)
    active_dialog_id: str | None = None
    autostart_enabled: bool = True

    @model_validator(mode="after")
    def dialogs_belong(self) -> InteractionsDraft:
        check_dialogs(self.dialogs, self.active_dialog_id)
        return self


class ApplyRequest(ApiModel):
    screen: ScreenName
    draft: dict
    expected_revision: int = Field(ge=1)


class ConnectRequest(ApiModel):
    address: str


class SpeakRequest(ApiModel):
    text: str = Field(min_length=1)
    action_key: str | None = Field(default=None, max_length=120)


class MotionRequest(ApiModel):
    motion: Literal["wave_left", "wave_right", "nod", "shake", "sit", "stand"]


class DialogTestRequest(ApiModel):
    action: Literal["dialog_next", "dialog_repeat", "dialog_previous"]


class SecretWrite(ApiModel):
    value: str


class ConfirmDelete(ApiModel):
    confirm: bool


class ContentWrite(ApiModel):
    kind: ContentKind
    name: str = Field(min_length=1)
    markdown: str


class ContentUpdate(ApiModel):
    name: str = Field(min_length=1)
    markdown: str


class PresetWrite(ApiModel):
    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    payload: dict


class PresetUpdate(ApiModel):
    name: str = Field(min_length=1)
    description: str = ""
    payload: dict


class SetupWrite(ApiModel):
    name: str = Field(min_length=1)
    description: str = ""
    preset_ids: dict[str, str]


class ScenarioWrite(LlmDraft):
    name: str = Field(min_length=1)
    description: str = ""
    triggers: dict[str, TriggerBinding]
    dialogs: list[Dialog] = Field(default_factory=list)
    active_dialog_id: str | None = None
    autostart_enabled: bool = True

    @model_validator(mode="after")
    def dialogs_belong(self) -> ScenarioWrite:
        check_dialogs(self.dialogs, self.active_dialog_id)
        return self


ROBOT_FIELDS = tuple(RobotDraft.model_fields)
