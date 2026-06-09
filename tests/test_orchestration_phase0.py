from __future__ import annotations

from application.orchestrator import ROOT_NODE_ID, Orchestrator
from domain.context import AgentContext
from domain.events import DomainEvent, EventEnvelope
from domain.subtasks import SubTask, SubTaskStatus, TaskGraph
from infrastructure.registries.workflow_registry import WorkflowDefinition
from infrastructure.state.memory_state_store import MemoryStateStore


def _context(workspace_id: str = "w1", project_id: str | None = "p1") -> AgentContext:
    event = DomainEvent(
        event_type="story.created", workspace_id=workspace_id, project_id=project_id
    )
    return AgentContext(
        workspace_id=workspace_id,
        project_id=project_id,
        source_event=EventEnvelope.wrap(event),
        workflow_id="story.decompose",
    )


def _workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        id="story.decompose",
        trigger_types=["domain.story.created"],
        required_skills=["task_decomposition", "risk_detection"],
        prompt_id="story.decompose",
        prompt_version="1.0.0",
        goal="Break down the story.",
    )


def test_orchestrator_builds_single_node_graph() -> None:
    graph = Orchestrator().build_task_graph(_context(), _workflow())

    assert len(graph.nodes) == 1
    root = graph.nodes[0]
    assert root.node_id == ROOT_NODE_ID
    assert root.skill_ids == ["task_decomposition", "risk_detection"]
    assert root.context_scope == []
    assert root.model_tier == "pro"
    assert root.depends_on == []
    assert graph.synthesizer_node is None
    assert graph.workspace_id == "w1"
    assert graph.project_id == "p1"


def test_task_graph_ready_nodes_respects_dependencies() -> None:
    graph = TaskGraph(
        workspace_id="w1",
        nodes=[
            SubTask(node_id="a"),
            SubTask(node_id="b", depends_on=["a"]),
        ],
    )

    ready = {node.node_id for node in graph.ready_nodes()}
    assert ready == {"a"}

    completed_a = graph.model_copy(
        update={
            "nodes": [
                graph.nodes[0].model_copy(update={"status": SubTaskStatus.COMPLETED}),
                graph.nodes[1],
            ]
        }
    )
    ready_after = {node.node_id for node in completed_a.ready_nodes()}
    assert ready_after == {"b"}


def test_independent_nodes_are_all_ready() -> None:
    graph = TaskGraph(
        workspace_id="w1",
        nodes=[SubTask(node_id="a"), SubTask(node_id="b"), SubTask(node_id="c")],
    )
    assert {node.node_id for node in graph.ready_nodes()} == {"a", "b", "c"}


def test_memory_state_store_task_graph_round_trip() -> None:
    store = MemoryStateStore()
    graph = TaskGraph(plan_id="run-1", workspace_id="w1", nodes=[SubTask(node_id="root")])

    assert store.get_task_graph("w1", "run-1") is None
    store.save_task_graph(graph)
    loaded = store.get_task_graph("w1", "run-1")

    assert loaded == graph
