from __future__ import annotations

import hashlib
import json
from typing import Any

from domain.actions import ActionStatus
from domain.context import ContextSnapshot
from domain.plans import AgentPlan
from domain.results import ToolResult, TraceEvent
from infrastructure.object_storage.oci_client import build_object_storage_client
from infrastructure.state.base import StateStore


class ObjectStorageStateStore(StateStore):
    """OCI Object Storage state adapter.

    Object keys:
    - plans/{workspace_id}/{plan_id}.json
    - contexts/{workspace_id}/{snapshot_id}.json
    - actions/{plan_id}/{action_id}.json
    - traces/{workspace_id}/{plan_id}.jsonl
    - idempotency/{sha256}.json
    """

    def __init__(self, namespace: str | None, bucket_name: str | None) -> None:
        if not namespace or not bucket_name:
            raise ValueError(
                "OCI_NAMESPACE and OCI_BUCKET_NAME are required for object storage state."
            )
        self.namespace = namespace
        self.bucket_name = bucket_name
        self._client: Any | None = None

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = build_object_storage_client()
        return self._client

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
        self._put_text(self.plan_key(plan.workspace_id, plan.plan_id), plan.model_dump_json())

    def get_plan(self, workspace_id: str, plan_id: str) -> AgentPlan | None:
        raw = self._get_text(self.plan_key(workspace_id, plan_id))
        return None if raw is None else AgentPlan.model_validate_json(raw)

    def update_plan(self, plan: AgentPlan) -> None:
        self.save_plan(plan)

    def save_context_snapshot(self, snapshot: ContextSnapshot) -> None:
        self._put_text(
            self.context_key(snapshot.workspace_id, snapshot.snapshot_id),
            snapshot.model_dump_json(),
        )

    def get_context_snapshot(self, workspace_id: str, snapshot_ref: str) -> ContextSnapshot | None:
        raw = self._get_text(self.context_key(workspace_id, snapshot_ref))
        return None if raw is None else ContextSnapshot.model_validate_json(raw)

    def save_action_result(self, result: ToolResult) -> None:
        self._put_text(
            self.action_result_key(result.plan_id, result.action_id),
            result.model_dump_json(),
        )

    def get_action_result(self, plan_id: str, action_id: str) -> ToolResult | None:
        raw = self._get_text(self.action_result_key(plan_id, action_id))
        return None if raw is None else ToolResult.model_validate_json(raw)

    def append_trace_event(self, trace_event: TraceEvent) -> None:
        key = self.trace_key(trace_event.workspace_id, trace_event.plan_id or "events")
        existing = self._get_text(key) or ""
        self._put_text(key, f"{existing}{trace_event.model_dump_json()}\n")

    def mark_action_completed(self, workspace_id: str, plan_id: str, action_id: str) -> None:
        plan = self.get_plan(workspace_id, plan_id)
        if plan is None:
            return
        action = plan.get_action(action_id)
        if action is None:
            return
        self.update_plan(plan.replace_action(action.with_status(ActionStatus.COMPLETED)))

    def check_idempotency_key(self, key: str) -> bool:
        return self._exists(self.idempotency_key(key))

    def record_idempotency_key(self, key: str) -> None:
        self._put_text(self.idempotency_key(key), json.dumps({"key": key}))

    @staticmethod
    def action_result_key(plan_id: str, action_id: str) -> str:
        return f"actions/{plan_id}/{action_id}.json"

    @staticmethod
    def idempotency_key(key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"idempotency/{digest}.json"

    def _put_text(self, object_name: str, content: str) -> None:
        self.client.put_object(
            self.namespace,
            self.bucket_name,
            object_name,
            content.encode("utf-8"),
        )

    def _get_text(self, object_name: str) -> str | None:
        try:
            response = self.client.get_object(self.namespace, self.bucket_name, object_name)
        except Exception as exc:
            if _is_oci_not_found(exc):
                return None
            raise
        data = response.data
        content = getattr(data, "content", None)
        if isinstance(content, bytes):
            return content.decode("utf-8")
        if isinstance(content, str):
            return content
        if hasattr(data, "read"):
            value = data.read()
            if isinstance(value, bytes):
                return value.decode("utf-8")
            return str(value)
        if isinstance(data, bytes):
            return data.decode("utf-8")
        if isinstance(data, str):
            return data
        raise TypeError("Unsupported OCI object response data type.")

    def _exists(self, object_name: str) -> bool:
        try:
            self.client.head_object(self.namespace, self.bucket_name, object_name)
        except Exception as exc:
            if _is_oci_not_found(exc):
                return False
            raise
        return True


def _is_oci_not_found(exc: Exception) -> bool:
    return getattr(exc, "status", None) == 404
