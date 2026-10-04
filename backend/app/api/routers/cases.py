"""Cases router: Command_Centre ranked list, case detail, and freshness (Req 4, 11.1).

Read surface over the triage/case service for the Command_Centre:

* ``GET /api/cases`` — open cases ranked non-increasing by governed risk
  (Req 4.2, Property 11), the Command_Centre's primary list.
* ``GET /api/cases/{case_id}`` — a single case with its governed risk aggregation
  and freshness indicator (Req 11.1).
* ``GET /api/cases/{case_id}/freshness`` — the stale/incomplete indicator at the
  supplied (or current) reference time (Req 4.4, Property 13).

Every route is guarded by the ``VIEW_CASE`` entitlement; a non-entitled caller is
denied with ``403`` and the denial is audited (Req 9.2).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import AppServices, get_services, load_case, require_action
from app.api.schemas import CaseRankingView
from app.models.case import Case
from app.models.governed import FreshnessIndicator
from app.services.auth_service import ProtectedAction

router = APIRouter(
    prefix="/api/cases",
    tags=["cases"],
    dependencies=[Depends(require_action(ProtectedAction.VIEW_CASE))],
)


@router.get("", response_model=list[CaseRankingView])
async def list_open_cases(
    services: AppServices = Depends(get_services),
) -> list[CaseRankingView]:
    """Return open cases ranked by governed risk for the Command_Centre (Req 4.2)."""
    rankings = services.triage_service.rank_open_cases()
    return [
        CaseRankingView(
            rank=r.rank,
            case_id=r.case_id,
            entity_id=r.entity_id,
            aggregate_score=r.aggregate_score,
        )
        for r in rankings
    ]


@router.get("/{case_id}", response_model=Case)
async def get_case(
    case_id: str,
    services: AppServices = Depends(get_services),
) -> Case:
    """Return a single case with its governed risk aggregation (Req 11.1)."""
    return load_case(services, case_id)


@router.get("/{case_id}/freshness", response_model=FreshnessIndicator)
async def get_case_freshness(
    case_id: str,
    reference_time: datetime | None = Query(
        default=None,
        description="Reference time for the staleness check; defaults to now (UTC).",
    ),
    services: AppServices = Depends(get_services),
) -> FreshnessIndicator:
    """Return the case's stale/incomplete indicator at ``reference_time`` (Req 4.4).

    The reference time is an explicit input (defaulting to now) so the staleness
    decision is deterministic, matching the triage service's freshness predicate
    (Property 13).
    """
    case = load_case(services, case_id)
    ref = reference_time or datetime.now(timezone.utc)
    return services.triage_service.freshness_state(case, ref)
