"""Persistence port for the governed semantic metrics layer.

The :class:`MetricService` reads every regulatory/risk figure through a
``MetricRepository`` *port* rather than touching Snowflake directly. This mirrors
how the audit trail is built (``app/services/audit_repository.py``): the service's
determinism/stamping logic stays independent of the physical store and is
unit-testable with an in-memory fake, while the Snowflake adapter reads the
governed semantic views (design §2; Req 3).

Crucially, the port exposes a **read-only** surface over the governed semantic
views and the metric-definition registry — there is deliberately no write/compute
method. Figures originate only inside the governed zone and the LLM has no path to
produce or alter them here (Req 3.2, Property 6).

Two row shapes cross the port:

* :class:`MetricValueRow` — one governed figure as read from
  ``SEM.ENTITY_RISK_METRIC_VALUES`` (the LONG read surface): the ``(entity_id,
  metric_name)`` key, the ``value``, the ``metric_definition_version`` it was
  computed under, and the human-readable ``display_name``/``definition`` (Req 3.1,
  3.4). The ``semantic_view`` the row came from travels with it so the service can
  stamp query lineage (Req 3.2).
* :class:`MetricDefinitionRow` — one *current* registry definition from
  ``SEM.METRIC_DEFINITION_REGISTRY``: the canonical ``metric_name``, its
  ``glossary_synonyms``, and current ``metric_definition_version``. These back
  ``resolve_term`` (Req 3.5).

Two adapters are provided:

* :class:`InMemoryMetricRepository` — a deterministic, in-process fake for tests
  and local runs.
* :class:`SnowflakeMetricRepository` — reads through the Snowpark session using
  SELECT only over the governed views (never writes a figure, Req 3.2).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Optional, Protocol, Sequence, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids importing Snowpark eagerly
    from snowflake.snowpark import Session


# Fully-qualified governed read surfaces (design "Data Models"; task 4.1 DDL).
# get_metric() / aggregate_entity_risk() read the LONG view; resolve_term() reads
# the registry. These names are the single place the physical surfaces are
# referenced from the service layer.
ENTITY_RISK_METRIC_VALUES_VIEW = "SEM.ENTITY_RISK_METRIC_VALUES"
METRIC_DEFINITION_REGISTRY_TABLE = "SEM.METRIC_DEFINITION_REGISTRY"


@dataclass(frozen=True)
class MetricValueRow:
    """One governed figure as read from the semantic view (Req 3.1, 3.2).

    This is the raw read-surface row the service converts into a
    :class:`~app.models.governed.GovernedMetricValue`, stamping it with lineage.
    ``value`` is carried as :class:`~decimal.Decimal` so the governed figure is
    exact and deterministic (no float drift) across callers (Req 3.3).
    """

    entity_id: str
    metric_name: str
    value: Decimal
    metric_definition_version: str
    display_name: Optional[str] = None
    definition: Optional[str] = None
    unit: Optional[str] = None
    #: The semantic view this row was read from, carried so the service can stamp
    #: :class:`~app.models.common.QueryLineage.semantic_view` (Req 3.2).
    semantic_view: str = ENTITY_RISK_METRIC_VALUES_VIEW


@dataclass(frozen=True)
class MetricDefinitionRow:
    """One current metric definition from the registry, backing ``resolve_term``.

    ``glossary_synonyms`` is the list of lower-cased business terms that resolve to
    this canonical ``metric_name`` (Req 3.5). Only *current* definitions are
    exposed through the port so a term resolves to the active version (Req 3.4).
    """

    metric_name: str
    metric_definition_version: str
    glossary_synonyms: list[str] = field(default_factory=list)
    display_name: Optional[str] = None


@runtime_checkable
class MetricRepository(Protocol):
    """Read-only port over the governed semantic views (Req 3).

    Implementations read figures from the semantic view and definitions from the
    registry. There is deliberately **no** method that writes or computes a figure
    — the governed views are the sole origin of regulatory numbers (Req 3.2).
    """

    def get_metric_value(self, entity_id: str, metric_name: str) -> Optional[MetricValueRow]:
        """Return the governed figure for ``(entity_id, metric_name)`` or ``None``.

        The returned value and version come straight from the semantic view; the
        same key always yields the same value + version for any caller (Req 3.3,
        Property 7).
        """
        ...

    def list_entity_metrics(self, entity_id: str) -> list[MetricValueRow]:
        """Return all governed figures for ``entity_id`` from the semantic view.

        Used by ``aggregate_entity_risk`` to build an ``EntityRiskView`` solely
        from governed values (Req 4.1, Property 10). Ordering is by ``metric_name``
        so aggregation is deterministic.
        """
        ...

    def list_current_definitions(self) -> list[MetricDefinitionRow]:
        """Return every *current* metric definition from the registry (Req 3.4, 3.5).

        Backs ``resolve_term``: the service matches an input term against each
        definition's ``glossary_synonyms`` to find the canonical metric.
        """
        ...


class InMemoryMetricRepository:
    """In-memory, deterministic metric read-surface for tests and local runs.

    Mirrors the Snowflake adapter's read semantics so the service behaves
    identically against either store (Req 3.3). Values are stored as
    :class:`~decimal.Decimal` and reads are copies of frozen rows, so no caller can
    mutate the governed figures.
    """

    def __init__(
        self,
        values: Optional[Sequence[MetricValueRow]] = None,
        definitions: Optional[Sequence[MetricDefinitionRow]] = None,
    ) -> None:
        # Keyed by (entity_id, metric_name) for O(1) point reads.
        self._values: dict[tuple[str, str], MetricValueRow] = {}
        self._definitions: dict[str, MetricDefinitionRow] = {}
        self._lock = threading.Lock()
        for row in values or ():
            self.put_value(row)
        for definition in definitions or ():
            self.put_definition(definition)

    def put_value(self, row: MetricValueRow) -> None:
        """Insert/replace a governed figure (test setup helper)."""
        with self._lock:
            self._values[(row.entity_id, row.metric_name)] = row

    def put_definition(self, row: MetricDefinitionRow) -> None:
        """Insert/replace a current metric definition (test setup helper)."""
        with self._lock:
            self._definitions[row.metric_name] = row

    def get_metric_value(self, entity_id: str, metric_name: str) -> Optional[MetricValueRow]:
        with self._lock:
            return self._values.get((entity_id, metric_name))

    def list_entity_metrics(self, entity_id: str) -> list[MetricValueRow]:
        with self._lock:
            rows = [r for (e, _m), r in self._values.items() if e == entity_id]
        return sorted(rows, key=lambda r: r.metric_name)

    def list_current_definitions(self) -> list[MetricDefinitionRow]:
        with self._lock:
            definitions = list(self._definitions.values())
        return sorted(definitions, key=lambda d: d.metric_name)


class SnowflakeMetricRepository:
    """Snowflake-backed metric read-surface over the governed semantic views.

    Uses the Snowpark session for SELECT only against
    ``SEM.ENTITY_RISK_METRIC_VALUES`` (figures) and
    ``SEM.METRIC_DEFINITION_REGISTRY`` (definitions). It never issues an
    INSERT/UPDATE/DELETE of a figure — the figure origin is the governed view
    alone (Req 3.2). Numeric values are coerced to :class:`~decimal.Decimal` so the
    governed figure stays exact across callers (Req 3.3).
    """

    def __init__(
        self,
        session: "Session",
        *,
        values_view: str = ENTITY_RISK_METRIC_VALUES_VIEW,
        registry_table: str = METRIC_DEFINITION_REGISTRY_TABLE,
    ) -> None:
        self._session = session
        self._values_view = values_view
        self._registry_table = registry_table

    def get_metric_value(self, entity_id: str, metric_name: str) -> Optional[MetricValueRow]:
        rows = self._session.sql(
            f"SELECT entity_id, metric_name, display_name, definition, metric_value, "
            f"unit, metric_definition_version "
            f"FROM {self._values_view} WHERE entity_id = ? AND metric_name = ? LIMIT 1",
            params=[entity_id, metric_name],
        ).collect()
        if not rows:
            return None
        return self._row_to_value(rows[0])

    def list_entity_metrics(self, entity_id: str) -> list[MetricValueRow]:
        rows = self._session.sql(
            f"SELECT entity_id, metric_name, display_name, definition, metric_value, "
            f"unit, metric_definition_version "
            f"FROM {self._values_view} WHERE entity_id = ? ORDER BY metric_name ASC",
            params=[entity_id],
        ).collect()
        return [self._row_to_value(row) for row in rows]

    def list_current_definitions(self) -> list[MetricDefinitionRow]:
        rows = self._session.sql(
            f"SELECT metric_name, display_name, glossary_synonyms, metric_definition_version "
            f"FROM {self._registry_table} WHERE is_current = TRUE ORDER BY metric_name ASC",
        ).collect()
        return [self._row_to_definition(row) for row in rows]

    def _row_to_value(self, row: object) -> MetricValueRow:
        data = self._as_dict(row)
        return MetricValueRow(
            entity_id=str(data["entity_id"]),
            metric_name=str(data["metric_name"]),
            value=_to_decimal(data.get("metric_value")),
            metric_definition_version=str(data["metric_definition_version"]),
            display_name=_opt_str(data.get("display_name")),
            definition=_opt_str(data.get("definition")),
            unit=_opt_str(data.get("unit")),
            semantic_view=self._values_view,
        )

    @staticmethod
    def _row_to_definition(row: object) -> MetricDefinitionRow:
        data = SnowflakeMetricRepository._as_dict(row)
        return MetricDefinitionRow(
            metric_name=str(data["metric_name"]),
            metric_definition_version=str(data["metric_definition_version"]),
            glossary_synonyms=_as_str_list(data.get("glossary_synonyms")),
            display_name=_opt_str(data.get("display_name")),
        )

    @staticmethod
    def _as_dict(row: object) -> dict[str, object]:
        data = row.as_dict() if hasattr(row, "as_dict") else dict(row)  # type: ignore[arg-type]
        return {str(k).lower(): v for k, v in data.items()}


def _to_decimal(value: object) -> Decimal:
    """Coerce a Snowflake numeric to an exact :class:`~decimal.Decimal` (Req 3.3).

    ``None`` becomes ``Decimal('0')`` so a missing figure never surfaces as a NULL
    governed value; floats are routed through ``str`` to avoid binary-float drift.
    """
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    return Decimal(str(value))


def _opt_str(value: object) -> Optional[str]:
    return None if value is None else str(value)


def _as_str_list(value: object) -> list[str]:
    """Coerce a Snowflake ARRAY column to a list of strings (NULL → empty).

    Snowpark surfaces an ARRAY/VARIANT column as a JSON-encoded *string*
    (e.g. '["structuring", "smurfing"]'), not a Python list. Parse that case so
    glossary synonyms resolve correctly (Req 3.5); fall back to prior behaviour
    for genuine lists/tuples or scalar values.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    if isinstance(value, str):
        import json

        parsed = None
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
        return [value]
    return [str(value)]
