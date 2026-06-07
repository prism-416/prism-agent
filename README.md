# Prizmatic

`prism-agent` is Prizmatic's recursive, event-driven AI project manager runtime.

## Problem Statement

Prizmatic helps software teams turn project activity into useful project-management
actions. The agent runtime consumes meaningful project events, hydrates project
context, creates a structured plan, executes one approved action at a time, and
validates the result before committing a mutation or creating a teammate-visible
suggestion. This keeps AI output out of direct state mutation paths while still
helping teams maintain backlogs, sprint plans, status updates, risk reports, and
feature-provisioning work.

Core runtime flow:

```text
Domain Event
-> Event Router
-> Context Hydration
-> Agent Planner
-> Structured Plan
-> Action Event
-> Action Executor
-> Validator
-> State Mutation or Suggestion
-> Follow-up Event
-> Recursive Processing
```

The same application flow runs in local memory mode and production OCI mode.
Only infrastructure adapters differ.

## Current Beta Scope

The beta runtime supports these core use cases:

- Routing meaningful product events into workflow-specific planning.
- Loading workflow, skill, and tool prompt definitions from YAML.
- Enforcing skill-based tool whitelists before planned actions execute.
- Running local recursive execution with in-memory queue and state adapters.
- Executing deterministic local plans for repeatable development and tests.
- Integrating Prizmatic API boundary methods for sprint and work item mutations.
- Providing OCI Function and object-storage adapter skeletons for production deployment.

Known beta limitations and schedule adjustments:

- `OCIQueue` is still an adapter skeleton; production queue enqueue/dequeue work is
  scheduled for final-release hardening.
- Sprint work item mapping is pending an agent tool. The backend API exposes
  sprint work item mutation endpoints, but the runtime currently creates sprints
  and work items without attaching generated work items to the sprint.
- Agent suggestions, dashboard insights, sprint reports, PR linked artifacts, and
  optimistic concurrency metadata are documented API gaps. See
  [docs/api_alignment.md](docs/api_alignment.md).
- Local development does not require a database or external services. Production
  mode requires Prizmatic API credentials and OCI resources.

## Repository Layout

```text
src/
  app/                 dependency container and wiring
  application/         orchestration use cases and handlers
  capabilities/        YAML-backed capability definitions and executable tools
    tools/             concrete tool implementations only
  domain/              pure event, plan, context, result, and policy models
  infrastructure/      LLM, queue, state, Prizmatic API, config, and registry adapters
  interfaces/          local, CLI, and OCI Function entrypoints
prompts/
  workflows/           workflow definitions
  skills/              reusable reasoning capabilities and tool whitelists
  tools/               LLM-facing tool usage, risk, approval, and input contracts
docs/
  api_alignment.md
  capability_model.md
  directory_structure.md
tests/
```

The runtime is capability-driven:

```text
Workflow -> selects Skills -> whitelist Tools -> execute Actions
```

See [docs/capability_model.md](docs/capability_model.md) and
[docs/directory_structure.md](docs/directory_structure.md) for architecture details.

## Supported Developer Operating Systems

These instructions are intended for the operating systems currently supported by
the team:

- macOS 13+ with zsh or bash
- Ubuntu 22.04+ or another modern Linux distribution with bash
- Windows 10/11 with PowerShell

All operating systems require:

- Git
- Python 3.11 or newer
- Internet access for the first dependency install

## Check Out the Source Code

The repository is hosted at
[https://github.com/prism-416/prism-agent](https://github.com/prism-416/prism-agent).

Use release tags when a stable release has been created:

```bash
git clone https://github.com/prism-416/prism-agent.git
cd prism-agent
git fetch --all --tags
git tag --list
git checkout <release-tag>
```

No release tag is currently committed in this repository. Until the first tagged
release exists, the beta integration branch is `develop`:

```bash
git clone https://github.com/prism-416/prism-agent.git
cd prism-agent
git switch develop
git pull --ff-only origin develop
```

## Set Up a Development Environment

### macOS and Linux

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

If `python3.11` is not available but `python3` points to Python 3.11 or newer,
use `python3 -m venv .venv`.

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

If PowerShell blocks virtual environment activation, run this once in the same
terminal session and then activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## Build the Software

For normal development, installing the package in editable mode is the build
step:

```bash
python -m pip install -e ".[dev]"
python -m compileall -q src tests
```

To create distribution artifacts under `dist/`:

```bash
python -m pip install build
python -m build
```

Do not commit `dist/`, virtual environments, caches, `.env`, or other generated
local files.

## Run the Local Agent

Local mode uses in-memory state and queue adapters. It does not require a
database, OCI, Prizmatic API credentials, or a live LLM key.

```bash
python -m interfaces.cli tests/fixtures/seed_events/story_created.json
```

Other seed events are available in `tests/fixtures/seed_events/`, including:

- `manual_decompose_story.json`
- `pr_merged.json`
- `scheduled_daily_summary.json`
- `story_created.json`

If imports fail because the package was not installed in editable mode, run with
`PYTHONPATH=src`:

```bash
PYTHONPATH=src python -m interfaces.cli tests/fixtures/seed_events/story_created.json
```

On Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m interfaces.cli tests/fixtures/seed_events/story_created.json
```

## Test the Software

Run the full local test suite:

```bash
python -m pytest -p no:cacheprovider
```

Run the lint and compile checks:

```bash
pre-commit run --all-files
```

If `pre-commit` is unavailable, run the equivalent checks directly:

```bash
ruff format --check src tests
ruff check src tests
python -m compileall -q src tests
python -m pytest -p no:cacheprovider
```

Before submitting implementation work, run:

```bash
pre-commit run --files <changed-python-files>
python -m pytest -p no:cacheprovider
```

## Services and Configuration

Local development uses defaults from `Settings.from_env()` and requires no
external services.

Optional environment variables:

| Variable | Use |
| --- | --- |
| `APP_ENV` | `local` or `prod`; live LLM planning only runs in `prod` with a Gemini key. |
| `STATE_BACKEND` | `memory`, `prism_api`, or legacy `object_storage`. |
| `QUEUE_BACKEND` | `memory` or `oci`. |
| `GEMINI_API_KEY` | Gemini API key for production LLM planning. |
| `DEFAULT_GEMINI_MODEL` | Default Gemini model; currently defaults to `gemini-2.5-pro`. |
| `MAX_RECURSION_DEPTH` | Safety limit for recursive event processing. |
| `PRISM_API_BASE_URL` | Base URL for live Prizmatic API calls. |
| `PRISM_API_TOKEN` | Internal API token sent as `x-internal-api-token` for live Prizmatic API calls. |
| `OCI_QUEUE_OCID` | Required when `QUEUE_BACKEND=oci`. |
| `OCI_NAMESPACE` | Required for OCI object storage state. |
| `OCI_BUCKET_NAME` | Required for OCI object storage state. |
| `OCI_PAYLOAD_NAMESPACE` | Optional separate namespace for feature-provisioning payloads. |
| `OCI_PAYLOAD_BUCKET_NAME` | Optional separate bucket for feature-provisioning payloads. |

Do not commit real secrets. Keep local secrets in an untracked `.env` file or in
your shell environment.

## API Design Documentation

The API alignment document is
[docs/api_alignment.md](docs/api_alignment.md). It lists the Prizmatic backend
endpoints currently used by the agent prompts and the endpoint gaps that affect
the beta and final-release schedule.

When the backend API changes, update these files in the same pull request:

- [docs/api_alignment.md](docs/api_alignment.md)
- Matching tool prompt YAML under `prompts/tools/`
- Matching executable tool implementation under `src/capabilities/tools/`
- Tests that cover the changed behavior

## Project Schedule

Milestone due dates come from the course schedule. This table tracks the current
repo status and the remaining work for the semester.

| Milestone | Status | Planned / completed work |
| --- | --- | --- |
| Milestone 1 | Completed | Project structure, runtime architecture, local entrypoint, domain event flow, initial README and documentation. |
| Milestone 2 | Completed | YAML-backed workflow, skill, and tool definitions; registries; local recursive execution; routing tests. |
| Milestone 3 | Completed | Prizmatic mutation tool alignment, planner whitelist validation, trigger-scope checks, idempotency and stale-context validation coverage. |
| Milestone 4 Beta | In progress | Beta runtime, updated README, API alignment notes, bug tracker process, local demo seed events, progress update document, and deploy/access link. |
| Final release | Planned | Complete production adapter work, close serious beta bugs, update API gaps, verify completed features, polish deployment documentation, and tag a release. |

Schedule adjustments made during beta:

- Production OCI queue work moved from beta to final release because the current
  adapter is a skeleton.
- Sprint work item attachment depends on a missing backend endpoint, so it is
  tracked as an API dependency instead of a completed beta feature.
- Live LLM planning is limited to production configuration; local development
  intentionally uses deterministic plans so tests remain repeatable.

Completed features must be verified by a teammate who did not implement the
feature. Verification bugs should be filed in GitHub Issues and linked from the
schedule item or pull request.

## Bug Tracking and Reporting

Outstanding bugs are tracked in GitHub Issues:

[https://github.com/prism-416/prism-agent/issues](https://github.com/prism-416/prism-agent/issues)

To report a bug:

1. Open a new GitHub Issue.
2. Use a clear title that starts with the affected area, such as `runtime:`.
3. Include operating system, Python version, branch or commit SHA, and setup mode.
4. Include steps to reproduce, expected behavior, actual behavior, and logs or trace output.
5. Add the `bug` label. Add severity labels when available.
6. For serious bugs, assign an owner and account for the fix in the project schedule.

Verification bugs found during milestone checks should also be filed as GitHub
Issues, even when the team plans to fix them immediately.

## Milestone 4 Progress Update Checklist

Before submitting Milestone 4, the team should commit a progress update document
that includes:

- Each member's scheduled tasks by this milestone.
- Each member's actual completed and partially completed tasks.
- Percent-complete estimates for partial work.
- A group comparison against the original design-document schedule.
- Whether progress is on track, ahead, or behind schedule.
- The team's self-assigned progress grade and reason.
- Any blockers, schedule changes, and work-process adjustments.
- The beta deployment or access link.

## Demo Checklist

For the weekly in-class meeting, each member should be ready to state:

- What they completed last week.
- What they plan to complete this week.
- Anything blocking their work.

Suggested local demo command:

```bash
python -m interfaces.cli tests/fixtures/seed_events/story_created.json
```
