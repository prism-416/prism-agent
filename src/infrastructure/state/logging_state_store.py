from __future__ import annotations

import logging
from typing import Any

from domain.results import TraceEvent
from infrastructure.observability.logging_config import log_json
from infrastructure.state.base import StateStore

# Trace events that represent a problem; logged at ERROR so they surface in OCI Logs.
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

# Compact, high-signal fields lifted from TraceEvent.data so log lines stay specific
# without dumping full hydrated context.
_DETAIL_KEYS = (
    "tool_name",
    "status",
    "plan_status",
    "node_id",
    "skill_ids",
    "attempt",
    "reason",
    "error",
    "error_type",
)


class LoggingStateStore:
    """Transparent StateStore proxy that logs every trace event as a JSON line.

    Wraps any concrete store and forwards all calls unchanged (via ``__getattr__``);
    the only added behavior is structured logging on ``append_trace_event``, giving a
    per-event, queryable play-by-play in OCI Logs.
    """

    def __init__(self, inner: StateStore, logger: logging.Logger) -> None:
        self._inner = inner
        self._logger = logger

    def append_trace_event(self, trace_event: TraceEvent) -> None:
        self._log(trace_event)
        self._inner.append_trace_event(trace_event)

    def __getattr__(self, name: str) -> Any:
        # Delegate everything not defined here (save_plan, traces, advance_task_graph, ...).
        return getattr(self._inner, name)

    def _log(self, trace_event: TraceEvent) -> None:
        level = logging.ERROR if trace_event.event_name in _FAILURE_EVENTS else logging.INFO
        record = {
            "log": "trace",
            "event": trace_event.event_name,
            "workspace_id": trace_event.workspace_id,
            "project_id": trace_event.project_id,
            "plan_id": trace_event.plan_id,
            "action_id": trace_event.action_id,
            "message": trace_event.message,
        }
        detail = {key: trace_event.data[key] for key in _DETAIL_KEYS if key in trace_event.data}
        if detail:
            record["detail"] = detail
        log_json(self._logger, level, record)
