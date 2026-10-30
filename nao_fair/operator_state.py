"""Local readiness, setup comparisons and library reference queries."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nao_fair.runtime import Runtime

API_VERSION = 2
CONTENT_SINGLE_KEYS = ('content_id', 'system_prompt_id', 'local_context_id', 'personality_id')
CONTENT_LIST_KEYS = ('content_ids', 'knowledge_ids')


def content_ids(payload: dict) -> set[str]:
    ids = {payload[key] for key in CONTENT_SINGLE_KEYS if isinstance(payload.get(key), str)}
    for key in CONTENT_LIST_KEYS:
        ids.update(value for value in payload.get(key, []) if isinstance(value, str))
    return ids


def references(runtime: Runtime, category: str, item_id: str) -> dict:
    store = runtime.store
    if category == 'content':
        applied = runtime.applied
        used = item_id in (applied.system_prompt_id, applied.local_context_id, applied.personality_id, *applied.knowledge_ids)
        presets = [p for p in store.list_presets() if item_id in content_ids(p['payload'])]
        preset_ids = {p['id'] for p in presets}
        setups = [s for s in store.list_setups() if preset_ids.intersection(s['preset_ids'].values())]
    else:
        used = False
        presets = []
        setups = [s for s in store.list_setups() if item_id in s['preset_ids'].values()]
    return {'applied': used, 'presets': [{'id': p['id'], 'name': p['name']} for p in presets],
            'setups': [{'id': s['id'], 'name': s['name']} for s in setups]}


def setup_preview(runtime: Runtime, setup_id: str) -> dict:
    from nao_fair.runtime import ApiError
    setup = runtime.store.get_setup(setup_id)
    if setup is None:
        raise ApiError(404, 'Setup wurde nicht gefunden.')
    try:
        candidate = runtime._merge_setup(setup)
        runtime._validate_references(candidate)
        target_content = runtime._content_snapshot(candidate)
    except ValueError as exc:
        raise ApiError(422, f'Setup enthält einen fehlenden oder ungültigen Baustein: {exc}') from exc
    before, after = runtime.applied.model_dump(mode='json'), candidate.model_dump(mode='json')
    changes = [{'key': key, 'before': before[key], 'after': after[key]} for key in after if key != 'revision' and before[key] != after[key]]
    for item_id, markdown in target_content.items():
        if item_id in runtime.applied_content and runtime.applied_content[item_id] != markdown:
            item = runtime.store.get_content(item_id)
            changes.append({'key': f"Text: {item['name']}", 'before': runtime.applied_content[item_id], 'after': markdown})
    return {'setup_id': setup_id, 'changes': changes}


async def system_report(runtime: Runtime, probe: bool = False) -> dict:
    connection = runtime.status()
    connected = connection['state'] == 'connected'
    nao_state = 'ready' if connected else 'disconnected'
    bridge_state = 'ready' if connected else 'unknown'
    stream_connected = None
    if connected and runtime.driver is None:
        transport = getattr(getattr(runtime._link, 'session', None), '_transport', None)
        client = getattr(transport, '_session', None)
        stream_connected = bool(getattr(client, 'is_connected', False))
        bridge_state = 'ready' if stream_connected else 'waiting'
    if probe and connection['address'] and runtime.driver is None:
        from nao_fair.robot.probe import tcp_accepts, bridge_accepts
        ports = await asyncio.gather(*(tcp_accepts(connection['address'], port) for port in runtime.nao_ports))
        reachable = any(ports)
        nao_state = 'ready' if reachable else 'missing'
        bridge_state = ('ready' if await bridge_accepts(connection['address'], runtime.bridge_port) else 'missing') if reachable else 'blocked'
    if connected and stream_connected is False and bridge_state == 'ready':
        bridge_state = 'waiting'
    checks = {
        'nao': {'state': nao_state, 'detail': connection.get('failure') or connection.get('address') or 'Keine Adresse verbunden.', 'action': 'address'},
        'bridge': {'state': bridge_state, 'detail': f'Mikrofonbrücke · Port {runtime.bridge_port}' + (' · Audio-Stream wird verbunden.' if bridge_state == 'waiting' else ''), 'action': 'bridge'},
    }
    for slot in ('openai', 'gladia'):
        present = runtime.secrets.status(slot) != 'missing'
        checks[slot] = {'state': 'ready' if present else 'missing', 'detail': 'Schlüssel vorhanden; Cloud-Zugang nicht geprüft.' if present else 'Schlüssel fehlt.', 'action': 'keys'}
    changes, setup_error = [], None
    if runtime.active_setup_id:
        try:
            changes = setup_preview(runtime, runtime.active_setup_id)['changes']
        except Exception as exc:
            from nao_fair.runtime import ApiError
            setup_error = exc.message if isinstance(exc, ApiError) else 'Setup kann nicht verglichen werden.'
    return {'api_version': API_VERSION, 'started_at': runtime.started_at, 'checks': checks,
            'conversation_ready': connected and checks['bridge']['state'] == 'ready' and (not runtime.applied.llm_enabled or checks['openai']['state'] == 'ready') and (not runtime.applied.stt_enabled or checks['gladia']['state'] == 'ready'),
            'setup_modified': bool(changes or setup_error), 'setup_changes': changes, 'setup_error': setup_error}
