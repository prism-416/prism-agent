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
  - `GET/PATCH/DELETE /projects/{projectId}/work-items/{itemId}`
  - `GET /projects/{projectId}/work-items/{itemId}/children`
  - priorities: `low`, `medium`, `high`, `urgent`
  - statuses: `todo`, `in_progress`, `in_review`, `done`, `archived`
  - assignment uses `assigneeUsernames` on create/update payloads
  - labels use `labelNames`
- Comments:
  - `POST /projects/{projectId}/work-items/{itemId}/comments`
  - `GET /projects/{projectId}/work-items/{itemId}/comments`
  - comments are the current teammate-visible collaboration surface
- Sprints:
  - `GET/POST /workspaces/{workspaceId}/sprints`
  - `GET/PATCH/DELETE /workspaces/{workspaceId}/sprints/{sprintId}`
  - `GET /workspaces/{workspaceId}/sprints/{sprintId}/work-items`
  - statuses: `planned`, `active`, `closed`, `cancelled`
- Feature provisioning:
  - `POST /workspaces/{workspaceId}/provision`
  - queue message is a pointer event with `payloadObjectName` and optional `payloadVersionId`
  - hydrated payload in Object Storage is the source of truth for `featureSpecification`, workspace context, project context, and workspace members

All standard successful response bodies are wrapped in `data`.

## Endpoint Gaps For Agent Runtime

The current OpenAPI document does not expose these surfaces:

- Agent runtime identity binding.
  - The API exposes service-account administration, but the runtime still needs a configured token with scopes that can create sprints and work items.
- Agent suggestions.
  - Recommended: `POST /projects/{projectId}/agent-suggestions`
  - Recommended follow-ups: list, get, apply, dismiss.
- Dashboard insights.
  - Recommended: `POST /projects/{projectId}/dashboard-insights`
- Sprint report artifacts.
  - Recommended: `POST /workspaces/{workspaceId}/sprints/{sprintId}/reports`
- Sprint work item mapping.
  - Current API exposes sprint work item reads but no mutation endpoint for `prism_sprint_work_item_map`.
  - Recommended: `POST /workspaces/{workspaceId}/sprints/{sprintId}/work-items`
  - Until this exists, feature provisioning creates sprints and work items but does not attach items to the sprint.
- GitHub PR linked artifacts.
  - Recommended: `POST /projects/{projectId}/work-items/{itemId}/linked-artifacts`
  - Needed for `pr.status_sync` beyond event-provided PR metadata.
- Entity versions or optimistic concurrency metadata for stale-context validation.
  - Recommended: expose `updatedAt` or `version` on mutable resources, especially `WorkItemResponseDto` and `SprintResponseDto`, and accept conditional update metadata for mutations.
