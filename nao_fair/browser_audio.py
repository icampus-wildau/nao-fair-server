"""A browser-owned microphone test of the shared conversation pipeline.

Wire format: little-endian PCM16, mono, 16 kHz in binary messages. JSON carries
state, transcript, and speech requests; speech completion is acknowledged so
the microphone stays muted until the browser has finished speaking.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from anyio import CancelScope

from nao_fair.runtime import ApiError, Runtime

logger = logging.getLogger(__name__)
SAMPLE_RATE = 16000
MAX_AUDIO_BYTES = SAMPLE_RATE * 2  # At most one second per message.


class BrowserSpeech:
    def __init__(self, test: BrowserTest) -> None:
        self.test = test
        self.pending: dict[str, asyncio.Future] = {}

    async def speak(self, text: str) -> None:
        speech_id = str(uuid4())
        finished = asyncio.get_running_loop().create_future()
        self.pending[speech_id] = finished
        applied = self.test.runtime.applied
        try:
            await self.test.send({
                "type": "speech", "id": speech_id, "text": text,
                "language": applied.tts_language, "volume": applied.volume / 100,
                "rate": min(4, max(0.5, applied.speech_speed / 100)),
            })
            await asyncio.wait_for(finished, timeout=max(30, len(text) / 5 + 10))
        except TimeoutError:
            await self.stop()
            await self.test.send({"type": "warning", "message": "Die Browser-Sprachausgabe hat nicht geantwortet. Die Antwort steht im Transkript."})
        finally:
            self.pending.pop(speech_id, None)

    async def stop(self) -> None:
        for finished in self.pending.values():
            if not finished.done():
                finished.set_result(None)
        self.pending.clear()
        await self.test.send({"type": "speech.cancel"})

    async def acknowledge(self, message: dict) -> None:
        speech_id = message.get("id")
        if not isinstance(speech_id, str):
            raise ApiError(422, "Ungültige Sprachausgabe-Bestätigung.")
        finished = self.pending.get(speech_id)
        if finished is None or finished.done():
            return
        finished.set_result(None)
        if message.get("error"):
            await self.test.send({"type": "warning", "message": "Browser-Sprachausgabe nicht verfügbar. Die Antwort steht im Transkript."})


class BrowserTest:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.applied = runtime.applied.model_copy(deep=True)
        self.outgoing: asyncio.Queue = asyncio.Queue(maxsize=128)
        self.speech = BrowserSpeech(self)
        self.session = None
        self.task: asyncio.Task | None = None
        self.closing = False
        self.is_ready = False
        self._state_tasks: set[asyncio.Task] = set()
        self._close_lock = asyncio.Lock()

    async def send(self, message: dict) -> None:
        if self.closing and self.outgoing.full():
            self.outgoing.get_nowait()
            self.outgoing.task_done()
        await self.outgoing.put(message)

    def _state(self, state: str) -> None:
        task = asyncio.create_task(self.send({"type": "state", "state": state}))
        self._state_tasks.add(task)
        task.add_done_callback(self._state_tasks.discard)

    async def _line(self, speaker: str, text: str) -> None:
        await self.send({"type": "transcript", "id": str(uuid4()), "speaker": speaker,
                         "text": text, "timestamp": datetime.now(timezone.utc).isoformat()})

    def build_session(self):
        # Robot packages are deliberately not imported by this path.
        from nao_fair.booth.config import AppConfig
        from nao_fair.booth.pipecat_session import PipecatSession
        from nao_fair.booth.processors.nao_speech_processor import set_tts_word_replacements
        from nao_fair.compose import enabled_vocabulary

        applied = self.applied
        config = AppConfig(
            llm_model=applied.llm_model,
            listening_timeout_secs=applied.listening_timeout_secs,
            context_reset_timeout_secs=applied.context_reset_timeout_secs,
            thinking_timeout_secs=applied.thinking_timeout_secs,
            unmute_delay_secs=applied.unmute_delay_secs,
            gladia_region=applied.gladia_region, gladia_language=applied.gladia_language,
            gladia_endpointing=applied.gladia_endpointing,
            gladia_max_utterance_secs=applied.gladia_max_utterance_secs,
            gladia_audio_enhancer=applied.gladia_audio_enhancer,
        )
        sections = self.runtime.sections_for(applied, "NAO")
        set_tts_word_replacements(self.runtime.replacement_pairs(applied))
        return PipecatSession(
            None, config=config, browser_speech=self.speech,
            gladia_api_key=self.runtime.secrets.value("gladia"),
            openai_api_key=self.runtime.secrets.value("openai"),
            vocabulary=enabled_vocabulary(applied), sections_provider=lambda: list(sections),
            on_visitor_line=lambda text: self._line("visitor", text),
            on_nao_line=lambda text: self._line("nao", text), on_state=self._state,
            temperature=applied.temperature, max_tokens=applied.max_tokens,
            microphone_enabled=applied.microphone_enabled, stt_enabled=applied.stt_enabled,
            llm_enabled=applied.llm_enabled, announce=False, bind_buttons=False,
        )

    async def run(self) -> None:
        try:
            self.session = self.build_session()
        except ImportError as exc:
            raise ApiError(503, 'Pipeline-Pakete fehlen. Im Server-Verzeichnis pip install -e ".[browser]" ausführen und den Server neu starten.') from exc
        self.task = asyncio.create_task(self.session.run(), name="browser-pipeline")
        ready = asyncio.create_task(self.session.ready.wait())
        try:
            done, _ = await asyncio.wait((self.task, ready), timeout=60, return_when=asyncio.FIRST_COMPLETED)
            if self.task in done:
                await self.task
                raise RuntimeError("pipeline ended during startup")
            if ready not in done:
                raise ApiError(504, "Die Pipeline konnte nicht rechtzeitig starten.")
            await self.listen()
            self.is_ready = True
            await self.send({"type": "ready", "sample_rate": SAMPLE_RATE})
            await asyncio.shield(self.task)
        finally:
            ready.cancel()
            await asyncio.gather(ready, return_exceptions=True)

    async def listen(self) -> None:
        await self.session.start_conversation()

    async def receive(self, websocket: WebSocket) -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            audio = message.get("bytes")
            if audio is not None:
                if not audio or len(audio) > MAX_AUDIO_BYTES or len(audio) % 2:
                    raise ApiError(422, "Ungültiges Mikrofon-Audio. Erwartet wird PCM16 mit 16 kHz und einem Kanal.")
                if self.is_ready:
                    await self.session.push_audio(audio)
            else:
                try:
                    if len(message.get("text") or "") > 2048:
                        raise ValueError()
                    control = json.loads(message.get("text") or "")
                    if not isinstance(control, dict):
                        raise ValueError()
                except (ValueError, TypeError) as exc:
                    raise ApiError(422, "Ungültige Mikrofon-Nachricht.") from exc
                if control.get("type") == "speech.finished":
                    await self.speech.acknowledge(control)
                elif control.get("type") == "listen" and self.is_ready:
                    await self.listen()
                else:
                    raise ApiError(422, "Unbekannte Mikrofon-Nachricht.")

    async def close(self, reason: str = "Test beendet.") -> None:
        async with self._close_lock:
            if self.closing:
                return
            self.closing = True
            self.is_ready = False
            await self.send({"type": "closed", "message": reason})
            await self.speech.stop()
            if self.session is not None:
                await self.session.stop(reason)
            if self.task is not None:
                done, _ = await asyncio.wait((self.task,), timeout=5)
                if not done:
                    self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            pending = list(self._state_tasks)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


async def browser_audio(websocket: WebSocket) -> None:
    runtime: Runtime = websocket.app.state.runtime
    await websocket.accept()
    test = BrowserTest(runtime)
    tasks = []
    reserved = False
    try:
        with runtime.lock:
            if runtime.connection.state == "connected" or runtime._link is not None:
                raise ApiError(409, "Für den Browser-Test zuerst den Roboter trennen.")
            if runtime.browser_test is not None:
                raise ApiError(409, "Ein Browser-Mikrofontest läuft bereits.")
            if not test.applied.microphone_enabled or not test.applied.stt_enabled:
                raise ApiError(409, "Für den Mikrofontest Mikrofon und Spracherkennung in den Robotereinstellungen aktivieren und übernehmen.")
            if not runtime.secrets.value("gladia") or (test.applied.llm_enabled and not runtime.secrets.value("openai")):
                raise ApiError(409, "Für den Mikrofontest die benötigten Gladia- und OpenAI-Schlüssel unter System → Schlüssel hinterlegen.")
            runtime._generation += 1  # Invalidates a robot dial still in flight.
            runtime.browser_test = test
            reserved = True

        async def write() -> None:
            while True:
                message = await test.outgoing.get()
                try:
                    await websocket.send_json(message)
                finally:
                    test.outgoing.task_done()

        tasks = [asyncio.create_task(test.run()), asyncio.create_task(test.receive(websocket)), asyncio.create_task(write())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if not (task.cancelled() and test.closing):
                await task
        if test.closing:
            await asyncio.wait_for(test.outgoing.join(), timeout=2)
        if not test.closing and tasks[0] in done:
            raise ApiError(502, "Die Gesprächspipeline wurde beendet. Bitte den Test erneut starten.")
    except (WebSocketDisconnect, OSError):
        pass
    except Exception as exc:
        message = exc.message if isinstance(exc, ApiError) else "Die Gesprächspipeline ist fehlgeschlagen. Schlüssel und Serverprotokoll prüfen."
        if not isinstance(exc, ApiError):
            logger.exception("Browser microphone pipeline failed")
        try:
            # Stop the single writer before sending the final error directly.
            if tasks:
                tasks[-1].cancel()
                await asyncio.gather(tasks[-1], return_exceptions=True)
            await websocket.send_json({"type": "error", "message": message})
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass
    finally:
        # ASGI may cancel the handler immediately after the tab closes. Finish
        # audio-service cleanup before releasing ownership, even in that case.
        with CancelScope(shield=True):
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if reserved:
                await test.close()
                with runtime.lock:
                    if runtime.browser_test is test:
                        runtime.browser_test = None
            try:
                await websocket.close()
            except (WebSocketDisconnect, RuntimeError, OSError):
                pass
