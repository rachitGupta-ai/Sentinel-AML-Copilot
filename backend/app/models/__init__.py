"""Pydantic domain and document models for the SentinelAML Copilot.

These records are the typed domain vocabulary shared across the service, API,
and persistence layers. The physical store is Snowflake; these models are the
in-process representation. See the design document's "Data Models" section for
the authoritative field definitions and the correctness properties that
constrain them.

Key invariants encoded by these models (design "Data Models"):

* ``TransactionEvent.event_id`` is the dedup key and ``is_duplicate_suppressed``
  defaults ``False`` (Req 2.5); every event carries ``ingested_at`` + ``source``
  (Req 2.4).
* ``GovernedMetricValue`` is the only source of regulatory figures and always
  carries a ``metric_definition_version`` + ``query_lineage`` (Req 3.1).
* ``SARDraft.is_ai_generated_decision_support`` defaults ``True`` (Req 6.4).
* ``Case.classification`` defaults ``risk_signal`` (Req 4.3) and ``Case.state``
  starts at ``CaseState.RECEIVED``.
* ``AuditRecord`` is immutable/append-only and carries the full required field
  set (Req 8.2).
"""

from __future__ import annotations

from app.models.answer import (
    Answer,
    AnswerStatus,
    Completeness,
    SARDraft,
)
from app.models.audit import AuditRecord, Lineage
from app.models.case import (
    Approval,
    Case,
    CaseState,
    Classification,
    Decision,
)
from app.models.common import (
    EvidenceItem,
    NumericClaim,
    QueryLineage,
    SemanticInterpretation,
    TextualClaim,
)
from app.models.governed import (
    Ambiguous,
    CanonicalMetricRef,
    EntityRiskView,
    FreshnessIndicator,
    GovernedMetricValue,
)
from app.models.source import (
    Alert,
    Entity,
    TransactionEvent,
)
from app.models.telemetry import (
    AnswerTelemetry,
    KpiProvenance,
    KpiSnapshot,
    KpiValue,
)

__all__ = [
    # source
    "Entity",
    "TransactionEvent",
    "Alert",
    # governed
    "GovernedMetricValue",
    "EntityRiskView",
    "FreshnessIndicator",
    "CanonicalMetricRef",
    "Ambiguous",
    # common / value objects
    "QueryLineage",
    "EvidenceItem",
    "NumericClaim",
    "TextualClaim",
    "SemanticInterpretation",
    # answer / sar
    "Answer",
    "AnswerStatus",
    "Completeness",
    "SARDraft",
    # case / approval
    "Case",
    "CaseState",
    "Classification",
    "Approval",
    "Decision",
    # audit
    "AuditRecord",
    "Lineage",
    # telemetry / kpi
    "AnswerTelemetry",
    "KpiProvenance",
    "KpiSnapshot",
    "KpiValue",
]
