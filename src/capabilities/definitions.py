from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PromptModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "gemini"
    name: str = "gemini-2.5-pro"
    temperature: float = 0.2


class BasePromptDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    version: str
    name: str
    description: str


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


class SkillPromptDefinition(BasePromptDefinition):
    kind: Literal["skill"] = "skill"
    instruction: str
    reasoning_instructions: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    guardrails: dict[str, Any] = Field(default_factory=dict)
    quality_expectations: list[str] = Field(default_factory=list)


class ToolPromptDefinition(BasePromptDefinition):
    kind: Literal["tool"] = "tool"
    usage: list[str] = Field(default_factory=list)
    do_not_use: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"
    approval_policy: str = "auto_commit"
    input_contract: dict[str, Any] = Field(default_factory=dict)


PromptDefinition = WorkflowPromptDefinition | SkillPromptDefinition | ToolPromptDefinition
