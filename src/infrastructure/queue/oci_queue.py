from __future__ import annotations

from collections.abc import Callable
from typing import Any

from domain.events import EventEnvelope
from infrastructure.oci_auth import load_oci_config_and_signer
from infrastructure.queue.base import Queue, QueueMessage


class OCIQueue(Queue):
    """OCI Queue adapter used by OCI Functions to enqueue follow-up events."""

    def __init__(
        self,
        queue_ocid: str | None,
        messages_endpoint: str | None = None,
        client: Any | None = None,
        put_messages_details_builder: Callable[[str], Any] | None = None,
    ) -> None:
        self.queue_ocid = queue_ocid
        if not queue_ocid:
            raise ValueError("OCI_QUEUE_OCID is required when QUEUE_BACKEND=oci")
        self.messages_endpoint = messages_endpoint
        self._client = client
        self._put_messages_details_builder = (
            put_messages_details_builder or _build_put_messages_details
        )

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = build_queue_client(self.messages_endpoint)
        return self._client

    def enqueue(self, envelope: EventEnvelope) -> QueueMessage:
        payload = envelope.model_dump_json()
        response = self.client.put_messages(
            queue_id=self.queue_ocid,
            put_messages_details=self._put_messages_details_builder(payload),
        )
        return QueueMessage(
            message_id=_published_message_id(response, envelope.envelope_id),
            envelope=envelope,
        )

    def dequeue(self) -> QueueMessage | None:
        raise NotImplementedError("OCI Functions receive queue messages via trigger payloads.")

    def ack(self, message: QueueMessage) -> None:
        _ = message
        raise NotImplementedError(
            "OCI Functions queue trigger acknowledgements are managed by OCI."
        )

    def fail(self, message: QueueMessage, reason: str) -> None:
        _ = message, reason
        raise NotImplementedError("OCI Functions queue trigger failures are managed by OCI.")

    def is_empty(self) -> bool:
        raise NotImplementedError("OCI Queue emptiness checks are not used by OCI Functions.")


def build_queue_client(messages_endpoint: str | None = None) -> Any:
    try:
        import oci
    except ImportError as exc:
        raise RuntimeError("Install the prod extra to use OCI Queue.") from exc

    kwargs: dict[str, Any] = {}
    if messages_endpoint:
        kwargs["service_endpoint"] = messages_endpoint

    config, signer = load_oci_config_and_signer("OCI Queue")
    if signer is not None:
        return oci.queue.QueueClient(config=config, signer=signer, **kwargs)
    return oci.queue.QueueClient(config, **kwargs)


def _build_put_messages_details(content: str) -> Any:
    try:
        import oci
    except ImportError as exc:
        raise RuntimeError("Install the prod extra to use OCI Queue.") from exc

    return oci.queue.models.PutMessagesDetails(
        messages=[oci.queue.models.PutMessagesDetailsEntry(content=content)]
    )


def _published_message_id(response: Any, fallback: str) -> str:
    data = getattr(response, "data", None)
    messages = getattr(data, "messages", None)
    if isinstance(messages, list) and messages:
        message_id = getattr(messages[0], "id", None)
        if message_id is not None:
            return str(message_id)
    return fallback
