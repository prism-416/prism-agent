from __future__ import annotations

from domain.subtasks import SubAgentResult, SubTask, SubTaskStatus, TaskGraph
from infrastructure.state.memory_state_store import MemoryStateStore
from infrastructure.state.prism_api_state_store import PrismApiStateStore

R = SubTaskStatus.RUNNING
C = SubTaskStatus.COMPLETED


class _FakeAgentStatePrismClient:
    """Minimal agent-state client: stores upserted memories and replays them."""

    is_configured = True

    def __init__(self) -> None:
        self.memories: list[dict] = []

    def get_agent_run_state(self, workspace_id: str, run_id: str) -> dict:
        _ = (workspace_id, run_id)
        return {
            "run": {"runId": run_id, "status": "running"},
            "steps": [],
            "actions": [],
            "actionEvents": [],
            "memories": self.memories,
        }

    def upsert_agent_memory(self, workspace_id: str, payload: dict) -> dict:
        _ = workspace_id
        for index, memory in enumerate(self.memories):
            if memory.get("memoryId") == payload.get("memoryId"):
                self.memories[index] = dict(payload)
                break
        else:
            self.memories.append(dict(payload))
        return {"memoryId": payload.get("memoryId")}


def _store(client: _FakeAgentStatePrismClient, *, persist: bool = False) -> PrismApiStateStore:
    return PrismApiStateStore(client, MemoryStateStore(), persist_agent_memories=persist)


def _graph() -> TaskGraph:
    return TaskGraph(
        plan_id="run-1",
        workspace_id="w1",
        project_id="p1",
        nodes=[SubTask(node_id="a", status=R)],
    )


def test_task_graph_and_results_persist_across_store_instances() -> None:
    client = _FakeAgentStatePrismClient()
    writer = _store(client, persist=True)

    writer.save_task_graph(_graph())
    writer.save_sub_agent_result("w1", "run-1", SubAgentResult(node_id="a", summary="done"))

    # A fresh store (cold fallback) only has what the durable backend replays.
    reader = _store(client)
    restored = reader.get_task_graph("w1", "run-1")
    results = reader.get_sub_agent_results("w1", "run-1")

    assert restored is not None
    assert restored.get_node("a").status == R
    assert [result.node_id for result in results] == ["a"]


def test_advance_reads_latest_durable_graph_and_bumps_version() -> None:
    client = _FakeAgentStatePrismClient()
    _store(client, persist=True).save_task_graph(_graph())

    # A different invocation advances the run off the durable graph.
    advance = _store(client, persist=True).advance_task_graph("w1", "run-1", "a", C)
    assert advance is not None
    assert advance.run_completed

    reread = _store(client).get_task_graph("w1", "run-1")
    assert reread is not None
    assert reread.get_node("a").status == C
    assert reread.version == 1


def test_duplicate_advance_is_idempotent_across_instances() -> None:
    client = _FakeAgentStatePrismClient()
    _store(client, persist=True).save_task_graph(_graph())

    first = _store(client, persist=True).advance_task_graph("w1", "run-1", "a", C)
    second = _store(client, persist=True).advance_task_graph("w1", "run-1", "a", C)

    assert first is not None and first.run_completed
    assert second is not None and second.already_processed
    # The redelivery did not bump the version a second time.
    assert _store(client).get_task_graph("w1", "run-1").version == 1
