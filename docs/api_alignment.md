# API Alignment Notes

The prompts are aligned against `https://api.prizmatic.app/dev/docs-json`.

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
  - `GET/POST /projects/{projectId}/work-items/internal`
  - `GET/PATCH/DELETE /projects/{projectId}/work-items/{itemId}`
  - `GET/PATCH/DELETE /projects/{projectId}/work-items/internal/{itemId}`
  - `GET /projects/{projectId}/work-items/{itemId}/children`
  - priorities: `low`, `medium`, `high`, `urgent`
  - statuses: `todo`, `in_progress`, `in_review`, `done`, `archived`
  - assignment uses `assigneeUsernames` on create/update payloads
  - labels use `labelNames`
  - internal mutation DTOs require `requestedByUserId`
- Comments:
  - `POST /projects/{projectId}/work-items/{itemId}/comments`
  - `GET /projects/{projectId}/work-items/{itemId}/comments`
  - comments are the current teammate-visible collaboration surface
- Sprints:
  - `GET/POST /workspaces/{workspaceId}/sprints`
  - `POST /workspaces/{workspaceId}/sprints/internal`
  - `GET/PATCH/DELETE /workspaces/{workspaceId}/sprints/{sprintId}`
  - `PATCH/DELETE /workspaces/{workspaceId}/sprints/internal/{sprintId}`
  - `GET /workspaces/{workspaceId}/sprints/{sprintId}/work-items`
  - `POST /workspaces/{workspaceId}/sprints/{sprintId}/work-items`
  - `POST /workspaces/{workspaceId}/sprints/internal/{sprintId}/work-items`
  - statuses: `planned`, `active`, `closed`, `cancelled`
  - internal mutation DTOs require `requestedByUserId`
- Feature provisioning:
  - `POST /workspaces/{workspaceId}/provision`
  - queue message is a pointer event with `payloadObjectName` and optional `payloadVersionId`
  - hydrated payload in Object Storage is the source of truth for `featureSpecification`, workspace context, project context, and workspace members
- Agent runtime state:
  - `GET /workspaces/{workspaceId}/agent-runs/{runId}`
  - `GET /workspaces/{workspaceId}/agent-runs/{runId}/actions`
  - `GET /workspaces/{workspaceId}/agent-actions/{actionId}`
  - action recursion hydrates DB-backed action state from the agent-run actions endpoint before selecting or executing the next action

All standard successful response bodies are wrapped in `data`.

## Endpoint Gaps For Agent Runtime

The current OpenAPI document does not expose these surfaces:

- Agent runtime identity binding.
  - The runtime must send `PRISM_API_TOKEN` as `x-internal-api-token`.
  - Required service-token scopes include `projects:write` and `sprints:write`.
- Agent suggestions.
  - Recommended: `POST /projects/{projectId}/agent-suggestions`
  - Recommended follow-ups: list, get, apply, dismiss.
- Dashboard insights.
  - Recommended: `POST /projects/{projectId}/dashboard-insights`
- Sprint report artifacts.
  - Recommended: `POST /workspaces/{workspaceId}/sprints/{sprintId}/reports`
- Sprint work item mapping runtime tool.
  - The API now exposes sprint work item mutation endpoints.
  - The agent runtime does not yet include a dedicated sprint-work-item mapping tool.
- GitHub PR linked artifacts.
  - Recommended: `POST /projects/{projectId}/work-items/{itemId}/linked-artifacts`
  - Needed for `pr.status_sync` beyond event-provided PR metadata.
- Entity versions or optimistic concurrency metadata for stale-context validation.
  - Recommended: expose `updatedAt` or `version` on mutable resources, especially `WorkItemResponseDto` and `SprintResponseDto`, and accept conditional update metadata for mutations.
