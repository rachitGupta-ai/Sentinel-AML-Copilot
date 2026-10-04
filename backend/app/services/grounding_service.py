"""Dual-Grounding & Groundedness Scorer (interface + ``Impl``).

This service decides whether a Cortex-generated draft answer may be surfaced as
*actionable* (design §5; Req 5.4, Req 5.5, Req 5.7, Req 13.3). It enforces the
one separation that makes the copilot audit-ready rather than a generic chatbot:
generative narrative is only actionable when **dual-grounded** — every numeric
claim cites a ``GovernedMetricValue`` + query lineage, and every textual claim
cites a policy/evidence passage. It performs three jobs:

* **Assemble citations.** :meth:`GroundingService.ground` collects the numeric
  citations (each numeric claim's governed metric + lineage) and textual
  citations (each textual claim's cited passage) attached to the draft answer. It
  does not invent, compute, or alter any figure — numeric values originate only
  in the governed zone (Req 3.2, Property 6); grounding merely checks and reports
  what the draft already carries.
* **Score + decide dual-grounding.** It computes a deterministic
  ``Groundedness_Score`` in ``[0, 1]`` from the proportion of claims that resolve
  to their evidence, and sets ``dual_grounded`` true *iff* every numeric claim is
  metric+lineage cited **and** every textual claim is passage cited (Req 5.4,
  Property 17). Resolving any cited claim returns that exact evidence.
* **Gate.** :meth:`GroundingService.gate` admits the answer as ``actionable`` *if
  and only if* ``score >= threshold`` **and** ``dual_grounded`` (threshold
  configurable, default ``0.7``); otherwise it returns a
  ``flagged_for_review`` / ``refused`` outcome carrying a reason, routed to human
  review rather than presented as actionable (Req 5.5, Req 13.3, Property 18).

Conflicting source records are **surfaced, never silently resolved**: any
conflicting evidence supplied with the draft is carried through verbatim and the
result is marked conflicted; no conflicting item is dropped (Req 5.7,
Property 20). Conflict additionally forces a non-actionable gate outcome so an
unresolved conflict is never presented as fact.

The scorer is **pure and deterministic**: the same ``(draft, evidence,
threshold)`` always yields the same :class:`GroundingResult` and gate outcome,
with no wall-clock time or ambient randomness in the decision path — consistent
with the deterministic-first design principle and making the service directly
unit/property testable without Snowflake or Cortex (design §5).
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.models.answer import Answer
from app.models.common import EvidenceItem, NumericClaim, TextualClaim

# Default groundedness threshold (Req 5.5). An answer is only actionable when its
# score is at or above this value AND it is dual-grounded. Configurable per call.
DEFAULT_GROUNDEDNESS_THRESHOLD = 0.7


class GateOutcome(str, Enum):
    """The three gate verdicts for a grounded answer (Req 5.5, Property 18).

    * ``ACTIONABLE`` — the answer is dual-grounded and at/above threshold and may
      be presented as actionable.
    * ``FLAGGED_FOR_REVIEW`` — the answer is grounded but below threshold, or
      carries unresolved conflicting evidence; it is routed to human review with a
      reason rather than presented as fact (Req 5.7, Req 13.3).
    * ``REFUSED`` — the answer is not dual-grounded (a claim is uncited); it is
      refused with a reason and no claim is presented as fact.
    """

    ACTIONABLE = "actionable"
    FLAGGED_FOR_REVIEW = "flagged_for_review"
    REFUSED = "refused"


class GroundingResult(BaseModel):
    """Outcome of grounding a draft answer against its evidence (Req 5.4, 5.5, 5.7).

    Carries the deterministic ``score`` in ``[0, 1]``, the ``dual_grounded``
    verdict, and the assembled ``numeric_citations`` / ``textual_citations`` so a
    caller can resolve any cited claim back to its exact evidence (Property 17).
    ``conflicting_evidence`` surfaces every conflicting item supplied with the
    draft and ``is_conflicted`` marks the result conflicted — conflicts are never
    silently resolved (Req 5.7, Property 20). ``uncited_claims`` names the claims
    (if any) that failed to resolve to evidence, which is what breaks
    dual-grounding and drives a refusal in the gate.
    """

    model_config = ConfigDict(frozen=True)

    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Deterministic groundedness score in [0, 1] (Req 5.5).",
    )
    dual_grounded: bool = Field(
        description="True iff every numeric and textual claim resolves to evidence (Req 5.4).",
    )
    numeric_citations: list[EvidenceItem] = Field(
        default_factory=list,
        description="Governed-metric + lineage citations, one per numeric claim (Req 5.4).",
    )
    textual_citations: list[EvidenceItem] = Field(
        default_factory=list,
        description="Policy/evidence-passage citations, one per textual claim (Req 5.4).",
    )
    conflicting_evidence: list[EvidenceItem] = Field(
        default_factory=list,
        description="Conflicting items surfaced rather than silently resolved (Req 5.7).",
    )
    is_conflicted: bool = Field(
        default=False,
        description="True when the evidence set contains unresolved conflicts (Req 5.7).",
    )
    uncited_claims: list[str] = Field(
        default_factory=list,
        description="Claim texts that failed to resolve to evidence (break dual-grounding).",
    )

    @property
    def citations(self) -> list[EvidenceItem]:
        """All assembled citations (numeric followed by textual), in order."""
        return [*self.numeric_citations, *self.textual_citations]


class GateDecision(BaseModel):
    """Result of applying the groundedness gate to a :class:`GroundingResult`.

    ``outcome`` is :attr:`GateOutcome.ACTIONABLE` *iff* ``score >= threshold`` and
    the result is dual-grounded and not conflicted; otherwise it is
    ``FLAGGED_FOR_REVIEW`` / ``REFUSED`` with a ``reason`` and ``routed_to_review``
    set, so the answer is handed to a human rather than presented as actionable
    (Req 5.5, Req 13.3, Property 18). ``threshold`` records the value the decision
    was taken against.
    """

    model_config = ConfigDict(frozen=True)

    outcome: GateOutcome = Field(description="Gate verdict (Req 5.5).")
    threshold: float = Field(
        ge=0.0,
        le=1.0,
        description="Threshold the decision was evaluated against (default 0.7).",
    )
    reason: str | None = Field(
        default=None,
        description="Why the answer was flagged/refused; None when actionable (Req 5.5).",
    )
    routed_to_review: bool = Field(
        default=False,
        description="True when the answer is routed to human review (Req 5.5, 13.3).",
    )

    @property
    def is_actionable(self) -> bool:
        """Whether the answer may be presented as actionable."""
        return self.outcome is GateOutcome.ACTIONABLE


@runtime_checkable
class GroundingService(Protocol):
    """Dual-grounding assembly, scoring, and the groundedness gate (design §5; Req 5)."""

    def ground(
        self, draft_answer: Answer, evidence: Sequence[EvidenceItem]
    ) -> GroundingResult:
        """Assemble citations, compute the score, and decide dual-grounding.

        For each numeric claim on ``draft_answer`` the citing governed metric +
        lineage is collected, and for each textual claim its cited passage is
        collected. ``dual_grounded`` is true iff every numeric claim is
        metric+lineage cited AND every textual claim is passage cited; the
        ``score`` is the deterministic proportion of claims that resolve to
        evidence (Req 5.4, Property 17). Any conflicting item in ``evidence`` (or
        on the draft) is surfaced in the result and the result is marked
        conflicted — never silently resolved (Req 5.7, Property 20).
        """
        ...

    def gate(
        self,
        result: GroundingResult,
        threshold: float = DEFAULT_GROUNDEDNESS_THRESHOLD,
    ) -> GateDecision:
        """Admit the answer iff grounded and at/above ``threshold``.

        Returns :attr:`GateOutcome.ACTIONABLE` if and only if ``result.score >=
        threshold`` and ``result.dual_grounded`` and not ``result.is_conflicted``;
        otherwise a ``FLAGGED_FOR_REVIEW`` / ``REFUSED`` decision carrying a reason
        and routed to human review (Req 5.5, Req 13.3, Property 18).
        """
        ...


class GroundingServiceImpl:
    """Default, pure :class:`GroundingService`.

    The implementation is a deterministic function of its inputs — it reads no
    external store and performs no I/O — so it is directly unit/property testable
    and produces an identical result for identical inputs (design §5). Numeric
    values are never computed or altered here; grounding only verifies and reports
    the citations the governed/metric layer already attached to the draft
    (Req 3.2, Property 6).
    """

    def ground(
        self, draft_answer: Answer, evidence: Sequence[EvidenceItem]
    ) -> GroundingResult:
        """See :meth:`GroundingService.ground`."""
        numeric_citations: list[EvidenceItem] = []
        textual_citations: list[EvidenceItem] = []
        uncited: list[str] = []

        # Assemble numeric citations: each numeric claim must cite a governed
        # metric + query lineage (Req 5.4). A claim resolves iff it carries
        # lineage; the citation points back to the exact governed metric so the
        # claim is replayable (Property 17).
        for claim in draft_answer.numeric_claims:
            citation = _numeric_citation(claim)
            if citation is not None:
                numeric_citations.append(citation)
            else:
                uncited.append(claim.text)

        # Assemble textual citations: each textual claim must cite a
        # policy/evidence passage (Req 5.4). The claim's evidence IS the citation;
        # a passage with no reference does not resolve.
        for claim in draft_answer.textual_claims:
            citation = _textual_citation(claim)
            if citation is not None:
                textual_citations.append(citation)
            else:
                uncited.append(claim.text)

        # Surface conflicts rather than resolving them (Req 5.7, Property 20).
        # Conflicts can come either from the supplied evidence set or from the
        # draft itself; both are carried through verbatim, de-duplicated by
        # (kind, ref) while preserving order so no conflicting item is dropped.
        conflicting = _dedupe_evidence(
            [*draft_answer.conflicting_evidence, *_conflicting_items(evidence)]
        )
        is_conflicted = bool(conflicting) or draft_answer.is_conflicted

        total_claims = len(draft_answer.numeric_claims) + len(draft_answer.textual_claims)
        resolved_claims = total_claims - len(uncited)
        dual_grounded = total_claims > 0 and not uncited
        score = _groundedness_score(resolved_claims, total_claims)

        return GroundingResult(
            score=score,
            dual_grounded=dual_grounded,
            numeric_citations=numeric_citations,
            textual_citations=textual_citations,
            conflicting_evidence=conflicting,
            is_conflicted=is_conflicted,
            uncited_claims=uncited,
        )

    def gate(
        self,
        result: GroundingResult,
        threshold: float = DEFAULT_GROUNDEDNESS_THRESHOLD,
    ) -> GateDecision:
        """See :meth:`GroundingService.gate`."""
        # Not dual-grounded: at least one claim is uncited, so a claim would be
        # presented without resolvable evidence. Refuse (Req 5.4, Property 18).
        if not result.dual_grounded:
            return GateDecision(
                outcome=GateOutcome.REFUSED,
                threshold=threshold,
                reason=_refusal_reason(result),
                routed_to_review=True,
            )

        # Unresolved conflict: evidence is dual-grounded but contradictory. Never
        # present a conflict as fact — route to human review (Req 5.7, Req 13.3).
        if result.is_conflicted:
            return GateDecision(
                outcome=GateOutcome.FLAGGED_FOR_REVIEW,
                threshold=threshold,
                reason="Conflicting evidence present; routed to human review.",
                routed_to_review=True,
            )

        # Below threshold: grounded but low confidence. Flag for review rather
        # than present as actionable (Req 5.5, Req 13.3, Property 18).
        if result.score < threshold:
            return GateDecision(
                outcome=GateOutcome.FLAGGED_FOR_REVIEW,
                threshold=threshold,
                reason=(
                    f"Groundedness score {result.score:.3f} is below the "
                    f"configured threshold {threshold:.3f}."
                ),
                routed_to_review=True,
            )

        # Dual-grounded, no unresolved conflict, at/above threshold: actionable.
        return GateDecision(
            outcome=GateOutcome.ACTIONABLE,
            threshold=threshold,
            reason=None,
            routed_to_review=False,
        )


def _numeric_citation(claim: NumericClaim) -> EvidenceItem | None:
    """Build the governed-metric citation for a numeric claim (Req 5.4, Property 17).

    A numeric claim resolves iff it carries query lineage tracing the value to its
    source rows. The returned :class:`EvidenceItem` references the governed metric
    by name + version and carries that lineage, so resolving the citation returns
    the exact governed evidence. Returns ``None`` when the claim is uncited.
    """
    if claim.lineage is None:
        return None
    return EvidenceItem(
        kind="governed_metric",
        ref=f"{claim.metric_name}@{claim.metric_definition_version}",
        excerpt=None,
        lineage=claim.lineage,
    )


def _textual_citation(claim: TextualClaim) -> EvidenceItem | None:
    """Return the cited passage for a textual claim (Req 5.4, Property 17).

    The claim's own :class:`EvidenceItem` is the citation. It resolves iff it
    carries a non-empty reference to the policy/evidence passage. Returns ``None``
    when the passage reference is absent.
    """
    evidence = claim.evidence
    if not evidence.ref or not evidence.ref.strip():
        return None
    return evidence


def _conflicting_items(evidence: Sequence[EvidenceItem]) -> list[EvidenceItem]:
    """Identify evidence items that conflict on the same claim (Req 5.7, Property 20).

    Two items conflict when they describe the *same* referent (same ``kind`` and
    ``ref``) yet carry differing content (``excerpt`` or lineage). Every item in
    such a conflicting group is returned so none is dropped; the caller surfaces
    them all and marks the result conflicted rather than choosing one.
    """
    groups: dict[tuple[str, str], list[EvidenceItem]] = {}
    for item in evidence:
        groups.setdefault((item.kind, item.ref), []).append(item)

    conflicting: list[EvidenceItem] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        distinct = {(m.excerpt, _lineage_key(m)) for m in members}
        if len(distinct) > 1:
            conflicting.extend(members)
    return conflicting


def _lineage_key(item: EvidenceItem) -> str | None:
    """A stable comparison key for an evidence item's lineage (for conflict detection)."""
    if item.lineage is None:
        return None
    lineage = item.lineage
    return "|".join(
        [
            lineage.generated_query,
            ",".join(lineage.source_row_refs),
            lineage.semantic_view or "",
            lineage.data_state_hash or "",
        ]
    )


def _dedupe_evidence(items: Sequence[EvidenceItem]) -> list[EvidenceItem]:
    """De-duplicate evidence items while preserving first-seen order.

    Conflicting items can arrive from both the draft and the supplied evidence
    set; identical duplicates are collapsed so the surfaced conflict set lists
    each distinct item once, but no *distinct* conflicting item is ever dropped
    (Req 5.7, Property 20).
    """
    seen: set[tuple[str, str, str | None, str | None]] = set()
    deduped: list[EvidenceItem] = []
    for item in items:
        key = (item.kind, item.ref, item.excerpt, _lineage_key(item))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _groundedness_score(resolved_claims: int, total_claims: int) -> float:
    """Deterministic groundedness score in ``[0, 1]`` (Req 5.5).

    The score is the proportion of claims that resolve to their cited evidence —
    a pure function of the counts, with no wall-clock time or randomness, so it is
    reproducible for a fixed draft (design §5). An answer carrying no claims scores
    ``0.0``: there is nothing grounded to present, so it cannot be actionable.
    """
    if total_claims <= 0:
        return 0.0
    return resolved_claims / total_claims


def _refusal_reason(result: GroundingResult) -> str:
    """Explain why an answer failed dual-grounding (used on refusal; Req 5.4, 5.6)."""
    if result.uncited_claims:
        joined = "; ".join(result.uncited_claims)
        return (
            "Answer is not dual-grounded: the following claim(s) do not resolve to "
            f"evidence: {joined}."
        )
    return "Answer is not dual-grounded: it carries no cited claims."


def should_route_to_review(decision: GateDecision) -> bool:
    """Whether a gated answer must go to human review rather than be presented (Req 13.3).

    A small, shared predicate so every surface (investigation, SAR, API) applies
    the *same* rule for "low-confidence / low-groundedness answers route to human
    review rather than being presented": an answer is presentable only when the
    gate admitted it as actionable; any ``flagged_for_review`` / ``refused``
    outcome (below threshold, not dual-grounded, or conflicted) is routed to review
    (Req 5.5, Req 13.3, Property 18). This simply reads the gate decision — the
    grounding gate remains the single place the decision is *made*; this unifies
    how callers *act* on it.
    """
    return not decision.is_actionable


def apply_gate_to_answer(answer: Answer, decision: GateDecision) -> Answer:
    """Return a copy of ``answer`` with gate outcome, score, and reason applied.

    Convenience for the investigation/SAR orchestration layers: it stamps the
    gated ``status`` (``actionable`` / ``flagged_for_review`` / ``refused``) and
    the ``refusal_reason`` onto the answer so a non-actionable answer is never
    presented as fact (Req 5.5, Req 5.6, Property 18). Immutable in, immutable out.
    """
    return answer.model_copy(
        update={
            "status": decision.outcome.value,
            "refusal_reason": decision.reason,
        }
    )
