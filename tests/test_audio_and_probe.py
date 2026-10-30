from fastapi.testclient import TestClient

from nao_fair.app import create_app
from nao_fair.test_driver import TestDriver


def test_direct_audio_preserves_conversation_and_other_settings(tmp_path):
    driver = TestDriver(name="Milo")
    app = create_app(driver=driver, db_path=tmp_path / "fair.sqlite", secrets_path=tmp_path / "secrets.json")
    with TestClient(app) as client:
        before = client.get('/api/config/applied').json()['applied']
        response = client.patch('/api/robot/audio', json={'volume': 67})
        assert response.status_code == 200
        assert response.json()['applied']['volume'] == 67
        client.post('/api/robot/connect', json={'address': '192.0.2.10'})
        driver.set_conversation_state('speaking')
        driver.append_transcript('nao', 'Hallo')
        response = client.patch('/api/robot/audio', json={'speech_speed': 125})
        assert response.status_code == 200
        after = response.json()['applied']
        assert after['speech_speed'] == 125
        assert after['volume'] == 67
        assert after['listening_timeout_secs'] == before['listening_timeout_secs']
        with client.websocket_connect('/api/events') as ws:
            picture = ws.receive_json()
        assert picture['conversation_state'] == 'speaking'
        assert picture['transcript'][0]['text'] == 'Hallo'
        assert client.patch('/api/robot/audio', json={'volume': 101}).status_code == 422
        assert client.patch('/api/robot/audio', json={'posture': 'Sit'}).status_code == 422
        assert client.patch('/api/robot/audio', json={}).status_code == 422


def test_robot_failure_precedes_bridge_failure(tmp_path, monkeypatch):
    from nao_fair.robot import probe
    app = create_app(db_path=tmp_path / "fair.sqlite", secrets_path=tmp_path / "secrets.json")
    calls = []
    reachable = False

    async def tcp(host, port, timeout=0.5):
        calls.append(port)
        return reachable

    async def bridge(host, port, timeout=0.5):
        calls.append('bridge')
        return False

    monkeypatch.setattr(probe, 'tcp_accepts', tcp)
    monkeypatch.setattr(probe, 'bridge_accepts', bridge)
    with TestClient(app) as client:
        failed = client.post('/api/robot/connect', json={'address': '192.0.2.10'}).json()
        assert 'NAO' in failed['failure']
        assert 'bridge' not in calls
        reachable = True
        calls.clear()
        failed = client.post('/api/robot/connect', json={'address': '192.0.2.10'}).json()
        assert 'Mikrofonbrücke' in failed['failure']
        assert calls[-1] == 'bridge'


def test_live_audio_retunes_without_replacing_the_pipeline(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from nao_fair.robot.live import LiveLink

    calls = []

    async def call_qi(command):
        command()

    nao = SimpleNamespace(
        audio=SimpleNamespace(service=SimpleNamespace(setOutputVolume=lambda value: calls.append(('volume', value)))),
        tts=SimpleNamespace(set_speed=lambda value: calls.append(('speed', value))),
        call_qi=call_qi,
    )
    link = LiveLink(None, nao, 'Milo')
    session = object()
    link.session = session
    app = create_app(db_path=tmp_path / 'fair.sqlite', secrets_path=tmp_path / 'secrets.json')
    applied = app.state.runtime.applied.model_copy(update={'volume': 66, 'speech_speed': 130})
    asyncio.run(link.on_audio_changed(applied))
    assert calls == [('volume', 66), ('speed', 130)]
    assert link.session is session
