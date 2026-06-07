from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from application.recursion_runner import RecursionRunner
from domain.feature_provisioning import (
    FeatureProvisioningPayload,
    FeatureProvisioningPointerEvent,
)
from infrastructure.object_storage.base import JsonPayloadStore
from infrastructure.state.base import StateStore


@dataclass(frozen=True)
class FeatureProvisioningResult:
    request_id: str
    event_id: str | None
    duplicate: bool = False


class FeatureProvisioningWorker:
    def __init__(
        self,
        payload_store: JsonPayloadStore,
        state_store: StateStore,
        recursion_runner: RecursionRunner,
    ) -> None:
        self.payload_store = payload_store
        self.state_store = state_store
        self.recursion_runner = recursion_runner

    def handle_pointer(self, content: dict[str, Any]) -> FeatureProvisioningResult:
        pointer = FeatureProvisioningPointerEvent.model_validate(content)
        run_id = pointer.agent_run_id or pointer.request_id
        if _check_idempotency_key(
            self.state_store,
            pointer.workspace_id,
            pointer.worker_idempotency_key,
            run_id,
        ):
            return FeatureProvisioningResult(
                request_id=pointer.request_id,
                event_id=None,
                duplicate=True,
            )

        payload_data = self.payload_store.fetch_json(
            pointer.payload_object_name,
            pointer.payload_version_id,
        )
        payload = FeatureProvisioningPayload.model_validate(payload_data)
        payload.validate_matches(pointer)
        event = payload.to_domain_event(pointer)
        traces = self.recursion_runner.run(event)

        failures = [
            trace
            for trace in traces
            if trace.event_name
            in {"event.failed", "action.failed", "plan.failed", "recursion.max_depth"}
        ]
        if failures:
            raise RuntimeError(failures[-1].message)
        if not any(trace.event_name == "plan.completed" for trace in traces):
            trace_summary = "; ".join(
                f"{trace.event_name}: {trace.message}" for trace in traces[-5:]
            )
            raise RuntimeError(
                "Feature provisioning did not complete all planned actions."
                f" Recent traces: {trace_summary}"
            )

        _record_idempotency_key(
            self.state_store,
            pointer.workspace_id,
            pointer.worker_idempotency_key,
            run_id,
        )
        return FeatureProvisioningResult(request_id=pointer.request_id, event_id=event.event_id)


def _check_idempotency_key(
    state_store: StateStore,
    workspace_id: str,
    key: str,
    run_id: str,
) -> bool:
    checker = getattr(state_store, "check_workspace_idempotency_key", None)
    if callable(checker):
        return bool(checker(workspace_id, key, run_id=run_id))
    return state_store.check_idempotency_key(key)


def _record_idempotency_key(
    state_store: StateStore,
    workspace_id: str,
    key: str,
    run_id: str,
) -> None:
    recorder = getattr(state_store, "record_workspace_idempotency_key", None)
    if callable(recorder):
        recorder(workspace_id, key, run_id=run_id)
        return
    state_store.record_idempotency_key(key)
