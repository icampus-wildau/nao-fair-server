"""Gladia live STT that stays disconnected while the robot is muted.

Gladia bills live sessions by the duration of audio uploaded. Silence has the
same duration as real microphone audio, so a muted robot must not send PCM.
The session is closed on mute and a new ``/v2/live`` session is opened on
unmute. If that init fails, the previous session URL is tried once.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from enum import Enum

from pipecat.frames.frames import StartFrame
from pipecat.services.gladia.stt import GladiaSTTService
from pipecat.services.stt_service import STTService

from nao_fair.booth.session_log import session_log

logger = logging.getLogger(__name__)


class ResumeResult(Enum):
    """Outcome of asking Gladia to accept microphone audio again."""

    OPEN = "open"
    DEFERRED = "deferred"
    FAILED = "failed"


class PausableGladiaSTTService(GladiaSTTService):
    """Gladia STT that does not upload audio while billing is paused.

    ``start()`` only records the pipeline sample rate. The websocket is opened
    by ``resume_billing()`` and closed by ``pause_billing()``.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._billing_lock = asyncio.Lock()
        self._billing_paused = True
        self._muted = True
        self._epoch = 0
        self._resume_when_ready = False
        self._previous_session_url: str | None = None
        self._forwarded_pcm_bytes = 0
        self._suppress_error_frames = False
        self._on_live_session_lost: Callable[[], Awaitable[None] | None] | None = None

    def set_live_session_lost_handler(
        self,
        handler: Callable[[], Awaitable[None] | None],
    ) -> None:
        """Called when a listening socket cannot be replaced."""
        self._on_live_session_lost = handler

    async def push_error(
        self,
        error_msg: str,
        exception: Exception | None = None,
        fatal: bool = False,
    ) -> None:
        if self._suppress_error_frames:
            logger.error("%s", error_msg)
            return
        await super().push_error(error_msg=error_msg, exception=exception, fatal=fatal)

    @property
    def session_is_open(self) -> bool:
        """True when a live session is connected and accepting audio."""
        return self._connection_active and not self._billing_paused and not self._muted

    @property
    def forwarded_pcm_seconds(self) -> float:
        """Seconds of PCM sent to the current Gladia session.

        This is the local stand-in for Gladia's ``billing_time``: muted audio
        never reaches ``_send_audio``, so the value stays flat while paused.
        """
        bytes_per_second = (
            self.sample_rate * self._channels * (self._bit_depth // 8)
        )
        if bytes_per_second <= 0:
            return 0.0
        return self._forwarded_pcm_bytes / bytes_per_second

    def mark_muted(self) -> None:
        """Drop audio already inside the STT service before the socket closes."""
        self._muted = True
        self._epoch += 1

    async def start(self, frame: StartFrame):
        """Record the sample rate without opening a Gladia session."""
        await STTService.start(self, frame)
        self._muted = True

    async def pause_billing(self) -> None:
        """Finalize the live session so further silence is not billed."""
        async with self._billing_lock:
            self._billing_paused = True
            self._muted = True
            self._resume_when_ready = False
            await self._close_unlocked()

    async def resume_billing(self) -> ResumeResult:
        """Open a new live session. Retry the previous URL if init fails."""
        epoch = self._epoch
        try:
            async with self._billing_lock:
                return await self._resume_unlocked(epoch)
        except asyncio.CancelledError:
            await self._rollback_resume()
            raise
        except Exception:
            logger.exception("Failed to resume Gladia billing session")
            await self._rollback_resume()
            return ResumeResult.FAILED

    async def _rollback_resume(self) -> None:
        async with self._billing_lock:
            if self._connection_active or self._session_url:
                await self._close_unlocked()
            await self._abort_resume()

    async def _resume_unlocked(self, epoch: int) -> ResumeResult:
        if self._epoch != epoch:
            await self._abort_resume()
            return ResumeResult.FAILED

        if self._sample_rate <= 0:
            self._resume_when_ready = True
            self._billing_paused = False
            self._muted = True
            return ResumeResult.DEFERRED

        if not self._billing_paused and self._connection_active:
            self._muted = False
            session_log.set_gladia("open")
            return ResumeResult.OPEN

        self._billing_paused = False
        self._muted = True
        opened = await self._open_fresh_unlocked()
        if self._epoch != epoch or not opened:
            if self._connection_active:
                await self._close_unlocked()
            await self._abort_resume()
            return ResumeResult.FAILED

        self._muted = False
        self._billing_paused = False
        logger.info(
            "Gladia session %s is accepting audio",
            self._session_id,
        )
        session_log.set_gladia("open")
        return ResumeResult.OPEN

    async def _abort_resume(self) -> None:
        self._muted = True
        self._billing_paused = True
        self._resume_when_ready = False

    async def _open_fresh_unlocked(self) -> bool:
        previous_url = self._previous_session_url
        await self._disconnect()
        self._connection_active = False
        self._clear_audio_buffer()
        self._session_url = None
        self._session_id = None
        self._forwarded_pcm_bytes = 0

        if await self._connect_capturing_errors():
            logger.info("Opened new Gladia session %s", self._session_id)
            return True

        if not previous_url:
            logger.error(
                "Gladia session did not open. Microphone audio stays muted."
            )
            session_log.set_gladia("closed")
            return False

        logger.warning(
            "New Gladia session failed; retrying the previous session URL"
        )
        await self._disconnect()
        self._connection_active = False
        self._session_url = previous_url
        if await self._connect_capturing_errors():
            logger.info("Reconnected to the previous Gladia session URL")
            return True

        logger.error(
            "Gladia session did not open on the new or previous URL. "
            "Microphone audio stays muted."
        )
        session_log.set_gladia("closed")
        return False

    async def _reconnect_websocket(self, attempt_number: int) -> bool:
        """Replace a dead live socket with a new Gladia session.

        The parent reconnect dials the same session URL again and reports an
        ``ErrorFrame``. Unmute already opens a fresh session; a drop while
        listening does that too, and leaves the receive loop running.
        """
        if self._muted or self._billing_paused:
            return False
        logger.warning(
            "Gladia live socket dropped; opening a new session (attempt %s)",
            attempt_number,
        )
        opened = await self._reopen_live_session()
        if not opened:
            return False
        if getattr(self, "_task_manager", None) is not None:
            try:
                await self._cancel_keepalive_task()
                self._create_keepalive_task()
            except Exception:
                logger.warning(
                    "Could not restart Gladia keepalive", exc_info=True
                )
        if self._websocket is None:
            return bool(self._connection_active)
        return await self._verify_connection()

    async def _maybe_try_reconnect(
        self,
        error_message: str,
        report_error,
        error: Exception | None = None,
    ) -> bool:
        """Recover a listening drop, including the parent's quick-failure stop."""
        if self._muted or self._billing_paused or self._disconnecting:
            return False
        continued = await super()._maybe_try_reconnect(
            error_message,
            report_error,
            error,
        )
        if continued or self._disconnecting or self._muted:
            return continued
        # _try_reconnect already asks the robot to stop listening. The parent
        # returns first, without that call, when the socket dies immediately
        # several times or reconnect is disabled.
        tracker = self._quick_failure_tracker
        gave_up = tracker.count >= tracker.max_consecutive_failures
        if gave_up or not self._reconnect_on_error:
            await self._notify_live_session_lost()
        return False

    async def _try_reconnect(
        self,
        max_retries: int = 3,
        report_error=None,
    ) -> bool:
        """Reopen a live session without pushing a connection ErrorFrame."""
        del report_error
        if self._muted or self._billing_paused:
            return False
        if self._reconnect_in_progress:
            return False
        self._reconnect_in_progress = True
        try:
            for attempt in range(1, max_retries + 1):
                try:
                    if await self._reconnect_websocket(attempt):
                        self._last_connect_time = time.monotonic()
                        return True
                except Exception:
                    logger.exception("Gladia live session reopen failed")
                await asyncio.sleep(min(float(attempt), 2.0))
            await self._notify_live_session_lost()
            return False
        finally:
            self._reconnect_in_progress = False

    async def _do_reconnect(self) -> None:
        if self._muted or self._billing_paused:
            return
        self._suppress_error_frames = True
        try:
            opened = await self._open_fresh_unlocked()
        finally:
            self._suppress_error_frames = False
        if not opened:
            await self._notify_live_session_lost()
            raise ConnectionError("Gladia live session did not reopen")

    async def _reopen_live_session(self) -> bool:
        """Same new-session order as unmute, without cancelling the receiver."""
        previous_url = self._session_url or self._previous_session_url
        self._suppress_error_frames = True
        try:
            self._connection_active = False
            try:
                await self._disconnect_websocket()
            except Exception:
                logger.warning(
                    "Gladia disconnect during live reopen failed",
                    exc_info=True,
                )
            self._clear_audio_buffer()
            self._session_url = None
            self._session_id = None
            self._forwarded_pcm_bytes = 0
            if await self._connect_capturing_errors():
                logger.info(
                    "Opened a new Gladia session %s after the live socket dropped",
                    self._session_id,
                )
                return True
            if not previous_url:
                logger.error(
                    "Gladia session did not reopen. Listening cannot continue."
                )
                return False
            logger.warning(
                "New Gladia session failed; retrying the previous session URL"
            )
            try:
                await self._disconnect_websocket()
            except Exception:
                logger.warning(
                    "Gladia disconnect before URL retry failed",
                    exc_info=True,
                )
            self._connection_active = False
            self._session_url = previous_url
            if await self._connect_capturing_errors():
                logger.info("Reconnected Gladia using the previous session URL")
                return True
            logger.error(
                "Gladia session did not reopen on the new or previous URL."
            )
            return False
        finally:
            self._suppress_error_frames = False

    async def _notify_live_session_lost(self) -> None:
        callback = self._on_live_session_lost
        if callback is None:
            return
        result = callback()
        if asyncio.iscoroutine(result):
            await result

    async def _connect_capturing_errors(self) -> bool:
        try:
            await self._connect()
        except Exception:
            logger.exception("Gladia connect failed")
            self._connection_active = False
            session_log.set_gladia("closed")
            return False
        return bool(self._connection_active)

    async def _close_unlocked(self) -> None:
        session_id = self._session_id
        forwarded_seconds = self.forwarded_pcm_seconds
        if self._session_url:
            self._previous_session_url = self._session_url

        try:
            await self._send_stop_recording()
        except Exception:
            logger.warning("Gladia stop_recording failed", exc_info=True)

        self._session_url = None
        self._session_id = None
        self._clear_audio_buffer()
        try:
            await self._disconnect()
        except Exception:
            logger.warning("Gladia disconnect failed", exc_info=True)
        self._connection_active = False
        session_log.set_gladia("closed")

        if session_id is not None or forwarded_seconds > 0:
            logger.info(
                "Closed Gladia session %s after forwarding %.2fs of PCM. "
                "Muted audio is not uploaded, so billing_time should stop "
                "increasing until listening resumes.",
                session_id,
                forwarded_seconds,
            )
        self._forwarded_pcm_bytes = 0

    def _clear_audio_buffer(self) -> None:
        self._audio_buffer = bytearray()
        self._bytes_sent = 0

    async def _send_audio(self, audio: bytes):
        """Upload PCM only while a resumed session is accepting audio."""
        if self._billing_paused or self._muted:
            return
        if audio:
            self._forwarded_pcm_bytes += len(audio)
        await super()._send_audio(audio)
