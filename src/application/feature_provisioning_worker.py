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
        if self.state_store.check_idempotency_key(pointer.worker_idempotency_key):
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
            if trace.event_name in {"event.failed", "action.failed", "recursion.max_depth"}
        ]
        if failures:
            raise RuntimeError(failures[-1].message)
        if not any(trace.event_name == "plan.completed" for trace in traces):
            raise RuntimeError("Feature provisioning did not complete all planned actions.")

        self.state_store.record_idempotency_key(pointer.worker_idempotency_key)
        return FeatureProvisioningResult(request_id=pointer.request_id, event_id=event.event_id)
