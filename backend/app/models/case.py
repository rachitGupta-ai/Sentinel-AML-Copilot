"""Case lifecycle and approval domain models.

A ``Case`` moves through the lifecycle state machine from ``RECEIVED`` to a
terminal state, emitting an append-only audit record on every transition
(design "Case lifecycle state machine"; Req 8.1). A candidate ``Risk_Signal``
never auto-advances to a confirmed ``Risk_Event`` — that requires human
confirmation through the Approval_Gate (Req 4.3, Req 7.1).

Design invariants encoded here:

* ``Case.classification`` defaults ``risk_signal`` and is never auto-promoted to
  ``risk_event`` by system operations (Req 4.3, Property 12).
* ``CaseState`` enumerates the lifecycle states; a case starts in ``RECEIVED``
  (Req 4.1, Property 10).
* ``Approval`` records the reviewer identity, timestamp, decision, and reason for
  every approval decision (Req 7.5, Property 28).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.governed import EntityRiskView, FreshnessIndicator


class CaseState(str, Enum):
    """States of the case lifecycle state machine (design state machine; Req 7).

    A case starts in ``RECEIVED`` and advances deterministically; material
    actions only occur after ``APPROVED`` (Req 7.1, Property 25). ``str`` mixin
    makes the enum serialize to its value for persistence/transport.
    """

    RECEIVED = "received"
    TRIAGED = "triaged"
    INVESTIGATING = "investigating"
    RECOMMENDATION_READY = "recommendation_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    ACTION_COMPLETED = "action_completed"
    ACTION_FAILED = "action_failed"
    CLOSED = "closed"


# Case classification (Req 4.3). A candidate ``risk_signal`` is only promoted to a
# confirmed ``risk_event`` by human confirmation through the Approval_Gate.
Classification = Literal["risk_signal", "risk_event"]


class Case(BaseModel):
    """An investigation case over the lifecycle state machine (Req 4, Req 7).

    Created in ``RECEIVED`` with a governed ``risk_aggregation`` populated solely
    from governed metrics (Req 4.1, Property 10). ``classification`` defaults to
    ``risk_signal`` and is never auto-promoted to ``risk_event`` by system
    operations (Req 4.3, Property 12). ``correlated_alert_ids`` covers the alerts
    collapsed into this case within the correlation window (Req 4.5,
    Property 14).
    """

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(description="Stable case identifier.")
    entity_id: str = Field(description="Entity under investigation.")
    state: CaseState = Field(
        default=CaseState.RECEIVED,
        description="Current lifecycle state; cases start in RECEIVED (Req 4.1).",
    )
    classification: Classification = Field(
        default="risk_signal",
        description="Defaults to risk_signal; never auto-promoted to risk_event (Req 4.3).",
    )
    risk_aggregation: EntityRiskView = Field(
        description="Governed risk aggregation populated solely from governed metrics (Req 4.1).",
    )
    freshness: FreshnessIndicator = Field(
        default_factory=FreshnessIndicator,
        description="Stale/incomplete data indicator for this case (Req 4.4).",
    )
    correlated_alert_ids: list[str] = Field(
        default_factory=list,
        description="Alert ids collapsed into this case within the window (Req 4.5).",
    )


# Approval decision outcomes (Req 7.5). Captured verbatim on every decision.
Decision = Literal["approved", "rejected", "overridden"]


class Approval(BaseModel):
    """A human approval decision over a case (Req 7.3, Req 7.5).

    Every approval decision persists the reviewer identity, a timestamp, the
    decision outcome, and (for rejections) a non-empty reason (Req 7.4,
    Property 27, Property 28).
    """

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(description="Case the decision applies to.")
    reviewer_id: str = Field(
        min_length=1,
        description="Non-empty reviewer identity recorded for every decision (Req 7.5).",
    )
    decision: Decision = Field(description="Outcome: approved / rejected / overridden (Req 7.3).")
    reason: str | None = Field(
        default=None,
        description="Reason for the decision; required and non-empty on rejection (Req 7.4).",
    )
    decided_at: datetime = Field(description="Timestamp the decision was recorded (Req 7.5).")
