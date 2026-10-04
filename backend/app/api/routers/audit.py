"""Audit router: replay a case's immutable lineage (Req 8).

``GET /api/audit/{case_id}`` reconstructs a case's full
:class:`~app.models.audit.Lineage` from append-only audit records alone — the
chain question → metric → query → rows → narrative → approval → outcome — so the
audit-trail view can show the replayable history (Req 8.4, Property 31). Reading
the audit trail requires the ``VIEW_CASE`` entitlement; a non-entitled request is
denied with ``403`` and the denial is itself audited (Req 9.2).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import AppServices, get_services, require_action
from app.models.audit import Lineage
from app.services.auth_service import ProtectedAction

router = APIRouter(
    prefix="/api/audit",
    tags=["audit"],
    dependencies=[Depends(require_action(ProtectedAction.VIEW_CASE))],
)


@router.get("/{case_id}", response_model=Lineage)
async def replay_lineage(
    case_id: str,
    services: AppServices = Depends(get_services),
) -> Lineage:
    """Replay the case's lineage from audit records alone (Req 8.4, Property 31).

    Returns an empty lineage (no records) for an unknown case rather than a 404:
    the audit trail is the source of truth, and "no records" is itself the honest
    answer for a case id that produced no auditable operations.
    """
    return services.audit_service.replay(case_id)
