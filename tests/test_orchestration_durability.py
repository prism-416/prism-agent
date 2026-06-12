from __future__ import annotations

from domain.context import AgentContext, ContextSnapshot
from domain.events import DomainEvent, EventEnvelope
from domain.plans import AgentPlan, PlanStatus
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
        self.run: dict | None = None

    def get_agent_run_state(self, workspace_id: str, run_id: str) -> dict:
        _ = workspace_id
        return {
            "run": self.run or {"runId": run_id, "status": "running"},
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


def test_plan_and_context_snapshot_persist_across_store_instances() -> None:
    # The queue-separated action invocation runs in a fresh process and must load the
    # plan + context snapshot the pointer invocation produced. Both round-trip via
    # agent memories when persistence is enabled.
    client = _FakeAgentStatePrismClient()
    writer = _store(client, persist=True)

    source = EventEnvelope.wrap(
        DomainEvent(
            event_type="feature.provisioning.requested",
            workspace_id="w1",
            project_id="p1",
        )
    )
    context = AgentContext(
        workspace_id="w1", project_id="p1", source_event=source, workflow_id="wf-1"
    )
    snapshot = ContextSnapshot(
        plan_id="run-1", workspace_id="w1", project_id="p1", workflow_id="wf-1", context=context
    )
    plan = AgentPlan(
        plan_id="run-1",
        source_event_id="evt-1",
        workspace_id="w1",
        project_id="p1",
        goal="provision",
        prompt_id="project_manager",
        prompt_version="1.0.0",
        context_snapshot_ref=snapshot.ref,
    )
    writer.save_plan(plan)
    writer.save_context_snapshot(snapshot)

    # Fresh store (cold fallback): get_plan replays the plan and, as a side effect,
    # the context snapshot into the fallback store the action handler reads from.
    reader = _store(client)
    restored_plan = reader.get_plan("w1", "run-1")
    restored_snapshot = reader.get_context_snapshot("w1", snapshot.ref)

    assert restored_plan is not None
    assert restored_plan.context_snapshot_ref == snapshot.ref
    assert restored_snapshot is not None
    assert restored_snapshot.context.workflow_id == "wf-1"


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


def test_run_state_with_work_item_identity_fields_hydrates_without_error() -> None:
    # The API now stamps the linked work item's readable identity onto every serialized
    # AgentRun (workItemCode / workItemTitle / projectId) for the agent overview UI. These
    # are server-derived and ride on the run object the /state endpoint returns. The runtime
    # consumes that run object as an opaque dict, so the added keys must pass through while
    # run-status hydration still works.
    client = _FakeAgentStatePrismClient()
    client.run = {
        "runId": "run-1",
        "workspaceId": "w1",
        "status": "completed",
        "workItemId": "11111111-1111-1111-1111-111111111111",
        "workItemCode": "PRSM-001",
        "workItemTitle": "Add SSO login",
        "projectId": "22222222-2222-2222-2222-222222222222",
    }
    writer = _store(client, persist=True)
    plan = AgentPlan(
        plan_id="run-1",
        source_event_id="evt-1",
        workspace_id="w1",
        project_id="p1",
        goal="provision",
        prompt_id="project_manager",
        prompt_version="1.0.0",
        context_snapshot_ref="ctx-1",
    )
    writer.save_plan(plan)

    restored = _store(client).get_plan("w1", "run-1")

    assert restored is not None
    # The enriched run object did not break deserialization; status still hydrated from it.
    assert restored.status == PlanStatus.COMPLETED


def test_run_state_with_null_work_item_identity_hydrates_without_error() -> None:
    # Workflow runs (e.g. refine_backlog) have no linked work item, so the identity fields
    # arrive as explicit null. The runtime must tolerate them just the same.
    client = _FakeAgentStatePrismClient()
    client.run = {
        "runId": "run-1",
        "workspaceId": "w1",
        "status": "failed",
        "workItemId": None,
        "workItemCode": None,
        "workItemTitle": None,
        "projectId": None,
    }
    writer = _store(client, persist=True)
    plan = AgentPlan(
        plan_id="run-1",
        source_event_id="evt-1",
        workspace_id="w1",
        project_id="p1",
        goal="refine backlog",
        prompt_id="project_manager",
        prompt_version="1.0.0",
        context_snapshot_ref="ctx-1",
    )
    writer.save_plan(plan)

    restored = _store(client).get_plan("w1", "run-1")

    assert restored is not None
    assert restored.status == PlanStatus.FAILED


def test_duplicate_advance_is_idempotent_across_instances() -> None:
    client = _FakeAgentStatePrismClient()
    _store(client, persist=True).save_task_graph(_graph())

    first = _store(client, persist=True).advance_task_graph("w1", "run-1", "a", C)
    second = _store(client, persist=True).advance_task_graph("w1", "run-1", "a", C)

    assert first is not None and first.run_completed
    assert second is not None and second.already_processed
    # The redelivery did not bump the version a second time.
    assert _store(client).get_task_graph("w1", "run-1").version == 1
