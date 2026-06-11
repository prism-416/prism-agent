from __future__ import annotations

import json

import pytest

from domain.events import EventEnvelope, SubAgentCompletedEvent, SubAgentTaskEvent
from infrastructure.config.settings import Settings
from infrastructure.state.memory_state_store import MemoryStateStore
from interfaces.oci_function_entry import (
    _decode_payload,
    _process,
    _queue_message_content,
    _raw_preview,
)

_EVENT = {"event": {"event_type": "story.created", "workspace_id": "w1"}}


def test_unwraps_messages_wrapper_with_single_message() -> None:
    payload = {"messages": [{"content": json.dumps(_EVENT)}]}
    assert _queue_message_content(payload) == _EVENT


def test_unwraps_bare_json_array_with_single_message() -> None:
    # Service Connector Hub may deliver a bare JSON array rather than a dict.
    payload = [{"content": json.dumps(_EVENT)}]
    assert _queue_message_content(payload) == _EVENT


def test_passthrough_for_bare_event_dict() -> None:
    assert _queue_message_content(_EVENT) == _EVENT


@pytest.mark.parametrize("payload", [[], [_EVENT, _EVENT], {"messages": []}])
def test_batch_other_than_one_message_raises(payload: object) -> None:
    with pytest.raises(ValueError, match="exactly one event per invocation"):
        _queue_message_content(payload)


def test_non_dict_non_list_payload_raises() -> None:
    with pytest.raises(ValueError, match="Unexpected queue payload type: int"):
        _queue_message_content(_decode_payload(b"5"))


def test_raw_preview_decodes_and_truncates() -> None:
    assert _raw_preview(b'{"event": 1}') == '{"event": 1}'
    assert _raw_preview("x" * 1000, limit=10) == "x" * 10


@pytest.mark.parametrize(
    "event",
    [
        SubAgentTaskEvent(
            workspace_id="w1",
            project_id="p1",
            plan_id="run-1",
            node_id="summary",
        ),
        SubAgentCompletedEvent(
            workspace_id="w1",
            project_id="p1",
            plan_id="run-1",
            node_id="summary",
        ),
    ],
)
def test_process_routes_subagent_events_to_coordinator(monkeypatch, event) -> None:
    class _Handler:
        def __init__(self) -> None:
            self.handled: list[EventEnvelope] = []

        def handle(self, envelope: EventEnvelope) -> None:
            self.handled.append(envelope)

    class _RecursionRunner:
        def exceeds_max_depth(self, envelope: EventEnvelope) -> bool:
            _ = envelope
            return False

        def record_max_depth_failure(self, event) -> None:
            _ = event

    action_handler = _Handler()
    domain_handler = _Handler()
    coordinator = _Handler()

    class _Container:
        state_store = MemoryStateStore()
        recursion_runner = _RecursionRunner()
        action_event_handler = action_handler
        domain_event_handler = domain_handler
        subagent_coordinator = coordinator

    monkeypatch.setattr(
        "interfaces.oci_function_entry.build_container", lambda settings: _Container
    )

    envelope = EventEnvelope.wrap(event)
    result, _ = _process(Settings(), envelope.model_dump(mode="json"))

    assert result["ok"] is True
    assert coordinator.handled and coordinator.handled[0].event.event_id == event.event_id
    assert action_handler.handled == []
    assert domain_handler.handled == []
