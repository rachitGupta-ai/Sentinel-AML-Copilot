"""Alert Triage & Case Service (interface + ``Impl``).

When an :class:`~app.models.source.Alert` is raised this service turns it into an
investigation :class:`~app.models.case.Case`, aggregating the entity's governed
risk, correlating/deduplicating related alerts, ranking open cases for the
Command_Centre, and flagging stale/incomplete data (design §3; Req 4). Four
invariants hold, each mapped to a correctness property:

* **Governed-only aggregation (Property 10).** :meth:`on_alert` creates a case in
  state ``RECEIVED`` whose ``risk_aggregation`` is populated *solely* from
  :class:`~app.models.governed.GovernedMetricValue` instances read through the
  injected :class:`~app.services.metric_service.MetricService`. Triage computes no
  figure itself — it only reads governed values (Req 4.1).
* **Total order on governed risk (Property 11).** :meth:`rank_open_cases` returns
  open cases ordered non-increasing by their governed ``aggregate_score``, with a
  deterministic ``case_id`` tie-break, so the ranking is a stable total order
  that is invariant under input permutation (Req 4.2).
* **Never auto-confirm (Property 12).** No triage operation ever sets a case's
  classification to ``risk_event``; a case stays a ``risk_signal`` until a human
  confirms it through the Approval_Gate (Req 4.3).
* **Correlation collapse (Property 14).** Alerts sharing a correlation group
  within the configurable window collapse into one case; that case's
  ``correlated_alert_ids`` exactly covers the group's alert ids (Req 4.5).

Freshness (:meth:`freshness_state`, Property 13) sets the stale/incomplete
indicator *iff* the data age exceeds the configured threshold **or** a required
field is missing. The predicate takes an explicit reference time rather than
reading the wall clock, so the decision is deterministic and testable (design §3
"Deterministic ... freshness takes a reference time").

Snowflake/case persistence is abstracted behind the
:class:`~app.services.case_repository.CaseRepository` port, so the service runs
against ``APP.CASE`` in production and an in-memory fake in tests, consistent with
the audit and metric services. The :class:`~app.services.metric_service.MetricService`
and :class:`~app.services.audit_service.AuditService` are injected. Every state
transition (case created / alert correlated) emits an append-only audit record
(Req 8.1).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.config.settings import TriageSettings
from app.models.case import Case, CaseState
from app.models.governed import EntityRiskView, FreshnessIndicator
from app.models.source import Alert
from app.services.audit_service import (
    SYSTEM_ACTOR,
    AuditService,
    AuditServiceImpl,
    build_audit_record,
)
from app.services.case_repository import CaseRepository, InMemoryCaseRepository
from app.services.metric_service import MetricService, MetricServiceImpl

# ---------------------------------------------------------------------------
# Canonical audit action names.
#
# Kept stable and package-private so the audit trail is queryable and the triage
# property tests (Property 10, Property 14) can assert the recorded transitions.
# ---------------------------------------------------------------------------

# Recorded when an alert opens a new case in RECEIVED (Req 4.1, Req 8.1).
ACTION_CASE_RECEIVED = "triage.case_received"

# Recorded when an alert is correlated into an existing case (Req 4.5, Req 8.1).
ACTION_ALERT_CORRELATED = "triage.alert_correlated"


class CaseRanking(BaseModel):
    """One open case positioned in the governed-risk ranking (Req 4.2, Property 11).

    ``rank`` is the 1-based position after ordering open cases non-increasing by
    ``aggregate_score`` (governed composite risk), ties broken by ``case_id``.
    Carrying the score and entity makes the ranking self-describing for the
    Command_Centre without a second lookup.
    """

    model_config = ConfigDict(frozen=True)

    rank: int = Field(ge=1, description="1-based position in the governed-risk ranking.")
    case_id: str = Field(description="The ranked case.")
    entity_id: str = Field(description="Entity under investigation for this case.")
    aggregate_score: Decimal = Field(
        description="Governed composite risk score the ranking is ordered by (Req 4.2).",
    )


class CaseCorrelation(BaseModel):
    """Result of correlating an alert into a case (Req 4.5, Property 14).

    ``is_new_case`` distinguishes opening a fresh case from joining an existing
    one. ``correlated_alert_ids`` is the full set of alert ids collapsed into the
    case so far — for a given correlation group within the window it exactly
    covers that group's inputs (Property 14).
    """

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(description="The case the alert was correlated into.")
    correlation_key: str = Field(description="The alert's correlation group key.")
    is_new_case: bool = Field(description="True when a new case was opened for this alert.")
    correlated_alert_ids: list[str] = Field(
        default_factory=list,
        description="All alert ids collapsed into the case (covers the group, Req 4.5).",
    )


@runtime_checkable
class TriageService(Protocol):
    """Alert triage, case creation, ranking, correlation, and freshness (Req 4)."""

    def on_alert(self, alert: Alert) -> Case:
        """Triage ``alert`` into a case in ``RECEIVED`` with governed risk aggregation.

        The returned case is a ``risk_signal`` in state ``RECEIVED`` whose
        ``risk_aggregation`` comes solely from governed metric values for the
        alert's entity (Req 4.1, Property 10). Alerts in the same correlation
        group within the window collapse into one case (Req 4.5, Property 14).
        The transition is recorded in the audit trail (Req 8.1).
        """
        ...

    def rank_open_cases(self) -> list[CaseRanking]:
        """Return open cases ordered non-increasing by governed risk (Req 4.2).

        The ordering is a stable total order (``aggregate_score`` desc, then
        ``case_id`` asc) and is therefore invariant under the order in which cases
        were stored (Property 11).
        """
        ...

    def correlate(self, alert: Alert) -> CaseCorrelation:
        """Correlate ``alert`` into a new or existing case (Req 4.5, Property 14)."""
        ...

    def freshness_state(self, case: Case, reference_time: datetime) -> FreshnessIndicator:
        """Compute the stale/incomplete indicator for ``case`` at ``reference_time``.

        Stale/incomplete is set *iff* the data age (``reference_time`` minus the
        latest event time) exceeds the configured freshness threshold, or a
        required field is missing from the freshness data (Req 4.4, Property 13).
        ``reference_time`` is supplied by the caller rather than read from the
        wall clock, keeping the decision deterministic.
        """
        ...


class TriageServiceImpl:
    """Default :class:`TriageService` backed by injected ports and services.

    Persistence goes through the :class:`CaseRepository` port; governed figures
    come from the :class:`MetricService`; every transition is audited through the
    :class:`AuditService`. When a collaborator is not supplied an in-memory /
    default implementation is used, convenient for local runs and unit tests.
    """

    def __init__(
        self,
        *,
        metric_service: Optional[MetricService] = None,
        audit_service: Optional[AuditService] = None,
        case_repository: Optional[CaseRepository] = None,
        settings: Optional[TriageSettings] = None,
    ) -> None:
        self._metrics: MetricService = (
            metric_service if metric_service is not None else MetricServiceImpl()
        )
        self._audit: AuditService = (
            audit_service if audit_service is not None else AuditServiceImpl()
        )
        self._cases: CaseRepository = (
            case_repository if case_repository is not None else InMemoryCaseRepository()
        )
        self._settings: TriageSettings = settings if settings is not None else TriageSettings()

    def on_alert(self, alert: Alert) -> Case:
        """See :meth:`TriageService.on_alert`.

        Delegates to :meth:`correlate` (which creates-or-joins the case, persists
        it, and emits the single state-transition audit record) and returns the
        resulting persisted case. This keeps correlation/dedup and the
        received/correlated audit transitions in one place (Property 10,
        Property 14).
        """
        correlation = self.correlate(alert)
        case = self._cases.get(correlation.case_id)
        if case is None:  # pragma: no cover - defensive; correlate just upserted it
            raise RuntimeError(
                f"Case '{correlation.case_id}' not found immediately after correlation."
            )
        return case

    def rank_open_cases(self) -> list[CaseRanking]:
        """See :meth:`TriageService.rank_open_cases`."""
        cases = self._cases.list_open_cases()
        # Non-increasing by governed aggregate score; deterministic case_id
        # tie-break makes this a stable total order invariant under permutation
        # (Property 11). No wall-clock/random input participates in the ordering.
        return [
            CaseRanking(
                rank=index + 1,
                case_id=case.case_id,
                entity_id=case.entity_id,
                aggregate_score=case.risk_aggregation.aggregate_score,
            )
            for index, case in enumerate(_stable_risk_order(cases))
        ]

    def correlate(self, alert: Alert) -> CaseCorrelation:
        """See :meth:`TriageService.correlate`."""
        existing = self._cases.find_by_correlation_key(alert.correlation_key)
        if existing is None:
            risk_aggregation = self._metrics.aggregate_entity_risk(alert.entity_id)
            case = self._new_case(alert, risk_aggregation)
            self._cases.upsert(case, alert.correlation_key)
            self._audit.append(
                build_audit_record(
                    action=ACTION_CASE_RECEIVED,
                    actor_id=SYSTEM_ACTOR,
                    case_ref=case.case_id,
                    entity_ref=case.entity_id,
                    input_refs=[alert.alert_id],
                    output_refs=[case.case_id],
                )
            )
            return CaseCorrelation(
                case_id=case.case_id,
                correlation_key=alert.correlation_key,
                is_new_case=True,
                correlated_alert_ids=list(case.correlated_alert_ids),
            )

        updated = self._add_alert_to_case(existing, alert)
        self._cases.upsert(updated, alert.correlation_key)
        self._audit.append(
            build_audit_record(
                action=ACTION_ALERT_CORRELATED,
                actor_id=SYSTEM_ACTOR,
                case_ref=updated.case_id,
                entity_ref=updated.entity_id,
                input_refs=[alert.alert_id],
                output_refs=[updated.case_id],
            )
        )
        return CaseCorrelation(
            case_id=updated.case_id,
            correlation_key=alert.correlation_key,
            is_new_case=False,
            correlated_alert_ids=list(updated.correlated_alert_ids),
        )

    def freshness_state(self, case: Case, reference_time: datetime) -> FreshnessIndicator:
        """See :meth:`TriageService.freshness_state`."""
        freshness = case.freshness
        # A required field is missing when the case has no data at all or no
        # latest-event timestamp to measure age against (Property 13, completeness
        # half of the predicate).
        missing_required_field = (not freshness.has_data) or freshness.latest_event_time is None

        stale_by_age = False
        if freshness.latest_event_time is not None:
            age_seconds = (reference_time - freshness.latest_event_time).total_seconds()
            stale_by_age = age_seconds > self._settings.freshness_threshold_seconds

        is_stale = missing_required_field or stale_by_age
        return FreshnessIndicator(
            latest_event_time=freshness.latest_event_time,
            has_data=freshness.has_data,
            is_stale=is_stale,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _new_case(self, alert: Alert, risk_aggregation: EntityRiskView) -> Case:
        """Build a fresh ``RECEIVED`` ``risk_signal`` case for ``alert`` (Req 4.1).

        The case always starts in ``RECEIVED`` with the default ``risk_signal``
        classification; it is never created as a ``risk_event`` (Property 12).
        """
        return Case(
            case_id=_new_case_id(),
            entity_id=alert.entity_id,
            state=CaseState.RECEIVED,
            classification="risk_signal",
            risk_aggregation=risk_aggregation,
            correlated_alert_ids=[alert.alert_id],
        )

    @staticmethod
    def _add_alert_to_case(case: Case, alert: Alert) -> Case:
        """Return a copy of ``case`` with ``alert`` added to its correlated set.

        Idempotent: an alert id already covered by the case is not duplicated, so
        ``correlated_alert_ids`` exactly covers the group's distinct inputs
        (Property 14). Classification and state are carried through unchanged —
        correlation never advances the lifecycle or confirms a risk event
        (Property 12). ``Case`` is frozen, so a new instance is produced via
        ``model_copy`` rather than an in-place mutation.
        """
        if alert.alert_id in case.correlated_alert_ids:
            return case
        return case.model_copy(
            update={"correlated_alert_ids": [*case.correlated_alert_ids, alert.alert_id]}
        )


def _stable_risk_order(cases: list[Case]) -> list[Case]:
    """Order ``cases`` non-increasing by governed risk, ties by ``case_id`` asc.

    A single ``sorted`` with a composite key cannot combine a descending numeric
    key with an ascending string tie-break via ``reverse``. Negating the score is
    unsafe for arbitrary ``Decimal``; instead we sort ascending by ``case_id``
    first, then stably sort by score descending, which yields: higher score
    first, and within equal scores ``case_id`` ascending. The result is a total
    order invariant under input permutation (Property 11).
    """
    by_case_id = sorted(cases, key=lambda c: c.case_id)
    return sorted(
        by_case_id,
        key=lambda c: c.risk_aggregation.aggregate_score,
        reverse=True,
    )


def _new_case_id() -> str:
    """Generate a stable, unique case id."""
    return str(uuid.uuid4())
