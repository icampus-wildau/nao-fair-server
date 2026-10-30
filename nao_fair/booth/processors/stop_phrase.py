"""Stop listening when a transcript contains a stop phrase."""

from __future__ import annotations

import re
from typing import Protocol

from pipecat.audio.vad.vad_analyzer import VADState
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.turns.user_stop import UserTurnStoppedParams

from nao_fair.booth.session_log import session_log

# "stop" or "stopp" only. Longer words such as "stoppen" do not match.
_STOP_WORD = r"stopp?"
# Gladia often writes the robot name NAO as "Now" or "Nau".
_NAO_ALIASES = ("nao", "now", "nau")


def contains_stop_phrase(text: str, *, robot_name: str | None = None) -> bool:
    """Return True when text contains a stop phrase.

    A stop phrase is two consecutive words, ignoring case and punctuation:
    stop/stopp twice, or stop/stopp next to NAO, "now", "nau", or the
    configured robot name, in either order.
    """
    return _stop_phrase_pattern(robot_name).search(text) is not None


def _stop_phrase_pattern(robot_name: str | None) -> re.Pattern[str]:
    stop_word = rf"(?<!\w){_STOP_WORD}(?!\w)"
    name_word = rf"(?<!\w)(?:{_name_alternation(robot_name)})(?!\w)"
    return re.compile(
        rf"(?i){stop_word}\W+{stop_word}"
        rf"|{name_word}\W+{stop_word}"
        rf"|{stop_word}\W+{name_word}"
    )


def _name_alternation(robot_name: str | None) -> str:
    names = list(_NAO_ALIASES)
    cleaned = (robot_name or "").strip()
    if cleaned:
        parts = [re.escape(part) for part in cleaned.split() if part]
        if parts:
            names.append(r"\W+".join(parts))
    unique: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)
    return "|".join(unique)


class _StopPhraseRobot(Protocol):
    listening_generation: int

    async def on_back_head_pressed(self) -> None: ...


class _TurnAggregation(Protocol):
    async def reset(self) -> None: ...


class StopPhraseProcessor(FrameProcessor):
    """Drop a stop phrase as soon as Gladia emits it and end listening.

    Partials are enough. The in-flight user turn is closed immediately so
    the turn watchdog does not later emit "user stopped speaking" into the
    next listen. Transcripts from the cancelled listen stay dropped until
    listening opens again.

    Gladia's STT service consumes ``STTMuteFrame`` and does not forward it,
    so unmute cannot be observed from here. ``listening_generation`` is the
    signal that a new listen has started.
    """

    def __init__(
        self,
        nao_manager: _StopPhraseRobot,
        user_aggregator: _TurnAggregation,
        *,
        enable_direct_mode: bool = False,
        on_final_transcript=None,
    ):
        super().__init__(enable_direct_mode=enable_direct_mode)
        self._nao_manager = nao_manager
        self._user_aggregator = user_aggregator
        self._dropped_generation: int | None = None
        self._on_final_transcript = on_final_transcript

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)):
            if await self._transcript_belongs_to_cancelled_listen():
                return
            _log_transcript(frame)
            if isinstance(frame, TranscriptionFrame) and self._on_final_transcript is not None:
                await self._on_final_transcript(frame.text)
            if contains_stop_phrase(frame.text, robot_name=_robot_name(self._nao_manager)):
                session_log.event("Stop phrase")
                self._dropped_generation = self._nao_manager.listening_generation
                await self._cancel_open_user_turn()
                await self._nao_manager.on_back_head_pressed()
                return

        await self.push_frame(frame, direction)

    async def _transcript_belongs_to_cancelled_listen(self) -> bool:
        if self._dropped_generation is None:
            return False
        if self._nao_manager.listening_generation != self._dropped_generation:
            self._dropped_generation = None
            # A final from the cancelled listen may already sit in the
            # aggregator. Drop it before the new listen's text is appended.
            await self._user_aggregator.reset()
            return False
        return True

    async def _cancel_open_user_turn(self) -> None:
        """End the current turn without telling the robot the user stopped.

        ``UserStoppedSpeakingFrame`` is what moves listening into thinking.
        The stop phrase already leaves listening, so that frame must not be
        emitted later by the turn watchdog (strategy ``None``).
        """
        await self._user_aggregator.reset()
        controller = getattr(self._user_aggregator, "_user_turn_controller", None)
        if controller is None:
            return

        # A stop is ignored while the controller still marks the user speaking.
        controller._user_speaking = False
        _quiet_vad(getattr(self._user_aggregator, "_vad_controller", None))
        for strategy in controller.user_turn_strategies.stop or []:
            if hasattr(strategy, "_vad_user_speaking"):
                strategy._vad_user_speaking = False

        if controller._user_turn:
            await controller._trigger_user_turn_stop(
                None,
                UserTurnStoppedParams(enable_user_speaking_frames=False),
            )


def _log_transcript(frame: InterimTranscriptionFrame | TranscriptionFrame) -> None:
    if isinstance(frame, InterimTranscriptionFrame):
        session_log.interim(frame.text)
        return
    session_log.user(frame.text)


def _robot_name(nao_manager: object) -> str | None:
    nao = getattr(nao_manager, "nao", None)
    name = getattr(nao, "robot_name", None)
    if isinstance(name, str) and name.strip():
        return name
    return None


def _quiet_vad(vad_controller: object | None) -> None:
    """Drop a mid-speech VAD state so mute does not emit a late speech stop."""
    if vad_controller is None:
        return
    vad_controller._vad_state = VADState.QUIET  # type: ignore[attr-defined]
    analyzer = getattr(vad_controller, "_vad_analyzer", None)
    if analyzer is None:
        return
    analyzer._vad_state = VADState.QUIET
    analyzer._vad_starting_count = 0
    analyzer._vad_stopping_count = 0
    if hasattr(analyzer, "_vad_buffer"):
        analyzer._vad_buffer = b""
