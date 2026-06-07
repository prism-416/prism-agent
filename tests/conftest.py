from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.plans import AgentPlan
from infrastructure.llm.pydantic_ai_agent_factory import RuntimePlanningAgent


@pytest.fixture
def prompts_path() -> Path:
    return Path(__file__).resolve().parents[1] / "prompts"


@pytest.fixture(autouse=True)
def stub_gemini_planning(monkeypatch) -> None:
    monkeypatch.setattr(
        RuntimePlanningAgent,
        "_generate_plan_with_pydantic_ai",
        _fake_generate_plan_with_gemini,
    )


def _fake_generate_plan_with_gemini(
    agent: RuntimePlanningAgent,
    context: AgentContext,
    context_snapshot_ref: str,
) -> AgentPlan:
    plan = AgentPlan(
        source_event_id=context.source_event.event_id,
        workspace_id=context.workspace_id,
        project_id=context.project_id,
        goal=agent.workflow_prompt.goal,
        prompt_id=agent.workflow_prompt.id,
        prompt_version=agent.workflow_prompt.version,
        skill_ids=[skill.id for skill in agent.skills],
        tool_names=[tool.name for tool in agent.tools],
        context_snapshot_ref=context_snapshot_ref,
    )
    actions: list[PlannedAction] = []
    previous_action_id: str | None = None
    for index, spec in enumerate(_fake_action_specs(agent, context), start=1):
        action = PlannedAction(
            plan_id=plan.plan_id,
            action_type=spec["action_type"],
            tool_name=spec["tool_name"],
            instruction=spec["instruction"],
            input=spec["input"],
            depends_on=[previous_action_id] if previous_action_id else [],
            idempotency_key=f"fake-gemini:{spec['tool_name']}:{index}",
            expected_entity_versions=context.entity_versions,
        )
        actions.append(action)
        previous_action_id = action.action_id
    return plan.model_copy(update={"actions": actions})


def _fake_action_specs(
    agent: RuntimePlanningAgent,
    context: AgentContext,
) -> list[dict[str, Any]]:
    tool_names = [tool.name for tool in agent.tools]
    if context.source_event.event_type == "feature.provisioning.requested":
        return _fake_feature_provisioning_specs(context, tool_names)

    specs: list[dict[str, Any]] = []
    if "find_duplicate_workitems" in tool_names:
        work_item = context.entities.get("work_item") or context.entities.get("story", {})
        specs.append(
            {
                "action_type": "analysis",
                "tool_name": "find_duplicate_workitems",
                "instruction": (
                    "Check whether the target work item duplicates existing backlog work."
                ),
                "input": {
                    "projectId": context.project_id,
                    "title": work_item.get("title", context.source_event.event_type),
                },
            }
        )
    if "create_agent_suggestion" in tool_names:
        specs.append(
            {
                "action_type": "suggestion",
                "tool_name": "create_agent_suggestion",
                "instruction": agent.workflow_prompt.goal,
                "input": {
                    "title": agent.workflow_prompt.name,
                    "body": (
                        f"{agent.workflow_prompt.goal}\n\n"
                        f"Generated from {context.source_event.event_type}."
                    ),
                    "target_entity_ref": next(iter(context.entity_versions), None),
                    "proposed_changes": {"workflow_id": context.workflow_id},
                },
            }
        )
    elif "create_dashboard_insight" in tool_names:
        specs.append(
            {
                "action_type": "insight",
                "tool_name": "create_dashboard_insight",
                "instruction": "Create a dashboard insight for the project.",
                "input": {
                    "title": agent.workflow_prompt.name,
                    "summary": agent.workflow_prompt.goal,
                },
            }
        )
    elif "generate_sprint_report" in tool_names:
        specs.append(
            {
                "action_type": "report",
                "tool_name": "generate_sprint_report",
                "instruction": "Generate a sprint report draft.",
                "input": {"report": agent.workflow_prompt.goal},
            }
        )
    elif tool_names:
        specs.append(
            {
                "action_type": "tool",
                "tool_name": tool_names[0],
                "instruction": agent.workflow_prompt.goal,
                "input": {"source_event_type": context.source_event.event_type},
            }
        )
    return specs


def _fake_feature_provisioning_specs(
    context: AgentContext,
    tool_names: list[str],
) -> list[dict[str, Any]]:
    payload = context.source_event.event.payload
    feature_specification = _feature_specification_text(payload)
    starts_at = context.source_event.event.occurred_at.replace(microsecond=0)
    ends_at = starts_at + timedelta(days=14)
    requested_by_user_id = _requested_by_user_id(payload)
    specs: list[dict[str, Any]] = []
    if "create_sprint" in tool_names:
        sprint_input = {
            "workspaceId": context.workspace_id,
            "name": _title_from_feature_specification(feature_specification, 50),
            "goal": feature_specification[:1000],
            "startsAt": starts_at.isoformat().replace("+00:00", "Z"),
            "endsAt": ends_at.isoformat().replace("+00:00", "Z"),
        }
        if requested_by_user_id:
            sprint_input["requestedByUserId"] = requested_by_user_id
        specs.append(
            {
                "action_type": "mutation",
                "tool_name": "create_sprint",
                "instruction": "Create a planned sprint for the requested feature.",
                "input": sprint_input,
            }
        )
    if "create_workitem" in tool_names:
        work_item_input = {
            "projectId": context.project_id,
            "title": _title_from_feature_specification(feature_specification, 100),
            "description": feature_specification,
            "priority": "medium",
            "status": "todo",
            "assigneeUsernames": _assignee_usernames(payload, context),
            "labelNames": ["feature-provisioning"],
        }
        if requested_by_user_id:
            work_item_input["requestedByUserId"] = requested_by_user_id
        specs.append(
            {
                "action_type": "mutation",
                "tool_name": "create_workitem",
                "instruction": "Create the top-level task for the requested feature.",
                "input": work_item_input,
            }
        )
    return specs


def _feature_specification_text(payload: dict[str, Any]) -> str:
    value = payload.get("featureSpecification") or payload.get("feature_specification")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "Provision requested feature."


def _title_from_feature_specification(feature_specification: str, max_length: int) -> str:
    first_line = next(
        (line.strip() for line in feature_specification.splitlines() if line.strip()),
        "Feature provisioning",
    )
    return first_line[:max_length]


def _assignee_usernames(payload: dict[str, Any], context: AgentContext) -> list[str]:
    explicit = payload.get("assigneeUsernames")
    if isinstance(explicit, list):
        return [str(username) for username in explicit if username]
    workload_candidates = [
        workload
        for workload in context.entities.get("member_workloads", [])
        if isinstance(workload, dict)
        and workload.get("username")
        and workload.get("jobNames")
        and workload.get("role") != "viewer"
    ]
    if workload_candidates:
        selected = min(
            workload_candidates,
            key=lambda workload: int(workload.get("activeItemCount") or 0),
        )
        return [str(selected["username"])]
    return []


def _requested_by_user_id(payload: dict[str, Any]) -> str | None:
    explicit = payload.get("requestedByUserId")
    if explicit:
        return str(explicit)
    queue_pointer = payload.get("queue_pointer", {})
    if isinstance(queue_pointer, dict) and queue_pointer.get("requestedByUserId"):
        return str(queue_pointer["requestedByUserId"])
    return None
