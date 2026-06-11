from __future__ import annotations

import json
from io import BytesIO
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from capabilities.tools.sprint_tools import CreateSprintTool
from capabilities.tools.suggestion_tools import CreateAgentSuggestionTool
from capabilities.tools.workitem_tools import CreateWorkItemTool
from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.events import DomainEvent, EventEnvelope
from infrastructure.prism_api.client import PrismApiClient
from infrastructure.registries.prompt_registry import PromptRegistry


class _FakeResponse:
    def __init__(self, body: bytes = b'{"data":{"ok":true}}') -> None:
        self.body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        _ = args

    def read(self) -> bytes:
        return self.body


@pytest.mark.parametrize(
    ("method_name", "args", "expected_url"),
    [
        (
            "create_sprint",
            ("workspace-1", {"startsAt": "2026-06-04T00:00:00Z"}),
            "https://api.example.test/workspaces/workspace-1/sprints/internal",
        ),
        (
            "create_work_item",
            ("project-1", {"title": "Task", "description": "Details"}),
            "https://api.example.test/projects/project-1/work-items/internal",
        ),
        (
            "update_work_item",
            ("project-1", "item-1", {"status": "done"}),
            "https://api.example.test/projects/project-1/work-items/internal/item-1",
        ),
    ],
)
def test_prism_api_client_uses_internal_endpoints_and_token_header(
    monkeypatch,
    method_name: str,
    args: tuple,
    expected_url: str,
) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)

    result = getattr(PrismApiClient("https://api.example.test", "secret-token"), method_name)(*args)

    request = captured["request"]
    headers = dict(request.header_items())
    assert result == {"ok": True}
    assert captured["timeout"] == 30
    assert request.full_url == expected_url
    assert headers["X-internal-api-token"] == "secret-token"
    assert "Authorization" not in headers


def test_prism_api_client_fetches_internal_agent_run_state(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse(
            b'{"data":{"run":{"runId":"run-1"},'
            b'"actions":[{"actionId":"action-1","status":"executed","requiresApproval":false}],'
            b'"steps":[],"actionEvents":[],"memories":[]}}'
        )

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)

    result = PrismApiClient("https://api.example.test", "secret-token").get_agent_run_state(
        "workspace-1",
        "run-1",
    )

    request = captured["request"]
    headers = dict(request.header_items())
    assert result["actions"] == [
        {"actionId": "action-1", "status": "executed", "requiresApproval": False}
    ]
    assert captured["timeout"] == 30
    assert (
        request.full_url
        == "https://api.example.test/workspaces/workspace-1/agent-runs/internal/run-1/state"
    )
    assert headers["X-internal-api-token"] == "secret-token"


@pytest.mark.parametrize(
    ("method_name", "args", "expected_method", "expected_url"),
    [
        (
            "create_agent_run",
            (
                "workspace-1",
                {
                    "agentType": "project_manager",
                    "objective": "Provision feature",
                    "triggerType": "event",
                },
            ),
            "POST",
            "https://api.example.test/workspaces/workspace-1/agent-runs/internal",
        ),
        (
            "update_agent_run_status",
            ("workspace-1", "run-1", {"status": "running"}),
            "PATCH",
            "https://api.example.test/workspaces/workspace-1/agent-runs/internal/run-1/status",
        ),
        (
            "upsert_agent_run_step",
            (
                "workspace-1",
                "run-1",
                {"stepOrder": 0, "stepType": "plan", "status": "completed", "title": "Plan"},
            ),
            "POST",
            "https://api.example.test/workspaces/workspace-1/agent-runs/internal/run-1/steps",
        ),
        (
            "upsert_agent_action",
            (
                "workspace-1",
                "run-1",
                {"actionType": "mutation", "targetType": "tool", "status": "proposed"},
            ),
            "POST",
            "https://api.example.test/workspaces/workspace-1/agent-runs/internal/run-1/actions",
        ),
        (
            "create_agent_action_event",
            ("workspace-1", "action-1", {"eventType": "action.executing"}),
            "POST",
            "https://api.example.test/workspaces/workspace-1/agent-actions/internal/action-1/events",
        ),
    ],
)
def test_prism_api_client_uses_internal_agent_run_endpoints(
    monkeypatch,
    method_name: str,
    args: tuple,
    expected_method: str,
    expected_url: str,
) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)

    getattr(PrismApiClient("https://api.example.test", "secret-token"), method_name)(*args)

    request = captured["request"]
    assert request.get_method() == expected_method
    assert request.full_url == expected_url


def test_prism_api_client_gets_pull_request_with_diff_query(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        _ = timeout
        return _FakeResponse(b'{"data":{"pullNumber":42,"headSha":"abc"}}')

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)

    result = PrismApiClient("https://api.example.test", "secret-token").get_pull_request(
        "project-1",
        42,
    )

    request = captured["request"]
    headers = dict(request.header_items())
    assert result == {"pullNumber": 42, "headSha": "abc"}
    assert request.get_method() == "GET"
    assert request.full_url == (
        "https://api.example.test/projects/project-1/pull-requests/internal/42"
        "?includeDiff=true&includeFiles=true"
    )
    assert headers["X-internal-api-token"] == "secret-token"


def test_prism_api_client_creates_pull_request_review(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        _ = timeout
        return _FakeResponse(b'{"data":{"reviewId":"r1","url":"https://gh/r1"}}')

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)

    result = PrismApiClient("https://api.example.test", "secret-token").create_pull_request_review(
        "project-1",
        42,
        {"headSha": "abc", "event": "COMMENT", "summary": "Looks good"},
    )

    request = captured["request"]
    assert result == {"reviewId": "r1", "url": "https://gh/r1"}
    assert request.get_method() == "POST"
    assert request.full_url == (
        "https://api.example.test/projects/project-1/pull-requests/internal/42/reviews"
    )


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (409, "PrismApiConflictError"),
        (422, "PrismApiUnprocessableError"),
    ],
)
def test_prism_api_client_maps_pull_request_review_errors(
    monkeypatch, status_code: int, expected_error: str
) -> None:
    from infrastructure.prism_api import client as client_module

    def fake_urlopen(request, timeout):
        _ = (request, timeout)
        raise HTTPError(
            "https://api.example.test/projects/project-1/pull-requests/internal/42/reviews",
            status_code,
            "error",
            {},
            BytesIO(b'{"message":"nope"}'),
        )

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)
    expected_exc = getattr(client_module, expected_error)

    with pytest.raises(expected_exc):
        PrismApiClient("https://api.example.test", "secret-token").create_pull_request_review(
            "project-1",
            42,
            {"headSha": "stale", "event": "COMMENT", "summary": "x"},
        )


def test_prism_api_client_upserts_agent_memory_with_bearer_header(
    monkeypatch,
) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)

    PrismApiClient("https://api.example.test", "secret-token").upsert_agent_memory(
        "workspace-1",
        {
            "memoryType": "agent_plan",
            "content": "{}",
            "contentHash": "hash",
        },
    )

    request = captured["request"]
    headers = dict(request.header_items())
    assert request.get_method() == "POST"
    assert request.full_url == "https://api.example.test/workspaces/workspace-1/agent-memories"
    assert headers["Authorization"] == "Bearer secret-token"
    assert "X-internal-api-token" not in headers


def test_prism_api_client_includes_payload_on_http_error(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        _ = (request, timeout)
        raise HTTPError(
            "https://api.example.test/workspaces/workspace-1/agent-runs/internal/run-1/status",
            500,
            "Internal Server Error",
            {},
            BytesIO(b'{"message":"Internal Server Error"}'),
        )

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError) as exc_info:
        PrismApiClient("https://api.example.test", "secret-token").update_agent_run_status(
            "workspace-1",
            "run-1",
            {"status": "running"},
        )

    message = str(exc_info.value)
    assert "failed: 500" in message
    assert 'request_payload={"status": "running"}' in message
    assert "secret-token" not in message


def test_prism_api_client_normalizes_transport_errors(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        _ = (request, timeout)
        raise URLError("connection refused")

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="unreachable"):
        PrismApiClient("https://api.example.test", "secret-token").get_workspace_members(
            "workspace-1"
        )


def test_prism_api_client_normalizes_invalid_json(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        _ = (request, timeout)
        return _FakeResponse(b"{not-json")

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="returned invalid JSON"):
        PrismApiClient("https://api.example.test", "secret-token").get_workspace_members(
            "workspace-1"
        )


def test_create_tools_add_requested_by_user_id_from_queue_pointer() -> None:
    prism_client = _CapturingPrismClient()
    context = _feature_provisioning_context()
    prompt_registry = PromptRegistry()

    sprint_action = PlannedAction(
        plan_id="plan-1",
        action_type="mutation",
        tool_name="create_sprint",
        instruction="Create sprint.",
        input={
            "workspaceId": "workspace-1",
            "startsAt": "2026-06-04T00:00:00Z",
            "endsAt": "2026-06-18T00:00:00Z",
        },
        idempotency_key="k1",
    )
    CreateSprintTool(
        prompt_registry.get_tool("create_sprint"),
        prism_client=prism_client,
    ).execute(sprint_action, context)

    work_item_action = PlannedAction(
        plan_id="plan-1",
        action_type="mutation",
        tool_name="create_workitem",
        instruction="Create work item.",
        input={
            "projectId": "project-1",
            "title": "Task",
            "description": "Details",
            "assigneeUsernames": ["alex"],
        },
        idempotency_key="k2",
    )
    CreateWorkItemTool(
        prompt_registry.get_tool("create_workitem"),
        prism_client=prism_client,
    ).execute(work_item_action, context)

    assert prism_client.calls == [
        (
            "create_sprint",
            "workspace-1",
            {
                "name": "Feature provisioning",
                "startsAt": "2026-06-04T00:00:00Z",
                "endsAt": "2026-06-18T00:00:00Z",
                "requestedByUserId": "user-1",
            },
        ),
        (
            "create_work_item",
            "project-1",
            {
                "title": "Task",
                "description": "Details",
                "assigneeUsernames": ["alex"],
                "requestedByUserId": "user-1",
            },
        ),
    ]


def test_prism_api_client_hydrates_member_jobs_and_workspace_workload(monkeypatch) -> None:
    calls = []
    responses = {
        "https://api.example.test/workspaces/workspace-1/member-workloads/internal": {
            "data": [
                {
                    "userId": "user-alex",
                    "fullName": "Alex Park",
                    "username": "alex",
                    "role": "member",
                    "jobIds": ["job-backend"],
                    "jobNames": ["Backend Engineer"],
                    "joinedAt": "2026-06-01T00:00:00Z",
                    "assignedItemCount": 8,
                    "activeItemCount": 3,
                    "todoItemCount": 1,
                    "inProgressItemCount": 1,
                    "inReviewItemCount": 1,
                    "doneItemCount": 5,
                    "archivedItemCount": 0,
                    "overdueItemCount": 1,
                    "dueTodayItemCount": 0,
                    "dueThisWeekItemCount": 2,
                },
                {
                    "userId": "user-sam",
                    "fullName": "Sam Lee",
                    "username": "sam",
                    "role": "member",
                    "jobIds": ["job-design"],
                    "jobNames": ["Product Designer"],
                    "joinedAt": "2026-06-01T00:00:00Z",
                    "assignedItemCount": 2,
                    "activeItemCount": 1,
                    "todoItemCount": 1,
                    "inProgressItemCount": 0,
                    "inReviewItemCount": 0,
                    "doneItemCount": 1,
                    "archivedItemCount": 0,
                    "overdueItemCount": 0,
                    "dueTodayItemCount": 0,
                    "dueThisWeekItemCount": 1,
                },
            ]
        },
    }

    def fake_urlopen(request, timeout):
        _ = timeout
        url = request.full_url
        calls.append(url)
        if url in responses:
            return _FakeResponse(json.dumps(responses[url]).encode("utf-8"))
        return _FakeResponse(b'{"data":[]}')

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)
    event = DomainEvent(
        event_type="feature.provisioning.requested",
        workspace_id="workspace-1",
        project_id="project-1",
        payload={
            "workspaceMembers": [
                {
                    "userId": "user-alex",
                    "username": "alex",
                    "role": "member",
                    "jobIds": ["job-backend"],
                    "jobNames": ["Backend Engineer"],
                    "joinedAt": "2026-06-01T00:00:00Z",
                },
                {
                    "userId": "user-sam",
                    "username": "sam",
                    "role": "member",
                    "jobIds": ["job-design"],
                    "jobNames": ["Product Designer"],
                    "joinedAt": "2026-06-01T00:00:00Z",
                },
            ],
            "workspaceJobs": [
                {
                    "jobId": "job-backend",
                    "workspaceId": "workspace-1",
                    "name": "Backend Engineer",
                    "description": "API implementation",
                    "createdAt": "2026-06-01T00:00:00Z",
                }
            ],
        },
    )

    entities = PrismApiClient(
        "https://api.example.test",
        "secret-token",
    ).fetch_context_entities(
        event,
        [
            "workspace_members",
            "workspace_jobs",
            "project_members",
            "project_jobs",
            "member_workloads",
        ],
    )

    assert entities["workspace_members"][0]["username"] == "alex"
    assert entities["workspace_members"][0]["jobNames"] == ["Backend Engineer"]
    assert entities["workspace_jobs"][0]["name"] == "Backend Engineer"
    assert entities["project_members"] == entities["workspace_members"]
    assert entities["project_jobs"] == entities["workspace_jobs"]
    assert calls == ["https://api.example.test/workspaces/workspace-1/member-workloads/internal"]
    assert entities["member_workloads"][0] == {
        "userId": "user-alex",
        "fullName": "Alex Park",
        "username": "alex",
        "role": "member",
        "jobIds": ["job-backend"],
        "jobNames": ["Backend Engineer"],
        "joinedAt": "2026-06-01T00:00:00Z",
        "assignedItemCount": 8,
        "activeItemCount": 3,
        "todoItemCount": 1,
        "inProgressItemCount": 1,
        "inReviewItemCount": 1,
        "doneItemCount": 5,
        "archivedItemCount": 0,
        "overdueItemCount": 1,
        "dueTodayItemCount": 0,
        "dueThisWeekItemCount": 2,
    }


def test_prism_api_client_fetches_single_workspace_member_workload(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse(
            json.dumps(
                {
                    "data": {
                        "userId": "user-alex",
                        "fullName": "Alex Park",
                        "username": "alex",
                        "role": "member",
                        "jobIds": ["job-backend"],
                        "jobNames": ["Backend Engineer"],
                        "joinedAt": "2026-06-01T00:00:00Z",
                        "assignedItemCount": 8,
                        "activeItemCount": 3,
                        "todoItemCount": 1,
                        "inProgressItemCount": 1,
                        "inReviewItemCount": 1,
                        "doneItemCount": 5,
                        "archivedItemCount": 0,
                        "overdueItemCount": 1,
                        "dueTodayItemCount": 0,
                        "dueThisWeekItemCount": 2,
                    }
                }
            ).encode("utf-8")
        )

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)

    result = PrismApiClient(
        "https://api.example.test",
        "secret-token",
    ).get_workspace_member_workload("workspace-1", "user-alex")

    assert result["username"] == "alex"
    assert result["activeItemCount"] == 3
    assert (
        captured["request"].full_url
        == "https://api.example.test/workspaces/workspace-1/member-workloads/internal/user-alex"
    )


def test_create_agent_suggestion_tool_is_runtime_internal() -> None:
    prism_client = _CapturingPrismClient()
    context = _story_context()
    prompt_registry = PromptRegistry()
    action = PlannedAction(
        plan_id="plan-1",
        action_type="suggestion",
        tool_name="create_agent_suggestion",
        instruction="Create suggestion.",
        input={
            "projectId": "project-1",
            "title": "Break down story",
            "body": "Create implementation tasks.",
            "target_entity_ref": "story:story-1",
            "proposed_changes": {"workflow_id": "story.decompose"},
        },
        idempotency_key="k1",
    )

    result = CreateAgentSuggestionTool(
        prompt_registry.get_tool("create_agent_suggestion"),
        prism_client=prism_client,
    ).execute(action, context)

    assert prism_client.calls == []
    assert "suggestion_id" in result.output
    assert result.suggestion is not None
    assert result.suggestion.suggestion_id == result.output["suggestion_id"]


def _feature_provisioning_context() -> AgentContext:
    event = DomainEvent(
        event_type="feature.provisioning.requested",
        workspace_id="workspace-1",
        project_id="project-1",
        payload={"queue_pointer": {"requestedByUserId": "user-1"}},
    )
    return AgentContext(
        workspace_id="workspace-1",
        project_id="project-1",
        source_event=EventEnvelope.wrap(event),
        workflow_id="feature.provision",
    )


def _story_context() -> AgentContext:
    event = DomainEvent(
        event_type="story.created",
        workspace_id="workspace-1",
        project_id="project-1",
        payload={"story": {"id": "story-1", "title": "Story", "version": 1}},
    )
    return AgentContext(
        workspace_id="workspace-1",
        project_id="project-1",
        source_event=EventEnvelope.wrap(event),
        workflow_id="story.decompose",
    )


class _CapturingPrismClient:
    is_configured = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def create_sprint(self, workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("create_sprint", workspace_id, payload))
        return {"sprintId": "sprint-1", **payload}

    def create_work_item(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("create_work_item", project_id, payload))
        return {"itemId": "item-1", **payload}


def test_prism_api_client_hydrates_slimmed_project_work_items(monkeypatch) -> None:
    calls = []
    responses = {
        "https://api.example.test/projects/project-1/work-items/internal?limit=100": {
            "data": {
                "items": [
                    {
                        "itemId": "item-1",
                        "workspaceId": "workspace-1",
                        "projectId": "project-1",
                        "parentId": None,
                        "title": "Notification center",
                        "description": "A long description that should not enter context.",
                        "status": "in_progress",
                        "priority": "high",
                        "sortOrder": 1,
                        "dueDate": "2026-06-30",
                        "assigneeUsernames": ["alex"],
                        "labelNames": ["notifications"],
                        "createdAt": "2026-06-01T00:00:00Z",
                    }
                ],
                "total": 1,
                "limit": 100,
                "offset": 0,
            }
        },
    }

    def fake_urlopen(request, timeout):
        _ = timeout
        url = request.full_url
        calls.append(url)
        if url in responses:
            return _FakeResponse(json.dumps(responses[url]).encode("utf-8"))
        return _FakeResponse(b'{"data":[]}')

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)
    event = DomainEvent(
        event_type="feature.provisioning.requested",
        workspace_id="workspace-1",
        project_id="project-1",
        payload={},
    )

    entities = PrismApiClient(
        "https://api.example.test",
        "secret-token",
    ).fetch_context_entities(event, ["project_work_items"])

    assert calls == ["https://api.example.test/projects/project-1/work-items/internal?limit=100"]
    assert entities["project_work_items"] == [
        {
            "itemId": "item-1",
            "title": "Notification center",
            "status": "in_progress",
            "priority": "high",
            "dueDate": "2026-06-30",
            "assigneeUsernames": ["alex"],
            "labelNames": ["notifications"],
        }
    ]


def test_project_work_items_prefer_payload_and_skip_api_when_unconfigured() -> None:
    explicit = [{"itemId": "item-9", "title": "Existing"}]
    event = DomainEvent(
        event_type="feature.provisioning.requested",
        workspace_id="workspace-1",
        project_id="project-1",
        payload={"projectWorkItems": explicit},
    )

    configured = PrismApiClient("https://api.example.test", "secret-token")
    assert configured.fetch_context_entities(event, ["project_work_items"]) == {
        "project_work_items": explicit
    }

    bare_event = DomainEvent(
        event_type="feature.provisioning.requested",
        workspace_id="workspace-1",
        project_id="project-1",
        payload={},
    )
    assert PrismApiClient().fetch_context_entities(bare_event, ["project_work_items"]) == {
        "project_work_items": []
    }
