"""Live NAOqi session. Imported only after the microphone bridge accepts a client."""

from __future__ import annotations

import asyncio
import logging
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
            await nao.close()
        except Exception:
            logger.exception("Could not close NAOqi")

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

    async def speak(self, text: str) -> None:
        from naoqi_control import say_timeout_secs, speak_text

        from nao_fair.booth.processors.nao_speech_processor import _normalize_tts

        normalized = await _normalize_tts(text)
        with self.runtime.lock:
            restore_idle = self.runtime.conversation_state == "idle"
        if restore_idle:
            self.runtime.note_state("speaking")
        try:
            await speak_text(self.nao, normalized, timeout=say_timeout_secs(normalized))
        finally:
            self.runtime.note_nao_line(text)
            if restore_idle:
                self.runtime.note_state("idle")

    async def stop_speech(self) -> None:
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
            button.on_pressed(self._on_pressed(sensor_id))

    def _on_pressed(self, sensor_id: str):
        async def callback() -> None:
            if self._closing:
                return
            self.runtime.note_sensor(sensor_id)

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
                except Exception as exc:
                    if not self._closing:
                        self.runtime.fail_disconnect(f"NAOqi link dropped: {exc}")
                    return
        except asyncio.CancelledError:
            return


async def _quiet(task: asyncio.Task) -> None:
    try:
        await task
    except (asyncio.CancelledError, Exception):
        return
