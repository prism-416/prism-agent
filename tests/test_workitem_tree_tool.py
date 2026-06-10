from __future__ import annotations

from typing import Any

from application.context_provider import ContextProvider
from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.events import DomainEvent, EventEnvelope
from infrastructure.prism_api.client import PrismApiClient
from infrastructure.registries.prompt_registry import PromptRegistry
from infrastructure.registries.skill_registry import SkillRegistry
from infrastructure.registries.tool_registry import ToolRegistry
from infrastructure.registries.workflow_registry import WorkflowRegistry


class _RecordingPrismClient:
    is_configured = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._counter = 0

    def create_work_item(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._counter += 1
        item_id = f"api-item-{self._counter}"
        self.calls.append((project_id, dict(payload)))
        return {"itemId": item_id, **payload}


def _context(prompts_path) -> AgentContext:
    event = DomainEvent(
        event_type="story.created",
        workspace_id="w1",
        project_id="p1",
        payload={"entity_versions": {"story:s1": 1}},
    )
    prompt_registry = PromptRegistry(prompts_path)
    workflow = WorkflowRegistry.from_prompt_registry(prompt_registry).get("story.decompose")
    return (
        ContextProvider(PrismApiClient(), prompt_registry)
        .hydrate(EventEnvelope.wrap(event), workflow)
        .context
    )


def _tree_tool(prompts_path, prism_client=None):
    prompt_registry = PromptRegistry(prompts_path)
    registry = ToolRegistry.from_prompt_registry(prompt_registry, prism_client=prism_client)
    return registry.get("create_workitem_tree")


def _action(input_data: dict[str, Any]) -> PlannedAction:
    return PlannedAction(
        plan_id="plan-1",
        action_type="mutation",
        tool_name="create_workitem_tree",
        instruction="Create the full breakdown.",
        input=input_data,
        idempotency_key="k1",
    )


def test_tree_tool_creates_hierarchy_locally(prompts_path) -> None:
    tool = _tree_tool(prompts_path)
    action = _action(
        {
            "projectId": "p1",
            "items": [
                {
                    "title": "Build API",
                    "description": "Context\n\nAcceptance Criteria",
                    "children": [
                        {"title": "Design schema", "description": "AC"},
                        {"title": "Implement endpoints", "description": "AC"},
                    ],
                },
                {"title": "Build UI", "description": "AC"},
            ],
        }
    )

    result = tool.execute(action, _context(prompts_path))

    assert result.success is True
    assert result.output["createdCount"] == 4
    items = result.output["items"]
    by_title = {item["title"]: item for item in items}
    assert by_title["Build API"]["parentId"] is None
    assert by_title["Design schema"]["parentId"] == by_title["Build API"]["itemId"]
    assert by_title["Implement endpoints"]["parentId"] == by_title["Build API"]["itemId"]
    assert by_title["Build UI"]["parentId"] is None
    assert len(result.emitted_events) == 1
    event = result.emitted_events[0].event
    assert event.event_type == "workitem.tree.created"
    assert event.payload["createdCount"] == 4


def test_tree_tool_links_children_to_api_assigned_parent_ids(prompts_path) -> None:
    client = _RecordingPrismClient()
    tool = _tree_tool(prompts_path, prism_client=client)
    action = _action(
        {
            "projectId": "p1",
            "parentId": "story-1",
            "requestedByUserId": "u1",
            "items": [
                {
                    "title": "Parent task",
                    "description": "AC",
                    "assigneeUsernames": ["alice"],
                    "not_in_contract": "dropped",
                    "children": [{"title": "Child task", "description": "AC"}],
                }
            ],
        }
    )

    result = tool.execute(action, _context(prompts_path))

    assert result.success is True
    parent_call = client.calls[0][1]
    child_call = client.calls[1][1]
    assert parent_call["parentId"] == "story-1"
    assert parent_call["assigneeUsernames"] == ["alice"]
    assert parent_call["requestedByUserId"] == "u1"
    assert "not_in_contract" not in parent_call
    assert "children" not in parent_call
    assert child_call["parentId"] == "api-item-1"
    assert child_call["requestedByUserId"] == "u1"


def test_tree_tool_rejects_empty_or_invalid_trees(prompts_path) -> None:
    tool = _tree_tool(prompts_path)
    context = _context(prompts_path)

    empty = tool.execute(_action({"projectId": "p1", "items": []}), context)
    assert empty.success is False
    assert "non-empty" in empty.error

    untitled = tool.execute(_action({"projectId": "p1", "items": [{"description": "AC"}]}), context)
    assert untitled.success is False
    assert "title" in untitled.error

    too_deep = tool.execute(
        _action(
            {
                "projectId": "p1",
                "items": [
                    {
                        "title": "L1",
                        "children": [
                            {
                                "title": "L2",
                                "children": [{"title": "L3", "children": [{"title": "L4"}]}],
                            }
                        ],
                    }
                ],
            }
        ),
        context,
    )
    assert too_deep.success is False
    assert "depth" in too_deep.error


def test_tree_tool_rejects_oversized_trees(prompts_path) -> None:
    tool = _tree_tool(prompts_path)
    items = [{"title": f"Task {index}", "description": "AC"} for index in range(31)]

    result = tool.execute(_action({"projectId": "p1", "items": items}), _context(prompts_path))

    assert result.success is False
    assert "max size" in result.error


def test_tree_tool_is_registered_and_allowed_by_pm_skills(prompts_path) -> None:
    prompt_registry = PromptRegistry(prompts_path)
    tool_registry = ToolRegistry.from_prompt_registry(prompt_registry)
    skill_registry = SkillRegistry.from_prompt_registry(prompt_registry)

    assert "create_workitem_tree" in tool_registry.names()
    assert "create_workitem_tree" in skill_registry.allowed_tools_for(["feature_provisioning"])
    assert "create_workitem_tree" in skill_registry.allowed_tools_for(["task_decomposition"])
    definition = tool_registry.definition("create_workitem_tree")
    assert definition.approval_policy == "auto_commit"
    assert definition.risk_level == "high"
