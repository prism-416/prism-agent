from __future__ import annotations

from datetime import timedelta
from typing import Any

from capabilities.definitions import WorkflowPromptDefinition
from capabilities.skills import RuntimeSkill
from capabilities.tools.base import BaseAgentTool
from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.plans import AgentPlan
from domain.policies import ActionApprovalPolicy
from infrastructure.llm.gemini_model_provider import GeminiModelProvider


class RuntimePlanningAgent:
    def __init__(
        self,
        workflow_prompt: WorkflowPromptDefinition,
        skills: list[RuntimeSkill],
        tools: list[BaseAgentTool],
        model_provider: GeminiModelProvider,
        live_llm_enabled: bool = False,
    ) -> None:
        self.workflow_prompt = workflow_prompt
        self.skills = skills
        self.tools = tools
        self.model_provider = model_provider
        self.live_llm_enabled = live_llm_enabled

    def generate_plan(
        self,
        context: AgentContext,
        context_snapshot_ref: str,
        approval_policy: ActionApprovalPolicy,
    ) -> AgentPlan:
        if self.live_llm_enabled:
            return self._generate_plan_with_pydantic_ai(context, context_snapshot_ref)
        return self._generate_deterministic_plan(context, context_snapshot_ref, approval_policy)

    def _generate_plan_with_pydantic_ai(
        self,
        context: AgentContext,
        context_snapshot_ref: str,
    ) -> AgentPlan:
        self.model_provider.configure_environment()
        try:
            from pydantic_ai import Agent
        except ImportError as exc:
            raise RuntimeError("pydantic-ai is required for live LLM planning.") from exc

        instructions = self._render_instructions()
        agent = Agent(
            self.model_provider.pydantic_ai_model_ref(self.workflow_prompt),
            output_type=AgentPlan,
            instructions=instructions,
        )
        result = agent.run_sync(self._render_user_prompt(context, context_snapshot_ref))
        return result.output

    def _generate_deterministic_plan(
        self,
        context: AgentContext,
        context_snapshot_ref: str,
        approval_policy: ActionApprovalPolicy,
    ) -> AgentPlan:
        plan = AgentPlan(
            source_event_id=context.source_event.event_id,
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            goal=self.workflow_prompt.goal,
            prompt_id=self.workflow_prompt.id,
            prompt_version=self.workflow_prompt.version,
            skill_ids=[skill.id for skill in self.skills],
            tool_names=[tool.name for tool in self.tools],
            context_snapshot_ref=context_snapshot_ref,
        )
        action_specs = self._deterministic_action_specs(context)
        actions: list[PlannedAction] = []
        previous_action_id: str | None = None
        idempotency_prefix = context.source_event.event.idempotency_key or plan.plan_id
        for spec in action_specs:
            tool_name = spec["tool_name"]
            action = PlannedAction(
                plan_id=plan.plan_id,
                action_type=spec["action_type"],
                tool_name=tool_name,
                instruction=spec["instruction"],
                input=spec["input"],
                depends_on=[previous_action_id] if previous_action_id else [],
                requires_approval=approval_policy.requires_approval(tool_name),
                idempotency_key=f"{idempotency_prefix}:{tool_name}:{len(actions) + 1}",
                expected_entity_versions=context.entity_versions,
            )
            actions.append(action)
            previous_action_id = action.action_id
        return plan.model_copy(update={"actions": actions})

    def _deterministic_action_specs(self, context: AgentContext) -> list[dict[str, Any]]:
        tool_names = [tool.name for tool in self.tools]
        if context.source_event.event_type == "feature.provisioning.requested":
            return self._feature_provisioning_action_specs(context, tool_names)

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
                    "instruction": self.workflow_prompt.goal,
                    "input": {
                        "title": self.workflow_prompt.name,
                        "body": (
                            f"{self.workflow_prompt.goal}\n\n"
                            f"Generated from {context.source_event.event_type}."
                        ),
                        "target_entity_ref": self._target_entity_ref(context),
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
                        "title": self.workflow_prompt.name,
                        "summary": self.workflow_prompt.goal,
                    },
                }
            )
        elif "generate_sprint_report" in tool_names:
            specs.append(
                {
                    "action_type": "report",
                    "tool_name": "generate_sprint_report",
                    "instruction": "Generate a sprint report draft.",
                    "input": {"report": self.workflow_prompt.goal},
                }
            )
        elif tool_names:
            specs.append(
                {
                    "action_type": "tool",
                    "tool_name": tool_names[0],
                    "instruction": self.workflow_prompt.goal,
                    "input": {"source_event_type": context.source_event.event_type},
                }
            )

        return specs

    def _feature_provisioning_action_specs(
        self,
        context: AgentContext,
        tool_names: list[str],
    ) -> list[dict[str, Any]]:
        payload = context.source_event.event.payload
        feature_specification = self._feature_specification_text(payload)
        starts_at = context.source_event.event.occurred_at.replace(microsecond=0)
        ends_at = starts_at + timedelta(days=14)
        sprint_name = self._title_from_feature_specification(feature_specification, 50)
        work_item_title = self._title_from_feature_specification(feature_specification, 100)
        specs: list[dict[str, Any]] = []

        if "create_sprint" in tool_names:
            specs.append(
                {
                    "action_type": "mutation",
                    "tool_name": "create_sprint",
                    "instruction": "Create a planned sprint for the requested feature.",
                    "input": {
                        "workspaceId": context.workspace_id,
                        "name": sprint_name,
                        "goal": feature_specification[:1000],
                        "startsAt": starts_at.isoformat().replace("+00:00", "Z"),
                        "endsAt": ends_at.isoformat().replace("+00:00", "Z"),
                        "status": "planned",
                    },
                }
            )

        if "create_workitem" in tool_names:
            specs.append(
                {
                    "action_type": "mutation",
                    "tool_name": "create_workitem",
                    "instruction": "Create the top-level task for the requested feature.",
                    "input": {
                        "projectId": context.project_id,
                        "title": work_item_title,
                        "description": feature_specification,
                        "priority": "medium",
                        "status": "todo",
                        "assigneeUsernames": self._assignee_usernames(payload),
                        "labelNames": ["feature-provisioning"],
                    },
                }
            )

        return specs

    @staticmethod
    def _feature_specification_text(payload: dict[str, Any]) -> str:
        value = payload.get("featureSpecification") or payload.get("feature_specification")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return "Provision requested feature."

    @staticmethod
    def _title_from_feature_specification(feature_specification: str, max_length: int) -> str:
        first_line = next(
            (line.strip() for line in feature_specification.splitlines() if line.strip()),
            "Feature provisioning",
        )
        return first_line[:max_length]

    @staticmethod
    def _assignee_usernames(payload: dict[str, Any]) -> list[str]:
        explicit = payload.get("assigneeUsernames")
        if isinstance(explicit, list):
            return [str(username) for username in explicit if username]
        return []

    def _render_instructions(self) -> str:
        return self.compile_system_prompt()

    def compile_system_prompt(self) -> str:
        sections = [
            self.workflow_prompt.system,
            self._render_workflow_prompt(),
            self._render_skill_prompts(),
            self._render_tool_prompts(),
        ]
        return "\n\n".join(section for section in sections if section).strip()

    def _render_workflow_prompt(self) -> str:
        lines = [
            f"Workflow: {self.workflow_prompt.name} ({self.workflow_prompt.id})",
            f"Goal: {self.workflow_prompt.goal}",
            f"Output schema: {self.workflow_prompt.output_schema}",
        ]
        if self.workflow_prompt.required_context:
            lines.append(
                "Required context:\n"
                + "\n".join(f"- {item}" for item in self.workflow_prompt.required_context)
            )
        if self.workflow_prompt.constraints:
            lines.append(
                "Workflow constraints:\n"
                + "\n".join(f"- {item}" for item in self.workflow_prompt.constraints)
            )
        return "\n".join(lines)

    def _render_skill_prompts(self) -> str:
        if not self.skills:
            return ""
        return "Skills:\n" + "\n\n".join(skill.render_instruction() for skill in self.skills)

    def _render_tool_prompts(self) -> str:
        if not self.tools:
            return ""
        rendered_tools = []
        for tool in self.tools:
            definition = tool.definition
            lines = [
                f"Tool: {definition.name} ({definition.id})",
                f"Description: {definition.description}",
                f"Risk level: {definition.risk_level}",
                f"Approval policy: {definition.approval_policy}",
            ]
            if definition.usage:
                lines.append("Use when:\n" + "\n".join(f"- {item}" for item in definition.usage))
            if definition.do_not_use:
                lines.append(
                    "Do not use when:\n" + "\n".join(f"- {item}" for item in definition.do_not_use)
                )
            rendered_tools.append("\n".join(lines))
        return "Tool permissions:\n" + "\n\n".join(rendered_tools)

    def _render_user_prompt(self, context: AgentContext, context_snapshot_ref: str) -> str:
        return (
            f"Goal: {self.workflow_prompt.goal}\n"
            f"Workflow: {context.workflow_id}\n"
            f"Context snapshot: {context_snapshot_ref}\n"
            f"Context: {context.model_dump_json()}"
        )

    @staticmethod
    def _target_entity_ref(context: AgentContext) -> str | None:
        for versions_ref in context.entity_versions:
            return versions_ref
        return None


class PydanticAIAgentFactory:
    def __init__(self, model_provider: GeminiModelProvider, live_llm_enabled: bool = False) -> None:
        self.model_provider = model_provider
        self.live_llm_enabled = live_llm_enabled

    def create_agent(
        self,
        workflow_prompt: WorkflowPromptDefinition,
        skills: list[RuntimeSkill],
        tools: list[BaseAgentTool],
    ) -> RuntimePlanningAgent:
        return RuntimePlanningAgent(
            workflow_prompt=workflow_prompt,
            skills=skills,
            tools=tools,
            model_provider=self.model_provider,
            live_llm_enabled=self.live_llm_enabled,
        )
