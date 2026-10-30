"""Optional smoke test: shared Pipecat stages, no cloud or robot connection."""

import asyncio

import pytest

pytest.importorskip("pipecat", reason="requires the browser optional dependencies")

from nao_fair.booth.config import AppConfig
from nao_fair.booth.pipecat_session import PipecatSession
from pipecat.frames.frames import LLMTextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame


def test_shared_pipeline_delivers_text_to_browser_and_shuts_down():
    async def scenario():
        spoken = []
        transcript = []
        finished = asyncio.Event()

        class Speech:
            async def speak(self, text):
                spoken.append(text)
                finished.set()

            async def stop(self):
                pass

        async def line(text):
            transcript.append(text)

        session = PipecatSession(
            None, config=AppConfig(), browser_speech=Speech(), on_nao_line=line,
            announce=False, bind_buttons=False, stt_enabled=False, llm_enabled=False,
        )
        task = asyncio.create_task(session.run())
        try:
            await asyncio.wait_for(session.ready.wait(), timeout=15)
            await session.push_audio(b"\x00\x00" * 320)
            await session._worker.queue_frames([
                LLMFullResponseStartFrame(),
                LLMTextFrame(text="Test "),
                LLMTextFrame(text="erfolgreich."),
                LLMFullResponseEndFrame(),
            ])
            await asyncio.wait_for(finished.wait(), timeout=3)
            assert spoken == ["Test erfolgreich."]
            assert transcript == ["Test erfolgreich."]
        finally:
            await session.stop("test completed")
            await asyncio.wait_for(task, timeout=5)

    asyncio.run(scenario())


def test_real_pipeline_over_websocket_waits_for_speech_ack(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from nao_fair.app import create_app
    from nao_fair.browser_audio import BrowserTest

    app = create_app(db_path=tmp_path / "db", secrets_path=tmp_path / "secrets")
    app.state.runtime.secrets.put("gladia", "test-gladia")
    app.state.runtime.secrets.put("openai", "test-openai")

    def build(test):
        return PipecatSession(
            None, config=AppConfig(), browser_speech=test.speech,
            on_visitor_line=lambda text: test._line("visitor", text),
            on_nao_line=lambda text: test._line("nao", text), on_state=test._state,
            announce=False, bind_buttons=False, stt_enabled=False, llm_enabled=False,
        )

    monkeypatch.setattr(BrowserTest, "build_session", build)

    def receive(ws, kind):
        while True:
            message = ws.receive_json()
            if message["type"] == kind:
                return message

    with TestClient(app) as client:
        with client.websocket_connect("/api/browser-audio") as ws:
            receive(ws, "ready")
            test = app.state.runtime.browser_test
            ws.send_bytes(b"\x00\x00" * 320)
            client.portal.call(test.session._worker.queue_frames, [
                LLMFullResponseStartFrame(),
                LLMTextFrame(text="Hallo "),
                LLMTextFrame(text="Browser."),
                LLMFullResponseEndFrame(),
            ])
            assert receive(ws, "transcript")["text"] == "Hallo Browser."
            speech = receive(ws, "speech")
            assert speech["text"] == "Hallo Browser."
            assert len(test.speech.pending) == 1
            ws.send_json({"type": "speech.finished", "id": speech["id"]})
            while receive(ws, "state")["state"] != "listening":
                pass
            assert not test.speech.pending
        assert app.state.runtime.browser_test is None
        assert test.task.done()
