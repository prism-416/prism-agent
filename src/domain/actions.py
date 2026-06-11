from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from domain.events import utc_now


class ActionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    REQUIRES_APPROVAL = "requires_approval"
    STALE = "stale"


class WorkItemLeafDraft(BaseModel):
    """A work item the planner proposes, in a schema structured output can enforce.

    The generic ``PlannedAction.input`` is an untyped object, which Gemini's response
    schema cannot constrain — models reliably leave it empty. Work item breakdowns
    therefore travel in the typed ``PlannedAction.work_items`` field and are
    materialized into the tool's ``input.items`` contract by the runtime.
    """

    model_config = ConfigDict(extra="forbid")

    title: str
    description: str = ""
    start_date: str | None = None
    due_date: str | None = None
    priority: str | None = Field(default=None, description="low | medium | high | urgent")
    status: str | None = None
    assignee_usernames: list[str] = Field(
        default_factory=list,
        description=(
            "Exact username values from hydrated member context (the username "
            "field, never a display name)."
        ),
    )
    label_names: list[str] = Field(default_factory=list)


class WorkItemChildDraft(WorkItemLeafDraft):
    children: list[WorkItemLeafDraft] = Field(default_factory=list)


class WorkItemDraft(WorkItemLeafDraft):
    children: list[WorkItemChildDraft] = Field(default_factory=list)


class WorkItemUpdateDraft(BaseModel):
    """A typed field-level update to one existing work item.

    Same rationale as WorkItemLeafDraft: structured output cannot fill the
    untyped action input, so bulk refinement updates travel here and are
    materialized into the update tool's input contract. None means "leave the
    field unchanged"; empty lists are also treated as no change.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    status: str | None = None
    due_date: str | None = None
    assignee_usernames: list[str] = Field(default_factory=list)
    label_names: list[str] = Field(default_factory=list)


class PlannedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(default_factory=lambda: str(uuid4()))
    plan_id: str
    action_type: str
    tool_name: str
    instruction: str
    input: dict[str, Any] = Field(default_factory=dict)
    work_items: list[WorkItemDraft] = Field(
        default_factory=list,
        description=(
            "For create_workitem_tree actions: the full work item breakdown. "
            "Each entry needs a title and description; use children to nest "
            "sub-items. Leave empty for other tools."
        ),
    )
    work_item_updates: list[WorkItemUpdateDraft] = Field(
        default_factory=list,
        description=(
            "For update_workitems_bulk actions: one entry per existing work "
            "item to change, with only the fields that should change. Leave "
            "empty for other tools."
        ),
    )
    target_item_ids: list[str] = Field(
        default_factory=list,
        description=(
            "For add_sprint_work_items actions: the itemIds of existing work "
            "items to add to the sprint. Leave empty for other tools."
        ),
    )
    depends_on: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    status: ActionStatus = ActionStatus.PENDING
    idempotency_key: str
    expected_entity_versions: dict[str, str | int] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def is_unblocked(self, completed_action_ids: set[str]) -> bool:
        return all(action_id in completed_action_ids for action_id in self.depends_on)

    def with_status(self, status: ActionStatus) -> PlannedAction:
        return self.model_copy(update={"status": status, "updated_at": utc_now()})
