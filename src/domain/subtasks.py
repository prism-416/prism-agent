from __future__ import annotations

from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class SubTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SubTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    skill_ids: list[str] = Field(default_factory=list)
    objective: str = ""
    depends_on: list[str] = Field(default_factory=list)
    context_scope: list[str] = Field(default_factory=list)
    model_tier: str = "pro"
    status: SubTaskStatus = SubTaskStatus.PENDING

    def is_unblocked(self, completed: set[str]) -> bool:
        return set(self.depends_on) <= completed


class TaskGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    workspace_id: str
    project_id: str | None = None
    context_snapshot_ref: str | None = None
    version: int = 0
    nodes: list[SubTask] = Field(default_factory=list)
    synthesizer_node: SubTask | None = None

    def bumped(self) -> TaskGraph:
        """Return a copy with the optimistic-lock version incremented."""
        return self.model_copy(update={"version": self.version + 1})

    def get_node(self, node_id: str) -> SubTask | None:
        node = next((node for node in self.nodes if node.node_id == node_id), None)
        if node is not None:
            return node
        if self.synthesizer_node is not None and self.synthesizer_node.node_id == node_id:
            return self.synthesizer_node
        return node

    def with_node_status(self, node_id: str, status: SubTaskStatus) -> TaskGraph:
        nodes = [
            node.model_copy(update={"status": status}) if node.node_id == node_id else node
            for node in self.nodes
        ]
        synthesizer = self.synthesizer_node
        if synthesizer is not None and synthesizer.node_id == node_id:
            synthesizer = synthesizer.model_copy(update={"status": status})
        return self.model_copy(update={"nodes": nodes, "synthesizer_node": synthesizer})

    def all_nodes_completed(self) -> bool:
        return bool(self.nodes) and all(
            node.status == SubTaskStatus.COMPLETED for node in self.nodes
        )

    def is_synthesizer(self, node_id: str) -> bool:
        return self.synthesizer_node is not None and self.synthesizer_node.node_id == node_id

    def claim_ready(self) -> tuple[TaskGraph, list[SubTask]]:
        """Atomically transition every runnable PENDING node to RUNNING.

        Returns the updated graph and the claimed nodes. Claiming up front is what
        makes parallel fan-out safe: a node already RUNNING is never picked again.
        """
        ready = self.ready_nodes()
        graph = self
        for node in ready:
            graph = graph.with_node_status(node.node_id, SubTaskStatus.RUNNING)
        return graph, [graph.get_node(node.node_id) for node in ready]

    def plan_advance(self, node_id: str, status: SubTaskStatus) -> TaskGraphAdvance:
        """Record a node's terminal status and decide what happens next.

        Pure and idempotent: re-applying a status a node already holds is a no-op,
        so duplicate completion events (at-least-once delivery) cannot double-dispatch
        the synthesizer or re-finalize the run. Newly runnable nodes and the
        synthesizer are claimed (set RUNNING) in the returned graph.
        """
        node = self.get_node(node_id)
        if node is None:
            return TaskGraphAdvance(graph=self)
        if node.status == status:
            return TaskGraphAdvance(graph=self, already_processed=True)

        graph = self.with_node_status(node_id, status)
        if status == SubTaskStatus.FAILED:
            return TaskGraphAdvance(graph=graph, run_failed=True)
        if status != SubTaskStatus.COMPLETED:
            return TaskGraphAdvance(graph=graph)

        if graph.is_synthesizer(node_id):
            return TaskGraphAdvance(graph=graph, is_synthesizer=True, run_completed=True)

        completed = graph.completed_node_ids()
        ready = graph.ready_nodes(completed)
        if ready:
            for ready_node in ready:
                graph = graph.with_node_status(ready_node.node_id, SubTaskStatus.RUNNING)
            return TaskGraphAdvance(
                graph=graph,
                newly_ready=[graph.get_node(n.node_id) for n in ready],
            )

        if graph.all_nodes_completed():
            synthesizer = graph.synthesizer_node
            if synthesizer is not None and synthesizer.status == SubTaskStatus.PENDING:
                graph = graph.with_node_status(synthesizer.node_id, SubTaskStatus.RUNNING)
                return TaskGraphAdvance(graph=graph, dispatch_synthesizer=graph.synthesizer_node)
            if synthesizer is not None and synthesizer.status != SubTaskStatus.COMPLETED:
                return TaskGraphAdvance(graph=graph)
            return TaskGraphAdvance(graph=graph, run_completed=True)

        if any(node.status == SubTaskStatus.RUNNING for node in graph.nodes):
            return TaskGraphAdvance(graph=graph)
        return TaskGraphAdvance(graph=graph, blocked=True)

    def completed_node_ids(self) -> set[str]:
        return {node.node_id for node in self.nodes if node.status == SubTaskStatus.COMPLETED}

    def ready_nodes(self, completed: set[str] | None = None) -> list[SubTask]:
        completed = self.completed_node_ids() if completed is None else completed
        return [
            node
            for node in self.nodes
            if node.status == SubTaskStatus.PENDING and node.is_unblocked(completed)
        ]


class SubAgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    skill_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    structured_output: dict = Field(default_factory=dict)
    status: SubTaskStatus = SubTaskStatus.COMPLETED


class TaskGraphAdvance(BaseModel):
    """Outcome of recording one node's terminal status against the task graph."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    graph: TaskGraph
    newly_ready: list[SubTask] = Field(default_factory=list)
    dispatch_synthesizer: SubTask | None = None
    run_completed: bool = False
    run_failed: bool = False
    blocked: bool = False
    is_synthesizer: bool = False
    already_processed: bool = False
