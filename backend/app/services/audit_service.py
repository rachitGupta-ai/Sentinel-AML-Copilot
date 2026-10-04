"""Immutable Audit Service (interface + ``Impl``).

Every auditable operation across the workflow — state transition, AI call,
metric computation, evidence retrieval, approval, simulated action — appends a
complete, append-only :class:`AuditRecord` through this service (design §8;
Req 8.1, Property 29). The service guarantees three things:

* **Append-only** — :meth:`AuditService.append` persists a new record and never
  overwrites an existing one. Each record carries all required fields: a UTC
  timestamp, case/entity references, actor id, action, input/output refs, and the
  ``metric_definition_version`` where a governed figure is involved (Req 8.1,
  Req 8.2, Property 29).
* **Immutability** — there is no update/delete path. An attempt to mutate an
  existing record is itself recorded as a *new* audit record via
  :meth:`AuditService.reject_mutation`, leaving the original untouched (Req 8.3,
  Property 30).
* **Replayable lineage** — :meth:`AuditService.replay` reconstructs a case's full
  :class:`Lineage` from audit records *alone*, with no other store consulted
  (Req 8.4, Property 31).

The service writes through an :class:`AuditRecordRepository` port so Snowflake
access is abstracted and testable (design §8). Later services (triage,
investigation, grounding, SAR, approval, kpi) emit audit records by calling this
service, which is why it is implemented early (tasks note; wave 2).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Protocol, Sequence, runtime_checkable

from app.models.audit import AuditRecord, Lineage
from app.services.audit_repository import (
    AuditRecordRepository,
    InMemoryAuditRecordRepository,
)

# Actor used when the system (not a human) performs an auditable operation
# (design "Data Models": actor is "user or system").
SYSTEM_ACTOR = "system"

# Canonical action name recorded when a mutation/deletion of an existing audit
# record is attempted and rejected (Req 8.3, Property 30). Kept stable so the
# rejected-attempt records are queryable and the property test can assert them.
ACTION_MUTATION_REJECTED = "audit.mutation_rejected"


@runtime_checkable
class AuditService(Protocol):
    """Append-only audit trail with lineage replay (design §8; Req 8).

    Implementations persist an immutable record per auditable operation, reject
    (and audit) any mutation attempt, and reconstruct a case's full lineage from
    audit records alone.
    """

    def append(self, record: AuditRecord) -> str:
        """Append ``record`` to the audit trail and return its ``audit_id``.

        The record is persisted exactly as given; it is never merged into or used
        to overwrite an existing record (Req 8.1, Req 8.3).
        """
        ...

    def reject_mutation(self, target_id: str, actor: str) -> str:
        """Record a rejected attempt to modify/delete audit record ``target_id``.

        The original record is **not** altered or removed. A new audit record is
        appended describing the rejected attempt (action
        :data:`ACTION_MUTATION_REJECTED`, ``input_refs=[target_id]``, actor =
        ``actor``) and its id is returned (Req 8.3, Property 30).
        """
        ...

    def replay(self, case_id: str) -> Lineage:
        """Reconstruct the full :class:`Lineage` of ``case_id`` from audit records.

        Uses only append-only audit records (no other store), ordered
        chronologically, so the chain question → metric → query → rows →
        calculation → narrative → approval → outcome is reproducible (Req 8.4,
        Property 31).
        """
        ...


class AuditServiceImpl:
    """Default :class:`AuditService` backed by an :class:`AuditRecordRepository`.

    All persistence goes through the injected repository port, so the same logic
    runs against Snowflake (``AUDIT.AUDIT_RECORD``) in production and against an
    in-memory store in tests (design §8). When no repository is supplied an
    :class:`InMemoryAuditRecordRepository` is used, which is convenient for local
    runs and unit tests.
    """

    def __init__(self, repository: Optional[AuditRecordRepository] = None) -> None:
        self._repository: AuditRecordRepository = (
            repository if repository is not None else InMemoryAuditRecordRepository()
        )

    def append(self, record: AuditRecord) -> str:
        """Persist ``record`` append-only; see :meth:`AuditService.append`."""
        return self._repository.append(record)

    def reject_mutation(self, target_id: str, actor: str) -> str:
        """Audit a rejected mutation attempt; see :meth:`AuditService.reject_mutation`.

        Crucially, this method reads nothing destructive and writes only a *new*
        record. The targeted record is referenced via ``input_refs`` and is left
        byte-for-byte unchanged (Req 8.3, Property 30). The case/entity references
        of the targeted record, when it exists, are copied onto the rejection
        record so the rejection shows up in the right case's lineage.
        """
        target = self._repository.get(target_id)
        rejection = AuditRecord(
            audit_id=_new_audit_id(),
            timestamp_utc=_now_utc(),
            case_ref=target.case_ref if target is not None else None,
            entity_ref=target.entity_ref if target is not None else None,
            actor_id=actor or SYSTEM_ACTOR,
            action=ACTION_MUTATION_REJECTED,
            input_refs=[target_id],
            output_refs=[],
            metric_definition_version=None,
        )
        return self._repository.append(rejection)

    def replay(self, case_id: str) -> Lineage:
        """Reconstruct ``case_id``'s lineage; see :meth:`AuditService.replay`."""
        records = self._repository.list_for_case(case_id)
        return Lineage(case_id=case_id, records=records)


def _now_utc() -> datetime:
    """Current time as a timezone-aware UTC timestamp (Req 8.2)."""
    return datetime.now(timezone.utc)


def _new_audit_id() -> str:
    """Generate a stable, unique audit-record id."""
    return str(uuid.uuid4())


def build_audit_record(
    *,
    action: str,
    actor_id: str = SYSTEM_ACTOR,
    case_ref: str | None = None,
    entity_ref: str | None = None,
    input_refs: Sequence[str] | None = None,
    output_refs: Sequence[str] | None = None,
    metric_definition_version: str | None = None,
    audit_id: str | None = None,
    timestamp_utc: datetime | None = None,
) -> AuditRecord:
    """Convenience factory producing a complete :class:`AuditRecord` (Req 8.2).

    Callers across the service layer use this to emit audit records without
    repeating id/timestamp boilerplate. The UTC timestamp and a unique id are
    generated when not supplied, and ``input_refs`` / ``output_refs`` default to
    empty lists — so every produced record satisfies the "complete record" field
    requirements (Property 29).

    Args:
        action: The operation/transition being audited (required, non-empty).
        actor_id: User or system id performing the operation; defaults to the
            system actor.
        case_ref: Case this record relates to, when applicable.
        entity_ref: Entity this record relates to, when applicable.
        input_refs: References to the operation's inputs.
        output_refs: References to the operation's outputs.
        metric_definition_version: Metric-definition version where a governed
            figure is involved (Req 8.2).
        audit_id: Explicit id; generated when omitted.
        timestamp_utc: Explicit UTC timestamp; generated when omitted.

    Returns:
        A fully-populated, immutable :class:`AuditRecord`.
    """
    return AuditRecord(
        audit_id=audit_id or _new_audit_id(),
        timestamp_utc=timestamp_utc or _now_utc(),
        case_ref=case_ref,
        entity_ref=entity_ref,
        actor_id=actor_id or SYSTEM_ACTOR,
        action=action,
        input_refs=list(input_refs) if input_refs is not None else [],
        output_refs=list(output_refs) if output_refs is not None else [],
        metric_definition_version=metric_definition_version,
    )
