"""NL Investigation Service (interface + ``Impl``).

This service answers an analyst's natural-language question about a case while
preserving the one separation that makes the copilot audit-ready rather than a
generic chatbot (design §4; Req 5). It performs four jobs, each mapped to a
correctness property:

* **Interpret before answering (Req 5.1).** :meth:`InvestigationService.interpret`
  maps the business terms in a question to canonical ``Governed_Metrics`` via the
  metric service's glossary (``resolve_term``) and surfaces the
  :class:`~app.models.common.SemanticInterpretation` — the resolved metrics,
  resolved entities, and any ambiguity — so the analyst sees what was understood
  *before* an answer is produced.

* **Ambiguity yields a clarification, never a guess (Req 5.2, Property 15).** When
  a term resolves to two or more candidate metrics, :meth:`answer` returns a
  :class:`Clarification` listing the candidates rather than silently choosing one.

* **Out-of-scope / ungroundable questions are refused (Req 5.6, Property 19).** A
  question that maps to no governed metric (and so cannot be grounded in governed
  figures) is refused with a reason; no numeric or textual claim is presented as
  fact. Likewise, when the grounding gate does not admit the drafted answer as
  actionable, the answer is flagged/refused and never presented as fact.

* **Generated narrative is distinguished from governed facts (Req 5.3, Req 9.5,
  Property 16).** The Cortex-generated ``narrative`` is carried separately from
  the governed ``numeric_claims`` (metric + lineage) and ``textual_claims`` (cited
  passages). The generated set and the governed source set are disjoint and
  together cover the answer's content — the LLM narrates around governed numbers,
  it never produces them (Req 3.2).

Orchestration wires the collaborators without ever letting the LLM compute a
figure: governed metric values come from the :class:`MetricService`; evidence is
retrieved (already injection-scanned) via the :class:`CortexAdapter`; the draft
narrative is generated via Cortex ``COMPLETE``; grounding + the gate are applied
through the :class:`GroundingService`. Every investigation emits append-only
audit records through the :class:`AuditService` (Req 8.1). If Cortex is
unavailable (:class:`CortexUnavailableError`) the service **fails safe** — it
returns a refusal/flagged answer with no actionable, ungrounded output
(Req 13.1, Req 13.3).

All collaborators are injected (with safe in-memory/default fallbacks) so the
service is unit-testable with fakes, consistent with the triage/grounding
services.
"""

from __future__ import annotations

from typing import Optional, Protocol, Union, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.models.answer import Answer
from app.models.case import Case
from app.models.common import (
    EvidenceItem,
    NumericClaim,
    SemanticInterpretation,
    TextualClaim,
)
from app.models.governed import Ambiguous, CanonicalMetricRef, GovernedMetricValue
from app.services.audit_service import (
    SYSTEM_ACTOR,
    AuditService,
    AuditServiceImpl,
    build_audit_record,
)
from app.services.grounding_service import (
    DEFAULT_GROUNDEDNESS_THRESHOLD,
    GroundingService,
    GroundingServiceImpl,
    apply_gate_to_answer,
)
from app.services.metric_service import (
    MetricNotFoundError,
    MetricService,
    MetricServiceImpl,
)
from app.snowflake.cortex import CortexAdapter, CortexUnavailableError

# ---------------------------------------------------------------------------
# Canonical audit action names.
#
# Kept stable and package-private so the audit trail is queryable and the
# investigation property tests (Property 15, 16, 19) can assert the recorded
# operations.
# ---------------------------------------------------------------------------

# Recorded when a question is interpreted (semantic interpretation produced).
ACTION_QUESTION_INTERPRETED = "investigation.question_interpreted"

# Recorded when an actionable/flagged answer is produced for a question.
ACTION_ANSWER_PRODUCED = "investigation.answer_produced"

# Recorded when an ambiguous question yields a clarification (Req 5.2).
ACTION_CLARIFICATION_REQUESTED = "investigation.clarification_requested"

# Recorded when an out-of-scope/ungroundable question is refused (Req 5.6), or
# when Cortex is unavailable and the service fails safe (Req 13.1).
ACTION_QUESTION_REFUSED = "investigation.question_refused"


class Clarification(BaseModel):
    """A request for the analyst to disambiguate their question (Req 5.2, Property 15).

    Returned by :meth:`InvestigationService.answer` instead of a guessed
    :class:`Answer` whenever term resolution yields two or more candidate metrics.
    It carries the term that was ambiguous and the candidate canonical metrics so
    the UI can present a concrete choice. The :class:`SemanticInterpretation` is
    included so the "interpretation before answering" contract (Req 5.1) still
    holds for an ambiguous question.
    """

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(description="Case the question was asked against.")
    question: str = Field(description="The original natural-language question.")
    ambiguous_term: str = Field(description="The business term that could not be uniquely resolved.")
    candidate_terms: list[str] = Field(
        default_factory=list,
        description="Candidate canonical metrics the term matched (>1 = ambiguous).",
    )
    semantic_interpretation: SemanticInterpretation = Field(
        description="How the question was interpreted, surfaced before any answer (Req 5.1).",
    )
    message: str = Field(description="Human-readable clarifying question for the analyst.")


class Refusal(BaseModel):
    """A refusal to answer an out-of-scope / ungroundable question (Req 5.6, Property 19).

    Returned when a question maps to no governed metric (and so cannot be grounded
    in governed figures) or cannot be supported by cited evidence. It carries a
    reason and presents **no** numeric or textual claim as fact — the whole point
    of the refusal is that nothing ungrounded is surfaced. The
    :class:`SemanticInterpretation` is included to preserve the
    interpretation-before-answering contract (Req 5.1).
    """

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(description="Case the question was asked against.")
    question: str = Field(description="The original natural-language question.")
    reason: str = Field(description="Why the question was refused (Req 5.6).")
    semantic_interpretation: SemanticInterpretation = Field(
        description="How the question was interpreted, surfaced before the refusal (Req 5.1).",
    )


# The result of answering a question: a grounded :class:`Answer`, a
# :class:`Clarification` for an ambiguous question, or a :class:`Refusal` for an
# out-of-scope/ungroundable one. Modelled as a union so the caller handles each
# case explicitly rather than inspecting a status flag (design §4).
InvestigationResult = Union[Answer, Clarification, Refusal]


@runtime_checkable
class InvestigationService(Protocol):
    """Interpret and answer NL investigation questions (design §4; Req 5)."""

    def interpret(self, question: str, case: Case) -> SemanticInterpretation:
        """Map ``question``'s terms to canonical metrics and surface the interpretation.

        Business terms are resolved to canonical ``Governed_Metrics`` via the
        metric glossary (``resolve_term``); the entity under investigation is
        attached as a resolved entity. The returned
        :class:`SemanticInterpretation` is shown *before* answering so the analyst
        sees what was understood (Req 5.1). It is marked ``ambiguous`` with
        ``candidate_terms`` when any term maps to more than one metric (Req 5.2,
        Property 15).
        """
        ...

    def answer(self, question: str, case: Case) -> InvestigationResult:
        """Answer ``question`` for ``case``, or clarify / refuse.

        Returns a :class:`Clarification` when the question is ambiguous (Req 5.2),
        a :class:`Refusal` when it is out-of-scope / ungroundable (Req 5.6), or an
        :class:`Answer` whose gate ``status`` reflects the grounding outcome
        (Req 5.5). The generated narrative is always distinguished from the
        governed facts (Req 5.3, Property 16); Cortex unavailability fails safe
        with no actionable, ungrounded output (Req 13.1).
        """
        ...


class InvestigationServiceImpl:
    """Default :class:`InvestigationService` wiring the governed collaborators.

    Figures come from the :class:`MetricService` (the only figure source);
    evidence + narrative from the :class:`CortexAdapter`; grounding + the gate
    from the :class:`GroundingService`; every operation is audited through the
    :class:`AuditService`. A :class:`CortexAdapter` must be supplied (it requires a
    session); the remaining collaborators default to in-memory/default
    implementations for local runs and unit tests.
    """

    def __init__(
        self,
        cortex: CortexAdapter,
        *,
        metric_service: Optional[MetricService] = None,
        grounding_service: Optional[GroundingService] = None,
        audit_service: Optional[AuditService] = None,
        threshold: float = DEFAULT_GROUNDEDNESS_THRESHOLD,
    ) -> None:
        self._cortex = cortex
        self._metrics: MetricService = (
            metric_service if metric_service is not None else MetricServiceImpl()
        )
        self._grounding: GroundingService = (
            grounding_service if grounding_service is not None else GroundingServiceImpl()
        )
        self._audit: AuditService = (
            audit_service if audit_service is not None else AuditServiceImpl()
        )
        self._threshold = threshold

    # ------------------------------------------------------------------
    # interpret
    # ------------------------------------------------------------------

    def interpret(self, question: str, case: Case) -> SemanticInterpretation:
        """See :meth:`InvestigationService.interpret`."""
        resolved_metrics: list[str] = []
        candidate_terms: list[str] = []
        ambiguous = False

        for term in _candidate_terms(question):
            resolution = self._metrics.resolve_term(term)
            if isinstance(resolution, CanonicalMetricRef):
                if resolution.metric_name not in resolved_metrics:
                    resolved_metrics.append(resolution.metric_name)
            elif isinstance(resolution, Ambiguous) and len(resolution.candidates) > 1:
                # A term that maps to >1 canonical metric is a true ambiguity: do
                # not guess (Req 5.2, Property 15). Record every candidate so the
                # clarification can present a concrete choice.
                ambiguous = True
                for candidate in resolution.candidates:
                    if candidate not in candidate_terms:
                        candidate_terms.append(candidate)
            # Ambiguous with 0 candidates = unknown term; contributes neither a
            # resolved metric nor an ambiguity (it drives a refusal if nothing
            # else resolves, Req 5.6).

        interpretation = SemanticInterpretation(
            question=question,
            resolved_metrics=resolved_metrics,
            resolved_entities=[case.entity_id],
            ambiguous=ambiguous,
            candidate_terms=candidate_terms,
        )

        self._audit.append(
            build_audit_record(
                action=ACTION_QUESTION_INTERPRETED,
                actor_id=SYSTEM_ACTOR,
                case_ref=case.case_id,
                entity_ref=case.entity_id,
                input_refs=[question],
                output_refs=list(resolved_metrics),
            )
        )
        return interpretation

    # ------------------------------------------------------------------
    # answer
    # ------------------------------------------------------------------

    def answer(self, question: str, case: Case) -> InvestigationResult:
        """See :meth:`InvestigationService.answer`."""
        # 1. Interpret first (Req 5.1). The interpretation is surfaced on every
        #    result type (answer / clarification / refusal).
        interpretation = self.interpret(question, case)

        # 2. Ambiguous question -> clarification, never a guess (Req 5.2, P15).
        if interpretation.ambiguous:
            return self._clarify(question, case, interpretation)

        # 3. Out-of-scope / ungroundable: nothing mapped to a governed metric, so
        #    the question cannot be grounded in governed figures (Req 5.6, P19).
        if not interpretation.resolved_metrics:
            return self._refuse(
                question,
                case,
                interpretation,
                reason=(
                    "Question could not be mapped to any governed metric; it is "
                    "out of scope or cannot be grounded in governed figures."
                ),
            )

        # 4. Fetch the governed figures (the ONLY source of numeric values,
        #    Req 3.2). Any missing governed value makes the question ungroundable.
        try:
            metric_values = self._fetch_metric_values(case, interpretation.resolved_metrics)
        except MetricNotFoundError as exc:
            return self._refuse(
                question,
                case,
                interpretation,
                reason=(
                    "Question maps to a governed metric with no value for this "
                    f"entity ('{exc.metric_name}'); it cannot be grounded."
                ),
            )

        # 5. Retrieve evidence (already injection-scanned by the Cortex adapter)
        #    and generate the narrative; fail safe if Cortex is unavailable so no
        #    ungrounded output is ever surfaced (Req 13.1, Req 13.3).
        try:
            evidence_items, textual_claims = self._retrieve_textual_evidence(question, case)
            narrative = self._generate_narrative(question, case, metric_values, evidence_items)
        except CortexUnavailableError as exc:
            return self._refuse(
                question,
                case,
                interpretation,
                reason=(
                    "AI narrative/evidence service is unavailable; failing safe "
                    f"with no ungrounded output ({exc.operation})."
                ),
            )

        # 6. Build the draft Answer, keeping the generated narrative DISJOINT from
        #    the governed facts (Req 5.3, Property 16): narrative is Cortex text;
        #    numeric_claims come from governed metrics; textual_claims cite
        #    retrieved passages. The LLM never produces a figure.
        numeric_claims = [_numeric_claim(mv) for mv in metric_values]
        draft = Answer(
            case_id=case.case_id,
            semantic_interpretation=interpretation,
            narrative=narrative,
            numeric_claims=numeric_claims,
            textual_claims=textual_claims,
            status="flagged_for_review",  # provisional until the gate decides
        )

        # 7. Ground + gate, then apply the outcome so a non-actionable answer is
        #    never presented as fact (Req 5.5, Req 5.6, Property 18).
        result = self._grounding.ground(draft, evidence_items)
        decision = self._grounding.gate(result, threshold=self._threshold)
        gated = apply_gate_to_answer(draft, decision)
        gated = gated.model_copy(
            update={
                "groundedness_score": result.score,
                "dual_grounded": result.dual_grounded,
                "conflicting_evidence": list(result.conflicting_evidence),
                "is_conflicted": result.is_conflicted,
            }
        )

        # 8. Audit the produced answer with its gate outcome.
        self._audit.append(
            build_audit_record(
                action=ACTION_ANSWER_PRODUCED,
                actor_id=SYSTEM_ACTOR,
                case_ref=case.case_id,
                entity_ref=case.entity_id,
                input_refs=[question],
                output_refs=[gated.status],
                metric_definition_version=_first_version(metric_values),
            )
        )
        return gated

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_metric_values(
        self, case: Case, metric_names: list[str]
    ) -> list[GovernedMetricValue]:
        """Read governed values for the resolved metrics from the metric service.

        Reuses the case's governed data-state hash (from its risk aggregation) so
        the figures are tied to the snapshot the case was triaged against; falls
        back to a per-case data-state key when the aggregation carries none. Reads
        only through the governed path — no figure is computed here (Req 3.2).
        """
        data_state = _case_data_state(case)
        return [
            self._metrics.get_metric(case.entity_id, metric_name, data_state)
            for metric_name in metric_names
        ]

    def _retrieve_textual_evidence(
        self, question: str, case: Case
    ) -> tuple[list[EvidenceItem], list[TextualClaim]]:
        """Retrieve guarded policy/evidence passages for the question (Req 9.1).

        Delegates to the Cortex adapter, which embeds the query, ranks chunks, and
        routes every chunk through the Guard injection scan before returning it, so
        only clean passages reach this layer. Each clean chunk becomes an
        :class:`EvidenceItem` (the citable passage) and a :class:`TextualClaim`
        that cites it, so textual assertions are dual-grounded (Req 5.4).
        """
        retrieval = self._cortex.retrieve_policy_evidence(
            question, case_ref=case.case_id, entity_ref=case.entity_id
        )
        evidence_items: list[EvidenceItem] = []
        textual_claims: list[TextualClaim] = []
        for chunk in retrieval.chunks:
            evidence = EvidenceItem(
                kind="policy_passage",
                ref=chunk.source_ref or chunk.chunk_id,
                excerpt=chunk.chunk_text,
                lineage=None,
            )
            evidence_items.append(evidence)
            textual_claims.append(
                TextualClaim(
                    text=chunk.chunk_text,
                    evidence=evidence,
                )
            )
        return evidence_items, textual_claims

    def _generate_narrative(
        self,
        question: str,
        case: Case,
        metric_values: list[GovernedMetricValue],
        evidence_items: list[EvidenceItem],
    ) -> str:
        """Generate the narrative via Cortex ``COMPLETE`` (narrates, never computes).

        The prompt is assembled from the governed figures and the already-scanned
        evidence passages; the LLM is handed numbers and asked only to narrate
        around them (Req 3.2). Any Cortex failure propagates as
        :class:`CortexUnavailableError`, which the caller turns into a fail-safe
        refusal (Req 13.1).
        """
        prompt = _build_prompt(question, case, metric_values, evidence_items)
        return self._cortex.complete(prompt)

    def _clarify(
        self, question: str, case: Case, interpretation: SemanticInterpretation
    ) -> Clarification:
        """Build + audit a :class:`Clarification` for an ambiguous question (Req 5.2)."""
        ambiguous_term = _first_ambiguous_term(question, interpretation)
        self._audit.append(
            build_audit_record(
                action=ACTION_CLARIFICATION_REQUESTED,
                actor_id=SYSTEM_ACTOR,
                case_ref=case.case_id,
                entity_ref=case.entity_id,
                input_refs=[question],
                output_refs=list(interpretation.candidate_terms),
            )
        )
        return Clarification(
            case_id=case.case_id,
            question=question,
            ambiguous_term=ambiguous_term,
            candidate_terms=list(interpretation.candidate_terms),
            semantic_interpretation=interpretation,
            message=(
                "Your question is ambiguous: it could refer to "
                f"{', '.join(interpretation.candidate_terms)}. "
                "Please clarify which you mean."
            ),
        )

    def _refuse(
        self,
        question: str,
        case: Case,
        interpretation: SemanticInterpretation,
        *,
        reason: str,
    ) -> Refusal:
        """Build + audit a :class:`Refusal` with no claim presented as fact (Req 5.6)."""
        self._audit.append(
            build_audit_record(
                action=ACTION_QUESTION_REFUSED,
                actor_id=SYSTEM_ACTOR,
                case_ref=case.case_id,
                entity_ref=case.entity_id,
                input_refs=[question],
                output_refs=[reason],
            )
        )
        return Refusal(
            case_id=case.case_id,
            question=question,
            reason=reason,
            semantic_interpretation=interpretation,
        )


# ---------------------------------------------------------------------------
# Pure helpers (deterministic; no I/O).
# ---------------------------------------------------------------------------


def _candidate_terms(question: str) -> list[str]:
    """Extract candidate business terms from a question, order-preserving.

    A lightweight tokenizer that yields single words and adjacent word bigrams
    (so multi-word metric synonyms like "exposure 90d" can resolve) while
    de-duplicating. Resolution against the glossary is what actually decides
    whether a term is a metric — this only proposes candidates (Req 5.1).
    """
    words = [w for w in _normalize(question).split() if w]
    terms: list[str] = []
    seen: set[str] = set()

    def _add(term: str) -> None:
        if term and term not in seen:
            seen.add(term)
            terms.append(term)

    for index, word in enumerate(words):
        if index + 1 < len(words):
            _add(f"{word} {words[index + 1]}")
    for word in words:
        _add(word)
    return terms


def _normalize(text: str) -> str:
    """Lower-case and strip punctuation so terms match glossary synonyms."""
    return "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text.lower())


def _first_ambiguous_term(question: str, interpretation: SemanticInterpretation) -> str:
    """Best-effort recovery of the term that drove the ambiguity (for the message)."""
    for term in _candidate_terms(question):
        if term in {c.lower() for c in interpretation.candidate_terms}:
            return term
    # Fall back to the question itself when the exact token can't be recovered.
    return question


def _numeric_claim(metric_value: GovernedMetricValue) -> NumericClaim:
    """Build a governed, lineage-bearing numeric claim from a governed value.

    The claim carries the governed value, its ``metric_definition_version``, and
    the query lineage verbatim — no value originates from narrative (Req 3.2,
    Property 6). The ``text`` is a plain rendering of the governed figure, kept
    separate from the Cortex narrative (Property 16).
    """
    return NumericClaim(
        text=f"{metric_value.metric_name} = {metric_value.value}",
        metric_name=metric_value.metric_name,
        value=metric_value.value,
        metric_definition_version=metric_value.metric_definition_version,
        lineage=metric_value.query_lineage,
    )


def _build_prompt(
    question: str,
    case: Case,
    metric_values: list[GovernedMetricValue],
    evidence_items: list[EvidenceItem],
) -> str:
    """Assemble the Cortex prompt from governed figures + scanned evidence.

    The LLM is explicitly instructed to narrate around the governed figures and
    not to invent or alter any number (Req 3.2). Figures and evidence are the only
    substantive content; the prompt carries no secret (Req 1.1).
    """
    figure_lines = "\n".join(
        f"- {mv.metric_name} = {mv.value} (version {mv.metric_definition_version})"
        for mv in metric_values
    ) or "- (no governed figures)"
    evidence_lines = "\n".join(
        f"- [{item.ref}] {item.excerpt}" for item in evidence_items if item.excerpt
    ) or "- (no supporting passages)"
    return (
        "You are an AML investigation assistant. Narrate an explanation for the "
        "analyst's question using ONLY the governed figures and cited policy "
        "passages below. Do not invent, compute, or alter any number.\n\n"
        f"Entity under investigation: {case.entity_id}\n"
        f"Question: {question}\n\n"
        f"Governed figures:\n{figure_lines}\n\n"
        f"Policy/evidence passages:\n{evidence_lines}\n"
    )


def _case_data_state(case: Case) -> str:
    """Derive the governed data-state key for the case's metric reads (Req 3.3).

    Prefers the data-state hash already stamped on the case's governed risk
    aggregation (so investigation figures match the snapshot the case was triaged
    against); falls back to a stable per-case key when none is present. Pure
    function of the case — deterministic and role-invariant (Property 7).
    """
    for metric in case.risk_aggregation.metrics:
        if metric.data_state_hash:
            return metric.data_state_hash
    return f"case:{case.case_id}"


def _first_version(metric_values: list[GovernedMetricValue]) -> str | None:
    """The metric-definition version to stamp on the answer's audit record (Req 8.2)."""
    for metric_value in metric_values:
        if metric_value.metric_definition_version:
            return metric_value.metric_definition_version
    return None


__all__ = [
    "InvestigationService",
    "InvestigationServiceImpl",
    "InvestigationResult",
    "Clarification",
    "Refusal",
    "ACTION_QUESTION_INTERPRETED",
    "ACTION_ANSWER_PRODUCED",
    "ACTION_CLARIFICATION_REQUESTED",
    "ACTION_QUESTION_REFUSED",
]
