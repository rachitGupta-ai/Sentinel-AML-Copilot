"""Investigate router: NL investigation over a case (Req 5).

Exposes the NL Investigation Service behind two routes:

* ``POST /api/investigate/{case_id}/interpret`` — show the semantic interpretation
  (resolved metrics/entities, ambiguity) *before* answering (Req 5.1).
* ``POST /api/investigate/{case_id}`` — answer the question, returning an
  :class:`~app.models.answer.Answer`, a ``Clarification`` (ambiguous, Req 5.2), or
  a ``Refusal`` (out-of-scope / ungroundable, Req 5.6). Governed narrative is kept
  distinct from governed facts and nothing ungrounded is presented as fact
  (Property 16, Property 18).

When an :class:`~app.models.answer.Answer` is produced, its AI-quality telemetry
is recorded through the KPI service — connecting investigation to observability
end to end so the system-health / AI-quality view and KPIs reflect real answers
(Req 10.2). Both routes require the ``VIEW_CASE`` entitlement; denials are audited
(Req 9.2).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import AppServices, get_services, load_case, require_action
from app.api.schemas import InvestigateRequest
from app.models.answer import Answer
from app.models.common import SemanticInterpretation
from app.services.auth_service import ProtectedAction

router = APIRouter(
    prefix="/api/investigate",
    tags=["investigate"],
    dependencies=[Depends(require_action(ProtectedAction.VIEW_CASE))],
)


@router.post("/{case_id}/interpret", response_model=SemanticInterpretation)
async def interpret_question(
    case_id: str,
    body: InvestigateRequest,
    services: AppServices = Depends(get_services),
) -> SemanticInterpretation:
    """Show how the question is interpreted before answering (Req 5.1)."""
    case = load_case(services, case_id)
    return services.investigation_service.interpret(body.question, case)


@router.post("/{case_id}", response_model=None)
async def answer_question(
    case_id: str,
    body: InvestigateRequest,
    services: AppServices = Depends(get_services),
):
    """Answer an NL question, or clarify / refuse (Req 5.2, 5.5, 5.6).

    Returns a grounded :class:`~app.models.answer.Answer`, a ``Clarification`` for
    an ambiguous question, or a ``Refusal`` for an out-of-scope / ungroundable one.
    When an answer is produced its telemetry is recorded through the KPI service
    (Req 10.2). The response is the service's union result serialised as-is; the
    caller discriminates on the shape (an answer carries ``status``, a
    clarification ``candidate_terms``, a refusal ``reason``).
    """
    case = load_case(services, case_id)
    result = services.investigation_service.answer(body.question, case)
    if isinstance(result, Answer):
        # Connect investigation -> observability: record per-answer telemetry so
        # the AI-quality view / KPIs reflect this real answer (Req 10.2).
        services.kpi_service.record_answer(result)
    return result
