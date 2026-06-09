from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PromptModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "gemini"
    name: str = "gemini-3.1-pro-preview"
    temperature: float = 0.2


class BasePromptDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    version: str
    name: str
    description: str


class OrchestrationNodeDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: str = ""
    skill: str | None = None
    skills: list[str] = Field(default_factory=list)
    objective: str = ""
    context_scope: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    model_tier: str | None = None

    def skill_ids(self) -> list[str]:
        ids = list(self.skills)
        if self.skill and self.skill not in ids:
            ids.insert(0, self.skill)
        return ids


class OrchestrationDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["hybrid", "static", "dynamic"] = "static"
    default_graph: list[OrchestrationNodeDef] = Field(default_factory=list)
    synthesizer: OrchestrationNodeDef | None = None


class WorkflowPromptDefinition(BasePromptDefinition):
    kind: Literal["workflow"] = "workflow"
    model: PromptModelSettings = Field(default_factory=PromptModelSettings)
    output_schema: str = "AgentPlan"
    goal: str
    system: str
    constraints: list[str] = Field(default_factory=list)
    required_context: list[str] = Field(default_factory=list)
    trigger_types: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    default_execution_mode: str = "suggest"
    approval_policy: dict[str, str] = Field(default_factory=dict)
    max_recursion_depth: int = 10
    orchestration: OrchestrationDef | None = None


class SkillPromptDefinition(BasePromptDefinition):
    kind: Literal["skill"] = "skill"
    instruction: str
    reasoning_instructions: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    guardrails: dict[str, Any] = Field(default_factory=dict)
    quality_expectations: list[str] = Field(default_factory=list)
    model_tier: str = "pro"


class ToolPromptDefinition(BasePromptDefinition):
    kind: Literal["tool"] = "tool"
    usage: list[str] = Field(default_factory=list)
    do_not_use: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"
    approval_policy: str = "auto_commit"
    input_contract: dict[str, Any] = Field(default_factory=dict)


PromptDefinition = WorkflowPromptDefinition | SkillPromptDefinition | ToolPromptDefinition
