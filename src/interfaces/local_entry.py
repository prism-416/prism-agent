from __future__ import annotations

import json

from app.container import build_container
from domain.events import DomainEvent, EventEnvelope, RuntimeEvent
from domain.results import TraceEvent
from infrastructure.config.settings import Settings


def run_local(
    seed_event: RuntimeEvent | EventEnvelope, settings: Settings | None = None
) -> list[TraceEvent]:
    local_settings = settings or Settings(
        app_env="local", state_backend="memory", queue_backend="memory"
    )
    container = build_container(local_settings)
    traces = container.recursion_runner.run(seed_event)
    print_trace(traces)
    return traces


def print_trace(traces: list[TraceEvent]) -> None:
    for trace in traces:
        plan = f" plan={trace.plan_id}" if trace.plan_id else ""
        action = f" action={trace.action_id}" if trace.action_id else ""
        print(f"[{trace.event_name}]{plan}{action} {trace.message}")
        if trace.data:
            print(_format_trace_data(trace.data))


def _format_trace_data(data: dict) -> str:
    rendered = json.dumps(data, indent=2, sort_keys=True, default=str)
    return "\n".join(f"  {line}" for line in rendered.splitlines())


def example_seed_event() -> DomainEvent:
    return DomainEvent(
        event_type="story.created",
        workspace_id="workspace-local",
        project_id="project-local",
        payload={
            "story": {
                "id": "story-1",
                "title": "Invite teammates to a Prism workspace",
                "version": 1,
            },
            "entity_versions": {"story:story-1": 1},
        },
    )


if __name__ == "__main__":
    run_local(example_seed_event())
