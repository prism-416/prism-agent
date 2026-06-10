"""Live plan-quality evals for the feature.provision workflow.

These call the real Gemini planner, so they are excluded from the default test
run (``testpaths = ["tests"]``). Run them explicitly after prompt or planner
changes:

    GEMINI_API_KEY=... python -m pytest evals/ -q -s

Each fixture in ``evals/fixtures`` carries a feature specification plus
expectations the generated plan must satisfy: action shape, item counts,
hierarchy, route-level granularity, description quality, assignment validity,
and no recreation of existing work items.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from application.context_provider import ContextProvider
from application.planner import Planner
from capabilities.plan_quality import plan_quality_issues
from capabilities.tools.workitem_tools import coerce_work_item_tree_items
from domain.events import DomainEvent, EventEnvelope
from infrastructure.config.settings import Settings
from infrastructure.llm.gemini_model_provider import GeminiModelProvider
from infrastructure.llm.pydantic_ai_agent_factory import PydanticAIAgentFactory
from infrastructure.prism_api.client import PrismApiClient
from infrastructure.registries.prompt_registry import PromptRegistry
from infrastructure.registries.skill_registry import SkillRegistry
from infrastructure.registries.tool_registry import ToolRegistry
from infrastructure.registries.workflow_registry import WorkflowRegistry

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
ROUTE_TITLE_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\b\s+/\S+")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("GEMINI_API_KEY"),
        reason="GEMINI_API_KEY not set; live plan eval skipped.",
    ),
    pytest.mark.skipif(
        importlib.util.find_spec("pydantic_ai") is None,
        reason="pydantic-ai not installed; live plan eval skipped.",
    ),
]


def _fixture_names() -> list[str]:
    return sorted(path.stem for path in FIXTURES_DIR.glob("*.json"))


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _flatten(items: list[Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            nodes.append(item)
            children = item.get("children")
            if isinstance(children, list):
                nodes.extend(_flatten(children))
    return nodes


@pytest.mark.parametrize("fixture_name", _fixture_names())
def test_feature_provision_plan_quality(fixture_name: str) -> None:
    fixture = _load_fixture(fixture_name)
    expectations = fixture["expectations"]

    prompt_registry = PromptRegistry()
    workflow = WorkflowRegistry.from_prompt_registry(prompt_registry).get("feature.provision")
    event = DomainEvent(
        event_type="domain.feature.provisioning.requested",
        workspace_id="eval-workspace",
        project_id="eval-project",
        payload=fixture["payload"],
        idempotency_key=f"eval-{fixture_name}",
    )
    snapshot = ContextProvider(PrismApiClient(), prompt_registry).hydrate(
        EventEnvelope.wrap(event), workflow
    )
    planner = Planner(
        prompt_registry,
        SkillRegistry.from_prompt_registry(prompt_registry),
        ToolRegistry.from_prompt_registry(prompt_registry),
        PydanticAIAgentFactory(GeminiModelProvider(Settings.from_env())),
    )

    plan = planner.create_plan(snapshot.context, snapshot.ref, workflow, f"eval-{fixture_name}")

    failures: list[str] = []
    tree_actions = [a for a in plan.actions if a.tool_name == "create_workitem_tree"]
    if len(tree_actions) != 1:
        failures.append(f"expected exactly 1 create_workitem_tree action, got {len(tree_actions)}")
    if expectations.get("expect_sprint") and not any(
        a.tool_name == "create_sprint" for a in plan.actions
    ):
        failures.append("expected a create_sprint action")

    nodes: list[dict[str, Any]] = []
    top_level: list[Any] = []
    if tree_actions:
        top_level = coerce_work_item_tree_items(tree_actions[0].input) or []
        nodes = _flatten(top_level)

    total = len(nodes)
    if not expectations["min_total_items"] <= total <= expectations["max_total_items"]:
        failures.append(
            f"total items {total} outside "
            f"[{expectations['min_total_items']}, {expectations['max_total_items']}]"
        )
    if len(top_level) < expectations["min_top_level_items"]:
        failures.append(f"top-level items {len(top_level)} < {expectations['min_top_level_items']}")

    route_tasks = [n for n in nodes if ROUTE_TITLE_RE.search(str(n.get("title", "")))]
    if len(route_tasks) < expectations["min_route_tasks"]:
        failures.append(
            f"route-level tasks {len(route_tasks)} < {expectations['min_route_tasks']} "
            f"(found: {[str(n.get('title')) for n in route_tasks]})"
        )

    haystack = " ".join(f"{n.get('title', '')} {n.get('description', '')}" for n in nodes).lower()
    for keyword in expectations.get("required_keywords", []):
        if keyword.lower() not in haystack:
            failures.append(f"keyword not covered by any task: {keyword!r}")

    known_usernames = {
        str(m.get("username"))
        for m in fixture["payload"].get("workspaceMembers", [])
        if m.get("username")
    }
    unknown_assignees = {
        str(u)
        for n in nodes
        for u in (n.get("assigneeUsernames") or [])
        if str(u) not in known_usernames
    }
    if unknown_assignees:
        failures.append(f"assignees outside the team: {sorted(unknown_assignees)}")

    forbidden = {t.lower() for t in expectations.get("forbidden_existing_titles", [])}
    recreated = [
        str(n.get("title")) for n in nodes if str(n.get("title", "")).strip().lower() in forbidden
    ]
    if recreated:
        failures.append(f"recreated existing work items: {recreated}")

    failures.extend(plan_quality_issues(plan, snapshot.context.entities))

    summary = (
        f"\n=== eval {fixture_name}: {total} items, {len(top_level)} top-level, "
        f"{len(route_tasks)} route tasks, {len(plan.actions)} actions ==="
    )
    print(summary)
    assert not failures, summary + "\n" + "\n".join(f"- {f}" for f in failures)
