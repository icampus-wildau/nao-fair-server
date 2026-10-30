
import asyncio
import time
from typing import TYPE_CHECKING

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

if TYPE_CHECKING:
    from naoqi_control import NaoController
from nao_fair.booth.session_log import session_log

from enum import Enum

from nao_fair.booth.processors.nao_speech_processor import NAOSpeechFilter, NAOSpeechOutput

import logging

logger = logging.getLogger(__name__)

_FAILURE_COOLDOWN_SECS = 10.0
_LISTEN_FAILURE_LINE = "Ich kann gerade nicht zuhören."
_PIPELINE_ERROR_LINE = "Das habe ich nicht verstanden."


class NAOState(Enum):
    IDLE = "idle"
    SPEAKING = "speaking"
    LISTENING = "listening"
    THINKING = "thinking"


class NaoNlpManager(FrameProcessor):

    def __init__(
        self,
        nao: "NaoController | None",
        *,
        context: LLMContext,
        context_reset_timeout_secs: float = 30.0,
        listening_timeout_secs: float = 3.0,
        thinking_timeout_secs: float = 20.0,
        unmute_delay_secs: float = 0.3,
        bind_buttons: bool = True,
        external_context_reset: bool = False,
        microphone_enabled: bool = True,
        stt_enabled: bool = True,
        llm_enabled: bool = True,
        on_state=None,
        speech=None,
    ):
        super().__init__()

        self.nao = nao
        self._speech = speech
        self._context = context
        self._context_reset_timeout_secs = context_reset_timeout_secs
        self._external_context_reset = external_context_reset
        self.microphone_enabled = microphone_enabled
        self.stt_enabled = stt_enabled
        self.llm_enabled = llm_enabled
        self._on_state = on_state
        self._context_reset_timeout_task: asyncio.Task | None = None
        self._listening_timeout_secs = listening_timeout_secs
        self._listening_timeout_task: asyncio.Task | None = None
        self._thinking_timeout_secs = thinking_timeout_secs
        self._thinking_timeout_task: asyncio.Task | None = None
        self._unmute_delay_secs = unmute_delay_secs
        self._user_speaking = False
        self._resume_listening = False
        # Bumps each time listening opens. The stop-phrase watch drops
        # transcripts from the generation that was cancelled.
        self.listening_generation = 0
        self._robot_io_lock = asyncio.Lock()
        self._last_failure_spoken_at = float("-inf")
        self._tracked_tasks: set[asyncio.Task] = set()

        self.state = NAOState.IDLE

        if bind_buttons and nao is not None:
            self.nao.buttons.front_head.on_pressed(self._on_front_head_button)
            self.nao.buttons.back_head.on_pressed(self._on_back_head_button)
            self.nao.buttons.left_bumper.on_pressed(self._on_left_bumper_button)
            self.nao.buttons.right_bumper.on_pressed(self._on_right_bumper_button)

        self.raw_audio_filter_frame_processor = self._create_raw_audio_filter_frame_processor()
        self.speech_output_frame_processor = self._create_speech_output_frame_processor()

        self.mute_microphone()
        self.state = NAOState.IDLE
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            self.track_task(self._paint_initial_idle_eyes())

    def _create_raw_audio_filter_frame_processor(self) -> NAOSpeechFilter:
        return NAOSpeechFilter(self)

    def _create_speech_output_frame_processor(self) -> NAOSpeechOutput:
        return NAOSpeechOutput(self)

    def track_task(self, coro) -> asyncio.Task:
        """Remember a fire-and-forget task so cleanup can cancel it."""
        task = asyncio.get_running_loop().create_task(coro)
        self._tracked_tasks.add(task)
        task.add_done_callback(self._tracked_tasks.discard)
        return task

    async def _nao_call(self, fn, *args, timeout: float, **kwargs):
        from naoqi_control import run_nao_call

        try:
            await run_nao_call(self.nao, fn, *args, timeout=timeout, **kwargs)
        except TimeoutError:
            # A slow qi call must not fail the frame. Pipecat would otherwise
            # turn it into "Error processing frame" and treat the turn as broken.
            logger.warning(
                "NAO call timed out after %.1fs: %s",
                timeout,
                getattr(fn, "__name__", fn),
            )

    async def _paint_initial_idle_eyes(self) -> None:
        async with self._robot_io_lock:
            if self.state != NAOState.IDLE:
                return
            await self._paint_eyes("idle")

    async def _paint_eyes(self, effect: str) -> None:
        if self.nao is None:
            return
        from naoqi_control import QI_EYE_TIMEOUT_SECS

        await self._nao_call(getattr(self.nao.eyes, effect), timeout=QI_EYE_TIMEOUT_SECS)

    async def speak_text(self, text: str) -> None:
        if self._speech is not None:
            await self._speech.speak(text)
            return
        from naoqi_control import say_timeout_secs, speak_text

        await speak_text(self.nao, text, timeout=say_timeout_secs(text))

    async def cancel_speech(self) -> None:
        if self._speech is not None:
            await self._speech.stop()
            return
        from naoqi_control import QI_STOP_SPEAKING_TIMEOUT_SECS

        await self._nao_call(self.stop_speaking, timeout=QI_STOP_SPEAKING_TIMEOUT_SECS)

    async def transition_to_state(
        self, new_state: NAOState, *, reset_context: bool = False
    ) -> None:
        async with self._robot_io_lock:
            await self._transition_to_state_locked(new_state, reset_context=reset_context)

    async def _transition_to_state_locked(
        self, new_state: NAOState, *, reset_context: bool = False
    ) -> None:

        microphone = "open" if new_state == NAOState.LISTENING else "muted"
        session_log.state(new_state.value, microphone=microphone)

        stop_tts = False
        match new_state:
            case NAOState.IDLE:
                if self.state == NAOState.SPEAKING:
                    stop_tts = True

                self._cancel_listening_timeout()
                self._cancel_thinking_timeout()
                self._user_speaking = False
                self.mute_microphone()
                self.state = NAOState.IDLE
                if reset_context:
                    self._cancel_context_reset_timeout()
                    self._context.set_messages([])
                else:
                    self._start_context_reset_timeout()
                if stop_tts:
                    await self.cancel_speech()
                await self._paint_eyes("idle")

            case NAOState.LISTENING:
                if self.state == NAOState.SPEAKING:
                    stop_tts = True

                self.listening_generation += 1
                self._cancel_thinking_timeout()
                self._cancel_context_reset_timeout()
                self.unmute_microphone()
                self.state = NAOState.LISTENING
                self._start_listening_timeout()
                if stop_tts:
                    await self.cancel_speech()
                await self._paint_eyes("listening")

            case NAOState.SPEAKING:
                self._cancel_listening_timeout()
                self._cancel_thinking_timeout()
                self._cancel_context_reset_timeout()
                self.mute_microphone()
                self.state = NAOState.SPEAKING
                await self._paint_eyes("speaking")

            case NAOState.THINKING:
                self._cancel_listening_timeout()
                self.mute_microphone()
                self._resume_listening = True
                self.state = NAOState.THINKING
                await self._paint_eyes("thinking")
                self._start_thinking_timeout()

        if self._on_state is not None:
            self._on_state(self.state.value)

    def _cancel_context_reset_timeout(self) -> None:
        if self._context_reset_timeout_task is None:
            return

        self._context_reset_timeout_task.cancel()
        self._context_reset_timeout_task = None

    def _start_context_reset_timeout(self) -> None:
        if self._external_context_reset:
            return
        if self.state != NAOState.IDLE:
            return

        if not self._task_manager:
            return

        self._cancel_context_reset_timeout()
        self._context_reset_timeout_task = self.create_task(
            self._context_reset_timeout_handler(),
            name="context_reset_timeout",
        )

    async def _context_reset_timeout_handler(self) -> None:
        try:
            await asyncio.sleep(self._context_reset_timeout_secs)
            if self.state == NAOState.IDLE:
                logger.info("Context reset timeout reached, resetting context")
                self._context.set_messages([])
        except asyncio.CancelledError:
            pass
        finally:
            if self._context_reset_timeout_task is asyncio.current_task():
                self._context_reset_timeout_task = None

    def _cancel_listening_timeout(self) -> None:
        task = self._listening_timeout_task
        self._listening_timeout_task = None
        if task is None or task is asyncio.current_task():
            return

        task.cancel()

    def _start_listening_timeout(self) -> None:
        if self.state != NAOState.LISTENING or self._user_speaking:
            return

        if not self._task_manager:
            return

        self._cancel_listening_timeout()
        self._listening_timeout_task = self.create_task(
            self._listening_timeout_handler(),
            name="listening_timeout",
        )

    async def _listening_timeout_handler(self) -> None:
        try:
            await asyncio.sleep(self._listening_timeout_secs)
            if self.state == NAOState.LISTENING and not self._user_speaking:
                await self.transition_to_state(NAOState.IDLE)
        except asyncio.CancelledError:
            pass
        finally:
            if self._listening_timeout_task is asyncio.current_task():
                self._listening_timeout_task = None

    def _cancel_thinking_timeout(self) -> None:
        task = self._thinking_timeout_task
        self._thinking_timeout_task = None
        if task is None or task is asyncio.current_task():
            return

        task.cancel()

    def _start_thinking_timeout(self) -> None:
        if self.state != NAOState.THINKING:
            return

        if not self._task_manager:
            return

        self._cancel_thinking_timeout()
        self._thinking_timeout_task = self.create_task(
            self._thinking_timeout_handler(),
            name="thinking_timeout",
        )

    async def _thinking_timeout_handler(self) -> None:
        try:
            await asyncio.sleep(self._thinking_timeout_secs)
            if self.state == NAOState.THINKING:
                # Same interruption the front-head button uses, so a late
                # model response is cancelled and does not speak afterwards.
                self._resume_listening = True
                await self.broadcast_interruption()
                if self.state == NAOState.THINKING:
                    await self.transition_to_state(NAOState.LISTENING)
        except asyncio.CancelledError:
            pass
        finally:
            if self._thinking_timeout_task is asyncio.current_task():
                self._thinking_timeout_task = None

    async def cleanup(self) -> None:
        if self._context_reset_timeout_task is not None:
            await self.cancel_task(self._context_reset_timeout_task)
            self._context_reset_timeout_task = None
        if self._listening_timeout_task is not None:
            await self.cancel_task(self._listening_timeout_task)
            self._listening_timeout_task = None
        if self._thinking_timeout_task is not None:
            await self.cancel_task(self._thinking_timeout_task)
            self._thinking_timeout_task = None
        pending = [task for task in list(self._tracked_tasks) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tracked_tasks.clear()
        await super().cleanup()

    async def _on_left_bumper_button(self):
        session_log.event("Left bumper")
        await self.on_left_bumper_pressed()

    async def _on_right_bumper_button(self):
        session_log.event("Right bumper")
        await self.on_right_bumper_pressed()

    async def _on_front_head_button(self):
        session_log.event("Front head")
        await self.on_front_head_pressed()

    async def _on_back_head_button(self):
        session_log.event("Back head")
        await self.on_back_head_pressed()

    async def on_left_bumper_pressed(self):
        self.nao.motion.wave(["left"])

    async def on_right_bumper_pressed(self):
        self.nao.motion.wave(["right"])

    async def on_front_head_pressed(self):

        match self.state:
            case NAOState.IDLE:
                await self.transition_to_state(NAOState.LISTENING)
            case NAOState.LISTENING:
                self._resume_listening = False
                await self.transition_to_state(NAOState.IDLE, reset_context=True)
            case NAOState.SPEAKING:
                await self.broadcast_interruption()
            case NAOState.THINKING:
                self._resume_listening = True
                await self.broadcast_interruption()
                await self.transition_to_state(NAOState.LISTENING)

    async def on_back_head_pressed(self):
        match self.state:
            case NAOState.IDLE:
                self._resume_listening = False
                await self.transition_to_state(NAOState.IDLE, reset_context=True)
            case NAOState.LISTENING:
                self._resume_listening = False
                await self.transition_to_state(NAOState.IDLE, reset_context=True)
            case NAOState.SPEAKING:
                # Clear before the interruption. Speech output emits
                # BotStoppedSpeakingFrame while that broadcast is awaited.
                self._resume_listening = False
                await self.broadcast_interruption()
                await self.transition_to_state(NAOState.IDLE, reset_context=True)
            case NAOState.THINKING:
                self._resume_listening = False
                await self.broadcast_interruption()
                await self.transition_to_state(NAOState.IDLE, reset_context=True)

    # async def _wait_for_answer

    def stop_speaking(self):
        self.nao.stop_speaking()

    async def report_listen_failure(self) -> None:
        """Go idle and say that listening could not start."""
        await self._report_spoken_failure(_LISTEN_FAILURE_LINE)

    async def report_pipeline_error(self) -> None:
        """Go idle and say that this turn was not understood."""
        await self._report_spoken_failure(_PIPELINE_ERROR_LINE)

    async def _report_spoken_failure(self, line: str) -> None:
        try:
            async with self._robot_io_lock:
                speak = self._claim_failure_speech()
            if self.state != NAOState.IDLE:
                await self.transition_to_state(NAOState.IDLE)
            if speak and not getattr(self.nao, "_closing", False):
                await self.speak_text(line)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Could not speak failure line")

    def _claim_failure_speech(self) -> bool:
        now = time.monotonic()
        if now - self._last_failure_spoken_at < _FAILURE_COOLDOWN_SECS:
            return False
        self._last_failure_spoken_at = now
        return True

    def mute_microphone(self):
        self.raw_audio_filter_frame_processor.mute()

    def unmute_microphone(self):
        if not self.microphone_enabled or not self.stt_enabled:
            self.mute_microphone()
            return
        self.raw_audio_filter_frame_processor.unmute(delay=self._unmute_delay_secs)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # if isinstance(frame, InputAudioRawFrame):
        #     if self.state != NAOState.LISTENING:
        #         return

        # if isinstance(frame, BotStartedSpeakingFrame):
        #     self.state = NAOState.SPEAKING

        # if isinstance(frame, BotStoppedSpeakingFrame):
        #     self.state = NAOState.LISTENING

        if isinstance(frame, BotStartedSpeakingFrame):
            if self.state != NAOState.IDLE or self._resume_listening:
                self._resume_listening = True
                await self.transition_to_state(NAOState.SPEAKING)

        if isinstance(frame, BotStoppedSpeakingFrame):
            if self.state == NAOState.SPEAKING and self._resume_listening:
                await self.transition_to_state(NAOState.LISTENING)
            elif self.state == NAOState.SPEAKING:
                await self.transition_to_state(NAOState.IDLE)

        if isinstance(frame, UserStartedSpeakingFrame):
            session_log.verbose("VAD: user started speaking")
            async with self._robot_io_lock:
                self._user_speaking = True
                self._cancel_listening_timeout()
                await self._paint_eyes("listening_to_user_speaking")

        if isinstance(frame, UserStoppedSpeakingFrame):
            session_log.verbose("VAD: user stopped speaking")
            go_thinking = False
            async with self._robot_io_lock:
                self._user_speaking = False
                if self.state == NAOState.LISTENING:
                    go_thinking = True
            if go_thinking:
                if self.llm_enabled:
                    await self.transition_to_state(NAOState.THINKING)
                else:
                    self._start_listening_timeout()

        await self.push_frame(frame, direction)
