from app.container import build_container
from domain.events import DomainEvent, ManualInvocationEvent, ScheduledEvent
from infrastructure.config.settings import Settings


def test_local_recursion_processes_story_created(prompts_path) -> None:
    container = build_container(
        Settings(
            app_env="local",
            state_backend="memory",
            queue_backend="memory",
            prompts_path=str(prompts_path),
            max_recursion_depth=10,
        )
    )
    event = DomainEvent(
        event_type="story.created",
        workspace_id="w1",
        project_id="p1",
        payload={
            "story": {"id": "s1", "title": "Invite teammates", "version": 1},
            "entity_versions": {"story:s1": 1},
        },
    )

    traces = container.recursion_runner.run(event)

    assert any(trace.event_name == "plan.created" for trace in traces)
    assert any(trace.event_name == "action.completed" for trace in traces)
    assert any(trace.event_name == "plan.completed" for trace in traces)


def test_local_recursion_processes_daily_summary(prompts_path) -> None:
    container = build_container(Settings(prompts_path=str(prompts_path)))
    event = ScheduledEvent(event_type="daily_summary", workspace_id="w1", project_id="p1")

    traces = container.recursion_runner.run(event)

    assert any(trace.event_name == "plan.created" for trace in traces)
    assert any(trace.event_name == "action.completed" for trace in traces)


def test_local_recursion_processes_pr_merged(prompts_path) -> None:
    container = build_container(Settings(prompts_path=str(prompts_path)))
    event = DomainEvent(
        event_type="pr.merged",
        workspace_id="w1",
        project_id="p1",
        payload={
            "pull_request": {"id": "pr1", "merged": True, "version": 4},
            "workitem": {"id": "wi1", "status": "in_review", "version": 2},
            "entity_versions": {"workitem:wi1": 2, "pull_request:pr1": 4},
        },
    )

    traces = container.recursion_runner.run(event)

    assert any(trace.event_name == "plan.created" for trace in traces)
    assert any(trace.event_name == "action.completed" for trace in traces)


def test_local_recursion_processes_manual_decompose_story(prompts_path) -> None:
    container = build_container(Settings(prompts_path=str(prompts_path)))
    event = ManualInvocationEvent(
        event_type="decompose_story",
        workspace_id="w1",
        project_id="p1",
        actor_id="user1",
        payload={
            "story": {"id": "s1", "title": "Invite teammates", "version": 1},
            "entity_versions": {"story:s1": 1},
        },
    )

    traces = container.recursion_runner.run(event)

    assert any(trace.event_name == "plan.created" for trace in traces)
