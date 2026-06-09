from __future__ import annotations

from domain.actions import ActionStatus, PlannedAction
from domain.context import AgentContext
from domain.plans import AgentPlan, PlanStatus
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


def _scope_context(context: AgentContext, context_scope: list[str] | None) -> AgentContext:
    """Restrict hydrated entities to a subagent's declared scope for planning.

    An empty or absent scope returns the context unchanged (whole-workflow planning).
    Only ``entities`` is sliced; versions, permissions, and runtime metadata stay
    intact so validation keeps working against the full set.
    """
    if not context_scope:
        return context
    scope = set(context_scope)
    sliced = {key: value for key, value in context.entities.items() if key in scope}
    return context.model_copy(update={"entities": sliced})


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
        agent_run_id: str,
        *,
        plan_id: str | None = None,
        skill_ids: list[str] | None = None,
        context_scope: list[str] | None = None,
        model_tier: str | None = None,
    ) -> AgentPlan:
        workflow_prompt = self.prompt_registry.get_workflow(
            workflow.prompt_id,
            workflow.prompt_version,
        )
        skill_names = skill_ids if skill_ids is not None else workflow.required_skills
        target_plan_id = plan_id or agent_run_id
        skills = self.skill_registry.select(skill_names)
        allowed_tool_names = self.skill_registry.allowed_tools_for(skill_names)
        tools = self.tool_registry.select(allowed_tool_names)
        approval_policy = self._approval_policy(
            self._tool_approval_policy(allowed_tool_names) | workflow.approval_policy
        )
        planning_context = _scope_context(context, context_scope)
        agent = self.agent_factory.create_agent(
            workflow_prompt, skills, tools, model_tier=model_tier
        )
        plan = agent.generate_plan(planning_context, context_snapshot_ref, approval_policy)
        plan = self._normalize_plan_for_runtime(
            plan,
            context,
            context_snapshot_ref,
            target_plan_id,
            self._idempotency_prefix(context, agent_run_id, plan_id),
            workflow_prompt.id,
            workflow_prompt.version,
            workflow_prompt.goal,
            [skill.id for skill in skills],
            [tool.name for tool in tools],
            approval_policy,
        )
        self._ensure_plan_uses_allowed_tools(plan, set(allowed_tool_names))
        return plan

    @staticmethod
    def _idempotency_prefix(context: AgentContext, agent_run_id: str, plan_id: str | None) -> str:
        # A scoped sub-plan keys idempotency off its own plan_id to avoid colliding
        # with sibling nodes that reuse the same tool. The default (whole-workflow)
        # plan preserves the original prefix exactly.
        if plan_id is not None and plan_id != agent_run_id:
            return plan_id
        return context.source_event.event.idempotency_key or agent_run_id

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

    @staticmethod
    def _normalize_plan_for_runtime(
        plan: AgentPlan,
        context: AgentContext,
        context_snapshot_ref: str,
        plan_id: str,
        idempotency_prefix: str,
        prompt_id: str,
        prompt_version: str,
        goal: str,
        skill_ids: list[str],
        tool_names: list[str],
        approval_policy: ActionApprovalPolicy,
    ) -> AgentPlan:
        normalized_actions: list[PlannedAction] = []
        previous_action_id: str | None = None
        for index, action in enumerate(plan.actions, start=1):
            normalized_action = action.model_copy(
                update={
                    "plan_id": plan_id,
                    "depends_on": [previous_action_id] if previous_action_id else [],
                    "requires_approval": approval_policy.requires_approval(action.tool_name),
                    "status": ActionStatus.PENDING,
                    "idempotency_key": f"{idempotency_prefix}:{action.tool_name}:{index}",
                    "expected_entity_versions": context.entity_versions,
                }
            )
            normalized_actions.append(normalized_action)
            previous_action_id = normalized_action.action_id

        return plan.model_copy(
            update={
                "plan_id": plan_id,
                "source_event_id": context.source_event.event_id,
                "workspace_id": context.workspace_id,
                "project_id": context.project_id,
                "goal": goal,
                "prompt_id": prompt_id,
                "prompt_version": prompt_version,
                "skill_ids": skill_ids,
                "tool_names": tool_names,
                "context_snapshot_ref": context_snapshot_ref,
                "actions": normalized_actions,
                "status": PlanStatus.PLANNED,
            }
        )
