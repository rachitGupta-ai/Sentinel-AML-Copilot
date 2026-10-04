"""Observability / KPI Service (interface + ``Impl``).

Makes the system's behaviour and value *measurable, not asserted* (design §10;
Req 10). The service does two things:

* **Per-answer telemetry (Req 10.2, Property 37).**
  :meth:`KpiService.record_answer_telemetry` records one
  :class:`~app.models.telemetry.AnswerTelemetry` per produced answer whose
  AI-quality fields — Groundedness_Score, dual-grounding (yes/no), refusal
  (yes/no), citation count — mirror the answer exactly. A structured log line and
  an append-only audit record are emitted for the recording so the telemetry is
  itself observable and traceable (Req 10.1).
* **Operational KPIs (Req 10.3, Property 38).** :meth:`KpiService.compute_kpis`
  aggregates recorded telemetry (plus audit-derived counts) into a
  :class:`~app.models.telemetry.KpiSnapshot` of five KPIs: investigation time per
  case, percentage of answers fully grounded, refusal-correctness count,
  duplicate-suppression count, and alerts-to-cases correlation count. Each
  aggregation equals its defined formula over the recorded set (Property 38).

**Demonstrated vs intended (Req 10.4, Property 39).** Every value in the snapshot
is labelled :class:`~app.models.telemetry.KpiProvenance` ``demonstrated`` (measured
this run from recorded telemetry/audit records) or ``intended`` (a production
target that was *not* measured). The two channels are disjoint: an intended target
is never emitted in the demonstrated channel, and the demonstrated values are
exactly those backed by recorded data. Intended targets are supplied explicitly by
the caller; when none are supplied the ``intended`` channel is empty rather than
being inferred from measured values.

The service is deterministic and store-agnostic. Telemetry persistence goes
through the :class:`~app.services.telemetry_repository.TelemetryRepository` port
(in-memory fake in tests, Snowflake-backed in production); audit-derived counts
and investigation time are reconstructed from append-only audit records via the
injected :class:`~app.services.audit_service.AuditService` (Req 8.4). All
aggregations are pure functions of the recorded telemetry and audit records, so
the KPI computations are unit-testable with fakes and reproducible.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Optional, Protocol, Sequence, runtime_checkable

from app.models.answer import Answer
from app.models.case import Case
from app.models.telemetry import (
    AnswerTelemetry,
    KpiProvenance,
    KpiSnapshot,
    KpiValue,
)
from app.services.audit_service import (
    SYSTEM_ACTOR,
    AuditService,
    AuditServiceImpl,
    build_audit_record,
)
from app.services.telemetry_repository import (
    InMemoryTelemetryRepository,
    TelemetryRepository,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical KPI names. Kept stable and package-private so the snapshot is
# queryable and the KPI property tests (Property 38, Property 39) can assert the
# computed values by name.
# ---------------------------------------------------------------------------

KPI_INVESTIGATION_TIME_PER_CASE = "investigation_time_per_case"
KPI_PERCENT_FULLY_GROUNDED = "percent_fully_grounded"
KPI_REFUSAL_CORRECTNESS_COUNT = "refusal_correctness_count"
KPI_DUPLICATE_SUPPRESSION_COUNT = "duplicate_suppression_count"
KPI_ALERTS_TO_CASES_CORRELATION_COUNT = "alerts_to_cases_correlation_count"

# Audit action name recorded when per-answer telemetry is captured (Req 10.1).
ACTION_TELEMETRY_RECORDED = "kpi.telemetry_recorded"

# Audit action names emitted by other services that the duplicate-suppression and
# correlation KPIs count. Imported by value (not symbol) to avoid a service->service
# import cycle; these mirror the constants in approval_service / triage_service.
_ACTION_DUPLICATE_SUPPRESSED = "approval.duplicate_suppressed"
_ACTION_ALERT_CORRELATED = "triage.alert_correlated"


@runtime_checkable
class KpiService(Protocol):
    """Per-answer telemetry recording and operational KPI computation (design §10)."""

    def record_answer_telemetry(self, telemetry: AnswerTelemetry) -> None:
        """Record one per-answer AI-quality telemetry record (Req 10.2, Property 37).

        The telemetry mirrors the answer (Groundedness_Score, dual-grounding
        yes/no, refusal yes/no, citation count). A structured log line and an
        append-only audit record are emitted so the recording is itself observable
        and traceable (Req 10.1).
        """
        ...

    def compute_kpis(
        self,
        cases: Optional[Iterable[Case | str]] = None,
        *,
        extra_duplicate_suppressions: int = 0,
        intended_targets: Optional[Sequence[KpiValue]] = None,
    ) -> KpiSnapshot:
        """Aggregate recorded telemetry + audit records into a :class:`KpiSnapshot`.

        Computes the five operational KPIs over the recorded telemetry and the
        audit records of the supplied ``cases`` (Req 10.3, Property 38). Each
        computed value is labelled ``demonstrated``; any ``intended_targets`` the
        caller supplies are carried through the disjoint ``intended`` channel and
        never mixed into the demonstrated values (Req 10.4, Property 39).
        """
        ...


class KpiServiceImpl:
    """Default :class:`KpiService` backed by injected ports and services.

    Telemetry persistence goes through the :class:`TelemetryRepository` port;
    investigation time and audit-derived counts are reconstructed from the
    :class:`AuditService`. When a collaborator is not supplied an in-memory /
    default implementation is used, convenient for local runs and unit tests.
    """

    def __init__(
        self,
        *,
        telemetry_repository: Optional[TelemetryRepository] = None,
        audit_service: Optional[AuditService] = None,
    ) -> None:
        self._telemetry: TelemetryRepository = (
            telemetry_repository
            if telemetry_repository is not None
            else InMemoryTelemetryRepository()
        )
        self._audit: AuditService = (
            audit_service if audit_service is not None else AuditServiceImpl()
        )

    def record_answer_telemetry(self, telemetry: AnswerTelemetry) -> None:
        """See :meth:`KpiService.record_answer_telemetry`."""
        self._telemetry.record(telemetry)
        # Structured log for the system-health / AI-quality view (Req 10.1).
        logger.info(
            "answer_telemetry_recorded",
            extra={
                "case_id": telemetry.case_id,
                "answer_id": telemetry.answer_id,
                "groundedness_score": telemetry.groundedness_score,
                "dual_grounded": telemetry.dual_grounded,
                "refusal": telemetry.refusal,
                "citation_count": telemetry.citation_count,
            },
        )
        # Append-only audit record so the telemetry capture is itself traceable.
        self._audit.append(
            build_audit_record(
                action=ACTION_TELEMETRY_RECORDED,
                actor_id=SYSTEM_ACTOR,
                case_ref=telemetry.case_id,
                input_refs=[telemetry.answer_id] if telemetry.answer_id else [],
                output_refs=[
                    f"groundedness={telemetry.groundedness_score}",
                    f"dual_grounded={telemetry.dual_grounded}",
                    f"refusal={telemetry.refusal}",
                    f"citations={telemetry.citation_count}",
                ],
            )
        )

    def record_answer(
        self,
        answer: Answer,
        *,
        answer_id: Optional[str] = None,
        refusal_correct: Optional[bool] = None,
    ) -> AnswerTelemetry:
        """Convenience: build telemetry mirroring ``answer`` and record it.

        Mirrors the answer exactly via :meth:`AnswerTelemetry.from_answer`
        (Property 37) and records it through :meth:`record_answer_telemetry`,
        returning the recorded telemetry for the caller's convenience.
        """
        telemetry = AnswerTelemetry.from_answer(
            answer,
            recorded_at=_now_utc(),
            answer_id=answer_id,
            refusal_correct=refusal_correct,
        )
        self.record_answer_telemetry(telemetry)
        return telemetry

    def compute_kpis(
        self,
        cases: Optional[Iterable[Case | str]] = None,
        *,
        extra_duplicate_suppressions: int = 0,
        intended_targets: Optional[Sequence[KpiValue]] = None,
    ) -> KpiSnapshot:
        """See :meth:`KpiService.compute_kpis`."""
        records = self._telemetry.list_all()
        case_ids = _normalise_case_ids(cases)

        demonstrated = [
            self._investigation_time_per_case(case_ids),
            self._percent_fully_grounded(records),
            self._refusal_correctness_count(records),
            self._duplicate_suppression_count(case_ids, extra_duplicate_suppressions),
            self._alerts_to_cases_correlation_count(case_ids),
        ]

        # Intended targets are supplied explicitly and carried through unchanged on
        # the disjoint ``intended`` channel; they are never derived from measured
        # values (Req 10.4, Property 39). Force their provenance so a caller cannot
        # accidentally mark a target as demonstrated.
        intended = [
            value.model_copy(update={"provenance": KpiProvenance.INTENDED})
            for value in (intended_targets or [])
        ]

        return KpiSnapshot(
            computed_at=_now_utc(),
            sample_size=len(records),
            demonstrated=demonstrated,
            intended=intended,
        )

    # ------------------------------------------------------------------
    # KPI definitions (pure functions of recorded telemetry / audit records).
    # Each returns a demonstrated KpiValue. Property 38 asserts each equals its
    # defined formula over the recorded set.
    # ------------------------------------------------------------------

    def _investigation_time_per_case(self, case_ids: list[str]) -> KpiValue:
        """Mean per-case investigation time in seconds, from audit timestamps.

        For each supplied case, the per-case investigation time is the elapsed
        wall-clock between its earliest and latest *workflow* audit record (first
        lifecycle step → latest step). The KPI service's own telemetry-recording
        records are excluded so the metric reflects investigation activity and is
        not polluted by when KPIs happened to be captured. The KPI is the mean of
        those per-case durations over cases that have at least one qualifying
        record; it is ``0.0`` when there are no such cases (an empty-sample zero
        distinguishable via ``sample_size`` / detail).
        """
        durations: list[float] = []
        for case_id in case_ids:
            lineage = self._audit.replay(case_id)
            timestamps = [
                record.timestamp_utc
                for record in lineage.records
                if record.action != ACTION_TELEMETRY_RECORDED
            ]
            if not timestamps:
                continue
            durations.append((max(timestamps) - min(timestamps)).total_seconds())
        mean_seconds = (sum(durations) / len(durations)) if durations else 0.0
        return KpiValue(
            name=KPI_INVESTIGATION_TIME_PER_CASE,
            value=mean_seconds,
            unit="seconds",
            provenance=KpiProvenance.DEMONSTRATED,
            detail=(
                f"Mean elapsed first→last audit record over {len(durations)} "
                "case(s) with recorded activity."
            ),
        )

    @staticmethod
    def _percent_fully_grounded(records: Sequence[AnswerTelemetry]) -> KpiValue:
        """Percentage of recorded answers that are fully grounded.

        An answer is fully grounded iff it was dual-grounded and not refused. The
        KPI is ``100 * fully_grounded / total`` over recorded telemetry, or ``0.0``
        when no answers were recorded.
        """
        total = len(records)
        fully_grounded = sum(
            1 for r in records if r.dual_grounded and not r.refusal
        )
        percent = (100.0 * fully_grounded / total) if total else 0.0
        return KpiValue(
            name=KPI_PERCENT_FULLY_GROUNDED,
            value=percent,
            unit="percent",
            provenance=KpiProvenance.DEMONSTRATED,
            detail=f"{fully_grounded} of {total} recorded answer(s) dual-grounded and not refused.",
        )

    @staticmethod
    def _refusal_correctness_count(records: Sequence[AnswerTelemetry]) -> KpiValue:
        """Count of refusals that were the correct outcome.

        Counts recorded telemetry where the answer was refused and the refusal was
        evaluated as correct (``refusal_correct is True``).
        """
        count = sum(1 for r in records if r.refusal and r.refusal_correct is True)
        return KpiValue(
            name=KPI_REFUSAL_CORRECTNESS_COUNT,
            value=float(count),
            unit="count",
            provenance=KpiProvenance.DEMONSTRATED,
            detail="Recorded refusals evaluated as correct.",
        )

    def _duplicate_suppression_count(
        self, case_ids: list[str], extra_duplicate_suppressions: int
    ) -> KpiValue:
        """Count of suppressed duplicate executions, from audit records.

        Sums the ``approval.duplicate_suppressed`` audit records across the
        supplied cases (idempotent action suppression) and adds any
        ``extra_duplicate_suppressions`` the caller measured elsewhere (e.g.
        ingestion dedup from ``RAW.TRANSACTION_SUPPRESSION_LOG``).
        """
        count = extra_duplicate_suppressions + self._count_audit_action(
            case_ids, _ACTION_DUPLICATE_SUPPRESSED
        )
        return KpiValue(
            name=KPI_DUPLICATE_SUPPRESSION_COUNT,
            value=float(count),
            unit="count",
            provenance=KpiProvenance.DEMONSTRATED,
            detail="Suppressed duplicate executions (idempotency) plus supplied ingestion dedup.",
        )

    def _alerts_to_cases_correlation_count(self, case_ids: list[str]) -> KpiValue:
        """Count of alerts correlated into existing cases, from audit records.

        Counts ``triage.alert_correlated`` audit records across the supplied cases
        — each is one alert collapsed into an existing case within the correlation
        window.
        """
        count = self._count_audit_action(case_ids, _ACTION_ALERT_CORRELATED)
        return KpiValue(
            name=KPI_ALERTS_TO_CASES_CORRELATION_COUNT,
            value=float(count),
            unit="count",
            provenance=KpiProvenance.DEMONSTRATED,
            detail="Alerts correlated into an existing case within the window.",
        )

    def _count_audit_action(self, case_ids: list[str], action: str) -> int:
        """Count audit records whose action equals ``action`` across ``case_ids``."""
        total = 0
        for case_id in case_ids:
            lineage = self._audit.replay(case_id)
            total += sum(1 for record in lineage.records if record.action == action)
        return total


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _normalise_case_ids(cases: Optional[Iterable[Case | str]]) -> list[str]:
    """Return a de-duplicated, order-preserving list of case ids from ``cases``.

    Accepts :class:`Case` instances or raw case-id strings so callers can pass
    either. De-duplication keeps the aggregation stable when the same case is
    supplied more than once.
    """
    if cases is None:
        return []
    seen: dict[str, None] = {}
    for item in cases:
        case_id = item.case_id if isinstance(item, Case) else str(item)
        seen.setdefault(case_id, None)
    return list(seen.keys())


def _now_utc() -> datetime:
    """Current time as a timezone-aware UTC timestamp (Req 10.1)."""
    return datetime.now(timezone.utc)
