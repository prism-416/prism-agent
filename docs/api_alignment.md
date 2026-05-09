# API Alignment Notes

The prompts are aligned against `http://localhost:4000/docs-json`.

## Existing API Shape Used By Prompts

- Workspace membership:
  - `GET /workspaces/{workspaceId}/members`
  - member roles: `admin`, `member`, `viewer`
- Project membership:
  - `GET /projects/{projectId}/members`
  - project members expose `username` and `jobNames`
- Work items:
  - `GET /projects/{projectId}/work-items`
  - `POST /projects/{projectId}/work-items`
  - `GET/PATCH/DELETE /projects/{projectId}/work-items/{itemId}`
  - `GET /projects/{projectId}/work-items/{itemId}/children`
  - types: `epic`, `story`, `task`
  - priorities: `low`, `medium`, `high`, `urgent`
  - statuses: `todo`, `in_progress`, `in_review`, `done`
  - assignment uses `assigneeUsernames`
  - labels use `labelNames`
- Comments:
  - `POST /projects/{projectId}/work-items/{itemId}/comments`
  - `GET /projects/{projectId}/work-items/{itemId}/comments`
  - comments are the current teammate-visible collaboration surface
- Sprints:
  - `GET/POST /projects/{projectId}/sprints`
  - `GET/PATCH/DELETE /projects/{projectId}/sprints/{sprintId}`
  - `GET /projects/{projectId}/sprints/{sprintId}/work-items`
  - statuses: `backlog`, `in_progress`, `done`

All standard successful response bodies are wrapped in `data`.

## Endpoint Gaps For Agent Runtime

The current OpenAPI document does not expose these surfaces:

- Agent/service-account identity or bot teammate provisioning.
  - The runtime can act as a teammate only if it authenticates as a normal Prism user that is a workspace/project member.
- Agent suggestions.
  - Recommended: `POST /projects/{projectId}/agent-suggestions`
  - Recommended follow-ups: list, get, apply, dismiss.
- Dashboard insights.
  - Recommended: `POST /projects/{projectId}/dashboard-insights`
- Sprint report artifacts.
  - Recommended: `POST /projects/{projectId}/sprints/{sprintId}/reports`
- GitHub PR linked artifacts.
  - Recommended: `POST /projects/{projectId}/work-items/{itemId}/linked-artifacts`
  - Needed for `pr.status_sync` beyond event-provided PR metadata.
- Entity versions or optimistic concurrency metadata for stale-context validation.
  - Recommended: expose `updatedAt` or `version` on mutable resources, especially `WorkItemResponseDto` and `SprintResponseDto`, and accept conditional update metadata for mutations.

