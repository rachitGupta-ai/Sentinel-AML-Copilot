"""Answer and SAR-draft domain models (dual-grounded generative output).

Generative narrative is only surfaced when **dual-grounded**: every numeric
claim traces to a governed metric + lineage and every textual claim traces to a
cited passage. Output below the groundedness threshold, or not dual-grounded, is
flagged or refused — never presented as fact (Req 5, Req 6).

Design invariants encoded here:

* ``Answer.status`` is one of ``actionable`` / ``flagged_for_review`` /
  ``refused``; a ``refused`` answer carries a ``refusal_reason`` (Req 5.5,
  Req 5.6, Property 18, Property 19).
* ``SARDraft.is_ai_generated_decision_support`` always defaults ``True`` and the
  draft is never auto-filed (Req 6.4, Property 24).
* ``SARDraft.completeness`` is ``incomplete`` with ``ungrounded_elements``
  listing the ungrounded items when any figure/citation cannot be grounded
  (Req 6.3, Property 23).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.common import (
    EvidenceItem,
    NumericClaim,
    SemanticInterpretation,
    TextualClaim,
)
from app.models.governed import GovernedMetricValue

# Answer lifecycle statuses (Req 5.5, 5.6). An answer is only ``actionable`` when
# dual-grounded and at/above the groundedness threshold; otherwise it is
# ``flagged_for_review`` or ``refused`` and routed to human review.
AnswerStatus = Literal["actionable", "flagged_for_review", "refused"]

# SAR completeness (Req 6.3). ``incomplete`` means one or more required figures
# or citations could not be grounded, so the draft is not filing-ready.
Completeness = Literal["complete", "incomplete"]


class Answer(BaseModel):
    """A grounded answer to an NL investigation question (Req 5).

    The ``narrative`` is Cortex-generated and visibly marked as generated;
    governed facts are carried separately as ``numeric_claims`` (metric +
    lineage) and ``textual_claims`` (cited passages). The generated narrative
    and the governed source facts are disjoint, together covering the rendered
    content (Req 5.3, Property 16). ``status`` reflects the groundedness gate:
    ``actionable`` iff ``dual_grounded`` and ``groundedness_score`` meets the
    threshold, else ``flagged_for_review`` / ``refused`` with a
    ``refusal_reason`` (Req 5.5, Property 18).
    """

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(description="Case this answer belongs to.")
    semantic_interpretation: SemanticInterpretation = Field(
        description="How the question was interpreted, shown before answering (Req 5.1).",
    )
    narrative: str = Field(
        description="Cortex-generated narrative, visibly marked as generated (Req 5.3).",
    )
    numeric_claims: list[NumericClaim] = Field(
        default_factory=list,
        description="Numeric claims, each citing a governed metric + lineage (Req 5.4).",
    )
    textual_claims: list[TextualClaim] = Field(
        default_factory=list,
        description="Textual claims, each citing a policy/evidence passage (Req 5.4).",
    )
    conflicting_evidence: list[EvidenceItem] = Field(
        default_factory=list,
        description="Conflicting evidence surfaced rather than silently resolved (Req 5.7).",
    )
    is_conflicted: bool = Field(
        default=False,
        description="True when the evidence set contains unresolved conflicts (Req 5.7).",
    )
    groundedness_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Groundedness score in [0, 1] driving the gate (Req 5.5).",
    )
    dual_grounded: bool = Field(
        default=False,
        description="True iff numeric and textual claims are both fully cited (Req 5.4).",
    )
    status: AnswerStatus = Field(
        description="Gate outcome: actionable / flagged_for_review / refused (Req 5.5).",
    )
    refusal_reason: str | None = Field(
        default=None,
        description="Reason when flagged/refused; no claim presented as fact (Req 5.6).",
    )


class SARDraft(BaseModel):
    """An audit-ready Suspicious Activity Report draft (Req 6).

    Contains an entity summary, the suspicious pattern, governed figures (each
    linked to metric + version + lineage), and cited policy basis. When any
    required figure or citation cannot be grounded, ``completeness`` is
    ``incomplete``, ``ungrounded_elements`` lists exactly those elements, and the
    draft is not filing-ready (Req 6.3, Property 23). Always labelled
    AI-generated decision support and never auto-filed (Req 6.4, Property 24).
    """

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(description="Case this SAR draft was produced for.")
    entity_summary: str = Field(description="Narrative summary of the entity under review.")
    suspicious_pattern: str = Field(description="Description of the suspicious pattern.")
    governed_figures: list[GovernedMetricValue] = Field(
        default_factory=list,
        description="Governed figures, each linked to metric + version + lineage (Req 6.2).",
    )
    policy_citations: list[EvidenceItem] = Field(
        default_factory=list,
        description="Cited policy passages supporting regulatory assertions (Req 6.2).",
    )
    completeness: Completeness = Field(
        default="incomplete",
        description="'incomplete' when any element is ungrounded; not filing-ready (Req 6.3).",
    )
    ungrounded_elements: list[str] = Field(
        default_factory=list,
        description="Exactly the figures/citations that could not be grounded (Req 6.3).",
    )
    is_ai_generated_decision_support: bool = Field(
        default=True,
        description="Always true; the draft is decision support, never a filing (Req 6.4).",
    )
