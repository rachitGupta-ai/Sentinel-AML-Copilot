"""Shared value objects used across the SentinelAML domain models.

These records are the small, reusable building blocks that larger aggregates
(``Answer``, ``SARDraft``, ``GovernedMetricValue``, ``Case``) compose. They
capture the lineage, evidence, and claim structures that make an answer
*dual-grounded* and auditable (design "Data Models"; Req 3, Req 5, Req 8).

Design invariants encoded here:

* ``QueryLineage`` records the generated query plus the source-row references so
  any governed figure can be traced back to the rows it was computed from
  (Req 3.2, Req 8.4).
* ``EvidenceItem`` is the unit of citation. Numeric evidence (a governed metric
  or a transaction event) carries ``lineage``; textual evidence (a policy
  passage) carries an ``excerpt`` (Req 5.4, Req 6.2).
* ``NumericClaim`` always cites a governed metric + lineage and ``TextualClaim``
  always cites a policy/evidence passage — this is the dual-grounding contract
  (Req 5.4, Property 17).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class QueryLineage(BaseModel):
    """Traceable provenance for a governed figure (Req 3.2, Req 8.4).

    Captures the exact query that produced a value together with references to
    the source rows and the data snapshot it was computed against, so that any
    numeric claim can be replayed and verified from audit records alone
    (Property 6, Property 31).
    """

    model_config = ConfigDict(frozen=True)

    generated_query: str = Field(
        description="The governed/allow-listed query that produced the value."
    )
    source_row_refs: list[str] = Field(
        default_factory=list,
        description="References (ids/keys) of the source rows feeding the query.",
    )
    semantic_view: str | None = Field(
        default=None,
        description="Semantic view the figure was read from (e.g. SEM.ENTITY_RISK_METRICS).",
    )
    data_state_hash: str | None = Field(
        default=None,
        description="Hash identifying the data snapshot the query ran against.",
    )


class EvidenceItem(BaseModel):
    """A single citable piece of evidence (Req 5.4, Req 6.2).

    ``kind`` selects the evidence type. Textual evidence (``policy_passage``)
    populates ``excerpt``; numeric evidence (``transaction_event`` /
    ``governed_metric``) populates ``lineage``. The grounding layer assembles
    these so every claim in an actionable answer resolves to its exact evidence
    (Property 17).
    """

    model_config = ConfigDict(frozen=True)

    kind: str = Field(
        description="Evidence type: 'policy_passage', 'transaction_event', or 'governed_metric'.",
    )
    ref: str = Field(
        description="Reference: policy-passage id, event_id, or governed-metric ref.",
    )
    excerpt: str | None = Field(
        default=None,
        description="Verbatim excerpt for textual (policy_passage) evidence.",
    )
    lineage: QueryLineage | None = Field(
        default=None,
        description="Query lineage for numeric (metric/event) evidence.",
    )


class NumericClaim(BaseModel):
    """A numeric assertion in an answer, grounded in a governed metric.

    Every numeric claim cites a ``GovernedMetricValue`` (by metric name +
    version) and the ``QueryLineage`` that produced it. No numeric value may
    originate from Cortex-generated narrative (Req 3.2, Req 5.3, Property 6).
    """

    model_config = ConfigDict(frozen=True)

    text: str = Field(description="Human-readable claim as rendered in the narrative.")
    metric_name: str = Field(description="Canonical governed metric cited by this claim.")
    value: Decimal = Field(description="The governed value asserted (never LLM-produced).")
    metric_definition_version: str = Field(
        description="Version of the metric definition the value was computed under (Req 3.4)."
    )
    lineage: QueryLineage = Field(
        description="Lineage tracing the value to its source rows (Req 3.2)."
    )


class TextualClaim(BaseModel):
    """A textual/regulatory assertion grounded in a cited passage.

    Every textual claim cites a policy/evidence passage so it can be resolved
    back to that exact source (Req 5.4, Req 6.2, Property 17).
    """

    model_config = ConfigDict(frozen=True)

    text: str = Field(description="Human-readable assertion as rendered in the narrative.")
    evidence: EvidenceItem = Field(
        description="The cited policy/evidence passage supporting this assertion."
    )


class SemanticInterpretation(BaseModel):
    """How an NL question was interpreted before answering (Req 5.1).

    The investigation service presents this interpretation — the canonical
    metrics and entities a question was mapped to — before producing an answer,
    so the analyst can see what was understood. ``ambiguous`` with multiple
    candidates drives a clarification rather than a guess (Req 5.2, Property 15).
    """

    model_config = ConfigDict(frozen=True)

    question: str = Field(description="The original natural-language question.")
    resolved_metrics: list[str] = Field(
        default_factory=list,
        description="Canonical governed metrics the question maps to.",
    )
    resolved_entities: list[str] = Field(
        default_factory=list,
        description="Entity ids the question refers to.",
    )
    ambiguous: bool = Field(
        default=False,
        description="True when term resolution yields two or more candidates (Req 5.2).",
    )
    candidate_terms: list[str] = Field(
        default_factory=list,
        description="Candidate interpretations when ambiguous.",
    )
