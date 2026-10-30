from __future__ import annotations
import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING
import uuid
from collections.abc import Awaitable, Callable

from pipecat.utils.text.transforms.replacements import replace_text

from pipecat.frames.frames import (
    AggregatedTextFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    StartFrame,
    STTMuteFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
)

from nao_fair.booth.services.pausable_gladia import (
    PausableGladiaSTTService,
    ResumeResult,
)

if TYPE_CHECKING:
    from nao_fair.booth.nao.nao_nlp_manager import NAOState, NaoNlpManager

logger = logging.getLogger(__name__)

_PIPELINE_READY_TIMEOUT_SECS = 30.0
_PIPELINE_READY_POLL_SECS = 0.05

_DEFAULT_COALESCE_HOLD_SECS = 0.35
_DEFAULT_COALESCE_GRACE_SECS = 0.15
_DEFAULT_COALESCE_MAX_SENTENCES = 5
_DEFAULT_COALESCE_MAX_CHARS = 400

_TTS_REPLACEMENTS = {
    "\u2013": "-",
    "\u2014": "-",
    "\u201c": "\"",
    "\u201d": "\"",
    "\u2018": "'",
    "\u2019": "'",
    "\u2026": "...",
    "\u201e": "\"",
}

_TTS_WORD_REPLACEMENTS_PATH = (
    Path(__file__).resolve().parents[3] / "prompts" / "tts_replacements.txt"
)


def _load_tts_word_replacements(
    path: Path | None = None,
) -> list[tuple[str, str]]:
    file_path = path if path is not None else _TTS_WORD_REPLACEMENTS_PATH
    if not file_path.is_file():
        return []
    content = file_path.read_text(encoding="utf-8")
    if not content.strip():
        return []
    pairs: list[tuple[str, str]] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if " -> " not in stripped:
            continue
        left, right = stripped.split(" -> ", 1)
        left = left.strip()
        right = right.strip()
        if not left or not right:
            continue
        pairs.append((rf"\b{re.escape(left)}\b", right))
    return pairs


_tts_word_pairs: list[tuple[str, str]] = _load_tts_word_replacements()


def set_tts_word_replacements(pairs: list[tuple[str, str]]) -> None:
    """Use these literal source/replacement pairs on the next spoken phrase."""
    global _tts_word_pairs
    compiled: list[tuple[str, str]] = []
    for source, replacement in pairs:
        compiled.append((rf"\b{re.escape(source)}\b", replacement))
    _tts_word_pairs = compiled


class _WaitAbort:
    """A coalesce wait was woken because speech was interrupted."""


_WAIT_ABORT = _WaitAbort()

_QueueItem = AggregatedTextFrame | LLMFullResponseEndFrame


def _join_text(frames: list[AggregatedTextFrame]) -> str:
    return " ".join(frame.text.strip() for frame in frames)


async def _normalize_tts(text: str) -> str:
    for old, new in _TTS_REPLACEMENTS.items():
        text = text.replace(old, new)
    transform = replace_text(list(_tts_word_pairs))
    return await transform(text, "*")


async def _cancel_task(task: asyncio.Task) -> None:
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


SpeechStartedCallback = Callable[[], Awaitable[None]]
SpeechStoppedCallback = Callable[[bool], Awaitable[None]]

_CONNECTION_ERROR_MARKERS = (
    "gladia",
    "websocket",
    "reconnect",
    "connection",
    "transport",
    "audio bridge",
)


def error_frame_is_connection_failure(frame: ErrorFrame) -> bool:
    """Transport and speech-service drops are not a missed utterance."""
    text = (frame.error or "").lower()
    return any(marker in text for marker in _CONNECTION_ERROR_MARKERS)


def _notify_manager(manager, method_name: str) -> None:
    notify = getattr(manager, method_name, None)
    if notify is None:
        return
    track = getattr(manager, "track_task", None)
    if track is not None:
        track(notify())
        return
    asyncio.create_task(notify())


class NAOSpeechFilter(FrameProcessor):
    """Drop microphone audio while muted and keep Gladia disconnected.

    Replacing muted audio with zeros still has a duration, and Gladia bills
    that duration. Frames are discarded instead, and the live session is
    closed until listening resumes.
    """

    def __init__(
        self,
        nao_manager: NaoNlpManager,
        *,
        enable_direct_mode: bool = False,
    ):
        super().__init__(enable_direct_mode=enable_direct_mode)

        self.nao_manager = nao_manager

        self.is_muted = False
        self._stt: PausableGladiaSTTService | None = None
        self._generation = 0
        self._op_task: asyncio.Task | None = None
        self._pipeline_started = False

    def attach_stt(self, stt: PausableGladiaSTTService) -> None:
        """Connect the filter to the Gladia service it gates."""
        self._stt = stt
        setter = getattr(stt, "set_live_session_lost_handler", None)
        if setter is not None:
            setter(self._on_gladia_session_lost)
        if self.is_muted:
            stt.mark_muted()

    def _cancel_op_task(self) -> None:
        if self._op_task is not None:
            self._op_task.cancel()
            self._op_task = None

    def _spawn(self, coro: Awaitable[None]) -> None:
        self._cancel_op_task()
        self._op_task = asyncio.create_task(coro)

    def mute(self):
        self._generation += 1
        self.is_muted = True
        if self._stt is not None:
            self._stt.mark_muted()
        self._spawn(self._pause_gladia(self._generation))

    def unmute(self, delay: float = 0.3):
        if (
            not self.is_muted
            and self._stt is not None
            and self._stt.session_is_open
        ):
            return

        self._generation += 1
        generation = self._generation
        self._spawn(self._delayed_unmute(delay, generation))

    async def _pause_gladia(self, generation: int) -> None:
        try:
            if self._stt is not None:
                await self._stt.pause_billing()
            if generation != self._generation:
                return
            if self._pipeline_started:
                await self.push_frame(STTMuteFrame(mute=True))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to pause Gladia while muted")
        finally:
            if self._op_task is asyncio.current_task():
                self._op_task = None

    async def _delayed_unmute(self, delay: float, generation: int) -> None:
        connect_task: asyncio.Task[ResumeResult] | None = None
        try:
            if self._stt is None:
                logger.error("Cannot unmute: Gladia STT is not attached")
                return

            connect_task = asyncio.create_task(self._resume_when_ready(generation))
            await asyncio.sleep(delay)
            if generation != self._generation:
                return

            result = await connect_task
            connect_task = None
            if generation != self._generation or result is not ResumeResult.OPEN:
                if result is ResumeResult.FAILED:
                    logger.error(
                        "Gladia session did not resume; microphone stays muted"
                    )
                    _notify_manager(self.nao_manager, "report_listen_failure")
                return

            self.is_muted = False
            if self._pipeline_started:
                await self.push_frame(STTMuteFrame(mute=False))
        except asyncio.CancelledError:
            raise
        finally:
            if connect_task is not None:
                connect_task.cancel()
            if self._op_task is asyncio.current_task():
                self._op_task = None

    async def _resume_when_ready(self, generation: int) -> ResumeResult:
        assert self._stt is not None
        deadline = asyncio.get_running_loop().time() + _PIPELINE_READY_TIMEOUT_SECS
        while generation == self._generation:
            result = await self._stt.resume_billing()
            if result is not ResumeResult.DEFERRED:
                return result
            if asyncio.get_running_loop().time() >= deadline:
                logger.error(
                    "Gladia sample rate was not ready within %.0fs",
                    _PIPELINE_READY_TIMEOUT_SECS,
                )
                return ResumeResult.FAILED
            await asyncio.sleep(_PIPELINE_READY_POLL_SECS)
        return ResumeResult.FAILED

    def _on_gladia_session_lost(self) -> None:
        """A live Gladia socket died and a fresh session could not be opened."""
        if self.is_muted:
            return
        _notify_manager(self.nao_manager, "report_listen_failure")

    async def cleanup(self) -> None:
        self._generation += 1
        task = self._op_task
        self._op_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await super().cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._pipeline_started = True

        if isinstance(frame, InputAudioRawFrame) and self.is_muted:
            return

        await self.push_frame(frame, direction)


class NAOSpeechOutput(FrameProcessor):
    def __init__(
        self,
        nao_manager: NaoNlpManager,
        *,
        coalesce_hold_secs: float = _DEFAULT_COALESCE_HOLD_SECS,
        coalesce_grace_secs: float = _DEFAULT_COALESCE_GRACE_SECS,
        coalesce_max_sentences: int = _DEFAULT_COALESCE_MAX_SENTENCES,
        coalesce_max_chars: int = _DEFAULT_COALESCE_MAX_CHARS,
    ):
        super().__init__()

        self.nao_manager = nao_manager
        self._coalesce_hold_secs = coalesce_hold_secs
        self._coalesce_grace_secs = coalesce_grace_secs
        self._coalesce_max_sentences = coalesce_max_sentences
        self._coalesce_max_chars = coalesce_max_chars

        self._speaking = False
        self._context_id: str | None = None
        self._generation = 0

        self._queue: asyncio.Queue[_QueueItem] = asyncio.Queue()
        self._pushback: _QueueItem | None = None
        self._wake = asyncio.Event()
        self._worker_task: asyncio.Task | None = None

        self.on_speech_started: SpeechStartedCallback | None = None
        self.on_speech_stopped: SpeechStoppedCallback | None = None
        self.on_phrase: Callable[[str], Awaitable[None]] | None = None

    async def cleanup(self) -> None:
        self._generation += 1
        self._drain_queue()
        self._wake.set()
        task = self._worker_task
        self._worker_task = None
        if task is not None:
            await _cancel_task(task)
        await super().cleanup()

    def set_lifecycle_callbacks(
        self,
        *,
        on_started: SpeechStartedCallback,
        on_stopped: SpeechStoppedCallback,
    ):
        self.on_speech_started = on_started
        self.on_speech_stopped = on_stopped

    async def process_frame(
        self,
        frame: Frame,
        direction: FrameDirection,
    ) -> None:
        await super().process_frame(frame, direction)

        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, ErrorFrame):
            if not error_frame_is_connection_failure(frame):
                _notify_manager(self.nao_manager, "report_pipeline_error")
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterruptionFrame):

            self._generation += 1
            self._drain_queue()
            self._wake.set()

            await self.nao_manager.cancel_speech()

            await self.push_frame(frame, direction)
            await self._finish_speaking(interrupted=True)

            return

        if isinstance(frame, AggregatedTextFrame):
            # print(f"####### Got AggregatedTextFrame: {frame.text}")
            if frame.skip_tts:
                await self.push_frame(frame, direction)
                return

            if not frame.text.strip():
                return

            self._queue.put_nowait(frame)
            self._ensure_worker()
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            # Important: don't forward yet.
            # It must stay ordered behind all queued speech.
            self._queue.put_nowait(frame)
            self._ensure_worker()
            return

        await self.push_frame(frame, direction)

    def _drain_queue(self):
        self._pushback = None
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _has_pending(self) -> bool:
        return self._pushback is not None or not self._queue.empty()

    def _take_nowait(self) -> _QueueItem | None:
        if self._pushback is not None:
            item = self._pushback
            self._pushback = None
            return item
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def _fits(self, batch: list[AggregatedTextFrame], frame: AggregatedTextFrame) -> bool:
        if len(batch) >= self._coalesce_max_sentences:
            return False
        extra = frame.text.strip()
        current = _join_text(batch)
        prospective = extra if not current else f"{current} {extra}"
        return len(prospective) <= self._coalesce_max_chars

    def _ensure_worker(self):
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._say_worker())

    async def _say_worker(self):
        try:
            await self._run_chunks()
        finally:
            current = asyncio.current_task()
            if self._worker_task is current:
                self._worker_task = None
                if (
                    current is not None
                    and not current.cancelling()
                    and self._has_pending()
                ):
                    self._ensure_worker()

    async def _run_chunks(self):
        """Speak queued sentences in capped chunks.

        The first chunk of a reply waits briefly so a short burst can share
        one ``say()``. Later chunks drain whatever finished while speech was
        in progress, or wait out a shorter grace period when the queue is empty.
        """
        while True:
            generation = self._generation
            grace_deadline: float | None = None
            item = self._take_nowait()
            if item is None:
                if not self._speaking or generation != self._generation:
                    return
                if self._coalesce_grace_secs <= 0:
                    return
                grace_deadline = (
                    asyncio.get_running_loop().time() + self._coalesce_grace_secs
                )
                item = await self._wait_item(self._coalesce_grace_secs, generation)
                if item is _WAIT_ABORT or generation != self._generation:
                    return
                if item is None:
                    return

            if generation != self._generation:
                return

            if isinstance(item, LLMFullResponseEndFrame):
                await self._finish_speaking(interrupted=False)
                await self.push_frame(item, FrameDirection.DOWNSTREAM)
                continue

            batch = [item]
            loop = asyncio.get_running_loop()
            if not self._speaking:
                deadline: float | None = loop.time() + self._coalesce_hold_secs
            else:
                deadline = grace_deadline

            end_frame = await self._collect(batch, generation, deadline)
            if end_frame is _WAIT_ABORT or generation != self._generation:
                return

            try:
                await self._speak_batch(batch, generation)
            except Exception:
                logger.exception("Failed to speak aggregated text")
                if self._speaking:
                    await self._finish_speaking(interrupted=True)

            if generation != self._generation:
                return

            if isinstance(end_frame, LLMFullResponseEndFrame):
                await self._finish_speaking(interrupted=False)
                await self.push_frame(end_frame, FrameDirection.DOWNSTREAM)

    async def _collect(
        self,
        batch: list[AggregatedTextFrame],
        generation: int,
        deadline: float | None,
    ) -> LLMFullResponseEndFrame | _WaitAbort | None:
        """Append queued sentences until the cap, the deadline, or response end."""
        while len(batch) < self._coalesce_max_sentences:
            item = self._take_nowait()
            if item is None:
                if deadline is None:
                    return None
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return None
                item = await self._wait_item(remaining, generation)
                if item is _WAIT_ABORT:
                    return _WAIT_ABORT
                if item is None:
                    return None

            if isinstance(item, LLMFullResponseEndFrame):
                return item
            if not self._fits(batch, item):
                self._pushback = item
                return None
            batch.append(item)
        return None

    async def _wait_item(
        self,
        timeout: float,
        generation: int,
    ) -> _QueueItem | _WaitAbort | None:
        """Wait for the next queued frame, or abort if speech is interrupted."""
        if generation != self._generation:
            return _WAIT_ABORT
        self._wake.clear()
        if generation != self._generation:
            return _WAIT_ABORT
        if timeout <= 0:
            return None

        getter: asyncio.Task[_QueueItem] = asyncio.create_task(self._queue.get())
        waiter: asyncio.Task[bool] = asyncio.create_task(self._wake.wait())
        try:
            await asyncio.wait(
                {getter, waiter},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            await _cancel_task(getter)
            await _cancel_task(waiter)
            raise

        item: _QueueItem | None = None
        if getter.done() and not getter.cancelled():
            item = getter.result()
        else:
            await _cancel_task(getter)
            if getter.done() and not getter.cancelled():
                item = getter.result()

        aborted = generation != self._generation or (
            waiter.done() and not waiter.cancelled()
        )
        await _cancel_task(waiter)
        if aborted:
            return _WAIT_ABORT
        return item

    async def _speak_batch(
        self,
        batch: list[AggregatedTextFrame],
        generation: int,
    ) -> None:
        if generation != self._generation:
            return
        if not self._speaking:
            await self._start_speaking()
        if generation != self._generation:
            if self._speaking:
                await self._finish_speaking(interrupted=True)
            return

        spoken_text = _join_text(batch)
        if self.on_phrase is not None:
            await self.on_phrase(spoken_text)
        text = await _normalize_tts(spoken_text)
        await self.nao_manager.speak_text(text)
        if generation != self._generation:
            if self._speaking:
                await self._finish_speaking(interrupted=True)
            return

        for frame in batch:
            spoken = TTSTextFrame(
                text=frame.text,
                aggregated_by=frame.aggregated_by,
                context_id=self._context_id,
                raw_text=frame.raw_text,
            )
            spoken.append_to_context = frame.append_to_context
            spoken.includes_inter_frame_spaces = frame.includes_inter_frame_spaces
            await self.push_frame(spoken)

    async def _start_speaking(self):
        self._speaking = True
        self._context_id = str(uuid.uuid4())

        await self.push_frame(
            TTSStartedFrame(
                context_id=self._context_id,
                append_to_context=True,
            )
        )

        await self.broadcast_frame(BotStartedSpeakingFrame)

        if self.on_speech_started:
            await self.on_speech_started()

    async def _finish_speaking(self, interrupted: bool):
        if not self._speaking:
            return

        context_id = self._context_id

        self._speaking = False
        self._context_id = None

        await self.push_frame(
            TTSStoppedFrame(context_id=context_id)
        )

        await self.broadcast_frame(BotStoppedSpeakingFrame)

        if self.on_speech_stopped:
            await self.on_speech_stopped(interrupted)
