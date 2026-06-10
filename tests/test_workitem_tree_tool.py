from __future__ import annotations

import json
from typing import Any

from application.context_provider import ContextProvider
from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.events import DomainEvent, EventEnvelope
from domain.plans import AgentPlan
from infrastructure.config.settings import Settings
from infrastructure.llm.gemini_model_provider import GeminiModelProvider
from infrastructure.llm.pydantic_ai_agent_factory import RuntimePlanningAgent
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


def test_tree_tool_coerces_common_input_shapes(prompts_path) -> None:
    tool = _tree_tool(prompts_path)
    context = _context(prompts_path)

    stringified = tool.execute(
        _action(
            {
                "projectId": "p1",
                "items": json.dumps([{"title": "From string", "description": "AC"}]),
            }
        ),
        context,
    )
    assert stringified.success is True
    assert stringified.output["createdCount"] == 1

    alternate_key = tool.execute(
        _action({"projectId": "p1", "tasks": [{"title": "From tasks key", "description": "AC"}]}),
        context,
    )
    assert alternate_key.success is True
    assert alternate_key.output["createdCount"] == 1

    single_node = tool.execute(
        _action(
            {
                "projectId": "p1",
                "title": "Single root",
                "description": "AC",
                "children": [{"title": "Child", "description": "AC"}],
            }
        ),
        context,
    )
    assert single_node.success is True
    assert single_node.output["createdCount"] == 2


class _TreeRetryPlanningAgent(RuntimePlanningAgent):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.attempts = 0
        self.user_prompts: list[str] = []

    def _generate_plan_with_pydantic_ai(self, context, context_snapshot_ref):
        self.attempts += 1
        self.user_prompts.append(self._render_user_prompt(context, context_snapshot_ref))
        plan = AgentPlan(
            source_event_id=context.source_event.event_id,
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            goal=self.workflow_prompt.goal,
            prompt_id=self.workflow_prompt.id,
            prompt_version=self.workflow_prompt.version,
            context_snapshot_ref=context_snapshot_ref,
        )
        input_data: dict[str, Any] = {"projectId": context.project_id}
        if self.attempts > 1:
            input_data["items"] = [{"title": "Build feature", "description": "AC"}]
        return plan.model_copy(
            update={
                "actions": [
                    PlannedAction(
                        plan_id=plan.plan_id,
                        action_type="mutation",
                        tool_name="create_workitem_tree",
                        instruction="Create the breakdown.",
                        input=input_data,
                        idempotency_key=f"retry:create_workitem_tree:{self.attempts}",
                    )
                ]
            }
        )


def test_planning_retries_tree_action_without_items(prompts_path) -> None:
    prompt_registry = PromptRegistry(prompts_path)
    workflow_prompt = prompt_registry.get_workflow("feature.provision", "1.0.0")
    workflow = WorkflowRegistry.from_prompt_registry(prompt_registry).get("feature.provision")
    event = DomainEvent(
        event_type="feature.provisioning.requested",
        workspace_id="w1",
        project_id="p1",
        payload={"featureSpecification": "Add saved views."},
        idempotency_key="req-tree-retry",
    )
    snapshot = ContextProvider(PrismApiClient(), prompt_registry).hydrate(
        EventEnvelope.wrap(event), workflow
    )
    skill_registry = SkillRegistry.from_prompt_registry(prompt_registry)
    tool_registry = ToolRegistry.from_prompt_registry(prompt_registry)
    agent = _TreeRetryPlanningAgent(
        workflow_prompt,
        skill_registry.select(["feature_provisioning"]),
        tool_registry.select(skill_registry.allowed_tools_for(["feature_provisioning"])),
        GeminiModelProvider(Settings()),
    )

    plan = agent.generate_plan(snapshot.context, snapshot.ref, approval_policy=None)

    assert agent.attempts == 2
    assert plan.actions[0].input["items"]
    assert "Planning retry feedback:" not in agent.user_prompts[0]
    assert "create_workitem_tree action input was invalid" in agent.user_prompts[1]


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
