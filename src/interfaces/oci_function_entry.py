from __future__ import annotations

import json
from typing import Any

from app.container import build_container
from domain.events import AgentActionEvent, EventEnvelope
from infrastructure.config.settings import Settings


def handler(ctx: Any, data: bytes | str | dict[str, Any]) -> dict[str, Any]:
    _ = ctx
    payload = _decode_payload(data)
    envelope = EventEnvelope.model_validate(payload)
    container = build_container(Settings.from_env())

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
