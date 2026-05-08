from __future__ import annotations

from domain.events import EventEnvelope
from infrastructure.queue.base import Queue, QueueMessage


class OCIQueue(Queue):
    """OCI Queue adapter skeleton.

    Production deployments should provide OCI client construction here. The
    application layer only depends on the Queue interface, so this adapter can
    be completed without changing handlers or the recursion flow.
    """

    def __init__(self, queue_ocid: str | None) -> None:
        self.queue_ocid = queue_ocid
        if not queue_ocid:
            raise ValueError("OCI_QUEUE_OCID is required when QUEUE_BACKEND=oci")

    def enqueue(self, envelope: EventEnvelope) -> QueueMessage:
        payload = envelope.model_dump_json()
        _ = payload
        raise NotImplementedError("OCI Queue enqueue is an adapter skeleton.")

    def dequeue(self) -> QueueMessage | None:
        raise NotImplementedError("OCI Queue dequeue is an adapter skeleton.")

    def ack(self, message: QueueMessage) -> None:
        raise NotImplementedError("OCI Queue ack is an adapter skeleton.")

    def fail(self, message: QueueMessage, reason: str) -> None:
        raise NotImplementedError("OCI Queue fail/dead-letter handling is an adapter skeleton.")

    def is_empty(self) -> bool:
        raise NotImplementedError("OCI Queue emptiness checks are not used by OCI Functions.")
