from __future__ import annotations

from app.container import build_container
from application.agent_run_sync import AgentRunSync
from application.subagent_coordinator import SubAgentCoordinator
from application.subagent_runner import SubAgentRunner, sub_plan_id
from domain.actions import PlannedAction
from domain.context import AgentContext, ContextSnapshot
from domain.events import (
    AgentActionEvent,
    EventEnvelope,
    ScheduledEvent,
    SubAgentCompletedEvent,
    SubAgentTaskEvent,
)
from domain.plans import AgentPlan
from domain.subtasks import SubTask, SubTaskStatus, TaskGraph
from infrastructure.config.settings import Settings
from infrastructure.prism_api.client import PrismApiClient
from infrastructure.queue.memory_queue import MemoryQueue
from infrastructure.registries.workflow_registry import WorkflowRegistry
from infrastructure.state.memory_state_store import MemoryStateStore

C = SubTaskStatus.COMPLETED
F = SubTaskStatus.FAILED
R = SubTaskStatus.RUNNING
P = SubTaskStatus.PENDING


def _graph(nodes: list[SubTask], synth: SubTask | None = None) -> TaskGraph:
    return TaskGraph(plan_id="run-1", workspace_id="w1", nodes=nodes, synthesizer_node=synth)


# -- pure atomic-advance logic ---------------------------------------------


def test_claim_ready_claims_all_independent_nodes() -> None:
    graph, claimed = _graph([SubTask(node_id="a"), SubTask(node_id="b")]).claim_ready()
    assert {node.node_id for node in claimed} == {"a", "b"}
    assert graph.get_node("a").status == R
    assert graph.get_node("b").status == R


def test_claim_ready_respects_dependencies() -> None:
    graph = _graph([SubTask(node_id="a"), SubTask(node_id="b", depends_on=["a"])])
    _, claimed = graph.claim_ready()
    assert {node.node_id for node in claimed} == {"a"}


def test_advance_waits_for_in_flight_sibling_then_completes() -> None:
    graph = _graph([SubTask(node_id="a", status=R), SubTask(node_id="b", status=R)])

    first = graph.plan_advance("a", C)
    assert first.newly_ready == []
    assert not first.run_completed
    assert first.dispatch_synthesizer is None

    second = first.graph.plan_advance("b", C)
    assert second.run_completed


def test_advance_dispatches_synthesizer_exactly_once() -> None:
    synth = SubTask(node_id="synthesizer")
    graph = _graph([SubTask(node_id="a", status=R), SubTask(node_id="b", status=R)], synth)

    after_a = graph.plan_advance("a", C)
    assert after_a.dispatch_synthesizer is None  # b still running

    after_b = after_a.graph.plan_advance("b", C)
    assert after_b.dispatch_synthesizer is not None
    assert after_b.dispatch_synthesizer.node_id == "synthesizer"
    assert after_b.graph.synthesizer_node.status == R  # claimed, not re-dispatchable

    after_synth = after_b.graph.plan_advance("synthesizer", C)
    assert after_synth.run_completed
    assert after_synth.is_synthesizer


def test_advance_is_idempotent_on_duplicate_completion() -> None:
    graph = _graph([SubTask(node_id="a", status=R)])
    first = graph.plan_advance("a", C)
    assert first.run_completed

    duplicate = first.graph.plan_advance("a", C)
    assert duplicate.already_processed
    assert not duplicate.run_completed
    assert duplicate.dispatch_synthesizer is None


def test_advance_failed_node_fails_run() -> None:
    graph = _graph([SubTask(node_id="a", status=R), SubTask(node_id="b", status=R)])
    advance = graph.plan_advance("a", F)
    assert advance.run_failed
    assert advance.graph.get_node("a").status == F


def test_advance_diamond_dag_joins_at_leaves() -> None:
    graph = _graph(
        [
            SubTask(node_id="a", status=R),
            SubTask(node_id="b", depends_on=["a"]),
            SubTask(node_id="c", depends_on=["a"]),
        ]
    )

    after_a = graph.plan_advance("a", C)
    assert {node.node_id for node in after_a.newly_ready} == {"b", "c"}
    assert after_a.graph.get_node("b").status == R
    assert after_a.graph.get_node("c").status == R

    after_b = after_a.graph.plan_advance("b", C)
    assert after_b.newly_ready == []
    assert not after_b.run_completed

    after_c = after_b.graph.plan_advance("c", C)
    assert after_c.run_completed


def test_advance_blocked_when_dependency_never_satisfiable() -> None:
    graph = _graph([SubTask(node_id="a", status=R), SubTask(node_id="b", depends_on=["missing"])])
    advance = graph.plan_advance("a", C)
    assert advance.blocked


# -- parallel fan-out end to end -------------------------------------------


def test_initial_dispatch_fans_out_all_ready_nodes() -> None:
    container = build_container(
        Settings(
            app_env="local",
            state_backend="memory",
            queue_backend="memory",
            max_recursion_depth=30,
        )
    )
    traces = container.recursion_runner.run(
        ScheduledEvent(event_type="sprint_report_generation", workspace_id="w1", project_id="p1")
    )

    dispatched = [
        (i, t.data.get("node_id"))
        for i, t in enumerate(traces)
        if t.event_name == "subagent.dispatched"
    ]
    started = [i for i, t in enumerate(traces) if t.event_name == "subagent.started"]
    first_started = min(started)

    by_node = {node: i for i, node in dispatched}
    # Both leaf nodes are dispatched up front, before either subagent begins running.
    assert by_node["summary"] < first_started
    assert by_node["risk"] < first_started


# -- idempotent duplicate completion through the coordinator ----------------


def _coordinator(store: MemoryStateStore, queue: MemoryQueue) -> SubAgentCoordinator:
    return SubAgentCoordinator(
        store,
        queue,
        SubAgentRunner(planner=None),
        AgentRunSync(PrismApiClient(), enabled=False),
        WorkflowRegistry.with_defaults(),
    )


def _seed_graph(store: MemoryStateStore) -> None:
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
    store.save_task_graph(
        TaskGraph(
            plan_id="run-1",
            workspace_id="w1",
            project_id="p1",
            context_snapshot_ref=snapshot.ref,
            nodes=[
                SubTask(node_id="summary", status=R),
                SubTask(node_id="risk", status=R),
            ],
        )
    )


def test_coordinator_ignores_duplicate_completion() -> None:
    store = MemoryStateStore()
    queue = MemoryQueue()
    _seed_graph(store)
    coordinator = _coordinator(store, queue)

    event = EventEnvelope.wrap(
        SubAgentCompletedEvent(
            workspace_id="w1",
            project_id="p1",
            plan_id="run-1",
            node_id="summary",
            status="completed",
        )
    )
    coordinator.handle(event)
    coordinator.handle(event)  # duplicate delivery

    assert any(trace.event_name == "orchestration.duplicate" for trace in store.traces)
    assert store.get_task_graph("w1", "run-1").get_node("summary").status == C


def test_coordinator_resumes_existing_subplan_on_duplicate_task() -> None:
    store = MemoryStateStore()
    queue = MemoryQueue()
    store.save_task_graph(
        TaskGraph(
            plan_id="run-1",
            workspace_id="w1",
            project_id="p1",
            nodes=[SubTask(node_id="summary", status=R)],
        )
    )
    sub_plan = AgentPlan(
        plan_id=sub_plan_id("run-1", "summary"),
        parent_run_id="run-1",
        node_id="summary",
        source_event_id="evt-1",
        workspace_id="w1",
        project_id="p1",
        goal="Summarize sprint.",
        prompt_id="sprint.report",
        prompt_version="1.0.0",
        context_snapshot_ref="snapshot-1",
        actions=[
            PlannedAction(
                plan_id=sub_plan_id("run-1", "summary"),
                action_type="read",
                tool_name="summarize_project",
                instruction="Summarize the project.",
                idempotency_key="run-1:summary:action-1",
            )
        ],
    )
    store.save_plan(sub_plan)

    _coordinator(store, queue).handle(
        EventEnvelope.wrap(
            SubAgentTaskEvent(
                workspace_id="w1",
                project_id="p1",
                plan_id="run-1",
                node_id="summary",
            )
        )
    )

    message = queue.dequeue()
    assert message is not None
    action_event = message.envelope.event
    assert isinstance(action_event, AgentActionEvent)
    assert action_event.plan_id == sub_plan.plan_id
    assert action_event.action_id == sub_plan.actions[0].action_id
    assert any(trace.event_name == "subagent.resumed" for trace in store.traces)
