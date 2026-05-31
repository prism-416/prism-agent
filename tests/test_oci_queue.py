from __future__ import annotations

import json
from types import SimpleNamespace

from domain.events import DomainEvent, EventEnvelope
from infrastructure.config.settings import Settings
from infrastructure.queue.oci_queue import OCIQueue


class FakeQueueClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def put_messages(self, queue_id, put_messages_details):
        self.calls.append(
            {
                "queue_id": queue_id,
                "put_messages_details": put_messages_details,
            }
        )
        message = SimpleNamespace(id=12345)
        return SimpleNamespace(data=SimpleNamespace(messages=[message]))


def test_oci_queue_enqueue_publishes_event_envelope_json() -> None:
    client = FakeQueueClient()
    queue = OCIQueue(
        "ocid1.queue.oc1..queue",
        "https://messages.queue.example",
        client=client,
        put_messages_details_builder=lambda content: {"messages": [{"content": content}]},
    )
    envelope = EventEnvelope.wrap(
        DomainEvent(event_type="story.created", workspace_id="w1", project_id="p1")
    )

    message = queue.enqueue(envelope)

    assert message.message_id == "12345"
    assert message.envelope == envelope
    assert client.calls[0]["queue_id"] == "ocid1.queue.oc1..queue"
    content = client.calls[0]["put_messages_details"]["messages"][0]["content"]
    assert json.loads(content)["event"]["kind"] == "domain"
    assert json.loads(content)["event"]["event_type"] == "story.created"


def test_settings_loads_oci_queue_messages_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("OCI_QUEUE_MESSAGES_ENDPOINT", "https://messages.queue.example")

    settings = Settings.from_env()

    assert settings.oci_queue_messages_endpoint == "https://messages.queue.example"
