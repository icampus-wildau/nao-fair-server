from fastapi.testclient import TestClient
from nao_fair.app import create_app


def client_for(tmp_path):
    return TestClient(create_app(db_path=tmp_path/'fair.sqlite',secrets_path=tmp_path/'secrets.json'))


def test_system_reports_local_readiness_without_revealing_secrets(tmp_path):
    with client_for(tmp_path) as client:
        client.put('/api/secrets/openai',json={'value':'private-test-secret'})
        result=client.get('/api/system')
        assert result.status_code==200
        body=result.json()
        assert body['api_version']>=2
        assert body['checks']['nao']['state']=='disconnected'
        assert body['checks']['openai']['state']=='ready'
        assert body['checks']['gladia']['state']=='missing'
        assert 'private-test-secret' not in result.text


def test_seeded_scenario_keeps_prompt_selection_and_leaves_volume_out(tmp_path):
    with client_for(tmp_path) as client:
        applied=client.get('/api/config/applied').json()['applied']
        scenario=client.get('/api/scenarios').json()[0]
        assert scenario['name']=='Standard'
        assert scenario['personality_id']==applied['personality_id']
        assert scenario['system_prompt_id']==applied['system_prompt_id']
        assert 'volume' not in scenario
        client.patch('/api/robot/audio',json={'volume':62})
        assert client.get(f"/api/scenarios/{scenario['id']}").json()['personality_id']==applied['personality_id']


def test_referenced_content_requires_a_valid_replacement_and_updates_scenarios(tmp_path):
    with client_for(tmp_path) as client:
        original=client.get('/api/content',params={'kind':'system_prompt'}).json()[0]
        replacement=client.post('/api/content',json={'kind':'system_prompt','name':'Z Ersatz','markdown':'Hallo'}).json()
        refs=client.get(f"/api/content/{original['id']}/references")
        assert refs.status_code==200
        assert refs.json()['applied'] is True
        assert refs.json()['scenarios']
        assert client.delete(f"/api/content/{original['id']}").status_code==409
        # Running content stays protected, even with a replacement.
        assert client.delete(f"/api/content/{original['id']}",params={'replacement_id':replacement['id']}).status_code==409
        state=client.get('/api/config/applied').json()['applied']
        from nao_fair.models import LlmDraft
        draft={key:state[key] for key in LlmDraft.model_fields}
        draft['system_prompt_id']=replacement['id']
        assert client.post('/api/config/apply',json={'screen':'llm','draft':draft,'expected_revision':state['revision']}).status_code==200
        assert client.delete(f"/api/content/{original['id']}",params={'replacement_id':replacement['id']}).status_code==200
        assert all(item['system_prompt_id']!=original['id'] for item in client.get('/api/scenarios').json())


def test_interaction_apply_preserves_a_running_conversation(tmp_path):
    from nao_fair.test_driver import TestDriver
    driver=TestDriver(name='Milo')
    app=create_app(driver=driver,db_path=tmp_path/'fair.sqlite',secrets_path=tmp_path/'secrets.json')
    with TestClient(app) as client:
        client.post('/api/robot/connect',json={'address':'192.0.2.10'})
        driver.set_conversation_state('speaking')
        state=client.get('/api/config/applied').json()['applied']
        state['triggers']['middle_head']['action']='stop_speech'
        result=client.post('/api/config/apply',json={'screen':'interactions','draft':{'triggers':state['triggers']},'expected_revision':state['revision']})
        assert result.status_code==200
        assert client.get('/api/robot/status').json()['conversation_state']=='speaking'


def test_system_does_not_mark_a_disconnected_audio_stream_ready(tmp_path):
    from types import SimpleNamespace
    app=create_app(db_path=tmp_path/'fair.sqlite',secrets_path=tmp_path/'secrets.json')
    with TestClient(app) as client:
        runtime=app.state.runtime
        runtime.connection.state='connected'
        stream=SimpleNamespace(is_connected=False)
        runtime._link=SimpleNamespace(session=SimpleNamespace(_transport=SimpleNamespace(_session=stream)))
        try:
            assert client.get('/api/system').json()['checks']['bridge']['state']=='waiting'
            stream.is_connected=True
            assert client.get('/api/system').json()['checks']['bridge']['state']=='ready'
        finally:
            runtime._link=None
            runtime.connection.state='disconnected'
