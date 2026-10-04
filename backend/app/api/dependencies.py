"""Dependency-injection seam and the RBAC + purpose-check dependency layer.

This module is the single wiring point between the FastAPI routers (task 18) and
the service layer the earlier tasks built. It exists so that:

* **All services are connected end to end** behind one container
  (:class:`AppServices`) — triage, metric, investigation, SAR, approval, audit,
  KPI, auth, grounding, guard — with no orphaned code (design "Application
  Layer").
* **Cortex-dependent routers (investigate, sar) can be constructed with a fake
  adapter in tests.** The container is built from an injected
  :class:`~app.snowflake.cortex.CortexAdapter`; in production it is built from a
  live Snowpark session (:func:`build_app_services`), but a test can place a
  container built with an in-memory fake adapter onto ``app.state`` and every
  router picks it up — no live Snowflake/Cortex connection is required for import
  or for the non-Snowflake routes.
* **RBAC + a purpose check are enforced in one dependency layer** (Req 9.2). A
  router declares the :class:`ProtectedAction` it guards (its *purpose*) via
  :func:`require_action`; the dependency resolves the caller's
  :class:`~app.services.auth_service.Role` from a request header and calls
  :meth:`AuthService.authorize`. An unauthorised request yields ``403`` and the
  denial is already recorded in the audit trail (``AuthorizationError`` carries
  the denial ``audit_id``), so the block is provably audited before the guarded
  handler runs.

The DI design uses two layers:

* :class:`AppServices` — a plain, framework-free container holding one instance of
  each service. It wires the shared collaborators (one audit service, one metric
  service, one case repository, …) so that, e.g., a case created by the ingest
  router is visible to the cases/approval routers and every denial/transition
  lands in the *same* audit trail. This makes the golden path work against a
  single in-memory wiring in tests.
* FastAPI ``Depends`` functions — thin accessors that read the container from
  ``request.app.state`` so a test can override the whole container by assigning
  ``app.state.services`` (or by FastAPI dependency overrides on
  :func:`get_services`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

from fastapi import Depends, Header, HTTPException, Request, status

from app.services.approval_service import ApprovalRepository, ApprovalServiceImpl
from app.services.audit_service import AuditService, AuditServiceImpl
from app.services.auth_service import (
    AuthorizationError,
    AuthService,
    AuthServiceImpl,
    ProtectedAction,
    Role,
)
from app.services.case_repository import CaseRepository, InMemoryCaseRepository
from app.services.grounding_service import GroundingService, GroundingServiceImpl
from app.services.guard_service import GuardService, GuardServiceImpl
from app.services.investigation_service import InvestigationServiceImpl
from app.services.kpi_service import KpiServiceImpl
from app.services.metric_repository import InMemoryMetricRepository, MetricRepository
from app.services.metric_service import MetricService, MetricServiceImpl
from app.services.sar_service import SARServiceImpl
from app.services.telemetry_repository import (
    InMemoryTelemetryRepository,
    TelemetryRepository,
)
from app.services.triage_service import TriageServiceImpl
from app.snowflake.cortex import CortexAdapter

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids importing Snowpark eagerly
    from snowflake.snowpark import Session

# Header the caller presents its role in. Role resolution is deliberately
# header-driven (not a trusted session) because this is a demo copilot; the auth
# service fails closed on an unknown/missing role (an unknown role is entitled to
# nothing — Property 33), so an absent header simply means "no entitlement".
ROLE_HEADER = "X-Role"


@dataclass
class AppServices:
    """Framework-free container of fully-wired application services.

    One instance is held on ``app.state.services`` for the life of the app (or the
    life of a test). All services share the same audit service, metric service,
    and case repository, so a case created through the ingest router is visible to
    the cases/approval routers and every transition/denial lands in one audit
    trail — the services are connected end to end (design "Application Layer").

    The container is constructed from a :class:`CortexAdapter`, which is the only
    collaborator that needs a live session in production. In tests a fake adapter
    is supplied so the Cortex-dependent services (investigation, SAR) can be
    exercised without a live Snowflake/Cortex connection.
    """

    cortex: CortexAdapter
    audit_service: AuditService
    metric_service: MetricService
    guard_service: GuardService
    grounding_service: GroundingService
    case_repository: CaseRepository
    approval_repository: Optional[ApprovalRepository]
    telemetry_repository: TelemetryRepository
    auth_service: AuthService
    triage_service: TriageServiceImpl
    investigation_service: InvestigationServiceImpl
    sar_service: SARServiceImpl
    approval_service: ApprovalServiceImpl
    kpi_service: KpiServiceImpl


def build_app_services(
    cortex: CortexAdapter,
    *,
    audit_service: Optional[AuditService] = None,
    metric_repository: Optional[MetricRepository] = None,
    case_repository: Optional[CaseRepository] = None,
    approval_repository: Optional[ApprovalRepository] = None,
    telemetry_repository: Optional[TelemetryRepository] = None,
    guard_service: Optional[GuardService] = None,
    grounding_service: Optional[GroundingService] = None,
) -> AppServices:
    """Wire a fully-connected :class:`AppServices` around a :class:`CortexAdapter`.

    This is the one place the services are assembled with *shared* collaborators,
    so the whole workflow is coherent end to end:

    * a single :class:`AuditService` — every denial/transition/AI call across all
      services appends to the same append-only trail, so a case's lineage replay
      (``audit``/``kpi`` routers) sees the full chain;
    * a single :class:`MetricService` over one :class:`MetricRepository` — triage,
      investigation and SAR all read governed figures from the same semantic view;
    * a single :class:`CaseRepository` — a case opened by ingest/triage is the
      same case the cases/approval routers read and transition.

    Any collaborator may be overridden; omitted ones default to in-memory/default
    implementations so the container works without a live Snowflake connection
    (used by the TestClient wiring). ``cortex`` is always injected — a live
    adapter in production, a fake in tests — so the Cortex-dependent services are
    constructible either way.

    Args:
        cortex: The Cortex adapter the investigation/SAR services narrate through.
        audit_service: Shared audit service; defaults to an in-memory-backed one.
        metric_repository: Governed-metric read surface; defaults to in-memory.
        case_repository: Case store shared by triage/approval; defaults to in-memory.
        approval_repository: Approval-decision store; defaults inside the service.
        telemetry_repository: KPI telemetry store; defaults to in-memory.
        guard_service: Injection/allow-list guard; defaults to the pattern guard.
        grounding_service: Dual-grounding scorer; defaults to the pure scorer.

    Returns:
        A fully-wired :class:`AppServices` container.
    """
    audit = audit_service if audit_service is not None else AuditServiceImpl()
    metrics = MetricServiceImpl(
        repository=metric_repository
        if metric_repository is not None
        else InMemoryMetricRepository()
    )
    guard = guard_service if guard_service is not None else GuardServiceImpl(audit_service=audit)
    grounding = grounding_service if grounding_service is not None else GroundingServiceImpl()
    cases = case_repository if case_repository is not None else InMemoryCaseRepository()
    telemetry = (
        telemetry_repository
        if telemetry_repository is not None
        else InMemoryTelemetryRepository()
    )

    auth = AuthServiceImpl(audit_service=audit)
    triage = TriageServiceImpl(
        metric_service=metrics,
        audit_service=audit,
        case_repository=cases,
    )
    investigation = InvestigationServiceImpl(
        cortex,
        metric_service=metrics,
        grounding_service=grounding,
        audit_service=audit,
    )
    sar = SARServiceImpl(
        cortex,
        metric_service=metrics,
        audit_service=audit,
    )
    approval = ApprovalServiceImpl(
        audit_service=audit,
        case_repository=cases,
        approval_repository=approval_repository,
    )
    kpi = KpiServiceImpl(
        telemetry_repository=telemetry,
        audit_service=audit,
    )

    return AppServices(
        cortex=cortex,
        audit_service=audit,
        metric_service=metrics,
        guard_service=guard,
        grounding_service=grounding,
        case_repository=cases,
        approval_repository=approval_repository,
        telemetry_repository=telemetry,
        auth_service=auth,
        triage_service=triage,
        investigation_service=investigation,
        sar_service=sar,
        approval_service=approval,
        kpi_service=kpi,
    )


def build_app_services_from_session(session: "Session") -> AppServices:
    """Wire :class:`AppServices` for production from a live Snowpark ``session``.

    Builds the Cortex adapter and the Snowflake-backed repositories from the
    session, then delegates to :func:`build_app_services`. Imported lazily so that
    importing this module (and the app factory) never requires the Snowpark driver
    — the non-Snowflake routes and the whole test wiring work without it.
    """
    # Local imports keep the Snowpark-dependent adapters out of the import path
    # for tests and the config guard (consistent with app.snowflake.session).
    from app.services.audit_repository import SnowflakeAuditRecordRepository
    from app.services.case_repository import SnowflakeCaseRepository
    from app.services.metric_repository import SnowflakeMetricRepository
    from app.services.telemetry_repository import SnowflakeTelemetryRepository
    from app.snowflake.cortex import build_cortex_adapter

    audit = AuditServiceImpl(SnowflakeAuditRecordRepository(session))
    guard = GuardServiceImpl(audit_service=audit)
    cortex = build_cortex_adapter(session, guard=guard)
    return build_app_services(
        cortex,
        audit_service=audit,
        metric_repository=SnowflakeMetricRepository(session),
        case_repository=SnowflakeCaseRepository(session),
        telemetry_repository=SnowflakeTelemetryRepository(session),
        guard_service=guard,
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies.
# ---------------------------------------------------------------------------


def get_services(request: Request) -> AppServices:
    """Return the :class:`AppServices` container wired onto the app.

    Reads ``request.app.state.services``. A test overrides the whole service graph
    simply by assigning that attribute (or via a FastAPI dependency override on
    this function), which is the DI seam that lets the API run against in-memory
    fakes without a live Snowflake/Cortex connection.
    """
    services: Optional[AppServices] = getattr(request.app.state, "services", None)
    if services is None:  # pragma: no cover - defensive; set at app startup
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Application services are not initialised.",
        )
    return services


def get_role(x_role: Optional[str] = Header(default=None, alias=ROLE_HEADER)) -> Optional[Role]:
    """Resolve the caller's :class:`Role` from the role header (fail-closed).

    Returns the resolved role, or ``None`` when the header is absent or names an
    unrecognised role. The RBAC dependency treats ``None`` as "entitled to
    nothing" so the request fails closed with a ``403`` and an audited denial
    (Property 33). The raw header value is never trusted beyond role resolution.
    """
    return Role.from_string(x_role)


def load_case(services: AppServices, case_id: str):
    """Load a persisted :class:`~app.models.case.Case` or raise ``404``.

    Shared by the cases/investigate/sar/approval routers so a missing case id
    yields a consistent ``404`` rather than an opaque error. Reads through the
    shared case repository, so it sees cases opened by the ingest/triage router.
    """
    case = services.case_repository.get(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case '{case_id}' not found.",
        )
    return case


def require_action(action: ProtectedAction) -> Callable[..., Role | None]:
    """Build a dependency that enforces RBAC for one protected ``action`` (Req 9.2).

    The returned dependency is the single RBAC + purpose-check layer: ``action``
    is the handler's declared *purpose*, and the dependency resolves the caller's
    role and calls :meth:`AuthService.authorize`. On denial the auth service has
    already appended a denial audit record (the block is provably audited before
    the guarded handler runs); the dependency then raises ``403`` carrying the
    denial reason and the audit id. On success it returns the resolved
    :class:`Role` so a handler can use it (e.g. for role-aware views).

    Usage::

        @router.get("/cases", dependencies=[Depends(require_action(ProtectedAction.VIEW_CASE))])
        async def list_cases(...): ...
    """

    def _dependency(
        services: AppServices = Depends(get_services),
        role: Optional[Role] = Depends(get_role),
    ) -> Role | None:
        try:
            services.auth_service.authorize(role, action)
        except AuthorizationError as exc:
            # The denial is already in the audit trail; surface a 403 that
            # references the audited denial without leaking anything sensitive.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "forbidden",
                    "action": action.value,
                    "reason": exc.result.reason,
                    "audit_id": exc.result.audit_id,
                },
            ) from exc
        return role

    return _dependency
