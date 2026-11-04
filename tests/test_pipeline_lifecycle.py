from __future__ import annotations

import asyncio

from nao_fair.robot.live import LiveLink


def test_stopping_pipeline_for_rebuild_does_not_report_failure():
    class Runtime:
        failures = []

        def fail_disconnect(self, message):
            self.failures.append(message)

    class Session:
        def __init__(self):
            self.stop_event = asyncio.Event()

        async def run(self):
            await self.stop_event.wait()

        async def stop(self, reason):
            self.stop_event.set()
            await asyncio.sleep(0)

    async def scenario():
        runtime = Runtime()
        link = LiveLink(runtime, None, "NAO")
        link.session = Session()
        link._task = asyncio.create_task(link.session.run())
        link._task.add_done_callback(link._pipeline_done)
        await link._stop_pipeline()
        await asyncio.sleep(0)
        assert runtime.failures == []

    asyncio.run(scenario())


def test_a_finished_pipeline_leaves_the_robot_connected():
    class Runtime:
        failures = []
        noted = []

        def fail_disconnect(self, message):
            self.failures.append(message)

        def note_services(self, **payload):
            self.noted.append(payload)

    async def scenario():
        async def boom():
            raise RuntimeError("bridge gone")

        runtime = Runtime()
        link = LiveLink(runtime, None, "NAO")
        link._task = asyncio.create_task(boom())
        link._task.add_done_callback(link._pipeline_done)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert runtime.failures == []
        assert runtime.noted == [{"bridge_ready": False, "bridge": True}]

    asyncio.run(scenario())
