from __future__ import annotations

from domain.context import ContextSnapshot
from domain.plans import AgentPlan
from domain.results import ToolResult, TraceEvent
from infrastructure.state.base import StateStore


class ObjectStorageStateStore(StateStore):
    """OCI Object Storage state adapter skeleton.

    Object keys:
    - plans/{workspace_id}/{plan_id}.json
    - contexts/{workspace_id}/{plan_id}.json
    - actions/{workspace_id}/{plan_id}/{action_id}.json
    - traces/{workspace_id}/{plan_id}.jsonl
    """

    def __init__(self, namespace: str | None, bucket_name: str | None) -> None:
        if not namespace or not bucket_name:
            raise ValueError(
                "OCI_NAMESPACE and OCI_BUCKET_NAME are required for object storage state."
            )
        self.namespace = namespace
        self.bucket_name = bucket_name

    @staticmethod
    def plan_key(workspace_id: str, plan_id: str) -> str:
        return f"plans/{workspace_id}/{plan_id}.json"

    @staticmethod
    def context_key(workspace_id: str, snapshot_ref: str) -> str:
        return f"contexts/{workspace_id}/{snapshot_ref}.json"

    @staticmethod
    def action_key(workspace_id: str, plan_id: str, action_id: str) -> str:
        return f"actions/{workspace_id}/{plan_id}/{action_id}.json"

    @staticmethod
    def trace_key(workspace_id: str, plan_id: str) -> str:
        return f"traces/{workspace_id}/{plan_id}.jsonl"

    def save_plan(self, plan: AgentPlan) -> None:
        _ = plan.model_dump_json()
        raise NotImplementedError("Object Storage save_plan is an adapter skeleton.")

    def get_plan(self, workspace_id: str, plan_id: str) -> AgentPlan | None:
        _ = self.plan_key(workspace_id, plan_id)
        raise NotImplementedError("Object Storage get_plan is an adapter skeleton.")

    def update_plan(self, plan: AgentPlan) -> None:
        self.save_plan(plan)

    def save_context_snapshot(self, snapshot: ContextSnapshot) -> None:
        _ = snapshot.model_dump_json()
        raise NotImplementedError("Object Storage save_context_snapshot is an adapter skeleton.")

    def get_context_snapshot(self, workspace_id: str, snapshot_ref: str) -> ContextSnapshot | None:
        _ = self.context_key(workspace_id, snapshot_ref)
        raise NotImplementedError("Object Storage get_context_snapshot is an adapter skeleton.")

    def save_action_result(self, result: ToolResult) -> None:
        _ = result.model_dump_json()
        raise NotImplementedError("Object Storage save_action_result is an adapter skeleton.")

    def get_action_result(self, plan_id: str, action_id: str) -> ToolResult | None:
        _ = (plan_id, action_id)
        raise NotImplementedError("Object Storage get_action_result is an adapter skeleton.")

    def append_trace_event(self, trace_event: TraceEvent) -> None:
        _ = trace_event.model_dump_json()
        raise NotImplementedError("Object Storage append_trace_event is an adapter skeleton.")

    def mark_action_completed(self, workspace_id: str, plan_id: str, action_id: str) -> None:
        _ = (workspace_id, plan_id, action_id)
        raise NotImplementedError("Object Storage mark_action_completed is an adapter skeleton.")

    def check_idempotency_key(self, key: str) -> bool:
        _ = key
        raise NotImplementedError("Object Storage idempotency lookup is an adapter skeleton.")

    def record_idempotency_key(self, key: str) -> None:
        _ = key
        raise NotImplementedError("Object Storage idempotency recording is an adapter skeleton.")
