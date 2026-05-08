from __future__ import annotations

from collections import deque

from domain.events import EventEnvelope
from infrastructure.queue.base import Queue, QueueMessage


class MemoryQueue(Queue):
    def __init__(self) -> None:
        self._messages: deque[QueueMessage] = deque()
        self.failed: list[tuple[QueueMessage, str]] = []
        self.acked: list[str] = []

    def enqueue(self, envelope: EventEnvelope) -> QueueMessage:
        message = QueueMessage(envelope=envelope)
        self._messages.append(message)
        return message

    def dequeue(self) -> QueueMessage | None:
        if not self._messages:
            return None
        return self._messages.popleft()

    def ack(self, message: QueueMessage) -> None:
        self.acked.append(message.message_id)

    def fail(self, message: QueueMessage, reason: str) -> None:
        self.failed.append((message, reason))

    def is_empty(self) -> bool:
        return not self._messages

    def __len__(self) -> int:
        return len(self._messages)
