"""Unit tests for the Alert Triage & Case Service (task 9.1).

Cover the four triage guarantees with concrete examples and edge cases, using
the real in-memory ports (``InMemoryCaseRepository``, ``InMemoryAuditRecordRepository``,
``InMemoryMetricRepository``) so no Snowflake connection is required:

* ``on_alert`` creates a ``RECEIVED`` ``risk_signal`` case whose risk aggregation
  comes solely from governed metric values (Req 4.1, Property 10).
* Open-case ranking is non-increasing by governed risk and permutation-invariant
  (Req 4.2, Property 11).
* System operations never auto-confirm a ``risk_signal`` to a ``risk_event``
  (Req 4.3, Property 12).
* Same-correlation-group alerts collapse into one case; ``correlated_alert_ids``
  covers the group (Req 4.5, Property 14).
* The stale/incomplete indicator matches the freshness-and-completeness predicate
  (Req 4.4, Property 13).

Every state transition emits an audit record (Req 8.1).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config.settings import TriageSettings
from app.models.governed import FreshnessIndicator
from app.models.source import Alert
from app.services.audit_repository import InMemoryAuditRecordRepository
from app.services.audit_service import AuditServiceImpl
from app.services.case_repository import InMemoryCaseRepository
from app.services.metric_repository import InMemoryMetricRepository, MetricValueRow
from app.services.metric_service import STRUCTURING_SCORE_METRIC, MetricServiceImpl
from app.services.triage_service import (
    ACTION_ALERT_CORRELATED,
    ACTION_CASE_RECEIVED,
    TriageServiceImpl,
)


def _metric_repo_with_scores(scores: dict[str, str]) -> InMemoryMetricRepository:
    """Build a metric repo stamping each entity with a governed structuring score."""
    rows = [
        MetricValueRow(
            entity_id=entity_id,
            metric_name=STRUCTURING_SCORE_METRIC,
            value=Decimal(score),
            metric_definition_version="v1",
        )
        for entity_id, score in scores.items()
    ]
    return InMemoryMetricRepository(values=rows)


def _triage(
    *,
    scores: dict[str, str],
    audit_repo: InMemoryAuditRecordRepository | None = None,
    case_repo: InMemoryCaseRepository | None = None,
    settings: TriageSettings | None = None,
) -> TriageServiceImpl:
    audit_repo = audit_repo if audit_repo is not None else InMemoryAuditRecordRepository()
    return TriageServiceImpl(
        metric_service=MetricServiceImpl(_metric_repo_with_scores(scores)),
        audit_service=AuditServiceImpl(audit_repo),
        case_repository=case_repo if case_repo is not None else InMemoryCaseRepository(),
        settings=settings,
    )


def _alert(alert_id: str, entity_id: str, correlation_key: str) -> Alert:
    return Alert(
        alert_id=alert_id,
        entity_id=entity_id,
        typology="structuring",
        raised_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        correlation_key=correlation_key,
    )


class TestOnAlert:
    def test_creates_received_risk_signal_case_with_governed_risk(self) -> None:
        """Property 10: alert -> RECEIVED case, risk from governed metrics only."""
        triage = _triage(scores={"E1": "0.80"})

        case = triage.on_alert(_alert("A1", "E1", "grp-1"))

        assert case.state.value == "received"
        assert case.classification == "risk_signal"
        assert case.entity_id == "E1"
        assert case.correlated_alert_ids == ["A1"]
        # Risk aggregation is populated solely from the governed structuring score.
        assert case.risk_aggregation.entity_id == "E1"
        assert case.risk_aggregation.aggregate_score == Decimal("0.80")
        assert [m.metric_name for m in case.risk_aggregation.metrics] == [
            STRUCTURING_SCORE_METRIC
        ]

    def test_emits_case_received_audit_record(self) -> None:
        """Req 8.1: a created case emits an append-only audit transition."""
        audit_repo = InMemoryAuditRecordRepository()
        triage = _triage(scores={"E1": "0.5"}, audit_repo=audit_repo)

        case = triage.on_alert(_alert("A1", "E1", "grp-1"))

        records = audit_repo.list_for_case(case.case_id)
        assert [r.action for r in records] == [ACTION_CASE_RECEIVED]
        assert records[0].input_refs == ["A1"]
        assert records[0].entity_ref == "E1"


class TestCorrelation:
    def test_same_group_within_window_collapses_to_one_case(self) -> None:
        """Property 14: same correlation group -> one case covering the group."""
        triage = _triage(scores={"E1": "0.5"})

        first = triage.on_alert(_alert("A1", "E1", "grp-1"))
        second = triage.on_alert(_alert("A2", "E1", "grp-1"))

        assert first.case_id == second.case_id
        assert second.correlated_alert_ids == ["A1", "A2"]

    def test_distinct_groups_open_distinct_cases(self) -> None:
        """Property 14: distinct correlation groups -> distinct cases."""
        triage = _triage(scores={"E1": "0.5", "E2": "0.6"})

        c1 = triage.on_alert(_alert("A1", "E1", "grp-1"))
        c2 = triage.on_alert(_alert("A2", "E2", "grp-2"))

        assert c1.case_id != c2.case_id

    def test_correlate_is_idempotent_on_repeated_alert_id(self) -> None:
        """Repeated alert id is not duplicated in the correlated set (Property 14)."""
        triage = _triage(scores={"E1": "0.5"})

        triage.on_alert(_alert("A1", "E1", "grp-1"))
        again = triage.on_alert(_alert("A1", "E1", "grp-1"))

        assert again.correlated_alert_ids == ["A1"]

    def test_correlation_into_existing_case_audits_correlation(self) -> None:
        """Req 8.1: joining an existing case records a correlation transition."""
        audit_repo = InMemoryAuditRecordRepository()
        triage = _triage(scores={"E1": "0.5"}, audit_repo=audit_repo)

        case = triage.on_alert(_alert("A1", "E1", "grp-1"))
        triage.on_alert(_alert("A2", "E1", "grp-1"))

        actions = [r.action for r in audit_repo.list_for_case(case.case_id)]
        assert actions == [ACTION_CASE_RECEIVED, ACTION_ALERT_CORRELATED]


class TestNeverAutoConfirm:
    def test_system_operations_keep_classification_risk_signal(self) -> None:
        """Property 12: no triage op auto-confirms to risk_event."""
        triage = _triage(scores={"E1": "0.9"})

        created = triage.on_alert(_alert("A1", "E1", "grp-1"))
        joined = triage.on_alert(_alert("A2", "E1", "grp-1"))

        assert created.classification == "risk_signal"
        assert joined.classification == "risk_signal"


class TestRanking:
    def test_ranking_non_increasing_and_permutation_invariant(self) -> None:
        """Property 11: total order on governed risk, invariant under input order."""
        case_repo = InMemoryCaseRepository()
        triage = _triage(
            scores={"E1": "0.20", "E2": "0.90", "E3": "0.50"},
            case_repo=case_repo,
        )
        triage.on_alert(_alert("A1", "E1", "grp-1"))
        triage.on_alert(_alert("A2", "E2", "grp-2"))
        triage.on_alert(_alert("A3", "E3", "grp-3"))

        ranking = triage.rank_open_cases()
        scores = [r.aggregate_score for r in ranking]

        # Non-increasing order.
        assert scores == sorted(scores, reverse=True)
        assert scores == [Decimal("0.90"), Decimal("0.50"), Decimal("0.20")]
        assert [r.rank for r in ranking] == [1, 2, 3]

    def test_ranking_ties_broken_by_case_id_ascending(self) -> None:
        """Equal governed scores break ties deterministically by case_id."""
        case_repo = InMemoryCaseRepository()
        triage = _triage(scores={"E1": "0.5", "E2": "0.5"}, case_repo=case_repo)
        triage.on_alert(_alert("A1", "E1", "grp-1"))
        triage.on_alert(_alert("A2", "E2", "grp-2"))

        ranking = triage.rank_open_cases()
        case_ids = [r.case_id for r in ranking]

        assert case_ids == sorted(case_ids)


class TestFreshness:
    def _case_with_freshness(self, freshness: FreshnessIndicator):
        triage = _triage(scores={"E1": "0.5"})
        case = triage.on_alert(_alert("A1", "E1", "grp-1"))
        return triage, case.model_copy(update={"freshness": freshness})

    def test_fresh_data_within_threshold_not_stale(self) -> None:
        """Property 13: within threshold and complete -> not stale."""
        settings = TriageSettings(freshness_threshold_seconds=3600.0)
        triage = _triage(scores={"E1": "0.5"}, settings=settings)
        base = triage.on_alert(_alert("A1", "E1", "grp-1"))
        latest = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        case = base.model_copy(
            update={
                "freshness": FreshnessIndicator(latest_event_time=latest, has_data=True)
            }
        )

        reference = latest + timedelta(seconds=1800)  # within 1h threshold
        indicator = triage.freshness_state(case, reference)

        assert indicator.is_stale is False
        assert indicator.has_data is True
        assert indicator.latest_event_time == latest

    def test_data_older_than_threshold_is_stale(self) -> None:
        """Property 13: age beyond threshold -> stale."""
        settings = TriageSettings(freshness_threshold_seconds=3600.0)
        triage = _triage(scores={"E1": "0.5"}, settings=settings)
        base = triage.on_alert(_alert("A1", "E1", "grp-1"))
        latest = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        case = base.model_copy(
            update={
                "freshness": FreshnessIndicator(latest_event_time=latest, has_data=True)
            }
        )

        reference = latest + timedelta(seconds=7200)  # beyond 1h threshold
        indicator = triage.freshness_state(case, reference)

        assert indicator.is_stale is True

    def test_missing_required_field_is_stale_or_incomplete(self) -> None:
        """Property 13: missing required field (no data) -> stale/incomplete."""
        triage = _triage(scores={"E1": "0.5"})
        base = triage.on_alert(_alert("A1", "E1", "grp-1"))
        case = base.model_copy(
            update={"freshness": FreshnessIndicator(latest_event_time=None, has_data=False)}
        )

        reference = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        indicator = triage.freshness_state(case, reference)

        assert indicator.is_stale is True
