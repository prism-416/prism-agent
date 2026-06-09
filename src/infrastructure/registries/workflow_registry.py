from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from capabilities.definitions import OrchestrationDef, WorkflowPromptDefinition
from infrastructure.registries.prompt_registry import PromptRegistry


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    trigger_types: list[str]
    required_skills: list[str]
    prompt_id: str
    prompt_version: str
    goal: str = ""
    default_execution_mode: str = "suggest"
    approval_policy: dict[str, str] = Field(default_factory=dict)
    max_recursion_depth: int = 10
    orchestration: OrchestrationDef | None = None

    @property
    def workflow_id(self) -> str:
        return self.id


class WorkflowRegistry:
    def __init__(self) -> None:
        self._by_id: dict[str, WorkflowDefinition] = {}
        self._by_trigger: dict[str, WorkflowDefinition] = {}

    @classmethod
    def from_prompt_registry(cls, prompt_registry: PromptRegistry) -> WorkflowRegistry:
        registry = cls()
        for prompt in prompt_registry.list_workflows():
            registry.register(cls._from_prompt(prompt))
        return registry

    @classmethod
    def with_defaults(cls) -> WorkflowRegistry:
        return cls.from_prompt_registry(PromptRegistry())

    @staticmethod
    def _from_prompt(prompt: WorkflowPromptDefinition) -> WorkflowDefinition:
        return WorkflowDefinition(
            id=prompt.id,
            trigger_types=prompt.trigger_types,
            required_skills=prompt.required_skills,
            prompt_id=prompt.id,
            prompt_version=prompt.version,
            goal=prompt.goal,
            default_execution_mode=prompt.default_execution_mode,
            approval_policy=prompt.approval_policy,
            max_recursion_depth=prompt.max_recursion_depth,
            orchestration=prompt.orchestration,
        )

    def register(self, workflow: WorkflowDefinition) -> None:
        if not workflow.required_skills:
            raise ValueError(f"Workflow must require at least one skill: {workflow.id}")
        if not workflow.trigger_types:
            raise ValueError(f"Workflow must define at least one trigger: {workflow.id}")
        self._by_id[workflow.id] = workflow
        for trigger_type in workflow.trigger_types:
            self._by_trigger[trigger_type] = workflow

    def get(self, workflow_id: str) -> WorkflowDefinition:
        if workflow_id not in self._by_id:
            raise KeyError(f"Workflow not registered: {workflow_id}")
        return self._by_id[workflow_id]

    def get_by_trigger(self, trigger_type: str) -> WorkflowDefinition | None:
        return self._by_trigger.get(trigger_type)

    def list(self) -> list[WorkflowDefinition]:
        return list(self._by_id.values())
