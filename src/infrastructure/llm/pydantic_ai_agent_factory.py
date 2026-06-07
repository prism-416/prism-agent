from __future__ import annotations

import json

from capabilities.definitions import WorkflowPromptDefinition
from capabilities.skills import RuntimeSkill
from capabilities.tools.base import BaseAgentTool
from domain.context import AgentContext
from domain.plans import AgentPlan
from domain.policies import ActionApprovalPolicy
from infrastructure.llm.gemini_model_provider import GeminiModelProvider

PLAN_GENERATION_ATTEMPTS = 3


class RuntimePlanningAgent:
    def __init__(
        self,
        workflow_prompt: WorkflowPromptDefinition,
        skills: list[RuntimeSkill],
        tools: list[BaseAgentTool],
        model_provider: GeminiModelProvider,
    ) -> None:
        self.workflow_prompt = workflow_prompt
        self.skills = skills
        self.tools = tools
        self.model_provider = model_provider
        self._retry_feedback: str | None = None

    def generate_plan(
        self,
        context: AgentContext,
        context_snapshot_ref: str,
        approval_policy: ActionApprovalPolicy,
    ) -> AgentPlan:
        _ = approval_policy
        plan: AgentPlan | None = None
        self._retry_feedback = None
        try:
            for attempt in range(1, PLAN_GENERATION_ATTEMPTS + 1):
                if attempt > 1:
                    self._retry_feedback = (
                        "The previous planning attempt produced zero executable actions. "
                        "Return an AgentPlan with at least one action using an allowed tool "
                        "when the workflow goal requires work. Do not leave actions empty "
                        "for feature provisioning, task decomposition, or other actionable "
                        "workflows unless the hydrated context proves there is no safe action."
                    )
                plan = self._generate_plan_with_pydantic_ai(context, context_snapshot_ref)
                if plan.actions:
                    return plan
            return plan
        finally:
            self._retry_feedback = None

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
            if definition.input_contract:
                lines.append(
                    "Input contract:\n"
                    + json.dumps(definition.input_contract, indent=2, sort_keys=True)
                )
            rendered_tools.append("\n".join(lines))
        return "Tool permissions:\n" + "\n\n".join(rendered_tools)

    def _render_user_prompt(self, context: AgentContext, context_snapshot_ref: str) -> str:
        prompt = (
            f"Goal: {self.workflow_prompt.goal}\n"
            f"Workflow: {context.workflow_id}\n"
            f"Context snapshot: {context_snapshot_ref}\n"
            f"Context: {context.model_dump_json()}"
        )
        if self._retry_feedback:
            prompt += f"\n\nPlanning retry feedback: {self._retry_feedback}"
        return prompt


class PydanticAIAgentFactory:
    def __init__(self, model_provider: GeminiModelProvider) -> None:
        self.model_provider = model_provider

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
        )
