from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from app.container import build_container
from application.feature_provisioning_worker import FeatureProvisioningWorker
from domain.events import EventEnvelope
from domain.feature_provisioning import FeatureProvisioningPointerEvent
from infrastructure.config.settings import Settings
from infrastructure.object_storage.base import JsonPayloadStore
from infrastructure.object_storage.memory_payload_store import MemoryPayloadStore
from infrastructure.object_storage.oci_payload_store import ObjectStoragePayloadStore
from interfaces.local_entry import example_seed_event, print_trace, run_local

DEFAULT_ENV_FILE = ".env"
LOCAL_PLACEHOLDER_IDS = frozenset({"workspace-local", "project-local"})


def agent_local(argv: list[str] | None = None) -> int:
    try:
        return _agent_local(argv)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _agent_local(argv: list[str] | None = None) -> int:
    """Console-script entrypoint for local agent development."""
    parser = argparse.ArgumentParser(
        prog="agent-local",
        description="Run the Prism agent locally against a seed event or feature pointer.",
    )
    parser.add_argument(
        "input_path",
        nargs="?",
        help=(
            "Path to a runtime event/envelope JSON file. If the JSON is a "
            "feature.provisioning.requested pointer, the feature worker runs."
        ),
    )
    parser.add_argument(
        "--feature-provision",
        action="store_true",
        help="Treat input_path as a feature provisioning pointer.",
    )
    parser.add_argument(
        "--payload-file",
        help=(
            "Local feature provisioning payload JSON. In API mode, omit this to "
            "hydrate the payload from OCI Object Storage using the pointer."
        ),
    )
    parser.add_argument(
        "--oci-payload-store",
        action="store_true",
        help="Fetch the feature provisioning payload from configured OCI Object Storage.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Use Settings.from_env() as-is, including configured queue backend. "
            "By default, the local worker uses Prism API state and an in-memory queue."
        ),
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run fully in memory without Prism API calls.",
    )
    parser.add_argument(
        "--env-file",
        default=DEFAULT_ENV_FILE,
        help="Load env vars from this dotenv-style file before building settings.",
    )
    parser.add_argument(
        "--no-env-file",
        action="store_true",
        help="Do not load a local env file.",
    )
    args = parser.parse_args(argv)

    if not args.no_env_file:
        _load_env_file(Path(args.env_file))

    if args.feature_provision:
        if not args.input_path:
            parser.error("--feature-provision requires a pointer JSON path.")
        return _run_feature_provisioning(args)

    if args.input_path and _is_feature_provisioning_pointer_path(Path(args.input_path)):
        return _run_feature_provisioning(args)

    event = _load_envelope(args.input_path) if args.input_path else example_seed_event()
    _reject_placeholder_values(event.model_dump(mode="json"), args.offline)
    run_local(event, settings=_settings_for_run(args.live, args.offline))
    return 0


def agent_test(argv: list[str] | None = None) -> int:
    return agent_local(argv)


def main(argv: list[str] | None = None) -> int:
    return agent_local(argv)


_PIPELINE_FAILURE_TRACES = frozenset(
    {"event.failed", "action.failed", "plan.failed", "recursion.max_depth"}
)


def _run_feature_provisioning(args: argparse.Namespace) -> int:
    pointer_data = _load_json(Path(args.input_path))
    pointer = FeatureProvisioningPointerEvent.model_validate(pointer_data)
    _reject_placeholder_values(pointer_data, args.offline)
    settings = _settings_for_run(args.live, args.offline)
    container = build_container(settings.model_copy(update={"queue_backend": "memory"}))

    # The production entrypoint dispatches one step and lets the queue carry the rest.
    # The local CLI simulates the whole pipeline in-process by draining the in-memory
    # queue, so it can print the full trace.
    event = FeatureProvisioningWorker.resolve_seed_event(_payload_store(args, pointer), pointer)
    traces = container.recursion_runner.run(event)
    print_trace(traces)

    failures = [trace for trace in traces if trace.event_name in _PIPELINE_FAILURE_TRACES]
    if failures:
        raise RuntimeError(failures[-1].message)

    print(
        json.dumps(
            {
                "ok": True,
                "request_id": pointer.request_id,
                "event_id": event.event_id,
                "duplicate": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _settings_for_run(live: bool, offline: bool) -> Settings:
    # The local CLI renders traces itself via print_trace, so disable the stdout
    # JSON trace logger to avoid duplicate, noisy output.
    settings = Settings.from_env().model_copy(update={"log_traces": False})
    if live and offline:
        raise ValueError("--live and --offline cannot be used together.")
    if offline:
        return settings.model_copy(
            update={
                "app_env": "local",
                "state_backend": "memory",
                "queue_backend": "memory",
                "prism_api_base_url": None,
                "prism_api_token": None,
            }
        )
    if live:
        return settings
    _require_prism_api_config(settings)
    return settings.model_copy(
        update={
            "app_env": "local",
            "state_backend": "prism_api",
            "queue_backend": "memory",
        }
    )


def _payload_store(
    args: argparse.Namespace,
    pointer: FeatureProvisioningPointerEvent,
) -> JsonPayloadStore:
    if args.payload_file:
        return MemoryPayloadStore(
            {pointer.payload_object_name: _load_json(Path(args.payload_file))}
        )
    if args.offline:
        return MemoryPayloadStore({pointer.payload_object_name: _local_feature_payload(pointer)})

    settings = Settings.from_env()
    if not settings.object_storage_namespace or not settings.object_storage_bucket_name:
        raise RuntimeError(
            "agent-local feature provisioning API mode requires a real payload source. "
            "Pass --payload-file with the hydrated feature request JSON, or set "
            "OCI_OBJECT_STORAGE_NAMESPACE and OCI_OBJECT_STORAGE_BUCKET_NAME so the "
            "pointer can be hydrated from OCI Object Storage. Pass --offline only for "
            "generated dry-run payloads."
        )
    return ObjectStoragePayloadStore(
        settings.object_storage_namespace,
        settings.object_storage_bucket_name,
    )


def _reject_placeholder_values(value: Any, offline: bool) -> None:
    if offline:
        return
    placeholders = _placeholder_paths(value)
    if placeholders:
        names = ", ".join(placeholders[:5])
        suffix = "" if len(placeholders) <= 5 else f" and {len(placeholders) - 5} more"
        raise RuntimeError(
            f"Refusing to call Prism API with placeholder value(s): {names}{suffix}. "
            "Use an input JSON file with real Prism IDs, or pass --offline for an "
            "in-memory dry run."
        )


def _placeholder_paths(value: Any, path: str = "$") -> list[str]:
    if isinstance(value, str):
        return [f"{path}={value}"] if any(item in value for item in LOCAL_PLACEHOLDER_IDS) else []
    if isinstance(value, list):
        paths: list[str] = []
        for index, item in enumerate(value):
            paths.extend(_placeholder_paths(item, f"{path}[{index}]"))
        return paths
    if isinstance(value, dict):
        paths = []
        for key, item in value.items():
            key_path = f"{path}.{key}"
            if isinstance(key, str) and any(
                placeholder in key for placeholder in LOCAL_PLACEHOLDER_IDS
            ):
                paths.append(f"{key_path}=<key>")
            paths.extend(_placeholder_paths(item, key_path))
        return paths
    return []


def _require_prism_api_config(settings: Settings) -> None:
    missing = []
    if not settings.prism_api_base_url:
        missing.append("PRISM_API_BASE_URL")
    if not settings.prism_api_token:
        missing.append("PRISM_API_TOKEN")
    if missing:
        names = ", ".join(missing)
        raise RuntimeError(
            f"agent-local requires {names} for real Prism API calls. "
            "Set them in .env or the shell, or pass --offline for an in-memory dry run."
        )


def _load_envelope(path: str | None) -> EventEnvelope:
    if path is None:
        return EventEnvelope.wrap(example_seed_event())
    data = _load_json(Path(path))
    if "event" in data:
        return EventEnvelope.model_validate(data)
    return EventEnvelope.model_validate({"event": data})


def _is_feature_provisioning_pointer_path(path: Path) -> bool:
    try:
        return _load_json(path).get("type") == "feature.provisioning.requested"
    except (OSError, json.JSONDecodeError):
        return False


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return data


def _local_feature_payload(pointer: FeatureProvisioningPointerEvent) -> dict[str, Any]:
    return {
        "requestId": pointer.request_id,
        "workspace": {
            "workspaceId": pointer.workspace_id,
            "name": "Local Workspace",
        },
        "project": {
            "projectId": pointer.project_id,
            "name": "Local Project",
        },
        "featureSpecification": "Provision a placeholder feature for an offline dry run.",
        "workspaceMembers": [],
    }


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if __name__ == "__main__":
    raise SystemExit(main())
