import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pipecat")
from nao_fair.booth.pipecat_session import PipecatSession
from nao_fair.booth.nao.nao_nlp_manager import NAOState


def test_manual_message_starts_conversation_and_runs_llm():
    async def scenario():
        states = []
        async def transition(state):
            states.append(state)
            manager.state = state
        manager = SimpleNamespace(state=NAOState.IDLE, llm_enabled=True, transition_to_state=transition)
        aggregator = SimpleNamespace(queue_frame=AsyncMock())
        visitor = AsyncMock()
        session = object.__new__(PipecatSession)
        session.ready = asyncio.Event()
        session.ready.set()
        session._stopped = False
        session._nao_nlp_manager = manager
        session._user_aggregator = aggregator
        session._on_visitor_line = visitor
        session._robot_name = "NAO"
        await session.send_message("Hallo NAO")
        assert states == [NAOState.LISTENING, NAOState.THINKING]
        visitor.assert_awaited_once_with("Hallo NAO")
        frame = aggregator.queue_frame.await_args.args[0]
        assert frame.messages == [{"role": "user", "content": "Hallo NAO"}]
        assert frame.run_llm is True
        with pytest.raises(RuntimeError):
            await session.send_message("Noch eine Nachricht")
    asyncio.run(scenario())


def test_manual_stop_phrase_does_not_run_llm():
    async def scenario():
        manager = SimpleNamespace(state=NAOState.LISTENING, llm_enabled=True, on_back_head_pressed=AsyncMock())
        session = object.__new__(PipecatSession)
        session.ready = asyncio.Event()
        session.ready.set()
        session._stopped = False
        session._nao_nlp_manager = manager
        session._user_aggregator = SimpleNamespace(queue_frame=AsyncMock())
        session._on_visitor_line = AsyncMock()
        session._robot_name = "NAO"
        await session.send_message("NAO Stopp")
        manager.on_back_head_pressed.assert_awaited_once()
        session._user_aggregator.queue_frame.assert_not_awaited()
    asyncio.run(scenario())
