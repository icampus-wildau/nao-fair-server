"""Live NAOqi session. Imported only after the microphone bridge accepts a client."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from nao_fair.catalog import BINDING_MOTIONS
from nao_fair.models import Applied
from nao_fair.secrets import SecretStore

if TYPE_CHECKING:
    from nao_fair.runtime import Runtime

logger = logging.getLogger(__name__)

_BUTTON_ATTR = {
    "front_head": "front_head",
    "middle_head": "middle_head",
    "rear_head": "back_head",
    "left_hand": "left_hand",
    "right_hand": "right_hand",
    "left_bumper": "left_bumper",
    "right_bumper": "right_bumper",
}

_TTS_LANGUAGES = {
    "de": "German",
    "en": "English",
    "fr": "French",
    "es": "Spanish",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
}


def nao_language(code: str) -> str:
    return _TTS_LANGUAGES.get(code, code)


class LiveLink:
    """One robot connection. A bridge redial stays inside the copied pipeline."""

    def __init__(self, runtime: Runtime, nao: Any, robot_name: str) -> None:
        self.runtime = runtime
        self.nao = nao
        self.robot_name = robot_name
        self.session: Any = None
        self._closing = False
        self._task: asyncio.Task | None = None
        self._health: asyncio.Task | None = None
        self._lease_task: asyncio.Task | None = None
        self._lease_seq = 0
        self._assignment_seq = 0
        self.last_autostart_ack = None
        self.autostart_synced = False
        self._speech_lock = asyncio.Lock()
        self._speech_items: list[_QueuedSpeech] = []
        self._speech_current: str | None = None
        self._speech_epoch = 0
        self._speech_worker: asyncio.Task | None = None
        self._speech_held_idle = False
        self._accompanying = False
        self._accompanying_token: object | None = None

    @classmethod
    async def open(
        cls,
        *,
        runtime: Runtime,
        address: str,
        ports: list[int],
        bridge_port: int,
        bridge_token: str,
        applied: Applied,
        secrets: SecretStore,
    ) -> LiveLink:
        try:
            from naoqi_control import QI_CONNECT_TIMEOUT_SECS, NaoController
            from nao_fair.booth.nao.eyes import attach_eyes
        except Exception as exc:
            raise ConnectionError("NAOqi control is not available") from exc

        loop = asyncio.get_running_loop()

        def _connect() -> Any:
            nao = NaoController(address, ports, loop)
            attach_eyes(nao)
            return nao

        try:
            nao = await asyncio.wait_for(
                asyncio.to_thread(_connect),
                timeout=QI_CONNECT_TIMEOUT_SECS,
            )
        except Exception as exc:
            raise ConnectionError(f"NAOqi connection failed: {exc}") from exc

        link = cls(runtime, nao, nao.robot_name or "NAO")
        try:
            await link.begin_autostart(applied)
            await link._prepare(applied)
            link._bind_buttons()
            link._start_pipeline(applied, secrets, bridge_port, bridge_token, address)
            link._health = asyncio.create_task(link._watch_naoqi(), name="naoqi-health")
        except Exception as exc:
            await link.close()
            if isinstance(exc, ConnectionError):
                raise
            raise ConnectionError(str(exc)) from exc
        return link

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        async with self._speech_lock:
            self._discard_queued_locked()
        health = self._health
        self._health = None
        if health is not None:
            health.cancel()
            await _quiet(health)
        await self._stop_pipeline()
        nao = self.nao
        if nao is None:
            return
        try:
            await nao.tts.stop_async()
        except Exception:
            logger.exception("Could not stop speech")
        try:
            await nao.motion.cancel()
        except Exception:
            logger.exception("Could not cancel gesture")
        try:
            await self.release_autostart()
        except Exception:
            logger.exception("Could not release autostart")
        try:
            await nao.close()
        except Exception:
            logger.exception("Could not close NAOqi")

    async def begin_autostart(self, applied: Applied) -> None:
        """Store the assignment, take the lease, then quiet the robot."""
        try:
            self.autostart_synced = await self.sync_autostart(applied)
        except Exception:
            logger.exception("Could not push autostart")
            self.autostart_synced = False
        await self.hold_autostart()
        if self.nao is None:
            return
        try:
            await self.nao.tts.stop_async()
        except Exception:
            logger.exception("Could not stop speech before the session")
        try:
            await self.nao.motion.cancel()
        except Exception:
            logger.exception("Could not cancel a gesture before the session")

    async def sync_autostart(self, applied: Applied) -> bool:
        from nao_fair.robot.autostart import assignment_document, push_assignment

        self._assignment_seq += 1
        return await push_assignment(self.nao, assignment_document(applied), self._assignment_seq)

    async def hold_autostart(self) -> None:
        if self._lease_task is not None:
            return
        self._lease_seq += 1
        from nao_fair.robot.autostart import wait_lease_ack, write_lease

        await write_lease(self.nao, "hold", self._lease_seq)
        if self.autostart_synced:
            if not await wait_lease_ack(self.nao, self._lease_seq, timeout=10.0):
                raise RuntimeError("Der Autostart-Prozess hat die Steuerung nicht freigegeben.")
            self.last_autostart_ack = time.monotonic()
        self._lease_task = asyncio.create_task(self._renew_autostart(), name="autostart-lease")

    async def release_autostart(self) -> None:
        task = self._lease_task
        self._lease_task = None
        if task is not None:
            task.cancel()
            await _quiet(task)
        if self.nao is None:
            return
        from nao_fair.robot.autostart import write_lease

        self._lease_seq += 1
        await write_lease(self.nao, "release", self._lease_seq)

    async def _renew_autostart(self) -> None:
        from nao_fair.robot.autostart import LEASE_PERIOD_SECS, wait_lease_ack, write_lease

        try:
            while not self._closing:
                await asyncio.sleep(LEASE_PERIOD_SECS)
                if self._closing or self.nao is None:
                    return
                self._lease_seq += 1
                try:
                    await write_lease(self.nao, "hold", self._lease_seq)
                    if await wait_lease_ack(self.nao, self._lease_seq):
                        self.last_autostart_ack = time.monotonic()
                        if not self.autostart_synced and self.runtime is not None:
                            await self.runtime._push_autostart(self, notice=False)
                            self.autostart_synced = bool(self.runtime._autostart_synced)
                except Exception:
                    logger.exception("Could not renew the autostart lease; retrying")
        except asyncio.CancelledError:
            return

    async def on_applied(self, applied: Applied, screen: str) -> None:
        del screen
        from nao_fair.booth.processors.nao_speech_processor import set_tts_word_replacements

        set_tts_word_replacements(self.runtime.replacement_pairs(applied))
        await self._prepare_audio(applied)
        await self._stop_pipeline()
        self._start_pipeline(
            applied,
            self.runtime.secrets,
            self.runtime.bridge_port,
            self.runtime.bridge_token,
            self.nao.ip,
        )

    async def on_audio_changed(self, applied: Applied) -> None:
        def update() -> None:
            self.nao.audio.service.setOutputVolume(int(applied.volume))
            self.nao.tts.set_speed(int(applied.speech_speed))

        await self.nao.call_qi(update)

    async def discard_context(self) -> None:
        if self.session is not None:
            await self.session.discard_context()

    async def start_conversation(self) -> None:
        await self.session.start_conversation()

    async def send_message(self, text: str) -> None:
        await self.session.send_message(text)

    async def stop_conversation(self, *, clear: bool) -> None:
        from nao_fair.booth.nao.nao_nlp_manager import NAOState

        await self.session._nao_nlp_manager.transition_to_state(
            NAOState.IDLE, reset_context=clear
        )

    async def read_dialog_position(self) -> dict | None:
        from nao_fair.robot.autostart import POSITION

        def read() -> dict | None:
            try:
                raw = self.nao.session.service("ALMemory").getData(POSITION)
            except Exception:
                return None
            if not isinstance(raw, str) or not raw.strip():
                return None
            try:
                data = json.loads(raw)
            except ValueError:
                return None
            if not isinstance(data, dict) or not data.get("line_id"):
                return None
            return data

        try:
            return await self.nao.call_qi(read)
        except Exception:
            logger.exception("Could not read the dialog position")
            return None

    async def write_dialog_position(self, position: dict | None) -> None:
        from nao_fair.robot.autostart import POSITION, POSITION_SEQ

        payload = "" if not position else json.dumps(position)
        seq = 0 if not position else int(position.get("seq") or 0)

        def write() -> None:
            memory = self.nao.session.service("ALMemory")
            memory.insertData(POSITION, payload)
            memory.raiseEvent(POSITION_SEQ, seq)

        await self.nao.call_qi(write)

    async def speak_dialog_line(self, text: str, delivery: str, motion: str | None, on_started) -> None:
        from naoqi_control import say_timeout_secs
        from naoqi_control.qi import QiCall

        normalized = await self._normalized(text)
        token = self._begin_accompanying(delivery, motion)
        if delivery == "motion" and motion:
            await self.stop_motion()
            self._start_line_motion(motion)
        async with self._speech_lock:
            self._discard_queued_locked()
            await self._stop_tts()
            future = await self._post_say_locked(normalized, delivery)
        if future is None:
            self._end_accompanying(token)
            raise RuntimeError("could not start the line")
        await on_started()
        timeout = say_timeout_secs(normalized)
        try:
            await asyncio.wait_for(QiCall.wait(future, self.nao.call_qi, lambda: self._closing), timeout)
        except TimeoutError:
            await self.stop_speech()
        finally:
            self._end_accompanying(token)

    async def hush_for_reset(self) -> None:
        """Stop a dialog line, a fixed phrase, and a conversation reply.

        A Bewegung gesture is left running. The conversation stays up.
        """
        await self._hush_reply()
        if self._accompanying:
            self._accompanying = False
            self._accompanying_token = None
            # stopMove ends the keyframes. Cancelling the gesture would skip
            # the return to the start pose and leave the chain stiff.
            await self.nao.motion.stop()
        await self.stop_speech()

    async def speak_announcement(self, text: str) -> None:
        """Say reset words immediately, then let fixed phrases wait behind them."""
        key = f"announcement:{time.monotonic_ns()}"
        async with self._speech_lock:
            if self._closing:
                return
            item = _QueuedSpeech(key, text, "plain", None)
            self._speech_items.append(item)
            self._ensure_speech_worker_locked()
        await item.done.wait()
        if item.error is not None:
            raise item.error

    async def _hush_reply(self) -> None:
        session = self.session
        if session is None:
            return
        manager = session._nao_nlp_manager
        state = getattr(getattr(manager, "state", None), "value", None)
        if state not in ("speaking", "thinking"):
            return
        from nao_fair.booth.nao.nao_nlp_manager import NAOState

        manager._resume_listening = True
        await manager.broadcast_interruption()
        if getattr(getattr(manager, "state", None), "value", None) in ("speaking", "thinking"):
            await manager.transition_to_state(NAOState.LISTENING)

    def _begin_accompanying(self, delivery: str, motion: str | None) -> object | None:
        if delivery != "motion" or not motion:
            return None
        token = object()
        self._accompanying = True
        self._accompanying_token = token
        return token

    def _end_accompanying(self, token: object | None) -> None:
        if token is not None and self._accompanying_token is token:
            self._accompanying = False
            self._accompanying_token = None

    async def _normalized(self, text: str) -> str:
        from nao_fair.booth.processors.nao_speech_processor import _normalize_tts

        return await _normalize_tts(text)

    async def _post_say_locked(self, normalized: str, delivery: str):
        from naoqi_control.qi import QiCall

        if delivery == "animated":
            return await self.nao.call_qi(
                lambda: QiCall.start(self.nao.session.service("ALAnimatedSpeech"), "say", normalized)
            )
        return await self.nao.call_qi(self.nao.tts._post_say, normalized)

    def _start_line_motion(self, motion: str) -> None:
        body = self.nao.motion
        if motion == "wave_left":
            body.wave(["left"])
        elif motion == "wave_right":
            body.wave(["right"])
        elif motion == "nod":
            body.nod()
        elif motion == "shake":
            body.shake()

    async def speak(
        self,
        text: str,
        action_key: str | None = None,
        *,
        delivery: str = "plain",
        motion: str | None = None,
    ) -> None:
        """Speak ``text`` after earlier text-speak actions.

        A repeated ``action_key`` is dropped while that action is playing or
        already waiting. Different keys stay in order. Omitting the key treats
        the phrase itself as the action. Delivery waits its turn with the words.
        """
        key = action_key or f"phrase:{text}"
        async with self._speech_lock:
            if self._closing:
                return
            if key == self._speech_current or any(item.key == key for item in self._speech_items):
                return
            item = _QueuedSpeech(key, text, delivery, motion)
            self._speech_items.append(item)
            self._ensure_speech_worker_locked()
        await item.done.wait()
        if item.error is not None:
            raise item.error

    def _ensure_speech_worker_locked(self) -> None:
        worker = self._speech_worker
        if worker is None or worker.done():
            self._speech_worker = asyncio.create_task(self._drain_speech(), name="speak-queue")

    async def _drain_speech(self) -> None:
        while not self._closing:
            async with self._speech_lock:
                if self._closing or not self._speech_items:
                    self._speech_current = None
                    self._speech_worker = None
                    return
                item = self._speech_items.pop(0)
                self._speech_current = item.key
                epoch = self._speech_epoch
            try:
                await self._speak_queued(item.text, epoch, item.delivery, item.motion)
            except Exception as exc:
                item.error = exc
            finally:
                async with self._speech_lock:
                    if self._speech_current == item.key:
                        self._speech_current = None
                item.done.set()

    async def _speak_queued(
        self,
        text: str,
        epoch: int,
        delivery: str = "plain",
        motion: str | None = None,
    ) -> None:
        from naoqi_control import say_timeout_secs
        from naoqi_control.qi import QiCall

        normalized = await self._normalized(text)
        async with self._speech_lock:
            if epoch != self._speech_epoch or self._closing:
                return
        token = self._begin_accompanying(delivery, motion)
        if delivery == "motion" and motion:
            self._start_line_motion(motion)
        async with self._speech_lock:
            if epoch != self._speech_epoch or self._closing:
                self._end_accompanying(token)
                return
            future = await self._post_say_locked(normalized, delivery)
        if future is None:
            self._end_accompanying(token)
            return
        with self.runtime.lock:
            if self.runtime.conversation_state == "idle":
                self._speech_held_idle = True
        if self._speech_held_idle:
            self.runtime.note_state("speaking")
        try:
            try:
                await asyncio.wait_for(
                    QiCall.wait(future, self.nao.call_qi, lambda: self._closing),
                    say_timeout_secs(normalized),
                )
            except TimeoutError:
                await self.stop_speech()
        finally:
            self._end_accompanying(token)
            self.runtime.note_nao_line(text)
            async with self._speech_lock:
                more = bool(self._speech_items) and epoch == self._speech_epoch
            if self._speech_held_idle and not more:
                self._speech_held_idle = False
                self.runtime.note_state("idle")

    def _discard_queued_locked(self) -> None:
        self._speech_epoch += 1
        pending = self._speech_items
        self._speech_items = []
        self._speech_current = None
        for item in pending:
            item.done.set()

    async def stop_speech(self) -> None:
        async with self._speech_lock:
            self._discard_queued_locked()
            await self._stop_tts()

    async def _stop_tts(self) -> None:
        await self.nao.tts.stop_async()

    async def execute_motion(self, motion: str) -> None:
        if motion not in BINDING_MOTIONS:
            raise ValueError("unsupported motion")
        body = self.nao.motion
        if motion == "wave_left":
            await body.wave_and_wait(["left"])
        elif motion == "wave_right":
            await body.wave_and_wait(["right"])
        elif motion == "nod":
            await body.nod_and_wait()
        elif motion == "shake":
            await body.shake_and_wait()
        elif motion == "sit":
            await body.go_to_posture("Sit")
        elif motion == "stand":
            await body.go_to_posture("Stand")

    async def stop_motion(self) -> None:
        await self.nao.motion.stop()
        await self.nao.motion.cancel()

    async def _prepare(self, applied: Applied) -> None:
        await self.nao.prepare(
            volume=int(applied.volume),
            speech_speed=int(applied.speech_speed),
            posture=applied.posture,
        )
        await self._prepare_audio(applied)
        from nao_fair.booth.processors.nao_speech_processor import set_tts_word_replacements

        set_tts_word_replacements(self.runtime.replacement_pairs(applied))

    async def _prepare_audio(self, applied: Applied) -> None:
        volume = int(applied.volume)
        speed = int(applied.speech_speed)
        language = nao_language(applied.tts_language)

        def _apply() -> None:
            self.nao.audio.service.setOutputVolume(volume)
            self.nao.tts.set_speed(speed)
            self.nao.tts.set_language(language)

        await self.nao.call_qi(_apply)

    def _bind_buttons(self) -> None:
        for sensor_id, attr in _BUTTON_ATTR.items():
            button = getattr(self.nao.buttons, attr)
            button.on_pressed(self._on_down(sensor_id))
            button.on_released(self._on_up(sensor_id))

    def _on_down(self, sensor_id: str):
        async def callback() -> None:
            if self._closing:
                return
            self.runtime.note_sensor_down(sensor_id)

        return callback

    def _on_up(self, sensor_id: str):
        async def callback() -> None:
            if self._closing:
                return
            self.runtime.note_sensor_up(sensor_id)

        return callback

    def _start_pipeline(
        self,
        applied: Applied,
        secrets: SecretStore,
        bridge_port: int,
        bridge_token: str,
        address: str,
    ) -> None:
        from nao_fair.booth.config import AppConfig
        from nao_fair.booth.pipecat_session import PipecatSession
        from nao_fair.compose import enabled_vocabulary

        sections = self.runtime.sections_for(applied, self.robot_name)
        config = AppConfig(
            nao_ip=address,
            nao_ports=list(self.runtime.nao_ports),
            audio_bridge_port=bridge_port,
            nao_bridge_token=bridge_token,
            output_volume=int(applied.volume),
            tts_speed=int(applied.speech_speed),
            posture=applied.posture,
            llm_model=applied.llm_model,
            listening_timeout_secs=float(applied.listening_timeout_secs),
            context_reset_timeout_secs=float(applied.context_reset_timeout_secs),
            thinking_timeout_secs=float(applied.thinking_timeout_secs),
            unmute_delay_secs=float(applied.unmute_delay_secs),
            gladia_region=applied.gladia_region,
            gladia_endpointing=float(applied.gladia_endpointing),
            gladia_max_utterance_secs=int(applied.gladia_max_utterance_secs),
            gladia_audio_enhancer=bool(applied.gladia_audio_enhancer),
            gladia_language=applied.gladia_language,
        )
        self.session = PipecatSession(
            self.nao,
            config=config,
            gladia_api_key=secrets.value("gladia"),
            openai_api_key=secrets.value("openai"),
            vocabulary=enabled_vocabulary(applied),
            sections_provider=lambda: list(sections),
            on_visitor_line=self._visitor_line,
            on_nao_line=self._nao_line,
            on_state=self.runtime.note_state,
            temperature=applied.temperature,
            max_tokens=applied.max_tokens,
            microphone_enabled=applied.microphone_enabled,
            stt_enabled=applied.stt_enabled,
            llm_enabled=applied.llm_enabled,
            announce=False,
            bind_buttons=False,
            external_context_reset=True,
        )
        self._task = asyncio.create_task(self.session.run(), name="fair-pipeline")
        self._task.add_done_callback(self._pipeline_done)

    async def _visitor_line(self, text: str) -> None:
        self.runtime.note_visitor_line(text)

    async def _nao_line(self, text: str) -> None:
        self.runtime.note_nao_line(text)

    def _pipeline_done(self, task: asyncio.Task) -> None:
        if self._closing or task is not self._task or task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is None:
            self.runtime.fail_disconnect("conversation pipeline ended")
        else:
            self.runtime.fail_disconnect(f"pipeline failed: {exc}")

    async def _stop_pipeline(self) -> None:
        session = self.session
        task = self._task
        self._task = None
        if session is not None:
            try:
                await session.stop("disconnect")
            except Exception:
                logger.exception("Could not stop pipeline")
        if task is not None and not task.done():
            task.cancel()
            await _quiet(task)

    async def _watch_naoqi(self) -> None:
        # One timed-out probe is the worker being busy (a posture move), not a
        # dead link. Several in a row means robotName() is not coming back.
        misses = 0
        try:
            while not self._closing:
                await asyncio.sleep(2)
                if self._closing:
                    return
                try:
                    await self.nao.call_qi(
                        lambda: self.nao.session.service("ALSystem").robotName(),
                        timeout=3,
                    )
                except TimeoutError:
                    misses += 1
                    if misses < 3 or self._closing:
                        continue
                    self.runtime.fail_disconnect("NAOqi link dropped: health check timed out")
                    return
                except Exception as exc:
                    if not self._closing:
                        self.runtime.fail_disconnect(f"NAOqi link dropped: {exc}")
                    return
                else:
                    misses = 0
        except asyncio.CancelledError:
            return


class _QueuedSpeech:
    def __init__(self, key: str, text: str, delivery: str = "plain", motion: str | None = None) -> None:
        self.key = key
        self.text = text
        self.delivery = delivery
        self.motion = motion
        self.done = asyncio.Event()
        self.error: BaseException | None = None


async def _quiet(task: asyncio.Task) -> None:
    try:
        await task
    except (asyncio.CancelledError, Exception):
        return
