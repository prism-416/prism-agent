from pathlib import Path

from infrastructure.registries.prompt_registry import PromptRegistry
from infrastructure.registries.skill_registry import SkillRegistry
from infrastructure.registries.tool_registry import ToolRegistry
from infrastructure.registries.workflow_registry import WorkflowRegistry

PROMPT_INTERNAL_TERMS = (
    "OpenAPI",
    "Dto",
    "DTO",
    "/internal",
    "internal",
    "Object Storage",
    "OCI queue",
    "queue pointer",
    "endpoint",
    "API contract",
    "requestedByUserId",
    "runtime",
    "backend",
    "runtime_internal",
    "missing_endpoint",
)


def test_prompt_registry_loads_versioned_yaml(prompts_path) -> None:
    registry = PromptRegistry(prompts_path)
    workflow_prompt = registry.get_workflow("story.decompose", "1.0.0")
    skill_prompt = registry.get_skill("task_decomposition")
    tool_prompt = registry.get_tool("create_agent_suggestion")

    assert workflow_prompt.id == "story.decompose"
    assert workflow_prompt.output_schema == "AgentPlan"
    assert "task_decomposition" in workflow_prompt.required_skills
    assert "create_agent_suggestion" in skill_prompt.allowed_tools
    assert tool_prompt.approval_policy == "auto_commit"


def test_prompt_text_avoids_backend_and_runtime_internals(prompts_path) -> None:
    for path in prompts_path.rglob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        for term in PROMPT_INTERNAL_TERMS:
            assert term not in text, f"{path} leaks internal term {term!r}"


def test_tool_prompt_contracts_are_planner_facing(prompts_path) -> None:
    registry = PromptRegistry(prompts_path)
    tools = registry.list_tools()

    for tool in tools:
        extras = tool.model_extra or {}
        assert "api" not in extras
        assert "constraints" not in extras
        assert "requestedByUserId" not in str(tool.input_contract)

    suggestion_contract = registry.get_tool("create_agent_suggestion").input_contract
    assert "target_entity_ref" in suggestion_contract
    assert "targetType" not in suggestion_contract
    assert "targetId" not in suggestion_contract

    report_contract = registry.get_tool("generate_sprint_report").input_contract
    assert "sprint_id" in report_contract
    assert "sprintId" not in report_contract


def test_tool_registry_loads_default_tools() -> None:
    registry = ToolRegistry.with_defaults()

    assert "create_sprint" in registry.names()
    assert "create_workitem" in registry.names()
    assert registry.get("create_agent_suggestion").name == "create_agent_suggestion"
    assert registry.definition("create_workitem").risk_level == "high"
    assert registry.get("create_workitem").definition.id == "create_workitem"


def test_skill_registry_loads_default_skills() -> None:
    registry = SkillRegistry.with_defaults()

    assert "feature_provisioning" in registry.ids()
    assert "task_decomposition" in registry.ids()
    assert registry.get("risk_detection").render_instruction()
    assert "create_workitem" in registry.allowed_tools_for(["task_decomposition"])
    assert registry.get("task_decomposition").definition.id == "task_decomposition"


def test_workflow_registry_maps_trigger_to_prompt() -> None:
    registry = WorkflowRegistry.with_defaults()
    workflow = registry.get_by_trigger("domain.story.created")

    assert workflow is not None
    assert workflow.workflow_id == "story.decompose"
    assert workflow.prompt_id == "story.decompose"
    assert workflow.required_skills == [
        "task_decomposition",
        "backlog_analysis",
        "risk_detection",
    ]


def test_workflow_registry_maps_feature_provisioning_trigger() -> None:
    registry = WorkflowRegistry.with_defaults()
    workflow = registry.get_by_trigger("domain.feature.provisioning.requested")

    assert workflow is not None
    assert workflow.workflow_id == "feature.provision"
    assert workflow.required_skills == ["feature_provisioning"]


def test_skills_are_instantiated_from_yaml_without_inline_skill_modules() -> None:
    capabilities_dir = Path(__file__).resolve().parents[1] / "src" / "capabilities"
    skill_modules = sorted(path.name for path in capabilities_dir.glob("*.py"))

    assert skill_modules == ["__init__.py", "definitions.py", "skills.py"]


def test_tools_do_not_define_prompt_descriptions_inline() -> None:
    tools_dir = Path(__file__).resolve().parents[1] / "src" / "capabilities" / "tools"

    for path in tools_dir.glob("*.py"):
        if path.name in {"__init__.py", "base.py"}:
            continue
        assert "description =" not in path.read_text(encoding="utf-8")
