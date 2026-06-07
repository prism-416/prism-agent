from __future__ import annotations

import json
from typing import Any

from app.container import build_container
from application.feature_provisioning_worker import FeatureProvisioningWorker
from domain.events import AgentActionEvent, EventEnvelope
from infrastructure.config.settings import Settings
from infrastructure.object_storage.oci_payload_store import ObjectStoragePayloadStore


def handler(ctx: Any, data: bytes | str | dict[str, Any]) -> dict[str, Any]:
    _ = ctx
    payload = _queue_message_content(_decode_payload(data))
    settings = Settings.from_env()
    if _is_feature_provisioning_pointer(payload):
        return _handle_feature_provisioning_pointer(settings, payload)

    envelope = EventEnvelope.model_validate(payload)
    container = build_container(settings)
    if container.recursion_runner.exceeds_max_depth(envelope):
        container.recursion_runner.record_max_depth_failure(envelope.event)
        return {
            "ok": False,
            "event_id": envelope.event_id,
            "event_type": envelope.event_type,
            "reason": "max_recursion_depth_exceeded",
        }

    if isinstance(envelope.event, AgentActionEvent):
        container.action_event_handler.handle(envelope)
    else:
        container.domain_event_handler.handle(envelope)

    return {
        "ok": True,
        "event_id": envelope.event_id,
        "event_type": envelope.event_type,
    }


def _decode_payload(data: bytes | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(data, bytes):
        return json.loads(data.decode("utf-8"))
    if isinstance(data, str):
        return json.loads(data)
    return data


def _queue_message_content(payload: dict[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages")
    if isinstance(messages, list):
        if len(messages) != 1:
            raise ValueError("OCI queue function payload must contain exactly one message.")
        return _queue_message_content(messages[0])

    for key in ("content", "body", "message"):
        value = payload.get(key)
        if isinstance(value, str):
            decoded = json.loads(value)
            if not isinstance(decoded, dict):
                raise ValueError(f"Queue message {key} must decode to a JSON object.")
            return decoded
        if isinstance(value, dict):
            return value
    return payload


def _is_feature_provisioning_pointer(payload: dict[str, Any]) -> bool:
    return payload.get("type") == "feature.provisioning.requested"


def _handle_feature_provisioning_pointer(
    settings: Settings,
    payload: dict[str, Any],
) -> dict[str, Any]:
    payload_store = ObjectStoragePayloadStore(
        settings.object_storage_namespace,
        settings.object_storage_bucket_name,
    )
    worker_container = build_container(settings.model_copy(update={"queue_backend": "memory"}))
    worker = FeatureProvisioningWorker(
        payload_store=payload_store,
        state_store=worker_container.state_store,
        recursion_runner=worker_container.recursion_runner,
    )
    result = worker.handle_pointer(payload)
    return {
        "ok": True,
        "event_id": result.event_id,
        "event_type": "feature.provisioning.requested",
        "request_id": result.request_id,
        "duplicate": result.duplicate,
    }
