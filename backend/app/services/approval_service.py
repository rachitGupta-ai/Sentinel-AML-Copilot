"""Approval / Human-in-the-Loop (HITL) Service (interface + ``Impl``).

No AI output becomes a material action without a human passing the Approval_Gate
(design §7; Req 7). This service owns the back half of the case lifecycle state
machine — ``RECOMMENDATION_READY → AWAITING_APPROVAL → {APPROVED, REJECTED}`` and,
on approval, ``APPROVED → {ACTION_COMPLETED, ACTION_FAILED}`` with a recovery path
back to ``AWAITING_APPROVAL`` — and enforces five invariants, each mapped to a
correctness property:

* **Approval_Gate before any material action (Property 25).**
  :meth:`simulate_action` rejects any case that has not reached ``APPROVED``; no
  action-completion state change and no (simulated) external side effect occurs
  before approval (Req 7.1, Req 6.5).
* **Complete review payload on entering AWAITING_APPROVAL (Property 26).**
  :meth:`submit_for_approval` transitions a ``RECOMMENDATION_READY`` case to
  ``AWAITING_APPROVAL`` and returns an :class:`ApprovalItem` carrying the
  recommendation (the SAR draft), the Groundedness_Score, the full lineage, and
  all Evidence_Items the reviewer needs (Req 7.2).
* **Legal state transitions for approve/reject (Property 27).** :meth:`approve`
  moves ``AWAITING_APPROVAL → APPROVED`` (never skipping ``APPROVED``) and
  :meth:`reject` moves ``AWAITING_APPROVAL → REJECTED`` capturing a non-empty
  reason, with no action side effect on rejection (Req 7.3, Req 7.4).
* **Reviewer identity + timestamp on every decision (Property 28).** Every
  approve/reject persists an :class:`~app.models.case.Approval` with a decision in
  ``{approved, rejected, overridden}``, a non-empty ``reviewer_id``, and a
  ``decided_at`` timestamp; a decision without a reviewer identity is rejected
  (Req 7.5, Req 7.6).
* **Idempotent action execution (Property 36).** An approved action executes **at
  most once**. Replaying :meth:`simulate_action` for the same case returns the
  recorded result and increments a suppressed-duplicate count rather than
  re-executing; for ``N`` invocations the suppressed-duplicate count is ``N − 1``
  (Req 9.6).

The service is deterministic and store-agnostic. Case persistence goes through
the :class:`~app.services.case_repository.CaseRepository` port (in-memory fake in
tests, ``APP.CASE`` in production); approval records go through an
:class:`ApprovalRepository` port; every transition emits an append-only audit
record through the injected :class:`~app.services.audit_service.AuditService`
(Req 8.1). Idempotency is keyed on ``case_id`` (not wall-clock time) via a
recorded :class:`ActionResult`, so the at-most-once guarantee holds across repeated
calls and is unit-testable without Snowflake.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.models.answer import SARDraft
from app.models.audit import Lineage
from app.models.case import Approval, Case, CaseState, Decision
from app.models.common import EvidenceItem
from app.services.audit_service import (
    SYSTEM_ACTOR,
    AuditService,
    AuditServiceImpl,
    build_audit_record,
)
from app.services.case_repository import CaseRepository, InMemoryCaseRepository

# ---------------------------------------------------------------------------
# Canonical audit action names.
#
# Kept stable and package-private so the audit trail is queryable and the
# approval property tests (Property 25–28, Property 36) can assert the recorded
# transitions.
# ---------------------------------------------------------------------------

# Recorded on RECOMMENDATION_READY -> AWAITING_APPROVAL (Req 7.2, Req 8.1).
ACTION_SUBMITTED_FOR_APPROVAL = "approval.submitted"

# Recorded on AWAITING_APPROVAL -> APPROVED (Req 7.3, Req 8.1).
ACTION_APPROVED = "approval.approved"

# Recorded on AWAITING_APPROVAL -> REJECTED (Req 7.4, Req 8.1).
ACTION_REJECTED = "approval.rejected"

# Recorded on APPROVED -> ACTION_COMPLETED (simulated filing succeeded) (Req 7.3).
ACTION_COMPLETED = "approval.action_completed"

# Recorded on APPROVED -> ACTION_FAILED (simulated failure, recovery retained)
# (Req 7.3).
ACTION_FAILED = "approval.action_failed"

# Recorded when a duplicate execution of an already-executed approved action is
# suppressed (idempotency) (Req 9.6, Property 36).
ACTION_DUPLICATE_SUPPRESSED = "approval.duplicate_suppressed"

# Default simulated-filing action name presented in the review payload and used
# as the executed action reference in audit records.
MATERIAL_ACTION_FILE_SAR = "sar.simulated_filing"

# Injectable simulated-action executor. Returns True for a successful simulated
# filing or False to exercise the ACTION_FAILED + recovery path (Req 7.3). Kept as
# a module-level alias so it can be referenced in annotations and injected in tests.
ActionExecutor = Callable[["Case"], bool]

# States from which a material action may be driven. The Approval_Gate requires a
# case to have reached APPROVED before any action occurs (Req 7.1, Property 25).
# ACTION_FAILED is included so a recovery/retry can re-attempt the action; the
# recovery path first returns the case to AWAITING_APPROVAL (see design state
# machine: ACTION_FAILED --> AWAITING_APPROVAL).
_ACTIONABLE_STATES = frozenset({CaseState.APPROVED, CaseState.ACTION_FAILED})


class ApprovalError(RuntimeError):
    """Raised when an approval operation is attempted from an illegal state.

    Signals a violated precondition — e.g. submitting a case that is not
    ``RECOMMENDATION_READY``, approving a case that is not ``AWAITING_APPROVAL``,
    or driving a material action before the Approval_Gate has passed. The message
    identifies the operation and the offending state so the caller/UI can surface
    a clear error (Req 7.1, Property 25).
    """


class ApprovalItem(BaseModel):
    """The complete review payload presented when a case enters AWAITING_APPROVAL.

    Carries everything a reviewer needs to make an informed, auditable decision:
    the ``recommendation`` (the SAR draft), the ``groundedness_score`` behind it,
    the full ``lineage`` reconstructed from audit records, and all
    ``evidence_items`` cited (Req 7.2, Property 26). ``material_action`` names the
    action that approval authorises (default: simulated SAR filing); nothing is
    executed by constructing this item — it is a presentation payload only
    (Property 25).
    """

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(description="Case awaiting approval.")
    recommendation: SARDraft = Field(
        description="The SAR-draft recommendation presented to the reviewer (Req 7.2).",
    )
    groundedness_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Groundedness score behind the recommendation (Req 7.2).",
    )
    lineage: Lineage = Field(
        description="Full lineage reconstructed from audit records alone (Req 7.2, Req 8.4).",
    )
    evidence_items: list[EvidenceItem] = Field(
        default_factory=list,
        description="All Evidence_Items supporting the recommendation (Req 7.2).",
    )
    material_action: str = Field(
        default=MATERIAL_ACTION_FILE_SAR,
        description="The material action approval authorises (nothing runs yet, Property 25).",
    )


class ActionResult(BaseModel):
    """The recorded outcome of a (simulated) material action for a case.

    The service persists exactly one effective :class:`ActionResult` per case; the
    at-most-once idempotency guarantee is realised by returning this recorded
    result on any subsequent :meth:`ApprovalService.simulate_action` call and
    incrementing ``suppressed_duplicate_count`` instead of re-executing (Req 9.6,
    Property 36). ``succeeded`` reflects the simulated filing outcome; a failed
    action leaves the case in ``ACTION_FAILED`` with a recovery path retained
    (Req 7.3).
    """

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(description="Case the action was executed for.")
    action: str = Field(description="The material action that was executed.")
    succeeded: bool = Field(description="True when the simulated action completed successfully.")
    final_state: CaseState = Field(
        description="Case state after the action: ACTION_COMPLETED or ACTION_FAILED.",
    )
    executed_at: datetime = Field(description="UTC timestamp of the effective execution.")
    suppressed_duplicate_count: int = Field(
        default=0,
        ge=0,
        description="Count of suppressed duplicate executions; equals N-1 after N calls (Req 9.6).",
    )


@runtime_checkable
class ApprovalRepository(Protocol):
    """Persistence port for :class:`Approval` decision records (Req 7.5).

    Every approve/reject decision is appended here so that an auditable,
    queryable record of reviewer identity, timestamp, decision, and reason exists
    independent of the case's current state (Property 28).
    """

    def append(self, approval: Approval) -> None:
        """Append an approval decision record."""
        ...

    def list_for_case(self, case_id: str) -> list[Approval]:
        """Return all approval decisions recorded for ``case_id`` in insertion order."""
        ...


class InMemoryApprovalRepository:
    """In-memory, deterministic :class:`ApprovalRepository` for tests and local runs."""

    def __init__(self) -> None:
        self._by_case: dict[str, list[Approval]] = {}
        self._lock = threading.Lock()

    def append(self, approval: Approval) -> None:
        with self._lock:
            self._by_case.setdefault(approval.case_id, []).append(approval)

    def list_for_case(self, case_id: str) -> list[Approval]:
        with self._lock:
            return list(self._by_case.get(case_id, []))


@runtime_checkable
class ApprovalService(Protocol):
    """Human-in-the-loop approval and governed-action execution (design §7; Req 7)."""

    def submit_for_approval(self, case: Case) -> ApprovalItem:
        """Transition ``RECOMMENDATION_READY → AWAITING_APPROVAL`` and present the review payload.

        Returns an :class:`ApprovalItem` carrying the recommendation, the
        Groundedness_Score, the full lineage, and all Evidence_Items (Req 7.2,
        Property 26). Raises :class:`ApprovalError` if the case is not in
        ``RECOMMENDATION_READY``. The transition is audited (Req 8.1).
        """
        ...

    def approve(self, case: Case, reviewer: str) -> Case:
        """Approve an ``AWAITING_APPROVAL`` case, moving it to ``APPROVED`` (Req 7.3).

        Records an :class:`Approval` with the reviewer identity and timestamp
        (Property 28). No material action is executed here — approval only *admits*
        the subsequent :meth:`simulate_action` through the Approval_Gate
        (Property 25). Raises :class:`ApprovalError` on an illegal source state or
        a missing reviewer identity (Req 7.6).
        """
        ...

    def reject(self, case: Case, reviewer: str, reason: str) -> Case:
        """Reject an ``AWAITING_APPROVAL`` case, moving it to ``REJECTED`` (Req 7.4).

        Captures a non-empty ``reason`` and the reviewer identity + timestamp, and
        performs **no** material action (Property 27). Raises :class:`ApprovalError`
        on an illegal source state, a missing reviewer identity, or an empty reason.
        """
        ...

    def simulate_action(self, case: Case) -> ActionResult:
        """Execute the approved material action safely and idempotently (Req 7.3, Req 9.6).

        Enforces the Approval_Gate: raises :class:`ApprovalError` unless the case
        has reached ``APPROVED`` (Property 25). On first execution it drives
        ``APPROVED → ACTION_COMPLETED`` (or ``ACTION_FAILED`` on simulated failure,
        retaining a recovery path). Subsequent calls for the same case do **not**
        re-execute: they return the recorded result with an incremented
        ``suppressed_duplicate_count`` (``N − 1`` after ``N`` calls, Property 36).
        """
        ...


class ApprovalServiceImpl:
    """Default :class:`ApprovalService` backed by injected ports and services.

    Case persistence goes through the :class:`CaseRepository` port; approval
    decisions through the :class:`ApprovalRepository` port; every transition is
    audited through the :class:`AuditService`. When a collaborator is not supplied
    an in-memory / default implementation is used, convenient for local runs and
    unit tests.

    Idempotency state (the one effective :class:`ActionResult` per case and the
    suppressed-duplicate counter) is held in-process and guarded by a lock so the
    at-most-once guarantee is deterministic and keyed on ``case_id`` rather than
    wall-clock time (Property 36).

    An optional ``action_executor`` lets callers/tests inject the simulated action
    outcome deterministically: it is invoked with the approved :class:`Case` and
    returns ``True`` for a successful simulated filing or ``False`` to exercise the
    ``ACTION_FAILED`` + recovery path. The default executor always succeeds.
    """

    def __init__(
        self,
        *,
        audit_service: Optional[AuditService] = None,
        case_repository: Optional[CaseRepository] = None,
        approval_repository: Optional[ApprovalRepository] = None,
        action_executor: Optional[ActionExecutor] = None,
    ) -> None:
        self._audit: AuditService = (
            audit_service if audit_service is not None else AuditServiceImpl()
        )
        self._cases: CaseRepository = (
            case_repository if case_repository is not None else InMemoryCaseRepository()
        )
        self._approvals: ApprovalRepository = (
            approval_repository if approval_repository is not None else InMemoryApprovalRepository()
        )
        self._action_executor: ActionExecutor = (
            action_executor if action_executor is not None else _default_action_executor
        )
        # case_id -> recorded effective ActionResult (idempotency ledger).
        self._executed: dict[str, ActionResult] = {}
        self._lock = threading.Lock()

    def submit_for_approval(self, case: Case) -> ApprovalItem:
        """See :meth:`ApprovalService.submit_for_approval`."""
        if case.state is not CaseState.RECOMMENDATION_READY:
            raise ApprovalError(
                "submit_for_approval requires state RECOMMENDATION_READY, "
                f"got {case.state.value!r} for case {case.case_id!r}."
            )
        transitioned = self._transition(case, CaseState.AWAITING_APPROVAL)
        item = self._build_approval_item(transitioned)
        self._audit.append(
            build_audit_record(
                action=ACTION_SUBMITTED_FOR_APPROVAL,
                actor_id=SYSTEM_ACTOR,
                case_ref=transitioned.case_id,
                entity_ref=transitioned.entity_id,
                input_refs=[transitioned.case_id],
                output_refs=[item.material_action],
            )
        )
        return item

    def approve(self, case: Case, reviewer: str) -> Case:
        """See :meth:`ApprovalService.approve`."""
        self._require_reviewer(reviewer)
        if case.state is not CaseState.AWAITING_APPROVAL:
            raise ApprovalError(
                "approve requires state AWAITING_APPROVAL, "
                f"got {case.state.value!r} for case {case.case_id!r}."
            )
        approved = self._transition(case, CaseState.APPROVED)
        self._record_decision(approved, reviewer=reviewer, decision="approved", reason=None)
        self._audit.append(
            build_audit_record(
                action=ACTION_APPROVED,
                actor_id=reviewer,
                case_ref=approved.case_id,
                entity_ref=approved.entity_id,
                input_refs=[approved.case_id],
                output_refs=[CaseState.APPROVED.value],
            )
        )
        return approved

    def reject(self, case: Case, reviewer: str, reason: str) -> Case:
        """See :meth:`ApprovalService.reject`."""
        self._require_reviewer(reviewer)
        if reason is None or not reason.strip():
            raise ApprovalError(
                f"reject requires a non-empty reason for case {case.case_id!r} (Req 7.4)."
            )
        if case.state is not CaseState.AWAITING_APPROVAL:
            raise ApprovalError(
                "reject requires state AWAITING_APPROVAL, "
                f"got {case.state.value!r} for case {case.case_id!r}."
            )
        rejected = self._transition(case, CaseState.REJECTED)
        self._record_decision(rejected, reviewer=reviewer, decision="rejected", reason=reason)
        self._audit.append(
            build_audit_record(
                action=ACTION_REJECTED,
                actor_id=reviewer,
                case_ref=rejected.case_id,
                entity_ref=rejected.entity_id,
                input_refs=[rejected.case_id],
                output_refs=[CaseState.REJECTED.value],
            )
        )
        return rejected

    def simulate_action(self, case: Case) -> ActionResult:
        """See :meth:`ApprovalService.simulate_action`."""
        with self._lock:
            previous = self._executed.get(case.case_id)
            if previous is not None:
                # At-most-once: the action already ran for this case, so do not
                # re-execute regardless of the current state (the case may now be
                # ACTION_COMPLETED/ACTION_FAILED). Record a suppressed duplicate and
                # return the recorded result with an incremented count so that after
                # N calls the suppressed-duplicate count is N-1 (Property 36).
                suppressed = previous.model_copy(
                    update={
                        "suppressed_duplicate_count": previous.suppressed_duplicate_count + 1
                    }
                )
                self._executed[case.case_id] = suppressed
                self._audit.append(
                    build_audit_record(
                        action=ACTION_DUPLICATE_SUPPRESSED,
                        actor_id=SYSTEM_ACTOR,
                        case_ref=case.case_id,
                        entity_ref=case.entity_id,
                        input_refs=[case.case_id, previous.action],
                        output_refs=[previous.final_state.value],
                    )
                )
                return suppressed

            # Approval_Gate: a first execution requires a passed gate — the case
            # must have reached APPROVED (ACTION_FAILED is permitted for a
            # recovery/retry). No material action occurs before approval
            # (Property 25, Req 7.1). Checked inside the lock, before marking the
            # ledger, so a pre-approval call never records an execution.
            if case.state not in _ACTIONABLE_STATES:
                raise ApprovalError(
                    "simulate_action requires a passed Approval_Gate (state APPROVED); "
                    f"got {case.state.value!r} for case {case.case_id!r} — no action performed."
                )

            # First effective execution for this case.
            succeeded = bool(self._action_executor(case))
            final_state = (
                CaseState.ACTION_COMPLETED if succeeded else CaseState.ACTION_FAILED
            )
            result = ActionResult(
                case_id=case.case_id,
                action=MATERIAL_ACTION_FILE_SAR,
                succeeded=succeeded,
                final_state=final_state,
                executed_at=_now_utc(),
                suppressed_duplicate_count=0,
            )
            self._executed[case.case_id] = result

        # Persist the lifecycle transition and audit it outside the idempotency
        # lock (persistence/audit ports have their own synchronisation).
        self._transition(case, final_state)
        self._audit.append(
            build_audit_record(
                action=ACTION_COMPLETED if succeeded else ACTION_FAILED,
                actor_id=SYSTEM_ACTOR,
                case_ref=case.case_id,
                entity_ref=case.entity_id,
                input_refs=[case.case_id, result.action],
                output_refs=[final_state.value],
            )
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_reviewer(reviewer: str) -> None:
        """Reject a decision made without a reviewer identity (Req 7.6, Property 28)."""
        if reviewer is None or not reviewer.strip():
            raise ApprovalError("An approval decision requires a reviewer identity (Req 7.6).")

    def _transition(self, case: Case, new_state: CaseState) -> Case:
        """Produce and persist ``case`` moved to ``new_state`` (frozen -> model_copy).

        The case is persisted through the repository's state-preserving ``save``
        so the correlation group is retained. If the case was not previously
        stored (e.g. a unit test passes a freshly-constructed case), it is created
        via ``upsert`` keyed by its ``case_id`` so the transition still persists.
        """
        updated = case.model_copy(update={"state": new_state})
        try:
            self._cases.save(updated)
        except KeyError:
            # Case not yet in the store (common in focused unit tests). Persist it
            # so the lifecycle state is recorded; use the case_id as a stand-in
            # correlation key since none is known here.
            self._cases.upsert(updated, updated.case_id)
        return updated

    def _record_decision(
        self, case: Case, *, reviewer: str, decision: Decision, reason: str | None
    ) -> None:
        """Persist an :class:`Approval` record for an approve/reject (Property 28)."""
        self._approvals.append(
            Approval(
                case_id=case.case_id,
                reviewer_id=reviewer,
                decision=decision,
                reason=reason,
                decided_at=_now_utc(),
            )
        )

    def _build_approval_item(self, case: Case) -> ApprovalItem:
        """Assemble the complete review payload for ``case`` (Req 7.2, Property 26).

        The recommendation (SAR draft), groundedness score, and evidence are taken
        from the case's current recommendation state where available; the lineage
        is reconstructed from audit records alone via the audit service so the
        reviewer sees the full, replayable chain (Req 8.4). A minimal, clearly
        AI-generated-decision-support SAR placeholder is used when the case does
        not yet carry a persisted draft, so the payload is always complete.
        """
        lineage = self._audit.replay(case.case_id)
        recommendation = _recommendation_for(case)
        score = _groundedness_for(case)
        evidence = _evidence_for(case)
        return ApprovalItem(
            case_id=case.case_id,
            recommendation=recommendation,
            groundedness_score=score,
            lineage=lineage,
            evidence_items=evidence,
            material_action=MATERIAL_ACTION_FILE_SAR,
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _default_action_executor(_case: Case) -> bool:
    """Default simulated-filing executor: the action always succeeds.

    Replaceable via ``ApprovalServiceImpl(action_executor=...)`` to deterministically
    exercise the ``ACTION_FAILED`` + recovery path (Req 7.3).
    """
    return True


def _recommendation_for(case: Case) -> SARDraft:
    """Best-effort recommendation for the review payload.

    Returns the case's attached SAR draft when present; otherwise a minimal
    placeholder that is always AI-generated decision support and never filing-ready
    (Req 6.4, Property 24), so the review payload is structurally complete even
    before a full draft is persisted on the case.
    """
    draft = getattr(case, "sar_draft", None)
    if isinstance(draft, SARDraft):
        return draft
    return SARDraft(
        case_id=case.case_id,
        entity_summary=f"Entity {case.entity_id} under review for case {case.case_id}.",
        suspicious_pattern="Recommendation pending full SAR draft.",
        completeness="incomplete",
    )


def _groundedness_for(case: Case) -> float:
    """Groundedness score to present with the recommendation (Req 7.2).

    Reads a score attached to the case/recommendation when available, else ``0.0``
    (a conservative default that routes attention to review).
    """
    score = getattr(case, "groundedness_score", None)
    if isinstance(score, (int, float)) and 0.0 <= float(score) <= 1.0:
        return float(score)
    return 0.0


def _evidence_for(case: Case) -> list[EvidenceItem]:
    """Evidence items to present with the recommendation (Req 7.2)."""
    evidence = getattr(case, "evidence_items", None)
    if isinstance(evidence, list) and all(isinstance(e, EvidenceItem) for e in evidence):
        return list(evidence)
    return []


def _now_utc() -> datetime:
    """Current time as a timezone-aware UTC timestamp (Req 7.5)."""
    return datetime.now(timezone.utc)
