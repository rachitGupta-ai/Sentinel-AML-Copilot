"""KPI router: operational and AI-quality KPIs (Req 10).

``GET /api/kpi`` returns a :class:`~app.models.telemetry.KpiSnapshot` computed from
recorded per-answer telemetry and audit records: investigation time per case,
percent fully grounded, refusal-correctness count, duplicate-suppression count,
and alerts-to-cases correlation count (Req 10.3). Every value is labelled
``demonstrated`` (measured this run) vs ``intended`` (a production target) so the
system-health / AI-quality view never conflates the two (Req 10.4, Property 39).

The snapshot is scoped to the currently-open cases (so audit-derived counts reflect
the live workload); callers can broaden the scope as the dashboard evolves. Reading
KPIs requires the ``VIEW_CASE`` entitlement; denials are audited (Req 9.2).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import AppServices, get_services, require_action
from app.models.telemetry import KpiSnapshot
from app.services.auth_service import ProtectedAction

router = APIRouter(
    prefix="/api/kpi",
    tags=["kpi"],
    dependencies=[Depends(require_action(ProtectedAction.VIEW_CASE))],
)


@router.get("", response_model=KpiSnapshot)
async def get_kpis(
    services: AppServices = Depends(get_services),
) -> KpiSnapshot:
    """Compute the current KPI snapshot over open cases (Req 10.3, 10.4).

    Audit-derived KPIs (duplicate suppression, alerts-to-cases correlation,
    investigation time) are computed across the open cases' lineages; per-answer
    KPIs span all recorded telemetry. Demonstrated and intended values are kept
    disjoint (Property 39) — no intended target is supplied here, so the intended
    channel is empty rather than inferred from measured values.
    """
    open_case_ids = [r.case_id for r in services.triage_service.rank_open_cases()]
    return services.kpi_service.compute_kpis(open_case_ids)
