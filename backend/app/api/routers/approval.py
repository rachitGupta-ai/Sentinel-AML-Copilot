"""Approval router: the human Approval_Gate and simulated action (Req 7).

Exposes the HITL approval service so a compliance officer can review and decide:

* ``POST /api/approval/{case_id}/submit`` — move ``RECOMMENDATION_READY →
  AWAITING_APPROVAL`` and return the complete review payload (recommendation,
  groundedness score, full lineage, evidence) (Req 7.2, Property 26).
* ``POST /api/approval/{case_id}/approve`` — approve, recording reviewer identity
  + timestamp (Req 7.3, Property 28).
* ``POST /api/approval/{case_id}/reject`` — reject with a non-empty reason (Req 7.4).
* ``POST /api/approval/{case_id}/action`` — execute the approved material action
  safely and idempotently (Req 7.3, Req 9.6, Property 36).

Submitting is part of the review flow and needs ``VIEW_CASE``; approve / reject /
action are the Approval_Gate itself and require ``APPROVE_ACTION`` — only a
compliance officer passes, and every denial is audited (Req 9.2). Each successful
transition broadcasts a live approval-queue update (Req 10.5, 11.1). Illegal
state transitions / missing reviewer identity surface as ``409``/``422`` from the
service's :class:`ApprovalError`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import AppServices, get_services, load_case, require_action
from app.api.live import LiveEvent, LiveEventType, publish_event
from app.api.schemas import ApproveRequest, RejectRequest
from app.models.case import Case, CaseState
from app.services.approval_service import ActionResult, ApprovalError, ApprovalItem
from app.services.auth_service import ProtectedAction

router = APIRouter(prefix="/api/approval", tags=["approval"])


def _publish(request: Request, event_type: LiveEventType, case: Case, detail: str) -> None:
    """Broadcast an approval-queue live update for a case transition (Req 10.5)."""
    publish_event(
        getattr(request.app.state, "live", None),
        LiveEvent(
            type=event_type,
            case_id=case.case_id,
            entity_id=case.entity_id,
            state=case.state.value,
            detail=detail,
        ),
    )


@router.post(
    "/{case_id}/submit",
    response_model=ApprovalItem,
    dependencies=[Depends(require_action(ProtectedAction.VIEW_CASE))],
)
async def submit_for_approval(
    case_id: str,
    request: Request,
    services: AppServices = Depends(get_services),
) -> ApprovalItem:
    """Submit a recommendation-ready case for approval (Req 7.2)."""
    case = load_case(services, case_id)
    try:
        item = services.approval_service.submit_for_approval(case)
    except ApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    submitted = load_case(services, case_id)
    _publish(
        request,
        LiveEventType.CASE_SUBMITTED_FOR_APPROVAL,
        submitted,
        "Case submitted for approval.",
    )
    return item


@router.post(
    "/{case_id}/approve",
    response_model=Case,
    dependencies=[Depends(require_action(ProtectedAction.APPROVE_ACTION))],
)
async def approve_case(
    case_id: str,
    body: ApproveRequest,
    request: Request,
    services: AppServices = Depends(get_services),
) -> Case:
    """Approve an awaiting case, recording the reviewer (Req 7.3, Property 28)."""
    case = load_case(services, case_id)
    try:
        approved = services.approval_service.approve(case, body.reviewer_id)
    except ApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _publish(request, LiveEventType.CASE_APPROVED, approved, "Case approved.")
    return approved


@router.post(
    "/{case_id}/reject",
    response_model=Case,
    dependencies=[Depends(require_action(ProtectedAction.APPROVE_ACTION))],
)
async def reject_case(
    case_id: str,
    body: RejectRequest,
    request: Request,
    services: AppServices = Depends(get_services),
) -> Case:
    """Reject an awaiting case with a reason, recording the reviewer (Req 7.4)."""
    case = load_case(services, case_id)
    try:
        rejected = services.approval_service.reject(case, body.reviewer_id, body.reason)
    except ApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _publish(request, LiveEventType.CASE_REJECTED, rejected, "Case rejected.")
    return rejected


@router.post(
    "/{case_id}/action",
    response_model=ActionResult,
    dependencies=[Depends(require_action(ProtectedAction.APPROVE_ACTION))],
)
async def simulate_action(
    case_id: str,
    request: Request,
    services: AppServices = Depends(get_services),
) -> ActionResult:
    """Execute the approved material action, idempotently (Req 7.3, 9.6, Property 36).

    The Approval_Gate is enforced inside the service: an un-approved case raises
    :class:`ApprovalError` (surfaced as ``409``) and no action runs (Property 25).
    Replays return the recorded result with an incremented suppressed-duplicate
    count rather than re-executing.
    """
    case = load_case(services, case_id)
    try:
        result = services.approval_service.simulate_action(case)
    except ApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    final = load_case(services, case_id)
    event_type = (
        LiveEventType.ACTION_COMPLETED
        if result.final_state == CaseState.ACTION_COMPLETED
        else LiveEventType.ACTION_FAILED
    )
    _publish(request, event_type, final, f"Action {result.action} -> {result.final_state.value}.")
    return result
