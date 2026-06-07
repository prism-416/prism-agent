# API Alignment Notes

The prompts are aligned against `https://api.prizmatic.app/dev/docs-json`.

## Existing API Shape Used By Prompts

- Workspace membership:
  - `GET /workspaces/{workspaceId}/members`
  - workspace members expose `userId`, `username`, `role`, `jobIds`, and `jobNames`
  - member roles: `owner`, `admin`, `member`, `viewer`
- Workspace jobs:
  - `GET /workspaces/{workspaceId}/jobs`
  - workspace jobs expose `jobId`, `name`, and `description`
- Member workload:
  - `GET /workspaces/{workspaceId}/member-workloads/internal`
  - `GET /workspaces/{workspaceId}/member-workloads/internal/{userId}`
  - workload is workspace-scoped because members can belong to multiple projects in the same workspace
  - workload responses expose member identity, role, jobIds, jobNames, assignedItemCount, activeItemCount, status counts, and due/overdue counts
  - no dedicated capacity, availability, estimate, or project-breakdown fields exist in the live OpenAPI document
- Work items:
  - `GET/POST /projects/{projectId}/work-items/internal`
  - `GET/PATCH/DELETE /projects/{projectId}/work-items/internal/{itemId}`
  - `GET /projects/{projectId}/work-items/internal/{itemId}/children`
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
  - hydrated payload in Object Storage is the source of truth for `featureSpecification`, workspace context, and project context
  - runtime hydrates workspace member, job, and workload context from the Prism API when available
- Agent runtime state:
  - `GET /workspaces/{workspaceId}/agent-runs/internal/{runId}/state`
  - `PATCH /workspaces/{workspaceId}/agent-runs/internal/{runId}/status`
  - `POST /workspaces/{workspaceId}/agent-runs/internal/{runId}/steps`
  - `POST /workspaces/{workspaceId}/agent-runs/internal/{runId}/actions`
  - `POST /workspaces/{workspaceId}/agent-actions/internal/{actionId}/events`
  - runtime `plan_id` maps to API `runId`; feature provisioning may pass `agentRunId` on the queue pointer, otherwise the runtime reuses `correlation_id` / request id
  - action recursion hydrates DB-backed action state from the internal agent-run state endpoint before selecting or executing the next action
  - each planning and execution transition upserts run steps and action status back to the internal agent-run endpoints

All standard successful response bodies are wrapped in `data`.

## Endpoint Gaps For Agent Runtime

The current OpenAPI document does not expose these surfaces:

- Agent runtime identity binding.
  - The runtime must send `PRISM_API_TOKEN` as `x-internal-api-token`.
  - Required service-token scopes include `projects:write` and `sprints:write`.
- Agent suggestion follow-ups.
  - The live OpenAPI document has no dedicated agent suggestion endpoint.
  - Runtime persists suggestion actions through the internal agent-run/action endpoints.
  - Recommended follow-ups: list, get, apply, dismiss, or expose a dedicated suggestion API if the product needs that surface.
- Dashboard insights.
  - Recommended: `POST /projects/{projectId}/dashboard-insights`
- Sprint report artifacts.
  - Recommended: `POST /workspaces/{workspaceId}/sprints/{sprintId}/reports`
- Sprint work item mapping runtime tool.
  - The API now exposes sprint work item mutation endpoints.
  - The agent runtime does not yet include a dedicated sprint-work-item mapping tool.
- Member capacity and workload detail.
  - Recommended: extend workspace member workload responses with capacity/availability, estimates, and optional project/sprint breakdowns.
  - The current runtime can use workspace-wide active assigned counts, but reliable load balancing still needs first-class capacity and allocation data.
- GitHub PR linked artifacts.
  - Recommended: `POST /projects/{projectId}/work-items/{itemId}/linked-artifacts`
  - Needed for `pr.status_sync` beyond event-provided PR metadata.
- Entity versions or optimistic concurrency metadata for stale-context validation.
  - Recommended: expose `updatedAt` or `version` on mutable resources, especially `WorkItemResponseDto` and `SprintResponseDto`, and accept conditional update metadata for mutations.
