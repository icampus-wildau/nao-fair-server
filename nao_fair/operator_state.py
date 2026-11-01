"""Local readiness and library reference queries."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nao_fair.runtime import Runtime

API_VERSION = 2


def references(runtime: Runtime, category: str, item_id: str) -> dict:
    from nao_fair.scenarios import content_ids

    if category != "content":
        return {"applied": False, "scenarios": []}
    applied = runtime.applied
    used = item_id in (applied.system_prompt_id, applied.local_context_id, applied.personality_id, *applied.knowledge_ids)
    scenarios = [item for item in runtime.store.list_scenarios() if item_id in content_ids(item)]
    return {"applied": used, "scenarios": [{"id": item["id"], "name": item["name"]} for item in scenarios]}


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
    return {'api_version': API_VERSION, 'started_at': runtime.started_at, 'checks': checks,
            'conversation_ready': connected and checks['bridge']['state'] == 'ready' and (not runtime.applied.llm_enabled or checks['openai']['state'] == 'ready') and (not runtime.applied.stt_enabled or checks['gladia']['state'] == 'ready'),
            'setup_modified': False, 'setup_changes': [], 'setup_error': None}
