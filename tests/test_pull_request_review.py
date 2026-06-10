from __future__ import annotations

from typing import Any

import pytest

from capabilities.tools.github_tools import SubmitPullRequestReviewTool
from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.events import DomainEvent, EventEnvelope
from infrastructure.prism_api.client import (
    PrismApiClient,
    PrismApiConflictError,
    PrismApiUnprocessableError,
)
from infrastructure.registries.prompt_registry import PromptRegistry

DIFF = {
    "pullNumber": 42,
    "headSha": "abc123",
    "files": [{"filename": "src/app.py", "status": "modified", "patch": "@@"}],
}


def _context(payload: dict[str, Any]) -> AgentContext:
    event = DomainEvent(
        event_type="pr.opened",
        workspace_id="workspace-1",
        project_id="project-1",
        payload=payload,
    )
    return AgentContext(
        workspace_id="workspace-1",
        project_id="project-1",
        source_event=EventEnvelope.wrap(event),
        workflow_id="pr.review",
        entities={"pull_request_diff": DIFF},
    )


def _tool(prism_client: Any) -> SubmitPullRequestReviewTool:
    return SubmitPullRequestReviewTool(
        PromptRegistry().get_tool("submit_pull_request_review"),
        prism_client=prism_client,
    )


def _action(input_data: dict[str, Any]) -> PlannedAction:
    return PlannedAction(
        plan_id="plan-1",
        action_type="review",
        tool_name="submit_pull_request_review",
        instruction="Review the pull request.",
        input=input_data,
        idempotency_key="k1",
    )


class _ReviewPrismClient:
    is_configured = True

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.raises = raises
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []

    def create_pull_request_review(
        self, project_id: str, pull_number: Any, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((project_id, pull_number, payload))
        if self.raises is not None:
            raise self.raises
        return {"reviewId": "r1", "url": "https://gh/r1"}


def test_submit_review_posts_grounded_review_with_head_sha() -> None:
    client = _ReviewPrismClient()
    context = _context({"queue_pointer": {"requestedByUserId": "user-1"}})
    action = _action(
        {
            "event": "request_changes",
            "summary": "Needs a guard.",
            "comments": [
                {"path": "src/app.py", "line": 12, "body": "Handle None here."},
                {"path": "src/app.py", "line": 0, "body": "dropped: no line"},
            ],
        }
    )

    result = _tool(client).execute(action, context)

    assert result.success is True
    assert result.output == {"reviewId": "r1", "url": "https://gh/r1"}
    project_id, pull_number, payload = client.calls[0]
    assert project_id == "project-1"
    assert pull_number == 42
    assert payload["headSha"] == "abc123"
    assert payload["event"] == "REQUEST_CHANGES"
    assert payload["requestedByUserId"] == "user-1"
    assert payload["comments"] == [{"path": "src/app.py", "line": 12, "body": "Handle None here."}]


def test_submit_review_returns_failure_on_stale_head() -> None:
    client = _ReviewPrismClient(raises=PrismApiConflictError("409"))
    result = _tool(client).execute(_action({"event": "COMMENT", "summary": "x"}), _context({}))

    assert result.success is False
    assert result.output["reason"] == "stale_pull_request_head"
    assert result.error is not None


def test_submit_review_returns_failure_on_invalid_anchor() -> None:
    client = _ReviewPrismClient(raises=PrismApiUnprocessableError("422"))
    result = _tool(client).execute(_action({"event": "COMMENT", "summary": "x"}), _context({}))

    assert result.success is False
    assert result.output["reason"] == "invalid_review_comment_anchor"


def test_submit_review_offline_echoes_without_client_call() -> None:
    result = _tool(None).execute(
        _action({"event": "APPROVE", "summary": "LGTM"}),
        _context({}),
    )

    assert result.success is True
    assert result.output["event"] == "APPROVE"
    assert result.output["pullNumber"] == 42


def test_pull_request_diff_hydration_uses_get_endpoint(monkeypatch) -> None:
    captured = {}

    class _FakeResponse:
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            _ = args

        def read(self) -> bytes:
            return b'{"data":{"pullNumber":42,"headSha":"abc123"}}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        _ = timeout
        return _FakeResponse()

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)

    event = DomainEvent(
        event_type="pr.opened",
        workspace_id="workspace-1",
        project_id="project-1",
        payload={"pullNumber": 42},
    )
    entities = PrismApiClient("https://api.example.test", "secret-token").fetch_context_entities(
        event,
        ["pull_request_diff"],
    )

    assert entities["pull_request_diff"] == {"pullNumber": 42, "headSha": "abc123"}
    assert captured["url"] == (
        "https://api.example.test/projects/project-1/pull-requests/internal/42"
        "?includeDiff=true&includeFiles=true"
    )


def test_pull_request_diff_hydration_prefers_explicit_payload() -> None:
    event = DomainEvent(
        event_type="pr.opened",
        workspace_id="workspace-1",
        project_id="project-1",
        payload={"pull_request_diff": DIFF, "pullNumber": 42},
    )
    entities = PrismApiClient().fetch_context_entities(event, ["pull_request_diff"])

    assert entities["pull_request_diff"] == DIFF


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
