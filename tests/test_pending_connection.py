from __future__ import annotations

import asyncio

from nao_fair.app import create_app
from nao_fair.test_driver import TestDriver


class DelayedDriver(TestDriver):
    def __init__(self):
        super().__init__(name="Milo")
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def open(self, address):
        self.started.set()
        await self.release.wait()
        return self.name


def test_disconnect_aborts_pending_connection(tmp_path):
    async def scenario():
        driver = DelayedDriver()
        app = create_app(driver=driver, db_path=tmp_path / "fair.sqlite", secrets_path=tmp_path / "secrets.json")
        runtime = app.state.runtime
        runtime.attach_loop(asyncio.get_running_loop())
        connecting = asyncio.create_task(runtime.connect("192.0.2.10"))
        await driver.started.wait()
        await runtime.disconnect()
        driver.release.set()
        await connecting
        assert runtime.status()["state"] == "disconnected"
        await runtime.shutdown()

    asyncio.run(scenario())


def test_event_stream_unsubscribes_when_browser_disconnects(tmp_path):
    from nao_fair.app import events

    class Socket:
        def __init__(self, app):
            self.app = app
            self.sent = []

        async def accept(self):
            pass

        async def send_json(self, payload):
            self.sent.append(payload)

        async def receive(self):
            return {"type": "websocket.disconnect", "code": 1000}

    async def scenario():
        app = create_app(db_path=tmp_path / "fair.sqlite", secrets_path=tmp_path / "secrets.json")
        runtime = app.state.runtime
        runtime.attach_loop(asyncio.get_running_loop())
        socket = Socket(app)
        await asyncio.wait_for(events(socket), timeout=1)
        assert socket.sent[0]["type"] == "picture"
        assert runtime.hub._subscribers == []
        await runtime.shutdown()

    asyncio.run(scenario())
