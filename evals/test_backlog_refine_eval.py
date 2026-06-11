"""Live plan-quality evals for the backlog.refine workflow.

Run with the feature evals:

    GEMINI_API_KEY=... python -m pytest evals/ -q -s

The fixture plants known backlog problems (overdue underprioritized work, an
unassigned urgent item, a too-thin description) plus a healthy item and a done
item; the generated plan must fix or flag the planted problems without touching
closed work or inventing item ids.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any

import pytest

from application.context_provider import ContextProvider
from application.planner import Planner
from capabilities.plan_quality import plan_quality_issues
from capabilities.tools.workitem_tools import coerce_work_item_updates
from domain.events import EventEnvelope, ManualInvocationEvent
from infrastructure.config.settings import Settings
from infrastructure.llm.gemini_model_provider import GeminiModelProvider
from infrastructure.llm.pydantic_ai_agent_factory import PydanticAIAgentFactory
from infrastructure.prism_api.client import PrismApiClient
from infrastructure.registries.prompt_registry import PromptRegistry
from infrastructure.registries.skill_registry import SkillRegistry
from infrastructure.registries.tool_registry import ToolRegistry
from infrastructure.registries.workflow_registry import WorkflowRegistry

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "backlog"

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


@pytest.mark.parametrize("fixture_name", _fixture_names())
def test_backlog_refine_plan_quality(fixture_name: str) -> None:
    fixture = _load_fixture(fixture_name)
    expectations = fixture["expectations"]

    prompt_registry = PromptRegistry()
    workflow = WorkflowRegistry.from_prompt_registry(prompt_registry).get("backlog.refine")
    event = ManualInvocationEvent(
        event_type="refine_backlog",
        workspace_id="eval-workspace",
        project_id="eval-project",
        actor_id="eval-user",
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
    if len(plan.actions) > expectations["max_actions"]:
        failures.append(
            f"expected at most {expectations['max_actions']} actions, got {len(plan.actions)}"
        )

    bulk_actions = [a for a in plan.actions if a.tool_name == "update_workitems_bulk"]
    if len(bulk_actions) != 1:
        failures.append(f"expected exactly 1 update_workitems_bulk action, got {len(bulk_actions)}")

    updates: list[dict[str, Any]] = []
    if bulk_actions:
        updates = coerce_work_item_updates(bulk_actions[0].input) or []
    if not updates:
        failures.append("the bulk action carries no updates")
    if len(updates) > expectations["max_updates"]:
        failures.append(f"too many updates: {len(updates)}")

    update_ids = {str(u.get("itemId")) for u in updates}
    known_ids = {str(item.get("itemId")) for item in fixture["payload"]["backlogWorkItems"]}
    invented = update_ids - known_ids
    if invented:
        failures.append(f"updates reference invented item ids: {sorted(invented)}")
    touched_closed = update_ids & set(expectations["must_not_touch"])
    if touched_closed:
        failures.append(f"updates touch closed items: {sorted(touched_closed)}")

    suggestion_text = " ".join(
        json.dumps(a.input) for a in plan.actions if a.tool_name == "create_agent_suggestion"
    )
    unhandled = [
        item_id
        for item_id in expectations["signal_item_ids"]
        if item_id not in update_ids and item_id not in suggestion_text
    ]
    if unhandled:
        failures.append(
            f"planted signal items neither updated nor flagged in a suggestion: {unhandled}"
        )

    failures.extend(plan_quality_issues(plan, snapshot.context.entities))

    summary = (
        f"\n=== eval backlog {fixture_name}: {len(plan.actions)} actions, "
        f"{len(updates)} updates ==="
    )
    print(summary)
    assert not failures, summary + "\n" + "\n".join(f"- {f}" for f in failures)
