from __future__ import annotations

import json

from domain.results import TraceEvent
from infrastructure.notifications.discord_notifier import DiscordNotifier
from interfaces.oci_function_entry import (
    _failure_message,
    _payload_label,
    _summary_message,
)


class _Recorder:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, bytes]] = []
        self.fail = fail

    def __call__(self, url: str, body: bytes, timeout: float) -> None:
        _ = timeout
        if self.fail:
            raise RuntimeError("discord down")
        self.calls.append((url, body))


# -- DiscordNotifier --------------------------------------------------------


def test_notifier_is_noop_without_webhook() -> None:
    recorder = _Recorder()
    notifier = DiscordNotifier(None, transport=recorder)

    assert notifier.enabled is False
    notifier.send("hello")
    assert recorder.calls == []


def test_notifier_posts_content_when_enabled() -> None:
    recorder = _Recorder()
    notifier = DiscordNotifier("https://discord.test/webhook", transport=recorder)

    notifier.send("▶️ Triggered: x")

    assert len(recorder.calls) == 1
    url, body = recorder.calls[0]
    assert url == "https://discord.test/webhook"
    assert json.loads(body.decode("utf-8")) == {"content": "▶️ Triggered: x"}


def test_notifier_truncates_long_content() -> None:
    recorder = _Recorder()
    notifier = DiscordNotifier("https://discord.test/webhook", transport=recorder)

    notifier.send("a" * 5000)

    content = json.loads(recorder.calls[0][1].decode("utf-8"))["content"]
    assert len(content) == 1900


def test_notifier_never_raises_on_transport_error() -> None:
    notifier = DiscordNotifier("https://discord.test/webhook", transport=_Recorder(fail=True))
    # Must not raise even though the transport blows up.
    notifier.send("anything")


# -- message construction ---------------------------------------------------


def _trace(event_name: str, *, message: str = "", plan_id: str | None = None, **data) -> TraceEvent:
    return TraceEvent(
        event_name=event_name,
        workspace_id="w1",
        project_id="p1",
        plan_id=plan_id,
        message=message,
        data=data,
    )


def test_payload_label_for_event_and_pointer() -> None:
    assert (
        _payload_label({"event_type": "pr.merged", "workspace_id": "w1"}) == "`pr.merged` · ws `w1`"
    )
    assert _payload_label({"type": "feature.provisioning.requested", "workspaceId": "w9"}) == (
        "`feature.provisioning.requested` · ws `w9`"
    )
    assert _payload_label({}) == "`unknown`"


def test_summary_lists_completed_actions_and_run_id() -> None:
    traces = [
        _trace("action.completed", plan_id="run-1", tool_name="create_workitem"),
        _trace("action.completed", plan_id="run-1", tool_name="create_workitem"),  # deduped
        _trace("action.completed", plan_id="run-1", tool_name="update_workitem_status"),
        _trace("plan.completed", plan_id="run-1"),
    ]

    message = _summary_message("`pr.merged` · ws `w1`", traces)

    assert message.startswith("✅ `pr.merged` · ws `w1`")
    assert "run: `run-1`" in message
    assert "actions(2): create_workitem, update_workitem_status" in message
    assert "issues:" not in message


def test_summary_flags_in_run_failures() -> None:
    traces = [
        _trace("action.completed", plan_id="run-1", tool_name="create_workitem"),
        _trace("action.failed", plan_id="run-1", message="Tool create_workitem failed: 409"),
    ]

    message = _summary_message("`story.created`", traces)

    assert message.startswith("⚠️ ")
    assert "issues:" in message
    assert "action.failed: Tool create_workitem failed: 409" in message


def test_summary_with_no_actions() -> None:
    assert "actions: none" in _summary_message("`x`", [])


def test_failure_message_includes_exception_type_and_text() -> None:
    message = _failure_message("`pr.merged` · ws `w1`", ValueError("bad payload"))
    assert message == "❌ Failed: `pr.merged` · ws `w1`\n`ValueError: bad payload`"
