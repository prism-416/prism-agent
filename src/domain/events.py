from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class EventCausality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_event_id: str | None = None
    parent_event_id: str | None = None
    depth: int = 0
    chain: list[str] = Field(default_factory=list)

    def child(self, parent_event_id: str) -> EventCausality:
        root_event_id = self.root_event_id or parent_event_id
        return EventCausality(
            root_event_id=root_event_id,
            parent_event_id=parent_event_id,
            depth=self.depth + 1,
            chain=[*self.chain, parent_event_id],
        )


class BaseRuntimeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    workspace_id: str
    project_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=utc_now)
    correlation_id: str | None = None
    causality: EventCausality = Field(default_factory=EventCausality)
    idempotency_key: str | None = None


class DomainEvent(BaseRuntimeEvent):
    kind: Literal["domain"] = "domain"


class ScheduledEvent(BaseRuntimeEvent):
    kind: Literal["scheduled"] = "scheduled"
    schedule_id: str | None = None


class ManualInvocationEvent(BaseRuntimeEvent):
    kind: Literal["manual"] = "manual"
    actor_id: str | None = None


class AgentActionEvent(BaseRuntimeEvent):
    kind: Literal["agent_action"] = "agent_action"
    event_type: str = "agent.action.requested"
    plan_id: str
    action_id: str


RuntimeEvent = Annotated[
    DomainEvent | ScheduledEvent | ManualInvocationEvent | AgentActionEvent,
    Field(discriminator="kind"),
]


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envelope_id: str = Field(default_factory=lambda: str(uuid4()))
    event: RuntimeEvent
    received_at: datetime = Field(default_factory=utc_now)
    attempt: int = 0

    @classmethod
    def wrap(cls, event: RuntimeEvent) -> EventEnvelope:
        return cls(event=event)

    @property
    def event_id(self) -> str:
        return self.event.event_id

    @property
    def event_type(self) -> str:
        return self.event.event_type
