from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from application.context_provider import ContextProvider
from capabilities.plan_quality import plan_quality_issues
from capabilities.tools.workitem_tools import materialize_typed_action_inputs
from domain.actions import PlannedAction, WorkItemUpdateDraft
from domain.backlog import compute_backlog_signals
from domain.events import EventEnvelope, ManualInvocationEvent
from domain.plans import AgentPlan
from infrastructure.prism_api.client import PrismApiClient
from infrastructure.registries.prompt_registry import PromptRegistry
from infrastructure.registries.tool_registry import ToolRegistry
from infrastructure.registries.workflow_registry import WorkflowRegistry

NOW = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
UUID_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
UUID_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
UUID_C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
UUID_D = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
UUID_E = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
GOOD_DESCRIPTION = (
    "Implements the filter query against the indexed columns with permission "
    "checks and pagination so the board stays fast."
)


def _signals(items, workloads=()):
    return compute_backlog_signals(list(items), list(workloads), now=NOW)


def test_signals_flag_stale_overdue_urgent_thin_and_parent_mismatch() -> None:
    items = [
        {
            "itemId": UUID_A,
            "title": "Stale and overdue",
            "status": "in_progress",
            "statusChangedAt": "2026-05-01T00:00:00Z",
            "dueDate": "2026-06-01",
            "description": GOOD_DESCRIPTION,
        },
        {
            "itemId": UUID_B,
            "title": "Urgent unassigned and thin",
            "status": "todo",
            "statusChangedAt": "2026-06-10T00:00:00Z",
            "priority": "urgent",
            "description": "fix it",
        },
        {
            "itemId": UUID_C,
            "title": "Open child of closed parent",
            "status": "todo",
            "parentId": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            "statusChangedAt": "2026-06-10T00:00:00Z",
            "description": GOOD_DESCRIPTION,
        },
        {
            "itemId": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            "title": "Closed parent",
            "status": "done",
            "description": GOOD_DESCRIPTION,
        },
    ]
    workloads = [
        {"username": "alice", "activeItemCount": 9},
        {"username": "bob", "activeItemCount": 2},
    ]

    signals = _signals(items, workloads)

    assert [item["itemId"] for item in signals["staleItems"]] == [UUID_A]
    assert [item["itemId"] for item in signals["overdueItems"]] == [UUID_A]
    assert [item["itemId"] for item in signals["unassignedUrgentItems"]] == [UUID_B]
    assert [item["itemId"] for item in signals["thinDescriptionItems"]] == [UUID_B]
    assert [item["itemId"] for item in signals["closedParentWithOpenChildren"]] == [UUID_C]
    assert signals["overloadedMembers"] == [{"username": "alice", "activeItemCount": 9}]
    assert signals["openItemCount"] == 3


def test_signals_ignore_closed_items() -> None:
    items = [
        {
            "itemId": UUID_A,
            "title": "Done long ago",
            "status": "done",
            "statusChangedAt": "2025-01-01T00:00:00Z",
            "dueDate": "2025-02-01",
            "description": "",
        }
    ]

    signals = _signals(items)

    assert signals["staleItems"] == []
    assert signals["overdueItems"] == []
    assert signals["thinDescriptionItems"] == []


def test_backlog_signals_hydrated_from_explicit_backlog(prompts_path) -> None:
    event = ManualInvocationEvent(
        event_type="refine_backlog",
        workspace_id="w1",
        project_id="p1",
        actor_id="u1",
        payload={
            "backlogWorkItems": [
                {
                    "itemId": UUID_A,
                    "title": "Overdue item",
                    "status": "todo",
                    "dueDate": "2020-01-01",
                    "description": GOOD_DESCRIPTION,
                }
            ],
        },
    )
    prompt_registry = PromptRegistry(prompts_path)
    workflow = WorkflowRegistry.from_prompt_registry(prompt_registry).get("backlog.refine")

    snapshot = ContextProvider(PrismApiClient(), prompt_registry).hydrate(
        EventEnvelope.wrap(event), workflow
    )

    entities = snapshot.context.entities
    assert entities["backlog_work_items"][0]["itemId"] == UUID_A
    assert [i["itemId"] for i in entities["backlog_signals"]["overdueItems"]] == [UUID_A]


class _RecordingClient:
    is_configured = True

    def __init__(self) -> None:
        self.updates: list[tuple[str, str, dict[str, Any]]] = []
        self.sprint_items: list[tuple[str, str, dict[str, Any]]] = []
        self.sprints = [
            {"sprintId": UUID_C, "status": "planned", "createdAt": "2026-06-11T01:00:00Z"},
            {"sprintId": UUID_B, "status": "active", "createdAt": "2026-06-12T00:00:00Z"},
        ]

    def update_work_item(self, project_id, item_id, payload):
        self.updates.append((project_id, item_id, dict(payload)))
        return {"itemId": item_id, **payload}

    def get_workspace_sprints(self, workspace_id):
        _ = workspace_id
        return list(self.sprints)

    def add_sprint_work_items(self, workspace_id, sprint_id, payload):
        self.sprint_items.append((workspace_id, sprint_id, dict(payload)))
        return {}


def _refine_context(prompts_path):
    event = ManualInvocationEvent(
        event_type="refine_backlog",
        workspace_id="w1",
        project_id="p1",
        actor_id="u1",
        payload={
            "projectMembers": [{"username": "alice"}, {"username": "bob"}],
            "backlogWorkItems": [
                {"itemId": UUID_A, "title": "Open unowned", "status": "todo", "description": ""},
                {"itemId": UUID_B, "title": "Open todo", "status": "todo", "description": ""},
                {
                    "itemId": UUID_D,
                    "title": "Being worked on",
                    "status": "in_progress",
                    "description": GOOD_DESCRIPTION,
                    "assigneeUsernames": ["alice"],
                },
                {
                    "itemId": UUID_E,
                    "title": "Owned with real content",
                    "status": "todo",
                    "description": GOOD_DESCRIPTION,
                    "assigneeUsernames": ["alice"],
                },
            ],
        },
    )
    prompt_registry = PromptRegistry(prompts_path)
    workflow = WorkflowRegistry.from_prompt_registry(prompt_registry).get("backlog.refine")
    return (
        ContextProvider(PrismApiClient(), prompt_registry)
        .hydrate(EventEnvelope.wrap(event), workflow)
        .context
    )


def test_bulk_update_tool_applies_normalized_updates(prompts_path) -> None:
    client = _RecordingClient()
    registry = ToolRegistry.from_prompt_registry(PromptRegistry(prompts_path), prism_client=client)
    tool = registry.get("update_workitems_bulk")
    action = PlannedAction(
        plan_id="plan-1",
        action_type="mutation",
        tool_name="update_workitems_bulk",
        instruction="Refine backlog.",
        input={
            "projectId": "p1",
            "updates": [
                {"itemId": UUID_A, "priority": "High", "dueDate": "2026-06-20"},
                {"itemId": "not-a-uuid", "priority": "low"},
                {"itemId": UUID_B, "assigneeUsernames": ["ghost", "bob"]},
            ],
        },
        idempotency_key="k1",
    )

    result = tool.execute(action, _refine_context(prompts_path))

    assert result.success is True
    assert result.output["updatedCount"] == 2
    assert client.updates[0][1] == UUID_A
    assert client.updates[0][2]["priority"] == "high"
    assert client.updates[1][2]["assigneeUsernames"] == ["bob"]
    assert result.output["skipped"][0]["itemId"] == "not-a-uuid"
    assert result.output["skipped"][0]["reason"] == "invalid_item_id"


def test_bulk_update_policy_protects_human_work(prompts_path) -> None:
    client = _RecordingClient()
    registry = ToolRegistry.from_prompt_registry(PromptRegistry(prompts_path), prism_client=client)
    tool = registry.get("update_workitems_bulk")
    action = PlannedAction(
        plan_id="plan-1",
        action_type="mutation",
        tool_name="update_workitems_bulk",
        instruction="Refine backlog.",
        input={
            "projectId": "p1",
            "updates": [
                {"itemId": UUID_D, "priority": "high"},
                {"itemId": UUID_E, "description": "rewritten", "assigneeUsernames": ["bob"]},
                {"itemId": UUID_A, "title": "New title", "status": "done", "priority": "high"},
                {"itemId": UUID_C, "priority": "low"},
            ],
        },
        idempotency_key="k1",
    )

    result = tool.execute(action, _refine_context(prompts_path))

    assert result.success is True
    # In-progress item untouched.
    assert {"itemId": UUID_D, "reason": "item_started_or_closed"} in result.output["skipped"]
    # Owned item with real content: rewrite and reassignment both dropped.
    assert any(
        entry["itemId"] == UUID_E and entry["reason"] == "no_safe_fields"
        for entry in result.output["skipped"]
    )
    # Unknown-to-context item untouched.
    assert {"itemId": UUID_C, "reason": "not_in_hydrated_context"} in result.output["skipped"]
    # Title/status dropped, safe priority change applied.
    assert result.output["updatedCount"] == 1
    applied = result.output["updates"][0]
    assert applied["itemId"] == UUID_A
    assert applied["priority"] == "high"
    assert "title" not in applied and "status" not in applied
    assert sorted(applied["droppedFields"]) == ["status", "title"]
    assert client.updates[0][2] == {"priority": "high"}


def test_bulk_update_tool_rejects_empty_updates(prompts_path) -> None:
    registry = ToolRegistry.from_prompt_registry(PromptRegistry(prompts_path))
    tool = registry.get("update_workitems_bulk")
    action = PlannedAction(
        plan_id="plan-1",
        action_type="mutation",
        tool_name="update_workitems_bulk",
        instruction="Refine backlog.",
        input={"projectId": "p1"},
        idempotency_key="k1",
    )

    result = tool.execute(action, _refine_context(prompts_path))

    assert result.success is False
    assert "non-empty 'updates'" in result.error


def test_update_drafts_materialize_into_input() -> None:
    plan = AgentPlan(
        source_event_id="e1",
        workspace_id="w1",
        project_id="p1",
        goal="g",
        prompt_id="backlog.refine",
        prompt_version="1.0.0",
        context_snapshot_ref="ref",
    )
    bulk = PlannedAction(
        plan_id=plan.plan_id,
        action_type="mutation",
        tool_name="update_workitems_bulk",
        instruction="Fix priorities.",
        input={"projectId": "p1"},
        work_item_updates=[
            WorkItemUpdateDraft(item_id=UUID_A, priority="high", due_date="2026-06-20"),
            WorkItemUpdateDraft(item_id=UUID_B, assignee_usernames=["bob"]),
        ],
        idempotency_key="k1",
    )
    mapping = PlannedAction(
        plan_id=plan.plan_id,
        action_type="mutation",
        tool_name="add_sprint_work_items",
        instruction="Scope sprint.",
        input={},
        target_item_ids=[UUID_A, UUID_B],
        idempotency_key="k2",
    )
    plan = plan.model_copy(update={"actions": [bulk, mapping]})

    materialized = materialize_typed_action_inputs(plan)

    updates = materialized.get_action(bulk.action_id).input["updates"]
    assert updates == [
        {"itemId": UUID_A, "priority": "high", "dueDate": "2026-06-20"},
        {"itemId": UUID_B, "assigneeUsernames": ["bob"]},
    ]
    assert materialized.get_action(mapping.action_id).input["itemIds"] == [UUID_A, UUID_B]


def test_sprint_mapping_resolves_latest_planned_sprint(prompts_path) -> None:
    client = _RecordingClient()
    registry = ToolRegistry.from_prompt_registry(PromptRegistry(prompts_path), prism_client=client)
    tool = registry.get("add_sprint_work_items")
    action = PlannedAction(
        plan_id="plan-1",
        action_type="mutation",
        tool_name="add_sprint_work_items",
        instruction="Scope the sprint.",
        input={"itemIds": [UUID_A, "junk", UUID_A]},
        idempotency_key="k1",
    )

    result = tool.execute(action, _refine_context(prompts_path))

    assert result.success is True
    workspace_id, sprint_id, payload = client.sprint_items[0]
    assert workspace_id == "w1"
    assert sprint_id == UUID_C
    assert payload["itemIds"] == [UUID_A]
    assert result.output["addedCount"] == 1


def test_critic_flags_unknown_and_closed_update_targets() -> None:
    plan = AgentPlan(
        source_event_id="e1",
        workspace_id="w1",
        project_id="p1",
        goal="g",
        prompt_id="backlog.refine",
        prompt_version="1.0.0",
        context_snapshot_ref="ref",
    )
    action = PlannedAction(
        plan_id=plan.plan_id,
        action_type="mutation",
        tool_name="update_workitems_bulk",
        instruction="Fix priorities.",
        input={
            "projectId": "p1",
            "updates": [
                {"itemId": UUID_A, "priority": "high"},
                {"itemId": UUID_B, "status": "todo"},
                {"itemId": UUID_C, "priority": "low"},
            ],
        },
        idempotency_key="k1",
    )
    plan = plan.model_copy(update={"actions": [action]})
    entities = {
        "backlog_work_items": [
            {"itemId": UUID_A, "title": "Open item", "status": "todo"},
            {"itemId": UUID_B, "title": "Closed item", "status": "done"},
        ]
    }

    issues = plan_quality_issues(plan, entities)

    assert len(issues) == 2
    assert any(UUID_C in issue for issue in issues)
    assert any("Closed item" in issue and "started or closed" in issue for issue in issues)
