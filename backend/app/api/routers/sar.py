"""SAR router: draft an audit-ready SAR for a case (Req 6).

``POST /api/sar/{case_id}`` produces a :class:`~app.models.answer.SARDraft` via the
SAR Drafting Service — entity summary, suspicious pattern, governed figures (each
linked to metric + version + lineage), and cited policy basis. The draft is always
AI-generated decision support and is never auto-filed (Property 24); if any element
is ungrounded it is marked ``incomplete`` and not filing-ready (Property 23).

When a *complete* draft is produced, the case is advanced to
``RECOMMENDATION_READY`` so it becomes eligible for the human Approval_Gate
(design case lifecycle: ``INVESTIGATING → RECOMMENDATION_READY``). The transition
is persisted through the shared case repository (preserving the case's correlation
group) so the approval router sees the ready case. Drafting requires the
``VIEW_CASE`` entitlement; denials are audited (Req 9.2). Advancing the lifecycle
is a prerequisite for approval, not a material action — no filing occurs here
(Req 6.5).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import AppServices, get_services, load_case, require_action
from app.models.answer import SARDraft
from app.models.case import CaseState
from app.services.auth_service import ProtectedAction

router = APIRouter(
    prefix="/api/sar",
    tags=["sar"],
    dependencies=[Depends(require_action(ProtectedAction.VIEW_CASE))],
)


@router.post("/{case_id}", response_model=SARDraft)
async def draft_sar(
    case_id: str,
    services: AppServices = Depends(get_services),
) -> SARDraft:
    """Draft a SAR for the case and, if complete, mark it recommendation-ready (Req 6).

    The draft is produced from governed figures + cited policy only. A complete
    draft advances the case to ``RECOMMENDATION_READY`` so it can enter the
    Approval_Gate; an incomplete (ungrounded) draft leaves the case where it is —
    it is not filing-ready (Req 6.3) and must not be put forward for approval.
    """
    case = load_case(services, case_id)
    draft = services.sar_service.draft(case)

    if draft.completeness == "complete" and case.state != CaseState.RECOMMENDATION_READY:
        ready = case.model_copy(update={"state": CaseState.RECOMMENDATION_READY})
        # Persist the lifecycle transition in place (preserves the correlation
        # group). save() raises KeyError only for an unknown case, which cannot
        # happen here since load_case already fetched it.
        services.case_repository.save(ready)

    return draft
