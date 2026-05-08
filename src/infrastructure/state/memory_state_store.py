from __future__ import annotations

from domain.actions import ActionStatus
from domain.context import ContextSnapshot
from domain.plans import AgentPlan
from domain.results import ToolResult, TraceEvent
from infrastructure.state.base import StateStore


class MemoryStateStore(StateStore):
    def __init__(self) -> None:
        self.plans: dict[tuple[str, str], AgentPlan] = {}
        self.context_snapshots: dict[tuple[str, str], ContextSnapshot] = {}
        self.action_results: dict[tuple[str, str], ToolResult] = {}
        self.traces: list[TraceEvent] = []
        self.idempotency_keys: set[str] = set()

    def save_plan(self, plan: AgentPlan) -> None:
        self.plans[(plan.workspace_id, plan.plan_id)] = plan

    def get_plan(self, workspace_id: str, plan_id: str) -> AgentPlan | None:
        return self.plans.get((workspace_id, plan_id))

    def update_plan(self, plan: AgentPlan) -> None:
        self.save_plan(plan)

    def save_context_snapshot(self, snapshot: ContextSnapshot) -> None:
        self.context_snapshots[(snapshot.workspace_id, snapshot.snapshot_id)] = snapshot

    def get_context_snapshot(self, workspace_id: str, snapshot_ref: str) -> ContextSnapshot | None:
        return self.context_snapshots.get((workspace_id, snapshot_ref))

    def save_action_result(self, result: ToolResult) -> None:
        self.action_results[(result.plan_id, result.action_id)] = result

    def get_action_result(self, plan_id: str, action_id: str) -> ToolResult | None:
        return self.action_results.get((plan_id, action_id))

    def append_trace_event(self, trace_event: TraceEvent) -> None:
        self.traces.append(trace_event)

    def mark_action_completed(self, workspace_id: str, plan_id: str, action_id: str) -> None:
        plan = self.get_plan(workspace_id, plan_id)
        if plan is None:
            return
        action = plan.get_action(action_id)
        if action is None:
            return
        self.update_plan(plan.replace_action(action.with_status(ActionStatus.COMPLETED)))

    def check_idempotency_key(self, key: str) -> bool:
        return key in self.idempotency_keys

    def record_idempotency_key(self, key: str) -> None:
        self.idempotency_keys.add(key)
