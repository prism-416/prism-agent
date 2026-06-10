from __future__ import annotations

import json
import logging
from typing import Any

from app.container import build_container
from application.feature_provisioning_worker import FeatureProvisioningWorker
from domain.events import AgentActionEvent, EventEnvelope
from domain.results import TraceEvent
from infrastructure.config.settings import Settings
from infrastructure.notifications.discord_notifier import DiscordNotifier
from infrastructure.object_storage.oci_payload_store import ObjectStoragePayloadStore
from infrastructure.observability.logging_config import configure_logging, log_json

# Trace events that represent a problem worth surfacing in an invocation summary.
_FAILURE_EVENTS = {
    "event.failed",
    "plan.failed",
    "plan.empty",
    "action.failed",
    "action.execution_error",
    "action.validation_failed",
    "action.stale_context",
    "orchestration.failed",
    "orchestration.blocked",
    "recursion.max_depth",
}


def handler(ctx: Any, data: bytes | str | dict[str, Any]) -> dict[str, Any]:
    _ = ctx
    # Settings/logger/notifier are derived from the function environment, not the
    # invocation payload, so they are built up front: the except block needs them
    # to report any payload-handling failure.
    settings = Settings.from_env()
    logger = configure_logging(settings.log_level)
    notifier = DiscordNotifier(settings.discord_webhook_url)

    # Payload parsing lives inside the try so a malformed/unexpected payload (e.g. a
    # connector batch shape this function does not accept) surfaces as a logged,
    # alerted error instead of a bare 502 with no diagnostics.
    label = "unknown"
    payload: dict[str, Any] = {}
    try:
        payload = _queue_message_content(_decode_payload(data))
        label = _payload_label(payload)
        log_json(logger, logging.INFO, {"log": "invocation.start", **_payload_fields(payload)})
        notifier.send(f"▶️ Triggered: {label}")
        result, traces = _process(settings, payload)
    except Exception as exc:
        log_json(
            logger,
            logging.ERROR,
            {
                "log": "invocation.error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "raw_payload_preview": _raw_preview(data),
                **_payload_fields(payload),
            },
            exc_info=True,
        )
        notifier.send(_failure_message(label, exc))
        raise
    log_json(
        logger,
        logging.INFO,
        {
            "log": "invocation.end",
            "ok": result.get("ok"),
            "run_id": _run_id(traces),
            "actions": _completed_tools(traces),
            "issues": sum(1 for trace in traces if _is_failure(trace)),
            **_payload_fields(payload),
        },
    )
    notifier.send(_summary_message(label, traces))
    return result


def _process(
    settings: Settings, payload: dict[str, Any]
) -> tuple[dict[str, Any], list[TraceEvent]]:
    if _is_feature_provisioning_pointer(payload):
        return _handle_feature_provisioning_pointer(settings, payload)

    envelope = EventEnvelope.model_validate(payload)
    container = build_container(settings)
    if container.recursion_runner.exceeds_max_depth(envelope):
        container.recursion_runner.record_max_depth_failure(envelope.event)
        return (
            {
                "ok": False,
                "event_id": envelope.event_id,
                "event_type": envelope.event_type,
                "reason": "max_recursion_depth_exceeded",
            },
            _traces(container.state_store),
        )

    if isinstance(envelope.event, AgentActionEvent):
        container.action_event_handler.handle(envelope)
    else:
        container.domain_event_handler.handle(envelope)

    return (
        {
            "ok": True,
            "event_id": envelope.event_id,
            "event_type": envelope.event_type,
        },
        _traces(container.state_store),
    )


def _decode_payload(data: bytes | str | dict[str, Any]) -> Any:
    if isinstance(data, bytes):
        return json.loads(data.decode("utf-8"))
    if isinstance(data, str):
        return json.loads(data)
    return data


def _queue_message_content(payload: Any) -> dict[str, Any]:
    # A Service Connector / Queue trigger may deliver the batch as a bare JSON array
    # or wrapped in {"messages": [...]}. This function processes one event per
    # invocation, so a batch of any size other than one is rejected loudly.
    if isinstance(payload, list):
        return _queue_message_content(_single_message(payload))

    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected queue payload type: {type(payload).__name__}")

    messages = payload.get("messages")
    if isinstance(messages, list):
        return _queue_message_content(_single_message(messages))

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


def _single_message(messages: list[Any]) -> Any:
    if len(messages) != 1:
        raise ValueError(
            f"OCI trigger delivered {len(messages)} messages; this function processes "
            "exactly one event per invocation. Configure the connector/queue batch "
            "size to 1."
        )
    return messages[0]


def _raw_preview(data: bytes | str | dict[str, Any], limit: int = 500) -> str:
    """Truncated repr of the raw invocation payload, for diagnosing shape mismatches."""
    if isinstance(data, bytes):
        text = data.decode("utf-8", errors="replace")
    elif isinstance(data, str):
        text = data
    else:
        text = repr(data)
    return text[:limit]


def _is_feature_provisioning_pointer(payload: dict[str, Any]) -> bool:
    return payload.get("type") == "feature.provisioning.requested"


def _handle_feature_provisioning_pointer(
    settings: Settings,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[TraceEvent]]:
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
    return (
        {
            "ok": True,
            "event_id": result.event_id,
            "event_type": "feature.provisioning.requested",
            "request_id": result.request_id,
            "duplicate": result.duplicate,
        },
        _traces(worker_container.state_store),
    )


def _traces(state_store: Any) -> list[TraceEvent]:
    traces = getattr(state_store, "traces", None)
    return list(traces) if traces else []


def _payload_label(payload: dict[str, Any]) -> str:
    fields = _payload_fields(payload)
    return _format_label(fields.get("event_type") or "unknown", fields.get("workspace_id"))


def _format_label(event_type: str, workspace_id: str | None) -> str:
    workspace = f" · ws `{workspace_id}`" if workspace_id else ""
    return f"`{event_type}`{workspace}"


def _payload_fields(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("type") == "feature.provisioning.requested":
        return {
            "event_type": "feature.provisioning.requested",
            "workspace_id": payload.get("workspaceId") or payload.get("workspace_id"),
            "request_id": payload.get("requestId"),
        }
    # Queue messages carry an EventEnvelope ({"event": {...}}); fall back to the
    # payload itself for a bare event dict.
    event = payload.get("event") if isinstance(payload.get("event"), dict) else payload
    return {
        "event_type": event.get("event_type"),
        "event_id": event.get("event_id"),
        "workspace_id": event.get("workspace_id"),
        "correlation_id": event.get("correlation_id"),
    }


def _summary_message(label: str, traces: list[TraceEvent]) -> str:
    tools = _completed_tools(traces)
    issues = [f"{trace.event_name}: {trace.message}" for trace in traces if _is_failure(trace)]
    run_id = _run_id(traces)

    lines = [f"{'⚠️' if issues else '✅'} {label}"]
    if run_id:
        lines.append(f"run: `{run_id}`")
    lines.append(f"actions({len(tools)}): {', '.join(tools)}" if tools else "actions: none")
    if issues:
        lines.append("issues:\n" + "\n".join(f"- {issue}" for issue in issues[:5]))
    return "\n".join(lines)


def _failure_message(label: str, exc: Exception) -> str:
    return f"❌ Failed: {label}\n`{type(exc).__name__}: {exc}`"


def _completed_tools(traces: list[TraceEvent]) -> list[str]:
    tools: list[str] = []
    for trace in traces:
        if trace.event_name != "action.completed":
            continue
        tool_name = trace.data.get("tool_name")
        if isinstance(tool_name, str) and tool_name and tool_name not in tools:
            tools.append(tool_name)
    return tools


def _is_failure(trace: TraceEvent) -> bool:
    return trace.event_name in _FAILURE_EVENTS


def _run_id(traces: list[TraceEvent]) -> str | None:
    for trace in traces:
        if trace.plan_id:
            return trace.plan_id
    return None
