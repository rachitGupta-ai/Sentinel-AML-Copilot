"""Unit tests for the Approval / HITL Service (task 15.1).

Cover the Approval_Gate, legal state transitions, review-payload completeness,
reviewer-identity requirement, and at-most-once idempotency with concrete
examples, using the real in-memory ports (``InMemoryCaseRepository``,
``InMemoryAuditRecordRepository``, ``InMemoryApprovalRepository``) so no Snowflake
connection is required:

* No material action occurs before ``APPROVED`` (Req 7.1, Req 6.5, Property 25).
* ``submit_for_approval`` moves ``RECOMMENDATION_READY → AWAITING_APPROVAL`` with a
  complete review payload (Req 7.2, Property 26).
* approve/reject follow legal transitions; reject captures a reason, no action
  side effect (Req 7.3, Req 7.4, Property 27).
* Every decision records reviewer identity + timestamp; a missing reviewer is
  rejected (Req 7.5, Req 7.6, Property 28).
* An approved action executes at most once; N calls -> suppressed count N-1
  (Req 9.6, Property 36).

Every state transition emits an audit record (Req 8.1).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.case import Case, CaseState
from app.models.governed import EntityRiskView
from app.services.approval_service import (
    ACTION_APPROVED,
    ACTION_COMPLETED,
    ACTION_DUPLICATE_SUPPRESSED,
    ACTION_FAILED,
    ACTION_REJECTED,
    ACTION_SUBMITTED_FOR_APPROVAL,
    ApprovalError,
    ApprovalServiceImpl,
    InMemoryApprovalRepository,
)
from app.services.audit_repository import InMemoryAuditRecordRepository
from app.services.audit_service import AuditServiceImpl
from app.services.case_repository import InMemoryCaseRepository


def _case(state: CaseState, *, case_id: str = "case-1", entity_id: str = "entity-1") -> Case:
    """Build a case in ``state`` with a minimal governed risk aggregation."""
    return Case(
        case_id=case_id,
        entity_id=entity_id,
        state=state,
        risk_aggregation=EntityRiskView(entity_id=entity_id, aggregate_score=Decimal("0.9")),
    )


def _service(
    *,
    audit_repo: InMemoryAuditRecordRepository | None = None,
    case_repo: InMemoryCaseRepository | None = None,
    approval_repo: InMemoryApprovalRepository | None = None,
    action_executor=None,
) -> tuple[ApprovalServiceImpl, InMemoryAuditRecordRepository, InMemoryCaseRepository, InMemoryApprovalRepository]:
    audit_repo = audit_repo or InMemoryAuditRecordRepository()
    case_repo = case_repo or InMemoryCaseRepository()
    approval_repo = approval_repo or InMemoryApprovalRepository()
    service = ApprovalServiceImpl(
        audit_service=AuditServiceImpl(repository=audit_repo),
        case_repository=case_repo,
        approval_repository=approval_repo,
        action_executor=action_executor,
    )
    return service, audit_repo, case_repo, approval_repo


def _actions(audit_repo: InMemoryAuditRecordRepository, case_id: str) -> list[str]:
    return [r.action for r in audit_repo.list_for_case(case_id)]


# ---------------------------------------------------------------------------
# Property 25 — Approval_Gate: no material action before APPROVED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [
        CaseState.RECEIVED,
        CaseState.TRIAGED,
        CaseState.INVESTIGATING,
        CaseState.RECOMMENDATION_READY,
        CaseState.AWAITING_APPROVAL,
        CaseState.REJECTED,
    ],
)
def test_simulate_action_rejected_before_approval(state: CaseState) -> None:
    service, audit_repo, _, _ = _service()
    with pytest.raises(ApprovalError):
        service.simulate_action(_case(state))
    # No action-completion transition was recorded.
    assert ACTION_COMPLETED not in _actions(audit_repo, "case-1")
    assert ACTION_FAILED not in _actions(audit_repo, "case-1")


# ---------------------------------------------------------------------------
# Property 26 — entering AWAITING_APPROVAL presents a complete review payload
# ---------------------------------------------------------------------------


def test_submit_for_approval_transitions_and_presents_payload() -> None:
    service, audit_repo, case_repo, _ = _service()
    case = _case(CaseState.RECOMMENDATION_READY)
    case_repo.upsert(case, "corr-1")

    item = service.submit_for_approval(case)

    assert item.case_id == case.case_id
    # Payload carries recommendation, score, lineage, evidence (Req 7.2).
    assert item.recommendation.case_id == case.case_id
    assert item.recommendation.is_ai_generated_decision_support is True
    assert 0.0 <= item.groundedness_score <= 1.0
    assert item.lineage.case_id == case.case_id
    assert isinstance(item.evidence_items, list)
    assert item.material_action
    # Persisted transition + audit record.
    assert case_repo.get(case.case_id).state is CaseState.AWAITING_APPROVAL
    assert ACTION_SUBMITTED_FOR_APPROVAL in _actions(audit_repo, case.case_id)


def test_submit_for_approval_requires_recommendation_ready() -> None:
    service, _, _, _ = _service()
    with pytest.raises(ApprovalError):
        service.submit_for_approval(_case(CaseState.INVESTIGATING))


# ---------------------------------------------------------------------------
# Property 27 — approve/reject follow legal transitions
# ---------------------------------------------------------------------------


def test_approve_moves_to_approved_without_executing_action() -> None:
    service, audit_repo, case_repo, _ = _service()
    case = _case(CaseState.AWAITING_APPROVAL)
    case_repo.upsert(case, "corr-1")

    approved = service.approve(case, reviewer="officer-1")

    assert approved.state is CaseState.APPROVED
    assert case_repo.get(case.case_id).state is CaseState.APPROVED
    actions = _actions(audit_repo, case.case_id)
    assert ACTION_APPROVED in actions
    # Approval alone performs no material action.
    assert ACTION_COMPLETED not in actions


def test_reject_moves_to_rejected_capturing_reason_no_action() -> None:
    service, audit_repo, case_repo, approval_repo = _service()
    case = _case(CaseState.AWAITING_APPROVAL)
    case_repo.upsert(case, "corr-1")

    rejected = service.reject(case, reviewer="officer-1", reason="Insufficient evidence")

    assert rejected.state is CaseState.REJECTED
    decisions = approval_repo.list_for_case(case.case_id)
    assert len(decisions) == 1
    assert decisions[0].decision == "rejected"
    assert decisions[0].reason == "Insufficient evidence"
    actions = _actions(audit_repo, case.case_id)
    assert ACTION_REJECTED in actions
    assert ACTION_COMPLETED not in actions


def test_reject_requires_non_empty_reason() -> None:
    service, _, _, _ = _service()
    case = _case(CaseState.AWAITING_APPROVAL)
    with pytest.raises(ApprovalError):
        service.reject(case, reviewer="officer-1", reason="   ")


@pytest.mark.parametrize("state", [CaseState.RECEIVED, CaseState.APPROVED, CaseState.CLOSED])
def test_approve_rejects_illegal_source_state(state: CaseState) -> None:
    service, _, _, _ = _service()
    with pytest.raises(ApprovalError):
        service.approve(_case(state), reviewer="officer-1")


def test_approve_then_simulate_action_completes() -> None:
    service, audit_repo, case_repo, _ = _service()
    case = _case(CaseState.AWAITING_APPROVAL)
    case_repo.upsert(case, "corr-1")

    approved = service.approve(case, reviewer="officer-1")
    result = service.simulate_action(approved)

    assert result.succeeded is True
    assert result.final_state is CaseState.ACTION_COMPLETED
    assert case_repo.get(case.case_id).state is CaseState.ACTION_COMPLETED
    assert ACTION_COMPLETED in _actions(audit_repo, case.case_id)


def test_simulated_failure_lands_in_action_failed_with_recovery() -> None:
    # Injected executor fails -> ACTION_FAILED, with a recovery path retained.
    service, audit_repo, case_repo, _ = _service(action_executor=lambda _case: False)
    case = _case(CaseState.APPROVED)
    case_repo.upsert(case, "corr-1")

    result = service.simulate_action(case)

    assert result.succeeded is False
    assert result.final_state is CaseState.ACTION_FAILED
    assert ACTION_FAILED in _actions(audit_repo, case.case_id)


# ---------------------------------------------------------------------------
# Property 28 — every decision records reviewer identity + timestamp
# ---------------------------------------------------------------------------


def test_decision_records_reviewer_and_timestamp() -> None:
    service, _, case_repo, approval_repo = _service()
    case = _case(CaseState.AWAITING_APPROVAL)
    case_repo.upsert(case, "corr-1")

    service.approve(case, reviewer="officer-7")

    decisions = approval_repo.list_for_case(case.case_id)
    assert len(decisions) == 1
    assert decisions[0].reviewer_id == "officer-7"
    assert decisions[0].decision == "approved"
    assert decisions[0].decided_at is not None


@pytest.mark.parametrize("reviewer", ["", "   "])
def test_decision_without_reviewer_identity_is_rejected(reviewer: str) -> None:
    service, _, case_repo, _ = _service()
    case = _case(CaseState.AWAITING_APPROVAL)
    case_repo.upsert(case, "corr-1")
    with pytest.raises(ApprovalError):
        service.approve(case, reviewer=reviewer)
    with pytest.raises(ApprovalError):
        service.reject(case, reviewer=reviewer, reason="n/a")


# ---------------------------------------------------------------------------
# Property 36 — approved actions execute at most once (idempotency)
# ---------------------------------------------------------------------------


def test_simulate_action_executes_at_most_once() -> None:
    executions = {"count": 0}

    def _executor(_case: Case) -> bool:
        executions["count"] += 1
        return True

    service, audit_repo, case_repo, _ = _service(action_executor=_executor)
    case = _case(CaseState.APPROVED)
    case_repo.upsert(case, "corr-1")

    n = 4
    last = None
    for _ in range(n):
        last = service.simulate_action(case)

    # Exactly one effective execution regardless of N calls.
    assert executions["count"] == 1
    # Suppressed-duplicate count equals N-1 (Property 36).
    assert last.suppressed_duplicate_count == n - 1
    # One completion record, N-1 suppression records.
    actions = _actions(audit_repo, case.case_id)
    assert actions.count(ACTION_COMPLETED) == 1
    assert actions.count(ACTION_DUPLICATE_SUPPRESSED) == n - 1
    # Final state is terminal ACTION_COMPLETED and unchanged by replays.
    assert case_repo.get(case.case_id).state is CaseState.ACTION_COMPLETED
