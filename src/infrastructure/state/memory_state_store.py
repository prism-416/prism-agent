from __future__ import annotations

from domain.actions import ActionStatus
from domain.context import ContextSnapshot
from domain.plans import AgentPlan
from domain.results import ToolResult, TraceEvent
from domain.subtasks import (
    SubAgentResult,
    SubTask,
    SubTaskStatus,
    TaskGraph,
    TaskGraphAdvance,
)
from infrastructure.state.base import StateStore


class MemoryStateStore(StateStore):
    def __init__(self) -> None:
        self.plans: dict[tuple[str, str], AgentPlan] = {}
        self.task_graphs: dict[tuple[str, str], TaskGraph] = {}
        self.sub_agent_results: dict[tuple[str, str], dict[str, SubAgentResult]] = {}
        self.context_snapshots: dict[tuple[str, str], ContextSnapshot] = {}
        self.action_results: dict[tuple[str, str], ToolResult] = {}
        self.traces: list[TraceEvent] = []
        self.idempotency_keys: set[str] = set()

    def save_plan(self, plan: AgentPlan) -> None:
        self.plans[(plan.workspace_id, plan.plan_id)] = plan

    def get_plan(self, workspace_id: str, plan_id: str) -> AgentPlan | None:
        return self.plans.get((workspace_id, plan_id))

    def save_task_graph(self, graph: TaskGraph) -> None:
        self.task_graphs[(graph.workspace_id, graph.plan_id)] = graph

    def get_task_graph(self, workspace_id: str, plan_id: str) -> TaskGraph | None:
        return self.task_graphs.get((workspace_id, plan_id))

    def save_sub_agent_result(self, workspace_id: str, run_id: str, result: SubAgentResult) -> None:
        self.sub_agent_results.setdefault((workspace_id, run_id), {})[result.node_id] = result

    def get_sub_agent_results(self, workspace_id: str, run_id: str) -> list[SubAgentResult]:
        return list(self.sub_agent_results.get((workspace_id, run_id), {}).values())

    def claim_ready_nodes(self, workspace_id: str, run_id: str) -> list[SubTask]:
        graph = self.get_task_graph(workspace_id, run_id)
        if graph is None:
            return []
        graph, claimed = graph.claim_ready()
        self.save_task_graph(graph)
        return claimed

    def advance_task_graph(
        self, workspace_id: str, run_id: str, node_id: str, status: SubTaskStatus
    ) -> TaskGraphAdvance | None:
        graph = self.get_task_graph(workspace_id, run_id)
        if graph is None:
            return None
        advance = graph.plan_advance(node_id, status)
        self.save_task_graph(advance.graph)
        return advance

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
