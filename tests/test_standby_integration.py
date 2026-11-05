"""Exercise the app and robot service together when both checkouts are present."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nao_fair.app import create_app
from nao_fair.robot.autostart import assignment_document
from nao_fair.robot.live import LiveLink


robot_repo = Path(__file__).resolve().parents[2].parent / 'nao-fair-autostart'
if not robot_repo.is_dir():
    pytest.skip('robot service checkout is not present', allow_module_level=True)
sys.path.insert(0, str(robot_repo))
from nao_fair_autostart.contract import read_assignment, write_assignment
from nao_fair_autostart.service import Service


class Memory:
    def __init__(self):
        self.data = {}
        self.callbacks = {}

    def getData(self, key):
        return self.data[key]

    def insertData(self, key, value):
        self.data[key] = value

    def raiseEvent(self, key, value):
        self.data[key] = value
        if key in self.callbacks:
            self.callbacks[key](value)

    def subscriber(self, name):
        return SimpleNamespace(signal=SimpleNamespace(connect=lambda cb: self.callbacks.__setitem__(name, cb)))


class Voice:
    def __init__(self):
        self.words = []

    def stopAll(self):
        pass

    def setParameter(self, *args):
        pass

    def setLanguage(self, *args):
        pass

    def say(self, text, _async=False):
        self.words.append(text)
        return SimpleNamespace(wait=lambda: None, isRunning=lambda: False)

    async def stop_async(self):
        pass


class Motion:
    def stopMove(self):
        pass

    async def cancel(self):
        pass


def test_reconnect_and_connected_bumper_chord_are_synchronized(tmp_path):
    async def run():
        runtime = create_app(db_path=tmp_path / 'db.sqlite', secrets_path=tmp_path / 'secrets').state.runtime
        memory, voice, motion = Memory(), Voice(), Motion()
        services = {'ALMemory': memory, 'ALTextToSpeech': voice, 'ALMotion': motion,
                    'ALAudioDevice': SimpleNamespace(setOutputVolume=lambda value: None),
                    'ALAutonomousLife': SimpleNamespace(setState=lambda value: None)}
        session = SimpleNamespace(service=lambda name: services.get(name, object()))
        clock = [100.0]
        document = assignment_document(runtime.applied)
        document['armed'] = False
        document['_arm_change_id'] = 'persisted'
        path = str(tmp_path / 'assignment.json')
        write_assignment(path, document)
        service = Service(session, path, now=lambda: clock[0])
        service.start()

        async def call_qi(fn, *args, **kwargs):
            return fn(*args)

        nao = SimpleNamespace(tts=voice, motion=motion, session=session, call_qi=call_qi)
        link = LiveLink(runtime, nao, 'NAO')
        try:
            await link.begin_autostart(runtime.applied)
            assert not runtime.applied.autostart_enabled
            assert service.engine.suppressed

            service._pressed('left_bumper')(1)
            clock[0] += 0.1
            service._pressed('right_bumper')(1)
            clock[0] += 2.1
            with service._lock:
                service._check_bumpers(clock[0])
            service._speech_worker.join(1)
            await link._read_robot_armed()
            assert runtime.applied.autostart_enabled
            assert read_assignment(path)['armed']
            assert 'Autostart ein' in voice.words
            assert await link.sync_autostart(runtime.applied)
            assert service.engine.assignment['armed']

            # A second chord races with an assignment prepared using the old ID.
            service._pressed('left_bumper')(0)
            service._pressed('right_bumper')(0)
            service._pressed('left_bumper')(1)
            service._pressed('right_bumper')(1)
            clock[0] += 2.1
            with service._lock:
                service._check_bumpers(clock[0])
            service._speech_worker.join(1)
            assert not await link.sync_autostart(runtime.applied)
            assert not read_assignment(path)['armed']
            await link._read_robot_armed()
            assert not runtime.applied.autostart_enabled
            assert await link.sync_autostart(runtime.applied)
            assert not read_assignment(path)['armed']
        finally:
            await link.release_autostart()
            service._stop.set()
            runtime.store.close()

    asyncio.run(run())
