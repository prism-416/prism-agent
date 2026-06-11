from __future__ import annotations

from typing import Any

from application.context_provider import ContextProvider
from capabilities.plan_quality import plan_quality_issues
from domain.actions import PlannedAction
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

GOOD_DESCRIPTION = (
    "Implements the route with request validation, permission checks against the "
    "project membership, and structured error responses."
)


def _plan_with_items(items: list[dict[str, Any]]) -> AgentPlan:
    plan = AgentPlan(
        source_event_id="e1",
        workspace_id="w1",
        project_id="p1",
        goal="g",
        prompt_id="feature.provision",
        prompt_version="1.0.0",
        context_snapshot_ref="ref",
    )
    action = PlannedAction(
        plan_id=plan.plan_id,
        action_type="mutation",
        tool_name="create_workitem_tree",
        instruction="Create the breakdown.",
        input={"projectId": "p1", "items": items},
        idempotency_key="k1",
    )
    return plan.model_copy(update={"actions": [action]})


def test_clean_plan_has_no_issues() -> None:
    plan = _plan_with_items(
        [
            {"title": "Implement GET /notifications", "description": GOOD_DESCRIPTION},
            {"title": "Implement POST /notifications", "description": GOOD_DESCRIPTION},
        ]
    )

    assert plan_quality_issues(plan, {}) == []


def test_duplicate_titles_are_flagged() -> None:
    plan = _plan_with_items(
        [
            {"title": "Implement GET /notifications", "description": GOOD_DESCRIPTION},
            {"title": "Implement GET /notifications ", "description": GOOD_DESCRIPTION},
        ]
    )

    issues = plan_quality_issues(plan, {})

    assert len(issues) == 1
    assert "repeats the same task" in issues[0]


def test_existing_work_item_duplicates_are_flagged() -> None:
    plan = _plan_with_items([{"title": "Notification center", "description": GOOD_DESCRIPTION}])
    entities = {"project_work_items": [{"itemId": "i1", "title": "Notification Center"}]}

    issues = plan_quality_issues(plan, entities)

    assert len(issues) == 1
    assert "already exist" in issues[0]


def test_weak_descriptions_are_flagged() -> None:
    plan = _plan_with_items(
        [
            {"title": "Implement GET /notifications", "description": "Do it"},
            {
                "title": "Build the settings page",
                "description": "Acceptance Criteria: settings page works correctly always",
            },
            {"title": "Fine task", "description": GOOD_DESCRIPTION},
        ]
    )

    issues = plan_quality_issues(plan, {})

    assert len(issues) == 1
    assert "Implement GET /notifications" in issues[0]
    assert "Build the settings page" in issues[0]
    assert "Fine task" not in issues[0]


def test_concentrated_assignment_is_flagged() -> None:
    items = [
        {
            "title": f"Task {index}",
            "description": GOOD_DESCRIPTION,
            "assigneeUsernames": ["alice"] if index < 5 else ["bob"],
        }
        for index in range(6)
    ]
    entities = {
        "project_members": [
            {"username": "alice"},
            {"username": "bob"},
            {"username": "carol"},
        ]
    }

    issues = plan_quality_issues(_plan_with_items(items), entities)

    assert len(issues) == 1
    assert "alice holds 5 of 6" in issues[0]


def test_balanced_assignment_passes() -> None:
    items = [
        {
            "title": f"Task {index}",
            "description": GOOD_DESCRIPTION,
            "assigneeUsernames": [["alice", "bob", "carol"][index % 3]],
        }
        for index in range(6)
    ]
    entities = {
        "project_members": [
            {"username": "alice"},
            {"username": "bob"},
            {"username": "carol"},
        ]
    }

    assert plan_quality_issues(_plan_with_items(items), entities) == []


class _CriticReviewedPlanningAgent(RuntimePlanningAgent):
    """Always emits the same weak-description plan, so the critic fires once."""

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
        return plan.model_copy(
            update={
                "actions": [
                    PlannedAction(
                        plan_id=plan.plan_id,
                        action_type="mutation",
                        tool_name="create_workitem_tree",
                        instruction="Create the breakdown.",
                        input={
                            "projectId": context.project_id,
                            "items": [{"title": "Build feature", "description": "Do it"}],
                        },
                        idempotency_key=f"critic:create_workitem_tree:{self.attempts}",
                    )
                ]
            }
        )


def test_critic_revises_once_then_accepts(prompts_path) -> None:
    prompt_registry = PromptRegistry(prompts_path)
    workflow_prompt = prompt_registry.get_workflow("feature.provision", "1.0.0")
    workflow = WorkflowRegistry.from_prompt_registry(prompt_registry).get("feature.provision")
    event = DomainEvent(
        event_type="feature.provisioning.requested",
        workspace_id="w1",
        project_id="p1",
        payload={"featureSpecification": "Add saved views."},
        idempotency_key="req-critic",
    )
    snapshot = ContextProvider(PrismApiClient(), prompt_registry).hydrate(
        EventEnvelope.wrap(event), workflow
    )
    skill_registry = SkillRegistry.from_prompt_registry(prompt_registry)
    tool_registry = ToolRegistry.from_prompt_registry(prompt_registry)
    agent = _CriticReviewedPlanningAgent(
        workflow_prompt,
        skill_registry.select(["feature_provisioning"]),
        tool_registry.select(skill_registry.allowed_tools_for(["feature_provisioning"])),
        GeminiModelProvider(Settings()),
    )

    plan = agent.generate_plan(snapshot.context, snapshot.ref, approval_policy=None)

    # One revision pass with reviewer feedback, then the plan is accepted even
    # though the agent kept the weak description.
    assert agent.attempts == 2
    assert "A reviewer found these problems" not in agent.user_prompts[0]
    assert "A reviewer found these problems" in agent.user_prompts[1]
    assert plan.actions[0].input["items"][0]["title"] == "Build feature"


def test_under_assignment_is_flagged_with_capacity_hint() -> None:
    items = [{"title": f"Task {index}", "description": GOOD_DESCRIPTION} for index in range(6)]
    items[0]["assigneeUsernames"] = ["alice"]
    entities = {
        "project_members": [{"username": "alice"}, {"username": "bob"}],
        "member_workloads": [
            {"username": "alice", "activeItemCount": 9},
            {"username": "bob", "activeItemCount": 1},
        ],
    }

    issues = plan_quality_issues(_plan_with_items(items), entities)

    assert len(issues) == 1
    assert "1 of 6 tasks have an assignee" in issues[0]
    assert "bob" in issues[0]
    assert "alice" not in issues[0].split("capacity:")[-1]


def test_fully_assigned_plan_passes_ratio_check() -> None:
    items = [
        {
            "title": f"Task {index}",
            "description": GOOD_DESCRIPTION,
            "assigneeUsernames": [["alice", "bob"][index % 2]],
        }
        for index in range(6)
    ]
    entities = {"project_members": [{"username": "alice"}, {"username": "bob"}]}

    assert plan_quality_issues(_plan_with_items(items), entities) == []
