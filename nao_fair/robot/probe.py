"""TCP probe used before a connect is allowed to proceed to NAOqi."""

from __future__ import annotations

import asyncio


async def tcp_accepts(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
    except Exception:
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return True


async def bridge_accepts(host: str, port: int, timeout: float = 0.5) -> bool:
    """Prefer the bridge client's probe. A missing install uses the same check."""
    try:
        from nao_websocket_bridge_client.reconnect import tcp_accepts as client_probe
    except Exception:
        client_probe = tcp_accepts
    return await client_probe(host, port, timeout)
