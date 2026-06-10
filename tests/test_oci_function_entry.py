from __future__ import annotations

import json

import pytest

from interfaces.oci_function_entry import (
    _decode_payload,
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
