from __future__ import annotations

import hashlib
import json
from typing import Any

from domain.actions import ActionStatus
from domain.agent_run import action_api_id
from domain.context import ContextSnapshot
from domain.events import utc_now
from domain.plans import AgentPlan, PlanStatus
from domain.results import ToolResult, TraceEvent
from infrastructure.prism_api.client import PrismApiClient, PrismApiNotFoundError
from infrastructure.state.base import StateStore
from infrastructure.state.memory_state_store import MemoryStateStore

_STATE_SCHEMA = "prism_agent_state.v1"
_FEATURE_PROVISIONING_IDEMPOTENCY_PREFIX = "feature_provisioning:"


class PrismApiStateStore(StateStore):
    """State adapter that hydrates plan action state from Prism's DB-backed API."""

    def __init__(
        self,
        prism_client: PrismApiClient,
        fallback_store: StateStore | None = None,
        *,
        persist_agent_memories: bool = False,
    ) -> None:
        if not prism_client.is_configured:
            raise ValueError("PRISM_API_BASE_URL is required when STATE_BACKEND=prism_api.")
        self.prism_client = prism_client
        self.fallback_store = fallback_store or MemoryStateStore()
        self.persist_agent_memories = persist_agent_memories
        self._idempotency_locations: dict[str, tuple[str, str]] = {}

    @property
    def traces(self) -> list[TraceEvent]:
        traces = getattr(self.fallback_store, "traces", None)
        return traces if isinstance(traces, list) else []

    def save_plan(self, plan: AgentPlan) -> None:
        self.fallback_store.save_plan(plan)
        self._remember_plan_idempotency(plan)
        self._persist_plan(plan)

    def get_plan(self, workspace_id: str, plan_id: str) -> AgentPlan | None:
        try:
            state = self._fetch_agent_run_state(workspace_id, plan_id)
        except RuntimeError:
            state = None
        if state is not None:
            self._load_state_memories(workspace_id, plan_id, state)

        plan = self.fallback_store.get_plan(workspace_id, plan_id)
        if plan is None:
            return None

        hydrated_plan = self._hydrate_plan_state(plan, state)
        if hydrated_plan != plan:
            self.fallback_store.update_plan(hydrated_plan)
        return hydrated_plan

    def update_plan(self, plan: AgentPlan) -> None:
        self.fallback_store.update_plan(plan)
        self._remember_plan_idempotency(plan)
        self._persist_plan(plan)

    def save_context_snapshot(self, snapshot: ContextSnapshot) -> None:
        self.fallback_store.save_context_snapshot(snapshot)
        self._persist_context_snapshot(snapshot)

    def get_context_snapshot(self, workspace_id: str, snapshot_ref: str) -> ContextSnapshot | None:
        return self.fallback_store.get_context_snapshot(workspace_id, snapshot_ref)

    def save_action_result(self, result: ToolResult) -> None:
        self.fallback_store.save_action_result(result)
        self._persist_action_result(result)

    def get_action_result(self, plan_id: str, action_id: str) -> ToolResult | None:
        return self.fallback_store.get_action_result(plan_id, action_id)

    def append_trace_event(self, trace_event: TraceEvent) -> None:
        self.fallback_store.append_trace_event(trace_event)

    def mark_action_completed(self, workspace_id: str, plan_id: str, action_id: str) -> None:
        self.fallback_store.mark_action_completed(workspace_id, plan_id, action_id)

    def check_idempotency_key(self, key: str) -> bool:
        if self.fallback_store.check_idempotency_key(key):
            return True
        location = self._idempotency_locations.get(key)
        if location is None:
            return False
        workspace_id, run_id = location
        return self.check_workspace_idempotency_key(workspace_id, key, run_id=run_id)

    def record_idempotency_key(self, key: str) -> None:
        self.fallback_store.record_idempotency_key(key)
        location = self._idempotency_locations.get(key)
        if location is None:
            return
        workspace_id, run_id = location
        self.record_workspace_idempotency_key(workspace_id, key, run_id=run_id)

    def check_workspace_idempotency_key(
        self,
        workspace_id: str,
        key: str,
        *,
        run_id: str | None = None,
    ) -> bool:
        if self.fallback_store.check_idempotency_key(key):
            return True
        candidate_run_id = run_id or self._run_id_from_idempotency_key(key)
        if candidate_run_id is None:
            return False
        try:
            state = self._fetch_agent_run_state(workspace_id, candidate_run_id)
        except RuntimeError:
            return False
        if state is None:
            return False
        self._load_state_memories(workspace_id, candidate_run_id, state)
        return self.fallback_store.check_idempotency_key(key)

    def record_workspace_idempotency_key(
        self,
        workspace_id: str,
        key: str,
        *,
        run_id: str | None = None,
    ) -> None:
        self.fallback_store.record_idempotency_key(key)
        candidate_run_id = run_id or self._run_id_from_idempotency_key(key)
        if candidate_run_id is None:
            return
        self._persist_idempotency_key(workspace_id, candidate_run_id, key)

    def _fetch_agent_run_state(self, workspace_id: str, plan_id: str) -> dict[str, Any] | None:
        try:
            return self.prism_client.get_agent_run_state(workspace_id, plan_id)
        except PrismApiNotFoundError:
            return None

    def _hydrate_plan_state(
        self,
        plan: AgentPlan,
        state: dict[str, Any] | None,
    ) -> AgentPlan:
        if state is None:
            return plan

        records = state.get("actions")
        if not isinstance(records, list):
            records = []

        actions_by_id = {
            str(record.get("actionId")): record
            for record in records
            if isinstance(record, dict) and record.get("actionId") is not None
        }
        if not actions_by_id:
            hydrated_plan = plan
        else:
            hydrated_plan = plan
            for action in plan.actions:
                record = actions_by_id.get(action.action_id) or actions_by_id.get(
                    action_api_id(action)
                )
                if record is None:
                    continue
                status = _action_status_from_record(record, action.status)
                if status != action.status:
                    hydrated_plan = hydrated_plan.replace_action(action.with_status(status))

        run_status = _plan_status_from_state(state, hydrated_plan.status)
        if run_status != hydrated_plan.status:
            hydrated_plan = hydrated_plan.model_copy(
                update={"status": run_status, "updated_at": utc_now()}
            )
        return hydrated_plan

    def _load_state_memories(
        self,
        workspace_id: str,
        run_id: str,
        state: dict[str, Any],
    ) -> None:
        memories = state.get("memories")
        if not isinstance(memories, list):
            return
        for memory in memories:
            if not isinstance(memory, dict):
                continue
            record = _decode_state_record(memory.get("content"))
            if record is None:
                continue
            kind = record.get("kind")
            try:
                if kind == "plan":
                    plan = AgentPlan.model_validate(record.get("plan"))
                    self.fallback_store.save_plan(plan)
                    self._remember_plan_idempotency(plan)
                elif kind == "context_snapshot":
                    snapshot = ContextSnapshot.model_validate(record.get("snapshot"))
                    self.fallback_store.save_context_snapshot(snapshot)
                elif kind == "action_result":
                    result = ToolResult.model_validate(record.get("result"))
                    self.fallback_store.save_action_result(result)
                elif kind == "idempotency_key":
                    key = record.get("key")
                    if isinstance(key, str) and key:
                        self.fallback_store.record_idempotency_key(key)
                        self._idempotency_locations[key] = (workspace_id, run_id)
            except ValueError:
                continue

    def _remember_plan_idempotency(self, plan: AgentPlan) -> None:
        for action in plan.actions:
            self._idempotency_locations[action.idempotency_key] = (
                plan.workspace_id,
                plan.plan_id,
            )

    def _persist_plan(self, plan: AgentPlan) -> None:
        self._persist_state_memory(
            plan.workspace_id,
            memory_id=_state_memory_id(plan.plan_id, "plan"),
            run_id=plan.plan_id,
            memory_type="agent_plan",
            title="Agent plan",
            record={
                "kind": "plan",
                "plan": plan.model_dump(mode="json"),
            },
        )

    def _persist_context_snapshot(self, snapshot: ContextSnapshot) -> None:
        if snapshot.plan_id is None:
            return
        self._persist_state_memory(
            snapshot.workspace_id,
            memory_id=_state_memory_id(snapshot.plan_id, snapshot.snapshot_id, "context"),
            run_id=snapshot.plan_id,
            memory_type="agent_decision",
            title="Context snapshot",
            record={
                "kind": "context_snapshot",
                "snapshot": snapshot.model_dump(mode="json"),
            },
        )

    def _persist_action_result(self, result: ToolResult) -> None:
        plan = self._plan_for_result(result)
        workspace_id = plan.workspace_id if plan is not None else None
        if workspace_id is None:
            return
        self._persist_state_memory(
            workspace_id,
            memory_id=_state_memory_id(result.plan_id, result.action_id, "result"),
            run_id=result.plan_id,
            step_id=result.action_id,
            memory_type="agent_result",
            title="Action result",
            record={
                "kind": "action_result",
                "result": result.model_dump(mode="json"),
            },
        )

    def _plan_for_result(self, result: ToolResult) -> AgentPlan | None:
        for workspace_id, run_id in self._idempotency_locations.values():
            if run_id != result.plan_id:
                continue
            return self.fallback_store.get_plan(workspace_id, run_id)
        return None

    def _persist_idempotency_key(self, workspace_id: str, run_id: str, key: str) -> None:
        self._persist_state_memory(
            workspace_id,
            memory_id=_state_memory_id("idempotency", key),
            run_id=run_id,
            memory_type="agent_decision",
            title="Idempotency key",
            record={
                "kind": "idempotency_key",
                "key": key,
            },
        )

    def _persist_state_memory(
        self,
        workspace_id: str,
        *,
        memory_id: str,
        run_id: str,
        memory_type: str,
        title: str,
        record: dict[str, Any],
        step_id: str | None = None,
    ) -> None:
        if not self.persist_agent_memories:
            # The live API exposes agent memory writes only on bearer client scope.
            return
        record = {"schema": _STATE_SCHEMA, **record}
        content = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
        payload: dict[str, Any] = {
            "memoryId": memory_id,
            "runId": run_id,
            "memoryType": memory_type,
            "title": title[:100],
            "content": content,
            "contentHash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        if step_id:
            payload["stepId"] = _state_memory_id(run_id, step_id, "step")
        self.prism_client.upsert_agent_memory(workspace_id, payload)

    @staticmethod
    def _run_id_from_idempotency_key(key: str) -> str | None:
        if key.startswith(_FEATURE_PROVISIONING_IDEMPOTENCY_PREFIX):
            return key.removeprefix(_FEATURE_PROVISIONING_IDEMPOTENCY_PREFIX)
        return None


def _action_status_from_record(
    record: dict[str, Any],
    current_status: ActionStatus,
) -> ActionStatus:
    raw_status = str(record.get("status") or "").lower()
    requires_approval = bool(record.get("requiresApproval"))
    if requires_approval and raw_status in {
        "pending",
        "queued",
        "waiting",
        "proposed",
        "approval_required",
    }:
        return ActionStatus.REQUIRES_APPROVAL

    return {
        "pending": ActionStatus.PENDING,
        "queued": ActionStatus.PENDING,
        "proposed": ActionStatus.PENDING,
        "approved": ActionStatus.PENDING,
        "running": ActionStatus.RUNNING,
        "waiting": ActionStatus.REQUIRES_APPROVAL,
        "requires_approval": ActionStatus.REQUIRES_APPROVAL,
        "approval_required": ActionStatus.REQUIRES_APPROVAL,
        "completed": ActionStatus.COMPLETED,
        "executed": ActionStatus.COMPLETED,
        "succeeded": ActionStatus.COMPLETED,
        "success": ActionStatus.COMPLETED,
        "failed": ActionStatus.FAILED,
        "rejected": ActionStatus.SKIPPED,
        "error": ActionStatus.FAILED,
        "cancelled": ActionStatus.SKIPPED,
        "canceled": ActionStatus.SKIPPED,
    }.get(raw_status, current_status)


def _plan_status_from_state(
    state: dict[str, Any],
    current_status: PlanStatus,
) -> PlanStatus:
    run = state.get("run")
    if not isinstance(run, dict):
        return current_status
    raw_status = str(run.get("status") or "").lower()
    return {
        "completed": PlanStatus.COMPLETED,
        "failed": PlanStatus.FAILED,
        "cancelled": PlanStatus.CANCELLED,
        "canceled": PlanStatus.CANCELLED,
    }.get(raw_status, current_status)


def _state_memory_id(*parts: str) -> str:
    from domain.agent_run import stable_agent_run_uuid

    return stable_agent_run_uuid("state", *parts)


def _decode_state_record(content: Any) -> dict[str, Any] | None:
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict) or decoded.get("schema") != _STATE_SCHEMA:
        return None
    return decoded
