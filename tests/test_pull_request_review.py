from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from application.planner import Planner
from capabilities.tools.github_tools import SubmitPullRequestReviewTool
from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.events import DomainEvent, EventEnvelope
from infrastructure.observability.logging_config import get_logger
from infrastructure.prism_api.client import (
    PrismApiClient,
    PrismApiConflictError,
    PrismApiUnprocessableError,
)
from infrastructure.registries.prompt_registry import PromptRegistry
from infrastructure.registries.skill_registry import SkillRegistry
from infrastructure.registries.tool_registry import ToolRegistry
from infrastructure.registries.workflow_registry import WorkflowRegistry

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


def test_pull_request_diff_hydration_fetches_when_payload_has_stub(monkeypatch) -> None:
    captured = {}

    class _FakeResponse:
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            _ = args

        def read(self) -> bytes:
            return (
                b'{"data":{"pullNumber":42,"headSha":"fresh",'
                b'"files":[{"filename":"src/app.py","status":"modified",'
                b'"additions":1,"deletions":0,"patch":"@@ -1 +1 @@"}],'
                b'"commits":[],"truncated":false}}'
            )

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        _ = timeout
        return _FakeResponse()

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)

    event = DomainEvent(
        event_type="pr.opened",
        workspace_id="workspace-1",
        project_id="project-1",
        payload={
            "action": "opened",
            "number": 42,
            "pull_request_diff": {"headSha": "stub"},
        },
    )
    entities = PrismApiClient("https://api.example.test", "secret-token").fetch_context_entities(
        event,
        ["pull_request_diff"],
    )

    assert entities["pull_request_diff"]["headSha"] == "fresh"
    assert entities["pull_request_diff"]["files"][0]["patch"] == "@@ -1 +1 @@"
    assert captured["url"] == (
        "https://api.example.test/projects/project-1/pull-requests/internal/42"
        "?includeDiff=true&includeFiles=true"
    )


def test_pull_request_diff_hydration_uses_embedded_pull_request(
    monkeypatch, diff_log_capture
) -> None:
    # The real PR-review payload embeds the diff under pullRequest.files and
    # carries no separate pull_request_diff entity and no project_id, so the
    # API fetch can't fire. The diff must still hydrate straight from the event.
    def _unexpected_urlopen(request, timeout):  # pragma: no cover - must not run
        raise AssertionError("hydration should not call the API when the diff is embedded")

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", _unexpected_urlopen)
    handler = diff_log_capture
    event = DomainEvent(
        event_type="pr.review_requested",
        workspace_id="workspace-1",
        payload={
            "schemaVersion": "1.0",
            "workspaceId": "workspace-1",
            "repositoryFullName": "prism-416/prism-agent",
            "pullNumber": 33,
            "headSha": "fbd9682",
            "pullRequest": {
                "pullNumber": 33,
                "state": "open",
                "headSha": "fbd9682",
                "files": [
                    {
                        "filename": "tests/test_pull_request_review.py",
                        "status": "modified",
                        "additions": 1,
                        "deletions": 3,
                        "patch": "@@ -328,9 +328,7 @@\n-x\n+y",
                    }
                ],
                "truncated": False,
            },
        },
    )

    # Unconfigured client (no base_url) and no project_id: the failing prod shape.
    entities = PrismApiClient().fetch_context_entities(event, ["pull_request_diff"])

    diff = entities["pull_request_diff"]
    assert diff["pullNumber"] == 33
    assert diff["headSha"] == "fbd9682"
    assert diff["files"][0]["patch"].startswith("@@ -328,9 +328,7 @@")
    record = _diff_records(handler)[-1]
    assert record["source"] == "embedded"
    assert record["has_usable_diff"] is True
    assert record["level"] == logging.INFO


def test_pull_request_diff_hydration_prefers_explicit_payload() -> None:
    event = DomainEvent(
        event_type="pr.opened",
        workspace_id="workspace-1",
        project_id="project-1",
        payload={"pull_request_diff": DIFF, "pullNumber": 42},
    )
    entities = PrismApiClient().fetch_context_entities(event, ["pull_request_diff"])

    assert entities["pull_request_diff"] == DIFF


def test_pull_request_event_context_uses_pr_payload_metadata() -> None:
    event = DomainEvent(
        event_type="pr.opened",
        workspace_id="workspace-1",
        project_id="project-1",
        payload={
            "action": "opened",
            "number": 42,
            "pull_request": {"title": "Add diff fetch", "state": "open"},
            "repository": {"full_name": "octo/repo"},
        },
    )
    entities = PrismApiClient().fetch_context_entities(event, ["pull_request_event"])

    assert entities["pull_request_event"] == {
        "pullRequest": {"title": "Add diff fetch", "state": "open"},
        "pullNumber": 42,
        "action": "opened",
        "repository": {"full_name": "octo/repo"},
    }


class _CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def diff_log_capture():
    logger = get_logger()
    handler = _CapturingHandler()
    previous_level = logger.level
    previous_propagate = logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


def _diff_records(handler: _CapturingHandler) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in handler.records:
        try:
            payload = json.loads(record.getMessage())
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("log") == "diff.hydration":
            records.append({"level": record.levelno, **payload})
    return records


def _fake_response(body: bytes):
    class _FakeResponse:
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            _ = args

        def read(self) -> bytes:
            return body

    return _FakeResponse()


def test_diff_hydration_logs_info_when_payload_is_hydrated(diff_log_capture) -> None:
    handler = diff_log_capture
    event = DomainEvent(
        event_type="pr.opened",
        workspace_id="workspace-1",
        project_id="project-1",
        payload={"pull_request_diff": DIFF},
    )

    PrismApiClient().fetch_context_entities(event, ["pull_request_diff"])

    records = _diff_records(handler)
    assert len(records) == 1
    record = records[0]
    assert record["level"] == logging.INFO
    assert record["source"] == "payload"
    assert record["fetch_attempted"] is False
    assert record["has_usable_diff"] is True
    assert record["files_count"] == 1
    assert "guard" not in record


def test_diff_hydration_warns_when_fetch_returns_fileless_pr(monkeypatch, diff_log_capture) -> None:
    handler = diff_log_capture
    monkeypatch.setattr(
        "infrastructure.prism_api.client.urlopen",
        lambda request, timeout: _fake_response(b'{"data":{"pullNumber":42,"headSha":"abc123"}}'),
    )
    event = DomainEvent(
        event_type="pr.review_requested",
        workspace_id="workspace-1",
        project_id="project-1",
        payload={"pullNumber": 42},
    )

    entities = PrismApiClient("https://api.example.test", "secret-token").fetch_context_entities(
        event,
        ["pull_request_diff"],
    )

    # Behaviour is unchanged: the fileless fetch result still passes through.
    assert entities["pull_request_diff"] == {"pullNumber": 42, "headSha": "abc123"}
    records = _diff_records(handler)
    assert len(records) == 1
    record = records[0]
    assert record["level"] == logging.WARNING
    assert record["source"] == "fetch"
    assert record["fetch_attempted"] is True
    assert record["pull_number"] == 42
    assert record["has_usable_diff"] is False
    assert record["files_count"] is None
    assert "guard" in record


def test_diff_hydration_warns_when_fetch_skipped_for_missing_project(diff_log_capture) -> None:
    handler = diff_log_capture
    event = DomainEvent(
        event_type="pr.review_requested",
        workspace_id="workspace-1",
        payload={"pullNumber": 42},
    )

    PrismApiClient("https://api.example.test", "secret-token").fetch_context_entities(
        event,
        ["pull_request_diff"],
    )

    records = _diff_records(handler)
    assert len(records) == 1
    record = records[0]
    assert record["level"] == logging.WARNING
    assert record["fetch_attempted"] is False
    assert record["skip_reason"] == "missing_project_id"
    assert record["has_usable_diff"] is False
    assert "guard" in record


def test_pr_review_workflow_auto_commits_submit_review() -> None:
    # The pr.review run must post its review directly: the effective approval
    # policy for submit_pull_request_review has to resolve to no approval gate,
    # built the same way Planner.create_plan merges tool defaults with the
    # workflow override (the workflow side wins the dict merge).
    prompt_registry = PromptRegistry()
    workflow = WorkflowRegistry.from_prompt_registry(prompt_registry).get("pr.review")
    skill_registry = SkillRegistry.from_prompt_registry(prompt_registry)
    tool_registry = ToolRegistry.from_prompt_registry(prompt_registry)

    allowed = skill_registry.allowed_tools_for(workflow.required_skills)
    assert "submit_pull_request_review" in allowed
    tool_modes = {name: tool_registry.definition(name).approval_policy for name in allowed}
    policy = Planner._approval_policy(tool_modes | workflow.approval_policy)

    assert policy.requires_approval("submit_pull_request_review") is False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
