from __future__ import annotations

from capabilities.definitions import OrchestrationNodeDef
from domain.context import AgentContext
from domain.subtasks import SubTask, TaskGraph
from infrastructure.registries.skill_registry import SkillRegistry
from infrastructure.registries.workflow_registry import WorkflowDefinition

ROOT_NODE_ID = "root"
SYNTHESIZER_NODE_ID = "synthesizer"
DEFAULT_MODEL_TIER = "pro"


class Orchestrator:
    """Decomposes a workflow goal into a TaskGraph of subagent nodes.

    ``build_task_graph`` is the runtime entry point and currently returns a
    single-node graph (Phase 0 behavior) so execution is unchanged.

    ``build_orchestrated_graph`` expands a workflow's declared ``orchestration``
    block into a real multi-node DAG with an optional synthesizer. It is the seam
    Phase 1 Increment 2 wires into the runtime once the subagent executor exists.
    Hybrid (LLM-refined) and dynamic decomposition build on top of this.
    """

    def __init__(self, skill_registry: SkillRegistry | None = None) -> None:
        self.skill_registry = skill_registry

    def build_task_graph(self, context: AgentContext, workflow: WorkflowDefinition) -> TaskGraph:
        root = SubTask(
            node_id=ROOT_NODE_ID,
            skill_ids=list(workflow.required_skills),
            objective=workflow.goal,
            depends_on=[],
            context_scope=[],  # empty scope = full context (current behavior)
            model_tier=DEFAULT_MODEL_TIER,
        )
        return TaskGraph(
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            nodes=[root],
        )

    def build_orchestrated_graph(
        self, context: AgentContext, workflow: WorkflowDefinition
    ) -> TaskGraph:
        orchestration = workflow.orchestration
        if orchestration is None or not orchestration.default_graph:
            return self.build_task_graph(context, workflow)

        nodes = [
            self._build_node(node_def, workflow, fallback_node_id=f"node_{index}")
            for index, node_def in enumerate(orchestration.default_graph)
        ]
        synthesizer = None
        if orchestration.synthesizer is not None:
            synthesizer = self._build_node(
                orchestration.synthesizer,
                workflow,
                fallback_node_id=SYNTHESIZER_NODE_ID,
                default_depends_on=[node.node_id for node in nodes],
            )
        return TaskGraph(
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            nodes=nodes,
            synthesizer_node=synthesizer,
        )

    def _build_node(
        self,
        node_def: OrchestrationNodeDef,
        workflow: WorkflowDefinition,
        *,
        fallback_node_id: str,
        default_depends_on: list[str] | None = None,
    ) -> SubTask:
        node_id = node_def.node or fallback_node_id
        skill_ids = node_def.skill_ids()
        if not skill_ids:
            raise ValueError(
                f"Orchestration node '{node_id}' in workflow '{workflow.id}' declares no skill."
            )
        depends_on = node_def.depends_on or list(default_depends_on or [])
        return SubTask(
            node_id=node_id,
            skill_ids=skill_ids,
            objective=node_def.objective or workflow.goal,
            depends_on=depends_on,
            context_scope=list(node_def.context_scope),
            model_tier=self._resolve_tier(node_def, skill_ids),
        )

    def _resolve_tier(self, node_def: OrchestrationNodeDef, skill_ids: list[str]) -> str:
        if node_def.model_tier:
            return node_def.model_tier
        if self.skill_registry is not None and skill_ids:
            try:
                return self.skill_registry.model_tier(skill_ids[0])
            except KeyError:
                return DEFAULT_MODEL_TIER
        return DEFAULT_MODEL_TIER
