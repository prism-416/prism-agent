from __future__ import annotations

import asyncio

import pytest

from infrastructure.llm.pydantic_ai_agent_factory import RuntimePlanningAgent


class _FakeResult:
    def __init__(self, output: str) -> None:
        self.output = output


class _LoopDrivingAgent:
    """Mimics pydantic-ai's Agent.run_sync, which drives a coroutine with
    ``get_event_loop().run_until_complete(...)`` and therefore raises
    "This event loop is already running" when called inside an active loop."""

    def run_sync(self, user_prompt: str) -> _FakeResult:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        async def _run() -> _FakeResult:
            return _FakeResult(f"planned:{user_prompt}")

        coro = _run()
        try:
            return loop.run_until_complete(coro)
        except BaseException:
            coro.close()  # avoid "coroutine was never awaited" when the loop is busy
            raise


def test_run_sync_directly_fails_inside_running_loop() -> None:
    # Reproduces the OCI/FDK failure: the handler runs inside an active event
    # loop, so a direct run_sync call blows up.
    agent = _LoopDrivingAgent()

    async def _invoke() -> None:
        agent.run_sync("x")

    with pytest.raises(RuntimeError, match="already running"):
        asyncio.run(_invoke())


def test_run_agent_sync_bridges_to_worker_thread_inside_running_loop() -> None:
    # The worker-thread bridge gives run_sync a fresh loop, so it succeeds even
    # when the caller is inside a running event loop.
    agent = _LoopDrivingAgent()

    async def _invoke() -> _FakeResult:
        return RuntimePlanningAgent._run_agent_sync(agent, "do-it")

    result = asyncio.run(_invoke())

    assert result.output == "planned:do-it"
