"""Governed Metric Service (interface + ``Impl``).

This service is the **only** path to regulatory/risk figures in the system
(design §2; Req 3). It reads governed figures from the semantic view, stamps each
with the ``Metric_Definition_Version`` it was computed under and full query
lineage, aggregates an entity's governed risk, and resolves business terms to
canonical metrics via the registry's glossary synonyms. Three guarantees hold:

* **Figures originate only in the governed zone.** Values are read from the
  semantic view (``SEM.ENTITY_RISK_METRIC_VALUES``) through the repository port and
  returned verbatim; there is no LLM write/compute path and no arithmetic on the
  figure here (Req 3.2, Property 6).
* **Determinism / role-invariance.** For a fixed ``(entity_id, metric_name,
  data_state)`` the returned value, ``metric_definition_version``, and
  ``QueryLineage`` are identical for any caller or role — the service derives
  nothing from wall-clock time, randomness, or the identity of the caller
  (Req 3.3, Property 7).
* **Versioning.** The version travels with the value exactly as the governed view
  stamps it, so a definition change (a new current version in the registry)
  flows onto all subsequent values and answers (Req 3.4, Property 8).

``resolve_term`` matches a business term against each current definition's
glossary synonyms and returns a :class:`CanonicalMetricRef` when exactly one
metric matches, or an :class:`Ambiguous` marker (listing candidates) when a term
maps to more than one metric or to none — never a guess (Req 3.5, Req 5.1,
Property 9).

Snowflake access is abstracted behind the :class:`MetricRepository` port so the
service is unit-testable with :class:`InMemoryMetricRepository`, consistent with
how the audit service was built (``app/services/audit_service.py``, design §2).
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Optional, Protocol, runtime_checkable

from app.models.common import QueryLineage
from app.models.governed import (
    Ambiguous,
    CanonicalMetricRef,
    EntityRiskView,
    GovernedMetricValue,
)
from app.services.metric_repository import (
    InMemoryMetricRepository,
    MetricDefinitionRow,
    MetricRepository,
    MetricValueRow,
)

# Canonical metric used to anchor an entity's governed composite risk score. All
# three MVP metrics share a version and are read together; the aggregate is a
# deterministic, governed-only derivation documented in ``_aggregate_score``.
STRUCTURING_SCORE_METRIC = "structuring_score"


class MetricNotFoundError(LookupError):
    """Raised when a requested ``(entity_id, metric_name)`` has no governed figure.

    The governed view is the sole source of figures (Req 3.2); when it has no row
    for the key, the service does not fabricate a value — it signals absence so the
    caller can degrade/clarify rather than present an ungrounded number.
    """

    def __init__(self, entity_id: str, metric_name: str) -> None:
        self.entity_id = entity_id
        self.metric_name = metric_name
        super().__init__(
            f"No governed value for metric '{metric_name}' and entity '{entity_id}' "
            "in the semantic view."
        )


@runtime_checkable
class MetricService(Protocol):
    """The only interface to governed regulatory/risk figures (design §2; Req 3)."""

    def get_metric(
        self, entity_id: str, metric_name: str, data_state: str
    ) -> GovernedMetricValue:
        """Return the governed figure for ``(entity_id, metric_name, data_state)``.

        The value and version come straight from the semantic view; the result is
        stamped with ``data_state`` as its data-state hash and with query lineage.
        Deterministic and role-invariant for a fixed key (Req 3.2, 3.3, 3.4,
        Property 6, Property 7). Raises :class:`MetricNotFoundError` when the
        governed view has no such figure.
        """
        ...

    def aggregate_entity_risk(self, entity_id: str) -> EntityRiskView:
        """Aggregate ``entity_id``'s governed risk from governed metric values only.

        The returned :class:`EntityRiskView` is populated solely from
        :class:`GovernedMetricValue` instances read from the semantic view
        (Req 4.1, Property 10).
        """
        ...

    def resolve_term(self, term: str) -> CanonicalMetricRef | Ambiguous:
        """Resolve a business ``term`` to a canonical metric, or report ambiguity.

        Matches ``term`` against each current definition's glossary synonyms.
        Returns a :class:`CanonicalMetricRef` on a unique match, or an
        :class:`Ambiguous` marker (candidate list) when ``term`` maps to more than
        one metric or to none — never a guess (Req 3.5, Req 5.1, Property 9).
        """
        ...


class MetricServiceImpl:
    """Default :class:`MetricService` backed by a :class:`MetricRepository`.

    All figure reads go through the injected repository port, so the same logic
    runs against the Snowflake governed views in production and an in-memory store
    in tests (design §2). When no repository is supplied an
    :class:`InMemoryMetricRepository` is used for local runs/tests.
    """

    def __init__(self, repository: Optional[MetricRepository] = None) -> None:
        self._repository: MetricRepository = (
            repository if repository is not None else InMemoryMetricRepository()
        )

    def get_metric(
        self, entity_id: str, metric_name: str, data_state: str
    ) -> GovernedMetricValue:
        """See :meth:`MetricService.get_metric`."""
        row = self._repository.get_metric_value(entity_id, metric_name)
        if row is None:
            raise MetricNotFoundError(entity_id, metric_name)
        return self._to_governed_value(row, data_state)

    def aggregate_entity_risk(self, entity_id: str) -> EntityRiskView:
        """See :meth:`MetricService.aggregate_entity_risk`.

        Reads all governed figures for the entity from the semantic view and wraps
        each as a :class:`GovernedMetricValue`. The composite ``aggregate_score``
        is a deterministic, governed-only derivation (see ``_aggregate_score``);
        no value here originates from narrative (Property 10). The data-state hash
        stamped on each value is derived deterministically from the governed rows
        themselves, so the view is reproducible for a fixed data snapshot (Req 3.3).
        """
        rows = self._repository.list_entity_metrics(entity_id)
        data_state = _derive_data_state(entity_id, rows)
        metrics = [self._to_governed_value(row, data_state) for row in rows]
        return EntityRiskView(
            entity_id=entity_id,
            metrics=metrics,
            aggregate_score=_aggregate_score(metrics),
        )

    def resolve_term(self, term: str) -> CanonicalMetricRef | Ambiguous:
        """See :meth:`MetricService.resolve_term`."""
        normalized = term.strip().lower()
        definitions = self._repository.list_current_definitions()

        # A metric matches if the normalized term equals the metric's canonical
        # name or any of its (lower-cased) glossary synonyms. Candidates are
        # collected deterministically (definitions are name-ordered by the port).
        candidates: list[str] = []
        matched: dict[str, MetricDefinitionRow] = {}
        for definition in definitions:
            synonyms = {s.strip().lower() for s in definition.glossary_synonyms}
            if normalized == definition.metric_name.strip().lower() or normalized in synonyms:
                if definition.metric_name not in matched:
                    candidates.append(definition.metric_name)
                    matched[definition.metric_name] = definition

        if len(candidates) == 1:
            definition = matched[candidates[0]]
            return CanonicalMetricRef(
                term=term,
                metric_name=definition.metric_name,
                metric_definition_version=definition.metric_definition_version,
                display_name=definition.display_name,
            )

        # Zero matches (unknown term) or more than one (true ambiguity): return the
        # Ambiguous marker so the caller clarifies rather than guesses (Req 5.2).
        return Ambiguous(term=term, candidates=candidates)

    @staticmethod
    def _to_governed_value(row: MetricValueRow, data_state: str) -> GovernedMetricValue:
        """Stamp a semantic-view row as a :class:`GovernedMetricValue` (Req 3.2, 3.4).

        The value and version are carried through verbatim from the governed view;
        the lineage records the governed read (semantic view + the single source
        row key + the data-state hash) so the figure is traceable and replayable
        (Property 6). The lineage query string is a deterministic function of the
        key alone, so it is identical for every caller (Property 7).
        """
        lineage = QueryLineage(
            generated_query=_lineage_query(row.entity_id, row.metric_name, row.semantic_view),
            source_row_refs=[f"{row.entity_id}:{row.metric_name}"],
            semantic_view=row.semantic_view,
            data_state_hash=data_state,
        )
        return GovernedMetricValue(
            metric_name=row.metric_name,
            entity_id=row.entity_id,
            value=row.value,
            metric_definition_version=row.metric_definition_version,
            data_state_hash=data_state,
            query_lineage=lineage,
        )


def _lineage_query(entity_id: str, metric_name: str, semantic_view: str) -> str:
    """Deterministic, allow-list-shaped read query recorded as lineage (Req 3.2).

    This is the governed read the figure traces back to. It is a pure function of
    the key, so the recorded lineage is identical for any caller/role (Property 7).
    It is a SELECT over the governed semantic view only — consistent with the
    generated-query allow-list (design "Trust boundaries").
    """
    return (
        f"SELECT metric_value, metric_definition_version FROM {semantic_view} "
        f"WHERE entity_id = '{entity_id}' AND metric_name = '{metric_name}'"
    )


def _derive_data_state(entity_id: str, rows: list[MetricValueRow]) -> str:
    """Derive a deterministic data-state hash from the governed rows (Req 3.3).

    The aggregate view does not receive an explicit ``data_state`` argument, so we
    derive one from the governed figures themselves — the (metric, value, version)
    tuples that make up the entity's risk picture. Being a pure function of the
    governed data, it yields the same hash for the same snapshot and any caller
    (Property 7), tying each stamped value to the exact data it summarises.
    """
    parts = [entity_id]
    for row in sorted(rows, key=lambda r: r.metric_name):
        parts.append(f"{row.metric_name}={row.value}@{row.metric_definition_version}")
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _aggregate_score(metrics: list[GovernedMetricValue]) -> Decimal:
    """Governed composite risk score, derived only from governed values (Req 4.2).

    The composite is the structuring score when present (the normalised 0-1
    structuring/smurfing indicator is the governed risk signal used to rank open
    cases), otherwise ``Decimal('0')``. This is a deterministic selection over
    governed figures — no narrative input and no wall-clock/random dependence —
    so the ranking is stable and role-invariant (Property 11). Returning the exact
    governed :class:`~decimal.Decimal` avoids float drift.
    """
    for metric in metrics:
        if metric.metric_name == STRUCTURING_SCORE_METRIC:
            return metric.value
    return Decimal("0")
