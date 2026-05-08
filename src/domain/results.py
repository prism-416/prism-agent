from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from domain.events import EventEnvelope, utc_now
from domain.suggestions import AgentSuggestion


class ValidationDecision(StrEnum):
    COMMIT = "commit"
    SUGGEST = "suggest"
    RETRY = "retry"
    REPLAN = "replan"
    FAIL = "fail"
    NOOP = "noop"


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str = Field(default_factory=lambda: str(uuid4()))
    plan_id: str
    action_id: str
    tool_name: str
    success: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    emitted_events: list[EventEnvelope] = Field(default_factory=list)
    suggestion: AgentSuggestion | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ValidationDecision
    valid: bool
    reason: str | None = None
    stale_entities: dict[str, dict[str, str | int | None]] = Field(default_factory=dict)
    emitted_events: list[EventEnvelope] = Field(default_factory=list)
    suggestion: AgentSuggestion | None = None


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    workspace_id: str
    project_id: str | None = None
    status: str
    summary: str
    emitted_events: list[EventEnvelope] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    event_name: str
    workspace_id: str
    project_id: str | None = None
    plan_id: str | None = None
    action_id: str | None = None
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
