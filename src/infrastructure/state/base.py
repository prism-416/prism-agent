from __future__ import annotations

from abc import ABC, abstractmethod

from domain.context import ContextSnapshot
from domain.plans import AgentPlan
from domain.results import ToolResult, TraceEvent


class StateStore(ABC):
    @abstractmethod
    def save_plan(self, plan: AgentPlan) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_plan(self, workspace_id: str, plan_id: str) -> AgentPlan | None:
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
