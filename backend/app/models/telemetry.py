"""Observability / KPI domain models (per-answer telemetry + KPI snapshot).

The Observability / KPI Service records one :class:`AnswerTelemetry` per produced
answer and aggregates recorded telemetry (plus audit-derived counts) into a
:class:`KpiSnapshot` whose every value is explicitly labelled ``demonstrated`` or
``intended`` (design §10; Req 10).

Design invariants encoded here:

* **Telemetry mirrors the answer (Property 37, Req 10.2).** The AI-quality fields
  of :class:`AnswerTelemetry` — ``groundedness_score``, ``dual_grounded`` (yes/no),
  ``refusal`` (yes/no), and ``citation_count`` — are exactly the corresponding
  values of the :class:`~app.models.answer.Answer` they were recorded from, so the
  telemetry record is a faithful mirror and never a re-derivation.
* **Demonstrated is never conflated with intended (Property 39, Req 10.4).** Every
  :class:`KpiValue` carries a :class:`KpiProvenance` of ``demonstrated`` (a value
  measured from recorded telemetry/audit records in this run) or ``intended`` (a
  production target that was *not* measured). The two channels are kept disjoint
  so an intended target is never presented as a measured result.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.answer import Answer


class KpiProvenance(str, Enum):
    """Whether a KPI value was measured in this run or is a production target.

    ``demonstrated`` values are computed from recorded telemetry/audit records and
    represent what the system actually did in the demo; ``intended`` values are
    forward-looking production targets that were **not** measured. The two must
    never be conflated — an ``intended`` target is never reported as a measured
    result (Req 10.4, Property 39). ``str`` mixin serialises the enum to its value.
    """

    DEMONSTRATED = "demonstrated"
    INTENDED = "intended"


class AnswerTelemetry(BaseModel):
    """Per-answer AI-quality telemetry mirroring a produced answer (Req 10.2).

    Recorded once per answer. The AI-quality fields mirror the answer verbatim:
    ``groundedness_score`` and ``dual_grounded`` are copied from the answer,
    ``refusal`` is ``True`` iff the answer's status is ``refused``, and
    ``citation_count`` is the answer's total number of numeric + textual claims
    (its citations) (Property 37). ``case_id`` ties the record to its case so
    investigation-time and per-case KPIs can be derived; ``recorded_at`` is a UTC
    timestamp. ``refusal_correct`` optionally labels a refusal as correct for the
    refusal-correctness KPI; it is ``None`` for non-refusals or when not evaluated.
    """

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(description="Case the answer belongs to (Req 10.2).")
    answer_id: Optional[str] = Field(
        default=None,
        description="Stable identifier of the answer, when available.",
    )
    groundedness_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Mirrors Answer.groundedness_score in [0, 1] (Req 10.2, Property 37).",
    )
    dual_grounded: bool = Field(
        description="Mirrors Answer.dual_grounded (yes/no) (Req 10.2, Property 37).",
    )
    refusal: bool = Field(
        description="True iff the answer was refused (yes/no) (Req 10.2, Property 37).",
    )
    citation_count: int = Field(
        ge=0,
        description="Number of citations (numeric + textual claims) on the answer (Req 10.2).",
    )
    refusal_correct: Optional[bool] = Field(
        default=None,
        description="For a refusal, whether it was the correct outcome; None otherwise (Req 10.3).",
    )
    recorded_at: datetime = Field(description="UTC timestamp the telemetry was recorded.")

    @classmethod
    def from_answer(
        cls,
        answer: Answer,
        *,
        recorded_at: datetime,
        answer_id: Optional[str] = None,
        refusal_correct: Optional[bool] = None,
    ) -> "AnswerTelemetry":
        """Build telemetry that mirrors ``answer`` exactly (Property 37, Req 10.2).

        The AI-quality fields are copied straight from the answer so the recorded
        telemetry is a faithful mirror: ``refusal`` is derived from the answer's
        status (``refused``) and ``citation_count`` is the total number of numeric
        and textual claims the answer cites.
        """
        return cls(
            case_id=answer.case_id,
            answer_id=answer_id,
            groundedness_score=answer.groundedness_score,
            dual_grounded=answer.dual_grounded,
            refusal=answer.status == "refused",
            citation_count=len(answer.numeric_claims) + len(answer.textual_claims),
            refusal_correct=refusal_correct,
            recorded_at=recorded_at,
        )


class KpiValue(BaseModel):
    """A single KPI value labelled with its provenance (Req 10.3, Req 10.4).

    ``name`` identifies the KPI, ``value`` is the numeric result, ``unit`` names
    its unit (e.g. ``seconds``, ``percent``, ``count``), and ``provenance``
    records whether the value was ``demonstrated`` (measured this run) or
    ``intended`` (a production target). A ``demonstrated`` value is always backed
    by recorded telemetry/audit records; an ``intended`` value never is
    (Property 39).
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="KPI identifier, e.g. 'percent_fully_grounded'.")
    value: float = Field(description="The computed or target value for this KPI.")
    unit: str = Field(description="Unit of the value: 'seconds', 'percent', or 'count'.")
    provenance: KpiProvenance = Field(
        description="Whether the value is demonstrated (measured) or intended (target) (Req 10.4).",
    )
    detail: Optional[str] = Field(
        default=None,
        description="Optional human-readable note on the definition or scope.",
    )


class KpiSnapshot(BaseModel):
    """A point-in-time snapshot of operational KPIs (Req 10.3, Req 10.4).

    ``demonstrated`` holds exactly the values measured from recorded telemetry and
    audit records in this run; ``intended`` holds production targets that were not
    measured. The two lists are disjoint by construction, so a measured result is
    never conflated with an intended target (Property 39). ``computed_at`` is a UTC
    timestamp and ``sample_size`` is the number of telemetry records the
    demonstrated values were computed over, so an empty-sample snapshot is
    distinguishable from a measured zero.
    """

    model_config = ConfigDict(frozen=True)

    computed_at: datetime = Field(description="UTC timestamp the snapshot was computed.")
    sample_size: int = Field(
        ge=0,
        description="Number of recorded answer-telemetry records the KPIs span.",
    )
    demonstrated: list[KpiValue] = Field(
        default_factory=list,
        description="KPI values measured from recorded telemetry/audit records (Req 10.4).",
    )
    intended: list[KpiValue] = Field(
        default_factory=list,
        description="Intended production targets, never presented as measured (Req 10.4).",
    )

    @property
    def demonstrated_by_name(self) -> dict[str, KpiValue]:
        """The demonstrated KPI values keyed by name for convenient lookup."""
        return {value.name: value for value in self.demonstrated}

    @property
    def intended_by_name(self) -> dict[str, KpiValue]:
        """The intended KPI targets keyed by name for convenient lookup."""
        return {value.name: value for value in self.intended}
