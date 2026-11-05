import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from nao_fair.app import create_app
from nao_fair.models import Connection
from nao_fair.robot.autostart import ARMED_STATE, read_armed_state
from nao_fair.robot.live import LiveLink


class Memory:
    def __init__(self):
        self.data = {}

    def getData(self, key):
        return self.data[key]


class Nao:
    def __init__(self, memory):
        self.session = SimpleNamespace(service=lambda name: memory)

    async def call_qi(self, fn, *args, **kwargs):
        return fn(*args)


def test_robot_switch_is_adopted_once_and_stored(tmp_path):
    runtime = create_app(db_path=tmp_path / 'fair.sqlite', secrets_path=tmp_path / 'secrets.json').state.runtime
    memory = Memory()
    link = LiveLink(runtime, Nao(memory), 'NAO')
    initial_revision = runtime.applied.revision
    memory.data[ARMED_STATE] = json.dumps({'armed': False, 'change': 'robot-1'})

    asyncio.run(link._read_robot_armed(force=True))
    assert runtime.applied.autostart_enabled is False
    assert runtime.applied.revision == initial_revision + 1
    assert runtime.store.load_runtime()[0].autostart_enabled is False

    # An explicit Apply can change the switch without an older robot read undoing it.
    runtime.adopt_robot_armed(True)
    asyncio.run(link._read_robot_armed())
    assert runtime.applied.autostart_enabled is True
    memory.data[ARMED_STATE] = json.dumps({'armed': False, 'change': 'robot-2'})
    asyncio.run(link._read_robot_armed())
    assert runtime.applied.autostart_enabled is False
    runtime.store.close()


def test_malformed_robot_switch_is_ignored():
    memory = Memory()
    nao = Nao(memory)
    for raw in ('', '{}', '{"armed": 1, "change": "x"}', '{"armed": true}'):
        memory.data[ARMED_STATE] = raw
        assert asyncio.run(read_armed_state(nao)) is None


def test_bumper_chord_does_not_run_individual_app_actions(tmp_path):
    runtime = create_app(db_path=tmp_path / 'fair.sqlite', secrets_path=tmp_path / 'secrets.json').state.runtime
    runtime.connection = Connection(state='connected', address='nao', name='NAO', failure=None)
    called = []
    def schedule(coro):
        called.append(True)
        coro.close()
    runtime._schedule = schedule
    with patch('nao_fair.runtime.time.monotonic', return_value=1):
        runtime.note_sensor_down('left_bumper')
    with patch('nao_fair.runtime.time.monotonic', return_value=1.1):
        runtime.note_sensor_down('right_bumper')
    with patch('nao_fair.runtime.time.monotonic', return_value=3.2):
        runtime._touch_due()
        runtime.note_sensor_up('left_bumper')
        runtime.note_sensor_up('right_bumper')
    assert called == []
    runtime.store.close()


def test_app_recognizes_long_press_on_release_before_next_tick():
    from nao_fair.touch import TouchClassifier
    touch = TouchClassifier()
    touch.down('left_bumper', 0)
    assert touch.tick(0.59) == []
    assert touch.up('left_bumper', 0.61, False) == 'long_press'
