from __future__ import annotations

from application.orchestrator import ROOT_NODE_ID
from application.planner import Planner
from domain.context import AgentContext
from domain.plans import AgentPlan
from domain.subtasks import SubTask
from infrastructure.registries.workflow_registry import WorkflowDefinition


def sub_plan_id(agent_run_id: str, node_id: str) -> str:
    return f"{agent_run_id}::{node_id}"


class SubAgentRunner:
    """Runs the planning pipeline scoped to a single subagent node.

    The synthetic root node (whole-workflow, Phase 0) is planned with no scoping,
    so its output stays byte-identical to the single-agent pipeline. Any other node
    is planned with its own ``skill_ids``, ``context_scope``, and a node-qualified
    ``plan_id`` so sibling sub-plans never collide.
    """

    def __init__(self, planner: Planner) -> None:
        self.planner = planner

    def plan_for_node(
        self,
        node: SubTask,
        context: AgentContext,
        context_snapshot_ref: str,
        workflow: WorkflowDefinition,
        agent_run_id: str,
    ) -> AgentPlan:
        if node.node_id == ROOT_NODE_ID:
            return self.planner.create_plan(
                context,
                context_snapshot_ref,
                workflow,
                agent_run_id,
            )
        plan = self.planner.create_plan(
            context,
            context_snapshot_ref,
            workflow,
            agent_run_id,
            plan_id=sub_plan_id(agent_run_id, node.node_id),
            skill_ids=node.skill_ids or None,
            context_scope=node.context_scope or None,
            model_tier=node.model_tier or None,
        )
        return plan.model_copy(update={"parent_run_id": agent_run_id, "node_id": node.node_id})
