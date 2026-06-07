from __future__ import annotations

import json
import tomllib
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from uuid import UUID

from interfaces.test_commands import agent_test, main


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args) -> None:
        _ = args

    def read(self) -> bytes:
        return self.body


def test_agent_test_runs_seed_event_entrypoint(capsys) -> None:
    result = agent_test(
        ["tests/fixtures/seed_events/story_created.json", "--offline", "--no-env-file"]
    )

    output = capsys.readouterr().out

    assert result == 0
    assert "[plan.created]" in output
    assert "[plan.completed]" in output


def test_agent_test_runs_feature_pointer_entrypoint(capsys) -> None:
    pointer = json.loads(
        Path("tests/fixtures/feature_provisioning_pointer.local.json").read_text(encoding="utf-8")
    )
    result = agent_test(
        [
            "tests/fixtures/feature_provisioning_pointer.local.json",
            "--offline",
            "--no-env-file",
        ]
    )

    output = capsys.readouterr().out

    assert result == 0
    assert "[plan.completed]" in output
    assert '"ok": true' in output
    assert f'"request_id": "{pointer["requestId"]}"' in output


def test_agent_local_requires_api_config_unless_offline(monkeypatch, capsys) -> None:
    monkeypatch.delenv("PRISM_API_BASE_URL", raising=False)
    monkeypatch.delenv("PRISM_API_TOKEN", raising=False)

    result = main(
        [
            "tests/fixtures/feature_provisioning_pointer.local.json",
            "--no-env-file",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "requires PRISM_API_BASE_URL, PRISM_API_TOKEN" in captured.err


def test_agent_local_feature_pointer_requires_real_payload_source(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("PRISM_API_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("PRISM_API_TOKEN", "secret-token")
    monkeypatch.delenv("OCI_OBJECT_STORAGE_NAMESPACE", raising=False)
    monkeypatch.delenv("OCI_OBJECT_STORAGE_BUCKET_NAME", raising=False)

    def fake_urlopen(request, timeout):
        _ = (request, timeout)
        raise AssertionError("Prism API should not be called without a feature payload source")

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)

    result = main(["tests/fixtures/feature_provisioning_pointer.local.json", "--no-env-file"])

    captured = capsys.readouterr()
    assert result == 2
    assert "requires a real payload source" in captured.err
    assert "--payload-file" in captured.err
    assert "OCI_OBJECT_STORAGE_NAMESPACE" in captured.err


def test_agent_local_rejects_placeholder_fixture_ids_in_api_mode(monkeypatch, capsys) -> None:
    monkeypatch.setenv("PRISM_API_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("PRISM_API_TOKEN", "secret-token")

    result = main(["tests/fixtures/seed_events/story_created.json", "--no-env-file"])

    captured = capsys.readouterr()
    assert result == 2
    assert "Refusing to call Prism API with placeholder value(s)" in captured.err
    assert "workspace-local" in captured.err
    assert "project-local" in captured.err


def test_agent_local_uses_input_json_ids_for_api_calls(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    monkeypatch.setenv("PRISM_API_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("PRISM_API_TOKEN", "secret-token")
    seed_event = json.loads(
        Path("tests/fixtures/seed_events/story_created.json").read_text(encoding="utf-8")
    )
    seed_event["workspace_id"] = "workspace-1"
    seed_event["project_id"] = "project-1"
    seed_path = tmp_path / "story_created.real.json"
    seed_path.write_text(json.dumps(seed_event), encoding="utf-8")
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.get_method(), request.full_url, request.data))
        if request.full_url.endswith("/state"):
            return _FakeResponse(b'{"data":{"actions":[]}}')
        return _FakeResponse(b'{"data":{"ok":true}}')

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)

    result = main(
        [
            str(seed_path),
            "--no-env-file",
        ]
    )

    output = capsys.readouterr().out
    urls = [url for _, url, _ in calls]
    assert result == 0
    assert "[plan.completed]" in output
    create_run_call = next(
        call
        for call in calls
        if call[0] == "POST"
        and call[1] == "https://api.example.test/workspaces/workspace-1/agent-runs/internal"
    )
    assert "https://api.example.test/workspaces/workspace-1/members" not in urls
    assert "https://api.example.test/workspaces/workspace-1/jobs" not in urls
    assert all("/agent-memories" not in url for url in urls)
    assert any(
        url.startswith("https://api.example.test/workspaces/workspace-1/agent-runs/internal/")
        and url.endswith("/status")
        for url in urls
    )
    assert any(
        url.startswith("https://api.example.test/workspaces/workspace-1/agent-runs/internal/")
        and url.endswith("/steps")
        for url in urls
    )
    assert any(
        url.startswith("https://api.example.test/workspaces/workspace-1/agent-runs/internal/")
        and url.endswith("/actions")
        for url in urls
    )
    assert any(
        url.startswith("https://api.example.test/workspaces/workspace-1/agent-actions/internal/")
        and url.endswith("/events")
        for url in urls
    )
    assert all("agent-suggestions" not in url for url in urls)
    assert all("workspace-local" not in url for url in urls)
    assert all("project-local" not in url for url in urls)
    create_payload = json.loads(create_run_call[2].decode("utf-8"))
    assert create_payload["agentType"] == "project_manager"
    assert create_payload["objective"].startswith("Break down a product story")
    assert create_payload["triggerType"] == "event"
    assert create_payload["status"] == "running"
    assert _is_uuid(create_payload["runId"])
    step_payloads = [
        json.loads(data.decode("utf-8"))
        for _, url, data in calls
        if data and url.endswith("/steps")
    ]
    action_payloads = [
        json.loads(data.decode("utf-8"))
        for _, url, data in calls
        if data and url.endswith("/actions")
    ]
    assert all(_is_uuid(payload["stepId"]) for payload in step_payloads)
    assert all(_is_uuid(payload["actionId"]) for payload in action_payloads)
    assert all(_is_uuid(payload["stepId"]) for payload in action_payloads if "stepId" in payload)
    assert any("stepId" not in payload for payload in action_payloads)
    assert {payload["actionType"] for payload in action_payloads} >= {
        "find_duplicate_workitems",
        "create_agent_suggestion",
    }
    assert all(
        payload["targetType"] == "tool" or "targetId" in payload for payload in action_payloads
    )


def test_agent_local_feature_pointer_uses_payload_file_for_plan_inputs(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    monkeypatch.setenv("PRISM_API_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("PRISM_API_TOKEN", "secret-token")
    monkeypatch.delenv("OCI_OBJECT_STORAGE_NAMESPACE", raising=False)
    monkeypatch.delenv("OCI_OBJECT_STORAGE_BUCKET_NAME", raising=False)
    pointer = json.loads(
        Path("tests/fixtures/feature_provisioning_pointer.local.json").read_text(encoding="utf-8")
    )
    feature_specification = (
        "Launch team capacity planning for customer onboarding.\n"
        "Acceptance Criteria: project managers can forecast weekly staffing needs."
    )
    payload_path = tmp_path / "feature_payload.json"
    payload_path.write_text(
        json.dumps(
            {
                "requestId": pointer["requestId"],
                "workspace": {
                    "workspaceId": pointer["workspaceId"],
                    "name": "Customer Success",
                },
                "project": {
                    "projectId": pointer["projectId"],
                    "name": "Onboarding",
                },
                "featureSpecification": feature_specification,
                "workspaceMembers": [{"username": "alex"}],
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_urlopen(request, timeout):
        _ = timeout
        calls.append((request.get_method(), request.full_url, request.data))
        if request.full_url.endswith("/state"):
            return _FakeResponse(b'{"data":{"actions":[]}}')
        return _FakeResponse(b'{"data":{"ok":true}}')

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)

    result = main(
        [
            "tests/fixtures/feature_provisioning_pointer.local.json",
            "--payload-file",
            str(payload_path),
            "--no-env-file",
        ]
    )

    output = capsys.readouterr().out
    request_bodies = [data.decode("utf-8") for _, _, data in calls if data is not None]
    assert result == 0
    assert "[plan.completed]" in output
    assert any(feature_specification.splitlines()[0] in body for body in request_bodies)
    assert not any("Prism agent development" in body for body in request_bodies)
    assert not any("local-user" in body for body in request_bodies)


def test_agent_local_prints_api_failure_payload_without_traceback(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    monkeypatch.setenv("PRISM_API_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("PRISM_API_TOKEN", "secret-token")
    pointer = json.loads(
        Path("tests/fixtures/feature_provisioning_pointer.local.json").read_text(encoding="utf-8")
    )
    payload_path = tmp_path / "feature_payload.json"
    payload_path.write_text(json.dumps(_feature_payload(pointer)), encoding="utf-8")

    def fake_urlopen(request, timeout):
        _ = timeout
        raise HTTPError(
            request.full_url,
            500,
            "Internal Server Error",
            {},
            BytesIO(b'{"message":"Internal Server Error"}'),
        )

    monkeypatch.setattr("infrastructure.prism_api.client.urlopen", fake_urlopen)

    result = main(
        [
            "tests/fixtures/feature_provisioning_pointer.local.json",
            "--payload-file",
            str(payload_path),
            "--no-env-file",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "Traceback" not in captured.err
    assert "POST /workspaces/" in captured.err
    assert "/agent-runs/internal failed" in captured.err
    assert "request_payload=" in captured.err
    assert '"agentType": "project_manager"' in captured.err
    assert "secret-token" not in captured.err


def test_console_scripts_use_error_handling_wrapper() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["scripts"]["agent-local"] == "interfaces.test_commands:main"
    assert project["project"]["scripts"]["agent-test"] == "interfaces.test_commands:main"


def _is_uuid(value: str) -> bool:
    UUID(value)
    return True


def _feature_payload(pointer: dict) -> dict:
    return {
        "requestId": pointer["requestId"],
        "workspace": {
            "workspaceId": pointer["workspaceId"],
            "name": "Feature Workspace",
        },
        "project": {
            "projectId": pointer["projectId"],
            "name": "Feature Project",
        },
        "featureSpecification": "Provision a customer-facing feature from the input payload.",
        "workspaceMembers": [],
    }
