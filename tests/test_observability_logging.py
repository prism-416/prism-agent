from __future__ import annotations

import json
import logging

from domain.results import TraceEvent
from infrastructure.observability import logging_config
from infrastructure.state.logging_state_store import LoggingStateStore
from infrastructure.state.memory_state_store import MemoryStateStore
from interfaces.oci_function_entry import _payload_fields, _payload_label


class _CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _logger() -> tuple[logging.Logger, _CapturingHandler]:
    logger = logging.getLogger("test_logging_state_store")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = _CapturingHandler()
    logger.addHandler(handler)
    return logger, handler


def test_logging_state_store_logs_and_delegates() -> None:
    logger, handler = _logger()
    inner = MemoryStateStore()
    store = LoggingStateStore(inner, logger)

    store.append_trace_event(
        TraceEvent(
            event_name="action.completed",
            workspace_id="w1",
            project_id="p1",
            plan_id="run-1",
            message="done",
            data={"tool_name": "create_workitem", "status": "completed"},
        )
    )

    # Delegated to the wrapped store.
    assert inner.traces[-1].event_name == "action.completed"
    # Emitted one structured JSON line at INFO.
    record = handler.records[-1]
    assert record.levelno == logging.INFO
    payload = json.loads(record.getMessage())
    assert payload["event"] == "action.completed"
    assert payload["plan_id"] == "run-1"
    assert payload["detail"] == {"tool_name": "create_workitem", "status": "completed"}


def test_logging_state_store_uses_error_level_for_failures() -> None:
    logger, handler = _logger()
    store = LoggingStateStore(MemoryStateStore(), logger)

    store.append_trace_event(
        TraceEvent(event_name="plan.failed", workspace_id="w1", message="boom")
    )

    assert handler.records[-1].levelno == logging.ERROR


def test_logging_state_store_proxies_other_methods() -> None:
    logger, _ = _logger()
    store = LoggingStateStore(MemoryStateStore(), logger)

    # __getattr__ delegation: arbitrary StateStore methods and attributes work.
    assert store.get_plan("w1", "missing") is None
    assert store.check_idempotency_key("k") is False
    assert store.traces == []


def test_configure_logging_is_idempotent() -> None:
    first = logging_config.configure_logging("INFO")
    handler_count = len(first.handlers)
    second = logging_config.configure_logging("DEBUG")

    assert first is second
    assert len(second.handlers) == handler_count  # no duplicate handler
    assert second.level == logging.DEBUG


def test_payload_fields_unwraps_event_envelope() -> None:
    fields = _payload_fields(
        {"event": {"event_type": "pr.merged", "workspace_id": "w1", "event_id": "e1"}}
    )
    assert fields["event_type"] == "pr.merged"
    assert fields["workspace_id"] == "w1"
    assert fields["event_id"] == "e1"

    assert _payload_label({"event": {"event_type": "pr.merged", "workspace_id": "w1"}}) == (
        "`pr.merged` · ws `w1`"
    )


def test_payload_fields_handles_feature_pointer() -> None:
    fields = _payload_fields(
        {"type": "feature.provisioning.requested", "workspaceId": "w9", "requestId": "r1"}
    )
    assert fields["event_type"] == "feature.provisioning.requested"
    assert fields["workspace_id"] == "w9"
    assert fields["request_id"] == "r1"
