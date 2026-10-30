import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from nao_fair.app import create_app
from nao_fair.browser_audio import BrowserSpeech, BrowserTest, MAX_AUDIO_BYTES
from nao_fair.test_driver import TestDriver


@pytest.fixture
def browser_app(tmp_path, monkeypatch):
    app = create_app(driver=TestDriver(), db_path=tmp_path / "db", secrets_path=tmp_path / "secrets")
    app.state.runtime.secrets.put("gladia", "test-gladia")
    app.state.runtime.secrets.put("openai", "test-openai")
    sessions = []

    class Session:
        def __init__(self, test):
            self.test = test
            self.ready = asyncio.Event()
            self.stopped = asyncio.Event()
            self.audio = []

        async def run(self):
            self.ready.set()
            await self.stopped.wait()

        async def start_conversation(self):
            self.test._state("listening")

        async def push_audio(self, audio):
            self.audio.append(audio)
            await self.test._line("visitor", "Mikrofon gehört")

        async def stop(self, reason):
            self.stopped.set()

    def build(test):
        session = Session(test)
        sessions.append(session)
        return session

    monkeypatch.setattr(BrowserTest, "build_session", build)
    return app, sessions


def receive(ws, kind):
    while True:
        message = ws.receive_json()
        if message["type"] == kind:
            return message


def test_pcm_enters_pipeline_and_disconnect_releases_session(browser_app):
    app, sessions = browser_app
    with TestClient(app) as client:
        with client.websocket_connect("/api/browser-audio") as ws:
            assert receive(ws, "ready")["sample_rate"] == 16000
            assert client.get("/api/robot/status").json()["state"] == "disconnected"
            audio = b"\x00\x40" * 320
            ws.send_bytes(audio)
            assert receive(ws, "transcript")["text"] == "Mikrofon gehört"
            assert sessions[0].audio == [audio]
            assert client.post("/api/robot/connect", json={"address": "192.0.2.1"}).status_code == 409
            with client.websocket_connect("/api/browser-audio") as second:
                assert "bereits" in receive(second, "error")["message"]
        # The WebSocket context waits for teardown, including pipeline cleanup.
        assert sessions[0].stopped.is_set()
        assert app.state.runtime.browser_test is None
        with client.websocket_connect("/api/browser-audio") as ws:
            receive(ws, "ready")
            ws.send_json({"type": "listen"})
            assert receive(ws, "state")["state"] == "listening"


@pytest.mark.parametrize("audio", [b"x", b"", b"x" * (MAX_AUDIO_BYTES + 2)])
def test_invalid_audio_closes_and_cleans_up(browser_app, audio):
    app, sessions = browser_app
    with TestClient(app) as client:
        with client.websocket_connect("/api/browser-audio") as ws:
            receive(ws, "ready")
            ws.send_bytes(audio)
            assert "PCM16" in receive(ws, "error")["message"]
        assert sessions[0].stopped.is_set()
        assert app.state.runtime.browser_test is None


def test_missing_credentials_and_connected_robot_do_not_start_pipeline(browser_app, monkeypatch):
    app, sessions = browser_app
    monkeypatch.delenv("GLADIA_API_KEY", raising=False)
    with TestClient(app) as client:
        app.state.runtime.secrets.delete("gladia")
        with client.websocket_connect("/api/browser-audio") as ws:
            assert "Schlüssel" in receive(ws, "error")["message"]
        assert not sessions
        client.post("/api/robot/connect", json={"address": "192.0.2.1"})
        with client.websocket_connect("/api/browser-audio") as ws:
            assert "Roboter trennen" in receive(ws, "error")["message"]
        assert not sessions


def test_applied_config_stops_test_with_restart_message(browser_app):
    app, sessions = browser_app
    with TestClient(app) as client:
        with client.websocket_connect("/api/browser-audio") as ws:
            receive(ws, "ready")
            draft = app.state.runtime.applied.model_dump()
            from nao_fair.models import RobotDraft

            response = client.post("/api/config/apply", json={
                "screen": "robot", "expected_revision": draft["revision"],
                "draft": {field: draft[field] for field in RobotDraft.model_fields},
            })
            assert response.status_code == 200
            assert "erneut starten" in receive(ws, "closed")["message"]
        assert sessions[0].stopped.is_set()
        assert app.state.runtime.browser_test is None


def test_speech_waits_for_browser_ack_and_stop_releases_waiter():
    async def scenario():
        messages = asyncio.Queue()

        async def send(message):
            await messages.put(message)

        test = SimpleNamespace(send=send, runtime=SimpleNamespace(applied=SimpleNamespace(
            tts_language="de", volume=70, speech_speed=110,
        )))
        speech = BrowserSpeech(test)
        task = asyncio.create_task(speech.speak("Hallo"))
        message = await messages.get()
        assert message["volume"] == .7
        assert not task.done()
        await speech.acknowledge({"id": "old"})
        assert not task.done()
        await speech.acknowledge({"id": message["id"]})
        await task
        task = asyncio.create_task(speech.speak("Noch eine Antwort"))
        await messages.get()
        await speech.stop()
        await task
        assert not speech.pending

    asyncio.run(scenario())
