from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from application.domain_event_handler import DomainEventHandler
from domain.events import DomainEvent, EventEnvelope
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
    """Resolves a feature-provisioning pointer to its seed domain event and dispatches
    a single processing step.

    Like every other event in the system, the seed event is handled one step per
    invocation: the handler plans and enqueues the first action, and follow-up events
    are delivered to the queue and processed by subsequent function invocations. The
    worker does not drain the recursion in-session.
    """

    def __init__(
        self,
        payload_store: JsonPayloadStore,
        state_store: StateStore,
        domain_event_handler: DomainEventHandler,
    ) -> None:
        self.payload_store = payload_store
        self.state_store = state_store
        self.domain_event_handler = domain_event_handler

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

        event = self.resolve_seed_event(self.payload_store, pointer)

        # Dispatch one step. The handler plans and enqueues the first action; the rest
        # of the workflow runs across later invocations. A transient failure (context
        # hydration, planning) raises and propagates, so the invocation fails and the
        # pointer is retried. The idempotency key is recorded only on a clean dispatch,
        # so retries re-run planning while a redelivered pointer is a no-op.
        self.domain_event_handler.handle(EventEnvelope.wrap(event))

        _record_idempotency_key(
            self.state_store,
            pointer.workspace_id,
            pointer.worker_idempotency_key,
            run_id,
        )
        return FeatureProvisioningResult(request_id=pointer.request_id, event_id=event.event_id)

    @staticmethod
    def resolve_seed_event(
        payload_store: JsonPayloadStore,
        pointer: FeatureProvisioningPointerEvent,
    ) -> DomainEvent:
        """Fetch and validate the referenced payload, returning the seed domain event."""
        payload_data = payload_store.fetch_json(
            pointer.payload_object_name,
            pointer.payload_version_id,
        )
        payload = FeatureProvisioningPayload.model_validate(payload_data)
        payload.validate_matches(pointer)
        return payload.to_domain_event(pointer)


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
