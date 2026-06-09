from __future__ import annotations

from app.container import build_container
from application.agent_run_sync import AgentRunSync
from application.subagent_coordinator import SubAgentCoordinator
from application.subagent_runner import SubAgentRunner, sub_plan_id
from domain.context import AgentContext, ContextSnapshot
from domain.events import EventEnvelope, ScheduledEvent, SubAgentCompletedEvent
from domain.subtasks import SubTask, SubTaskStatus, TaskGraph
from infrastructure.config.settings import Settings
from infrastructure.prism_api.client import PrismApiClient
from infrastructure.queue.memory_queue import MemoryQueue
from infrastructure.registries.workflow_registry import WorkflowRegistry
from infrastructure.state.memory_state_store import MemoryStateStore


def _local_container():
    return build_container(
        Settings(
            app_env="local",
            state_backend="memory",
            queue_backend="memory",
            max_recursion_depth=30,
        )
    )


def test_orchestrated_sprint_report_runs_nodes_and_synthesizer() -> None:
    container = _local_container()
    event = ScheduledEvent(
        event_type="sprint_report_generation", workspace_id="w1", project_id="p1"
    )

    traces = container.recursion_runner.run(event)
    names = [trace.event_name for trace in traces]

    assert "orchestration.started" in names
    started = next(trace for trace in traces if trace.event_name == "orchestration.started")
    run_id = started.plan_id
    assert run_id is not None

    started_nodes = {
        trace.data.get("node_id") for trace in traces if trace.event_name == "subagent.started"
    }
    assert {"summary", "risk", "synthesizer"} <= started_nodes

    assert "orchestration.completed" in names
    assert names.count("action.completed") >= 3

    # Each leaf node produced and stored a result for the synthesizer to reduce.
    results = {r.node_id for r in container.state_store.get_sub_agent_results("w1", run_id)}
    assert {"summary", "risk"} <= results

    # Sub-plans are node-qualified and linked back to the shared run.
    summary_plan = container.state_store.get_plan("w1", sub_plan_id(run_id, "summary"))
    assert summary_plan is not None
    assert summary_plan.parent_run_id == run_id
    assert summary_plan.node_id == "summary"


def test_orchestrated_run_scopes_skills_per_node() -> None:
    container = _local_container()
    event = ScheduledEvent(
        event_type="sprint_report_generation", workspace_id="w1", project_id="p1"
    )

    traces = container.recursion_runner.run(event)
    run_id = next(
        trace for trace in traces if trace.event_name == "orchestration.started"
    ).plan_id

    summary_plan = container.state_store.get_plan("w1", sub_plan_id(run_id, "summary"))
    risk_plan = container.state_store.get_plan("w1", sub_plan_id(run_id, "risk"))
    assert summary_plan.skill_ids == ["project_summary"]
    assert risk_plan.skill_ids == ["risk_detection"]


def _coordinator(store: MemoryStateStore, queue: MemoryQueue) -> SubAgentCoordinator:
    return SubAgentCoordinator(
        store,
        queue,
        SubAgentRunner(planner=None),  # completion handling never plans
        AgentRunSync(PrismApiClient(), enabled=False),
        WorkflowRegistry.with_defaults(),
    )


def _saved_graph(store: MemoryStateStore) -> TaskGraph:
    source = EventEnvelope.wrap(
        ScheduledEvent(event_type="sprint_report_generation", workspace_id="w1", project_id="p1")
    )
    snapshot = ContextSnapshot(
        plan_id="run-1",
        workspace_id="w1",
        project_id="p1",
        workflow_id="sprint.report",
        context=AgentContext(
            workspace_id="w1",
            project_id="p1",
            source_event=source,
            workflow_id="sprint.report",
        ),
    )
    store.save_context_snapshot(snapshot)
    graph = TaskGraph(
        plan_id="run-1",
        workspace_id="w1",
        project_id="p1",
        context_snapshot_ref=snapshot.ref,
        nodes=[SubTask(node_id="summary", skill_ids=["project_summary"]), SubTask(node_id="risk")],
        synthesizer_node=SubTask(node_id="synthesizer", skill_ids=["project_summary"]),
    )
    store.save_task_graph(graph)
    return graph


def _completed_event(node_id: str, status: str) -> EventEnvelope:
    return EventEnvelope.wrap(
        SubAgentCompletedEvent(
            workspace_id="w1",
            project_id="p1",
            plan_id="run-1",
            node_id=node_id,
            status=status,
        )
    )


def test_coordinator_advances_to_next_ready_node() -> None:
    store = MemoryStateStore()
    queue = MemoryQueue()
    _saved_graph(store)

    _coordinator(store, queue).handle(_completed_event("summary", "completed"))

    dispatched = queue.dequeue()
    assert dispatched is not None
    assert dispatched.envelope.event.node_id == "risk"
    graph = store.get_task_graph("w1", "run-1")
    assert graph.get_node("summary").status == SubTaskStatus.COMPLETED


def test_coordinator_dispatches_synthesizer_after_all_nodes() -> None:
    store = MemoryStateStore()
    queue = MemoryQueue()
    graph = _saved_graph(store)
    store.save_task_graph(graph.with_node_status("summary", SubTaskStatus.COMPLETED))

    _coordinator(store, queue).handle(_completed_event("risk", "completed"))

    dispatched = queue.dequeue()
    assert dispatched is not None
    assert dispatched.envelope.event.node_id == "synthesizer"


def test_coordinator_completes_run_after_synthesizer() -> None:
    store = MemoryStateStore()
    queue = MemoryQueue()
    graph = _saved_graph(store)
    graph = graph.with_node_status("summary", SubTaskStatus.COMPLETED)
    graph = graph.with_node_status("risk", SubTaskStatus.COMPLETED)
    store.save_task_graph(graph)

    _coordinator(store, queue).handle(_completed_event("synthesizer", "completed"))

    assert queue.is_empty()
    assert any(trace.event_name == "orchestration.completed" for trace in store.traces)


def test_coordinator_fails_run_on_failed_node() -> None:
    store = MemoryStateStore()
    queue = MemoryQueue()
    _saved_graph(store)

    _coordinator(store, queue).handle(_completed_event("summary", "failed"))

    assert queue.is_empty()
    assert any(trace.event_name == "orchestration.failed" for trace in store.traces)
    assert store.get_task_graph("w1", "run-1").get_node("summary").status == SubTaskStatus.FAILED


def test_coordinator_pauses_run_on_waiting_node() -> None:
    store = MemoryStateStore()
    queue = MemoryQueue()
    _saved_graph(store)

    _coordinator(store, queue).handle(_completed_event("summary", "waiting_for_approval"))

    assert queue.is_empty()
    assert any(trace.event_name == "orchestration.waiting" for trace in store.traces)
    assert not any(trace.event_name == "orchestration.completed" for trace in store.traces)
