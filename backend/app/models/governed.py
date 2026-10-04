"""Governed-metric domain models (the only source of regulatory figures).

A ``GovernedMetricValue`` is the sole legitimate origin of any regulatory/risk
figure in the system. Values are read from the semantic view, stamped with the
``metric_definition_version`` they were computed under, and carry full
``QueryLineage``. The LLM never computes or alters these numbers — it may only
narrate around them (Req 3, Req 5.3, Property 6, Property 7).

Design invariants encoded here:

* ``GovernedMetricValue`` always carries a ``metric_definition_version`` and
  ``query_lineage`` so values are versioned and traceable (Req 3.1, Req 3.4).
* ``EntityRiskView`` aggregates an entity's governed metrics solely from
  ``GovernedMetricValue`` instances (Req 4.1, Property 10).
* ``FreshnessIndicator`` reports the latest ingested event time, with a defined
  "no data" state for an empty dataset (Req 2.6, Property 5).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.common import QueryLineage


class GovernedMetricValue(BaseModel):
    """A versioned, lineage-bearing governed figure (Req 3.1, Req 3.2).

    This is the **only** source of regulatory figures. Every value is stamped
    with the ``metric_definition_version`` it was computed under (so definition
    changes produce a new version on all subsequent values, Req 3.4,
    Property 8), a ``data_state_hash`` identifying the data snapshot, and the
    ``query_lineage`` tracing it to source rows (Req 3.2, Property 6).
    """

    model_config = ConfigDict(frozen=True)

    metric_name: str = Field(description="Canonical metric name, e.g. 'exposure_90d'.")
    entity_id: str = Field(description="Entity the metric was computed for.")
    value: Decimal = Field(description="The deterministic governed value (exact decimal).")
    metric_definition_version: str = Field(
        description="Version of the metric definition this value was computed under (Req 3.1, 3.4).",
    )
    data_state_hash: str = Field(
        description="Hash identifying the data snapshot the value was computed against.",
    )
    query_lineage: QueryLineage = Field(
        description="Generated query + source-row references for this value (Req 3.2).",
    )


class EntityRiskView(BaseModel):
    """Aggregated governed-risk view for an entity (Req 4.1).

    Populated **solely** from ``GovernedMetricValue`` instances — no figure here
    originates from narrative (Property 10). ``aggregate_score`` is the governed
    composite used to rank open cases (Req 4.2, Property 11).
    """

    model_config = ConfigDict(frozen=True)

    entity_id: str = Field(description="Entity this risk view aggregates.")
    metrics: list[GovernedMetricValue] = Field(
        default_factory=list,
        description="Governed metric values making up this entity's risk picture.",
    )
    aggregate_score: Decimal = Field(
        default=Decimal("0"),
        description="Governed composite risk score used for case ranking (Req 4.2).",
    )


class CanonicalMetricRef(BaseModel):
    """A business term resolved to a single canonical governed metric (Req 3.5).

    Returned by ``resolve_term`` when an input term maps to exactly one canonical
    metric via the registry's glossary synonyms. Carries the canonical
    ``metric_name`` and the ``metric_definition_version`` of the current
    definition, plus the originating ``term`` for traceability (Property 9).
    """

    model_config = ConfigDict(frozen=True)

    term: str = Field(description="The input business term that was resolved.")
    metric_name: str = Field(description="Canonical governed metric name the term maps to.")
    metric_definition_version: str = Field(
        description="Current metric-definition version for the resolved metric (Req 3.4).",
    )
    display_name: str | None = Field(
        default=None,
        description="Human-readable display name of the resolved metric (Req 3.1).",
    )


class Ambiguous(BaseModel):
    """Result of resolving a term that maps to more than one canonical metric (Req 3.5).

    When a business term is a glossary synonym of two or more distinct canonical
    metrics, ``resolve_term`` must not guess: it returns this marker listing every
    candidate so the caller (e.g. the investigation service) can ask a clarifying
    question rather than silently choosing (Req 5.2, Property 15). ``candidates``
    is empty when the term matches no metric at all (unresolvable).
    """

    model_config = ConfigDict(frozen=True)

    term: str = Field(description="The input business term that could not be uniquely resolved.")
    candidates: list[str] = Field(
        default_factory=list,
        description="Canonical metric names the term matched (>1 = ambiguous, 0 = unknown).",
    )


class FreshnessIndicator(BaseModel):
    """Data-freshness state for the UI and triage staleness checks (Req 2.6).

    ``latest_event_time`` equals the maximum ingested event time over the data
    set; when no data exists ``has_data`` is ``False`` and the indicator reports
    the defined "no data" state (Property 5). ``is_stale`` is set by triage when
    the data age exceeds the configured freshness threshold (Req 4.4,
    Property 13).
    """

    model_config = ConfigDict(frozen=True)

    latest_event_time: datetime | None = Field(
        default=None,
        description="Max ingested event time; None when there is no data (Req 2.6).",
    )
    has_data: bool = Field(
        default=False,
        description="False reports the defined 'no data' state (Req 2.6).",
    )
    is_stale: bool = Field(
        default=False,
        description="True when data age exceeds the configured freshness threshold (Req 4.4).",
    )
