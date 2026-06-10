from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from capabilities.definitions import WorkflowPromptDefinition
from capabilities.plan_quality import plan_quality_issues
from capabilities.skills import RuntimeSkill
from capabilities.tools.base import BaseAgentTool
from capabilities.tools.workitem_tools import (
    coerce_work_item_tree_items,
    materialize_work_item_tree_actions,
    validate_work_item_tree,
)
from domain.context import AgentContext
from domain.plans import AgentPlan
from domain.policies import ActionApprovalPolicy
from infrastructure.llm.gemini_model_provider import GeminiModelProvider

PLAN_GENERATION_ATTEMPTS = 3

_EMPTY_PLAN_FEEDBACK = (
    "The previous planning attempt produced zero executable actions. "
    "Return an AgentPlan with at least one action using an allowed tool "
    "when the workflow goal requires work. Do not leave actions empty "
    "for feature provisioning, task decomposition, or other actionable "
    "workflows unless the hydrated context proves there is no safe action."
)


def _quality_review_feedback(issues: list[str]) -> str:
    numbered = "\n".join(f"{index}. {issue}" for index, issue in enumerate(issues, start=1))
    return (
        "A reviewer found these problems in your previous plan:\n"
        f"{numbered}\n"
        "Regenerate the plan fixing every problem above while keeping the rest of "
        "its content."
    )


def _plan_defect(plan: AgentPlan) -> str | None:
    """A retryable planning mistake, or None when the plan is executable.

    Catches defects that would deterministically fail at execution time — most
    importantly a create_workitem_tree action whose untyped input is missing the
    work item list — so planning retries with targeted feedback instead of the
    runtime burning action attempts on an unfixable input.
    """
    if not plan.actions:
        return _EMPTY_PLAN_FEEDBACK
    for action in plan.actions:
        if action.tool_name != "create_workitem_tree":
            continue
        error = validate_work_item_tree(coerce_work_item_tree_items(action.input))
        if error:
            return (
                f"The previous create_workitem_tree action was invalid: {error} "
                "Put the complete work item breakdown into the action's work_items "
                "field: a non-empty array of objects, each with a title, a "
                "description, and an optional children array of nested work items."
            )
    return None


class RuntimePlanningAgent:
    def __init__(
        self,
        workflow_prompt: WorkflowPromptDefinition,
        skills: list[RuntimeSkill],
        tools: list[BaseAgentTool],
        model_provider: GeminiModelProvider,
        model_tier: str | None = None,
    ) -> None:
        self.workflow_prompt = workflow_prompt
        self.skills = skills
        self.tools = tools
        self.model_provider = model_provider
        self.model_tier = model_tier
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
        quality_review_used = False
        try:
            for attempt in range(1, PLAN_GENERATION_ATTEMPTS + 1):
                try:
                    plan = self._generate_plan_with_pydantic_ai(context, context_snapshot_ref)
                except Exception:
                    # Large structured outputs can truncate or fail validation
                    # mid-generation; that costs an attempt, not the whole run.
                    if attempt >= PLAN_GENERATION_ATTEMPTS:
                        raise
                    continue
                plan = materialize_work_item_tree_actions(plan)
                defect = _plan_defect(plan)
                if defect is not None:
                    self._retry_feedback = defect
                    continue
                # Critic pass: one revision round for soft quality findings, then
                # accept the plan rather than fail the run on imperfect output.
                if not quality_review_used and attempt < PLAN_GENERATION_ATTEMPTS:
                    issues = plan_quality_issues(plan, context.entities)
                    if issues:
                        quality_review_used = True
                        self._retry_feedback = _quality_review_feedback(issues)
                        continue
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
            self.model_provider.pydantic_ai_model_ref(self.workflow_prompt, self.model_tier),
            output_type=AgentPlan,
            instructions=instructions,
        )
        user_prompt = self._render_user_prompt(context, context_snapshot_ref)
        result = self._run_agent_sync(agent, user_prompt)
        return result.output

    @staticmethod
    def _run_agent_sync(agent: object, user_prompt: str) -> object:
        # pydantic-ai's run_sync drives the agent with loop.run_until_complete, which
        # raises "This event loop is already running" when called inside the OCI/FDK
        # handler's already-running event loop. Executing it on a dedicated worker
        # thread gives run_sync a fresh event loop of its own. GEMINI_API_KEY and
        # other config are set via process-global env, so the worker thread sees them.
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(agent.run_sync, user_prompt).result()

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
        model_tier: str | None = None,
    ) -> RuntimePlanningAgent:
        return RuntimePlanningAgent(
            workflow_prompt=workflow_prompt,
            skills=skills,
            tools=tools,
            model_provider=self.model_provider,
            model_tier=model_tier,
        )
