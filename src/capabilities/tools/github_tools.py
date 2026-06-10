from __future__ import annotations

from typing import Any

from capabilities.tools.base import BaseAgentTool
from domain.actions import PlannedAction
from domain.context import AgentContext
from domain.results import ToolResult
from infrastructure.prism_api.client import (
    PrismApiConflictError,
    PrismApiUnprocessableError,
)

REVIEW_EVENTS = {"COMMENT", "APPROVE", "REQUEST_CHANGES"}


class LinkPullRequestTool(BaseAgentTool):
    name = "link_pull_request"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output={
                "pr": context.entities.get("pull_request", {}),
                "itemId": action.input.get("itemId"),
                "pull_request_id": action.input.get("pull_request_id"),
            },
        )


class SubmitPullRequestReviewTool(BaseAgentTool):
    name = "submit_pull_request_review"

    def execute(self, action: PlannedAction, context: AgentContext) -> ToolResult:
        diff = context.entities.get("pull_request_diff") or {}
        if not isinstance(diff, dict):
            diff = {}
        project_id = str(action.input.get("projectId") or context.project_id or "")
        pull_number = action.input.get("pullNumber") or diff.get("pullNumber")
        head_sha = str(action.input.get("headSha") or diff.get("headSha") or "")
        event = str(action.input.get("event") or "COMMENT").upper()
        if event not in REVIEW_EVENTS:
            event = "COMMENT"
        summary = str(action.input.get("summary") or action.instruction)
        comments = _normalize_comments(action.input.get("comments"))
        payload: dict[str, Any] = {
            "headSha": head_sha,
            "event": event,
            "summary": summary,
        }
        if comments:
            payload["comments"] = comments
        requested_by_user_id = _requested_by_user_id(action.input, context)
        if requested_by_user_id:
            payload["requestedByUserId"] = requested_by_user_id

        if self.prism_client and self.prism_client.is_configured and pull_number is not None:
            try:
                output = self.prism_client.create_pull_request_review(
                    project_id, pull_number, payload
                )
            except PrismApiConflictError as exc:
                return ToolResult(
                    plan_id=action.plan_id,
                    action_id=action.action_id,
                    tool_name=self.name,
                    success=False,
                    output={"reason": "stale_pull_request_head", "headSha": head_sha},
                    error=str(exc),
                )
            except PrismApiUnprocessableError as exc:
                return ToolResult(
                    plan_id=action.plan_id,
                    action_id=action.action_id,
                    tool_name=self.name,
                    success=False,
                    output={"reason": "invalid_review_comment_anchor"},
                    error=str(exc),
                )
        else:
            output = {
                "reviewId": f"local-{action.action_id}",
                "pullNumber": pull_number,
                "event": event,
                "comments": comments,
            }
        return ToolResult(
            plan_id=action.plan_id,
            action_id=action.action_id,
            tool_name=self.name,
            success=True,
            output=output,
        )


def _normalize_comments(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    comments: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        line = item.get("line")
        body = item.get("body")
        if not path or not body or not isinstance(line, int) or line < 1:
            continue
        comment: dict[str, Any] = {"path": str(path), "line": line, "body": str(body)}
        side = item.get("side")
        if side in {"LEFT", "RIGHT"}:
            comment["side"] = side
        comments.append(comment)
    return comments


def _requested_by_user_id(input_data: dict[str, Any], context: AgentContext) -> str | None:
    requested_by_user_id = input_data.get("requestedByUserId")
    if requested_by_user_id:
        return str(requested_by_user_id)
    queue_pointer = context.source_event.event.payload.get("queue_pointer", {})
    if isinstance(queue_pointer, dict) and queue_pointer.get("requestedByUserId"):
        return str(queue_pointer["requestedByUserId"])
    return None
