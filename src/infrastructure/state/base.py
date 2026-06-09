from __future__ import annotations

from abc import ABC, abstractmethod

from domain.context import ContextSnapshot
from domain.plans import AgentPlan
from domain.results import ToolResult, TraceEvent
from domain.subtasks import SubAgentResult, SubTask, SubTaskStatus, TaskGraph, TaskGraphAdvance


class StateStore(ABC):
    @abstractmethod
    def save_plan(self, plan: AgentPlan) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_plan(self, workspace_id: str, plan_id: str) -> AgentPlan | None:
        raise NotImplementedError

    @abstractmethod
    def save_task_graph(self, graph: TaskGraph) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_task_graph(self, workspace_id: str, plan_id: str) -> TaskGraph | None:
        raise NotImplementedError

    @abstractmethod
    def save_sub_agent_result(self, workspace_id: str, run_id: str, result: SubAgentResult) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_sub_agent_results(self, workspace_id: str, run_id: str) -> list[SubAgentResult]:
        raise NotImplementedError

    @abstractmethod
    def claim_ready_nodes(self, workspace_id: str, run_id: str) -> list[SubTask]:
        """Atomically claim all runnable nodes (PENDING -> RUNNING) and return them."""
        raise NotImplementedError

    @abstractmethod
    def advance_task_graph(
        self, workspace_id: str, run_id: str, node_id: str, status: SubTaskStatus
    ) -> TaskGraphAdvance | None:
        """Atomically record a node's terminal status and return what happens next.

        Returns None when the run's task graph is missing. Must be idempotent so
        duplicate completion events cannot double-advance the graph.
        """
        raise NotImplementedError

    @abstractmethod
    def update_plan(self, plan: AgentPlan) -> None:
        raise NotImplementedError

    @abstractmethod
    def save_context_snapshot(self, snapshot: ContextSnapshot) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_context_snapshot(self, workspace_id: str, snapshot_ref: str) -> ContextSnapshot | None:
        raise NotImplementedError

    @abstractmethod
    def save_action_result(self, result: ToolResult) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_action_result(self, plan_id: str, action_id: str) -> ToolResult | None:
        raise NotImplementedError

    @abstractmethod
    def append_trace_event(self, trace_event: TraceEvent) -> None:
        raise NotImplementedError

    @abstractmethod
    def mark_action_completed(self, workspace_id: str, plan_id: str, action_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def check_idempotency_key(self, key: str) -> bool:
        """Return True when the key has already been consumed."""
        raise NotImplementedError

    @abstractmethod
    def record_idempotency_key(self, key: str) -> None:
        raise NotImplementedError
