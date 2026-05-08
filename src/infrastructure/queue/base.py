from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from domain.events import EventEnvelope


class QueueMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(default_factory=lambda: str(uuid4()))
    envelope: EventEnvelope
    receipt: str | None = None


class Queue(ABC):
    @abstractmethod
    def enqueue(self, envelope: EventEnvelope) -> QueueMessage:
        raise NotImplementedError

    @abstractmethod
    def dequeue(self) -> QueueMessage | None:
        raise NotImplementedError

    @abstractmethod
    def ack(self, message: QueueMessage) -> None:
        raise NotImplementedError

    @abstractmethod
    def fail(self, message: QueueMessage, reason: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_empty(self) -> bool:
        raise NotImplementedError
