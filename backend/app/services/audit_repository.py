"""Persistence port for the immutable audit trail.

The :class:`AuditService` writes and reads audit records through an
``AuditRecordRepository`` *port* rather than touching Snowflake directly. This
keeps the service's append-only / replay logic independent of the physical store
and makes it unit-testable with the in-memory adapter, while the Snowflake
adapter persists to the append-only ``AUDIT.AUDIT_RECORD`` table (design §8;
Req 8).

Two adapters are provided:

* :class:`InMemoryAuditRecordRepository` — a deterministic, insertion-ordered
  store for tests and local runs. It enforces append-only semantics in-process
  (duplicate ``audit_id`` is rejected; there is no update/delete surface).
* :class:`SnowflakeAuditRecordRepository` — writes through the Snowpark session
  to ``AUDIT.AUDIT_RECORD`` using INSERT + SELECT only. Append-only is ultimately
  enforced at the Snowflake grant level (INSERT/SELECT granted; UPDATE/DELETE
  denied — see ``snowflake/ddl/04_audit.sql``, Req 8.3); this adapter simply
  never issues an UPDATE/DELETE statement.

The port intentionally exposes **no** update or delete method: there is no code
path by which an existing audit record can be mutated (Req 8.3, Property 30).
"""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

from app.models.audit import AuditRecord

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids importing Snowpark eagerly
    from snowflake.snowpark import Session


# Fully-qualified physical table backing the audit trail (design "Data Models").
AUDIT_RECORD_TABLE = "AUDIT.AUDIT_RECORD"


class DuplicateAuditRecordError(RuntimeError):
    """Raised when an ``audit_id`` that already exists is appended again.

    Audit ids are stable and unique (design "Data Models"). Re-appending an
    existing id would either be an accidental duplicate or an attempt to overwrite
    history; both are rejected to preserve the append-only, immutable contract
    (Req 8.3). The *rejection of a mutation attempt* is handled one layer up by
    the service, which records it as a new audit record (Property 30).
    """

    def __init__(self, audit_id: str) -> None:
        self.audit_id = audit_id
        super().__init__(f"Audit record '{audit_id}' already exists; audit trail is append-only.")


@runtime_checkable
class AuditRecordRepository(Protocol):
    """Append-only persistence port for :class:`AuditRecord`s (Req 8).

    Implementations persist records in a way that forbids modification or
    deletion. The port exposes only an append and read-by-case surface — there is
    deliberately no update/delete method (Req 8.3, Property 30).
    """

    def append(self, record: AuditRecord) -> str:
        """Persist ``record`` and return its ``audit_id``.

        Must reject a duplicate ``audit_id`` (raising
        :class:`DuplicateAuditRecordError`) and must never overwrite an existing
        row (Req 8.3).
        """
        ...

    def list_for_case(self, case_id: str) -> list[AuditRecord]:
        """Return all audit records referencing ``case_id`` (chronological).

        Ordering is by ``timestamp_utc`` ascending with ties broken by insertion
        order, so a case's lineage is reconstructed deterministically (Req 8.4,
        Property 31).
        """
        ...

    def get(self, audit_id: str) -> Optional[AuditRecord]:
        """Return the record with ``audit_id`` if it exists, else ``None``."""
        ...


class InMemoryAuditRecordRepository:
    """In-memory, insertion-ordered audit store for tests and local runs.

    Enforces append-only semantics in-process: a duplicate ``audit_id`` is
    rejected and there is no mutation surface. Reads for a case are returned in
    chronological order (``timestamp_utc`` ascending, ties by insertion order),
    matching the Snowflake adapter so lineage replay is store-independent
    (Req 8.4, Property 31).
    """

    def __init__(self) -> None:
        # Insertion order is preserved by dict; it is also the tie-breaker for
        # equal timestamps so replay is stable (Property 31).
        self._records: dict[str, AuditRecord] = {}
        self._order: dict[str, int] = {}
        self._seq = 0
        self._lock = threading.Lock()

    def append(self, record: AuditRecord) -> str:
        with self._lock:
            if record.audit_id in self._records:
                raise DuplicateAuditRecordError(record.audit_id)
            self._records[record.audit_id] = record
            self._order[record.audit_id] = self._seq
            self._seq += 1
            return record.audit_id

    def list_for_case(self, case_id: str) -> list[AuditRecord]:
        with self._lock:
            matching = [r for r in self._records.values() if r.case_ref == case_id]
        return sorted(
            matching,
            key=lambda r: (r.timestamp_utc, self._order[r.audit_id]),
        )

    def get(self, audit_id: str) -> Optional[AuditRecord]:
        with self._lock:
            return self._records.get(audit_id)


class SnowflakeAuditRecordRepository:
    """Snowflake-backed audit store writing to ``AUDIT.AUDIT_RECORD``.

    Uses the Snowpark session for INSERT + SELECT only; it never issues an
    UPDATE/DELETE, and the Snowflake grants make such statements fail anyway
    (append-only enforced at the grant level — ``snowflake/ddl/04_audit.sql``,
    Req 8.3). ``input_refs`` / ``output_refs`` map to Snowflake ``ARRAY`` columns.
    """

    def __init__(self, session: "Session", table: str = AUDIT_RECORD_TABLE) -> None:
        self._session = session
        self._table = table

    def append(self, record: AuditRecord) -> str:
        # Guard against an accidental duplicate id before inserting; the service
        # above turns a genuine mutation attempt into a new audit record (P30).
        if self.get(record.audit_id) is not None:
            raise DuplicateAuditRecordError(record.audit_id)

        # ``input_refs`` / ``output_refs`` map to Snowflake ARRAY columns. They are
        # bound as JSON-text scalars wrapped in PARSE_JSON(?) rather than as raw
        # Python lists: a list bind is interpreted by Snowpark as a *multi-row batch*
        # bind, so two array binds of differing length (e.g. input_refs of size 2 and
        # output_refs of size 1) collide with "Batch size of N for bind variable K not
        # the same as previous size of M". Serializing each array to a single JSON
        # string keeps every bind a scalar (batch size 1) and still produces an ARRAY.
        self._session.sql(
            f"INSERT INTO {self._table} ("
            "audit_id, timestamp_utc, case_ref, entity_ref, actor_id, action, "
            "input_refs, output_refs, metric_definition_version) "
            "SELECT ?, ?, ?, ?, ?, ?, PARSE_JSON(?), PARSE_JSON(?), ?",
            params=[
                record.audit_id,
                record.timestamp_utc,
                record.case_ref,
                record.entity_ref,
                record.actor_id,
                record.action,
                json.dumps(list(record.input_refs)),
                json.dumps(list(record.output_refs)),
                record.metric_definition_version,
            ],
        ).collect()
        return record.audit_id

    def list_for_case(self, case_id: str) -> list[AuditRecord]:
        rows = self._session.sql(
            f"SELECT audit_id, timestamp_utc, case_ref, entity_ref, actor_id, action, "
            f"input_refs, output_refs, metric_definition_version "
            f"FROM {self._table} WHERE case_ref = ? ORDER BY timestamp_utc ASC",
            params=[case_id],
        ).collect()
        return [self._row_to_record(row) for row in rows]

    def get(self, audit_id: str) -> Optional[AuditRecord]:
        rows = self._session.sql(
            f"SELECT audit_id, timestamp_utc, case_ref, entity_ref, actor_id, action, "
            f"input_refs, output_refs, metric_definition_version "
            f"FROM {self._table} WHERE audit_id = ? LIMIT 1",
            params=[audit_id],
        ).collect()
        if not rows:
            return None
        return self._row_to_record(rows[0])

    @staticmethod
    def _row_to_record(row: object) -> AuditRecord:
        """Map a Snowpark ``Row`` to an :class:`AuditRecord`.

        Snowflake ``ARRAY`` columns come back as JSON/sequences; coerce them to
        lists of strings and treat NULLs as empty lists so the model's defaults
        hold.
        """
        data = row.as_dict() if hasattr(row, "as_dict") else dict(row)
        normalized = {str(k).lower(): v for k, v in data.items()}

        def _as_str_list(value: object) -> list[str]:
            if value is None:
                return []
            if isinstance(value, (list, tuple)):
                return [str(v) for v in value]
            return [str(value)]

        return AuditRecord(
            audit_id=normalized["audit_id"],
            timestamp_utc=normalized["timestamp_utc"],
            case_ref=normalized.get("case_ref"),
            entity_ref=normalized.get("entity_ref"),
            actor_id=normalized["actor_id"],
            action=normalized["action"],
            input_refs=_as_str_list(normalized.get("input_refs")),
            output_refs=_as_str_list(normalized.get("output_refs")),
            metric_definition_version=normalized.get("metric_definition_version"),
        )
