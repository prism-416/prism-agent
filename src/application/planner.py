from __future__ import annotations

from domain.context import AgentContext
from domain.plans import AgentPlan
from domain.policies import ActionApprovalPolicy, ApprovalMode
from infrastructure.llm.pydantic_ai_agent_factory import PydanticAIAgentFactory
from infrastructure.registries.prompt_registry import PromptRegistry
from infrastructure.registries.skill_registry import SkillRegistry
from infrastructure.registries.tool_registry import ToolRegistry
from infrastructure.registries.workflow_registry import WorkflowDefinition

APPROVAL_VALUE_MAP = {
    "auto_commit": ApprovalMode.AUTO_COMMIT,
    "user_approval_required": ApprovalMode.USER_APPROVAL_REQUIRED,
    "suggest_only": ApprovalMode.SUGGEST_ONLY,
    "suggest": ApprovalMode.SUGGEST_ONLY,
}


class Planner:
    def __init__(
        self,
        prompt_registry: PromptRegistry,
        skill_registry: SkillRegistry,
        tool_registry: ToolRegistry,
        agent_factory: PydanticAIAgentFactory,
    ) -> None:
        self.prompt_registry = prompt_registry
        self.skill_registry = skill_registry
        self.tool_registry = tool_registry
        self.agent_factory = agent_factory

    def create_plan(
        self,
        context: AgentContext,
        context_snapshot_ref: str,
        workflow: WorkflowDefinition,
    ) -> AgentPlan:
        workflow_prompt = self.prompt_registry.get_workflow(
            workflow.prompt_id,
            workflow.prompt_version,
        )
        skills = self.skill_registry.select(workflow.required_skills)
        allowed_tool_names = self.skill_registry.allowed_tools_for(workflow.required_skills)
        tools = self.tool_registry.select(allowed_tool_names)
        approval_policy = self._approval_policy(
            self._tool_approval_policy(allowed_tool_names) | workflow.approval_policy
        )
        agent = self.agent_factory.create_agent(workflow_prompt, skills, tools)
        plan = agent.generate_plan(context, context_snapshot_ref, approval_policy)
        self._ensure_plan_uses_allowed_tools(plan, set(allowed_tool_names))
        return plan

    @staticmethod
    def _approval_policy(raw_policy: dict[str, str]) -> ActionApprovalPolicy:
        tool_modes = {
            tool_name: APPROVAL_VALUE_MAP.get(mode, ApprovalMode.USER_APPROVAL_REQUIRED)
            for tool_name, mode in raw_policy.items()
        }
        return ActionApprovalPolicy(default_mode=ApprovalMode.AUTO_COMMIT, tool_modes=tool_modes)

    def _tool_approval_policy(self, tool_names: list[str]) -> dict[str, str]:
        return {
            tool_name: self.tool_registry.definition(tool_name).approval_policy
            for tool_name in tool_names
        }

    @staticmethod
    def _ensure_plan_uses_allowed_tools(plan: AgentPlan, allowed_tool_names: set[str]) -> None:
        disallowed = {
            action.tool_name
            for action in plan.actions
            if action.tool_name not in allowed_tool_names
        }
        if disallowed:
            tools = ", ".join(sorted(disallowed))
            raise ValueError(f"Plan contains tools not allowed by selected skills: {tools}")
