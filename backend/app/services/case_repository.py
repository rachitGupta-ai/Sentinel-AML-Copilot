"""Persistence port for the alert-triage case store.

The :class:`~app.services.triage_service.TriageService` persists and reads cases
through a ``CaseRepository`` *port* rather than touching Snowflake directly. This
mirrors how the audit trail and governed metrics are built
(``app/services/audit_repository.py``, ``app/services/metric_repository.py``): the
triage service's correlation/ranking/freshness logic stays independent of the
physical store and is unit-testable with an in-memory fake, while the Snowflake
adapter persists to ``APP.CASE`` (design §3; Req 4).

Two adapters are provided:

* :class:`InMemoryCaseRepository` — a deterministic, in-process store for tests
  and local runs. It supports ``upsert`` (insert or replace by ``case_id``),
  point lookup, correlation-key lookup (so :meth:`correlate` can collapse alerts
  into one case), and listing open cases for ranking.
* :class:`SnowflakeCaseRepository` — persists through the Snowpark session using
  MERGE/SELECT over ``APP.CASE``. The correlation key and open/closed status are
  stored alongside the serialized :class:`~app.models.case.Case` so lookups are
  cheap.

The port deliberately exposes an upsert rather than a bare insert: correlating a
new alert into an existing case is an idempotent update of that case's
``correlated_alert_ids`` (Req 4.5, Property 14).
"""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

from app.models.case import Case, CaseState
from app.models.governed import EntityRiskView, FreshnessIndicator

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids importing Snowpark eagerly
    from snowflake.snowpark import Session


# Fully-qualified physical table backing the case store (design "Data Models").
CASE_TABLE = "APP.CASE"

# Lifecycle states considered "open" for ranking in the Command_Centre (Req 4.2).
# A case drops out of the open set once it reaches a terminal/closed state.
_TERMINAL_STATES: frozenset[CaseState] = frozenset(
    {CaseState.REJECTED, CaseState.ACTION_COMPLETED, CaseState.CLOSED}
)


def is_open_state(state: CaseState) -> bool:
    """Whether ``state`` counts as an open case for Command_Centre ranking (Req 4.2)."""
    return state not in _TERMINAL_STATES


@runtime_checkable
class CaseRepository(Protocol):
    """Persistence port for triage :class:`Case` records (Req 4).

    Implementations persist cases keyed by ``case_id`` and support looking a case
    up by its correlation key so repeated alerts in the same group collapse into
    one case (Req 4.5). Listing returns open cases for governed-risk ranking
    (Req 4.2).
    """

    def upsert(self, case: Case, correlation_key: str) -> None:
        """Insert or replace ``case``, indexed by ``correlation_key``.

        Upsert (not bare insert) because correlating a later alert into an
        existing case is an idempotent update of that case (Req 4.5).
        """
        ...

    def get(self, case_id: str) -> Optional[Case]:
        """Return the case with ``case_id`` if present, else ``None``."""
        ...

    def find_by_correlation_key(self, correlation_key: str) -> Optional[Case]:
        """Return the existing case for ``correlation_key`` if any, else ``None``.

        Used by :meth:`correlate` to decide whether a new alert joins an existing
        case or opens a new one (Req 4.5, Property 14).
        """
        ...

    def save(self, case: Case) -> None:
        """Persist a lifecycle transition of an already-stored ``case``.

        Unlike :meth:`upsert`, this does not take a correlation key: it replaces an
        existing case in place (keyed by ``case_id``) while preserving the
        correlation-key index established when the case was first created. It is
        used to drive lifecycle state transitions (e.g.
        ``RECOMMENDATION_READY → AWAITING_APPROVAL → APPROVED → ACTION_COMPLETED``)
        where only the case's state/fields change, not its correlation group
        (Req 7.2, Req 7.3). Raises ``KeyError`` when ``case.case_id`` is unknown,
        since a transition presupposes an existing case.
        """
        ...

    def list_open_cases(self) -> list[Case]:
        """Return all open (non-terminal) cases for ranking (Req 4.2).

        Ordering is by ``case_id`` so the repository read is deterministic; the
        triage service applies the governed-risk ordering on top.
        """
        ...


class InMemoryCaseRepository:
    """In-memory, deterministic case store for tests and local runs.

    Enforces a single case per correlation key and keeps the ``case_id`` →
    correlation-key index in sync so lookups stay consistent after an upsert
    changes nothing but the correlated-alert set. Reads return the stored frozen
    :class:`Case`, so no caller can mutate persisted state in place.
    """

    def __init__(self) -> None:
        self._cases: dict[str, Case] = {}
        self._by_correlation: dict[str, str] = {}
        self._lock = threading.Lock()

    def upsert(self, case: Case, correlation_key: str) -> None:
        with self._lock:
            self._cases[case.case_id] = case
            self._by_correlation[correlation_key] = case.case_id

    def get(self, case_id: str) -> Optional[Case]:
        with self._lock:
            return self._cases.get(case_id)

    def find_by_correlation_key(self, correlation_key: str) -> Optional[Case]:
        with self._lock:
            case_id = self._by_correlation.get(correlation_key)
            if case_id is None:
                return None
            return self._cases.get(case_id)

    def save(self, case: Case) -> None:
        with self._lock:
            if case.case_id not in self._cases:
                raise KeyError(case.case_id)
            # Replace in place; the correlation-key index is keyed by
            # correlation_key -> case_id and is unaffected by a state change.
            self._cases[case.case_id] = case

    def list_open_cases(self) -> list[Case]:
        with self._lock:
            cases = [c for c in self._cases.values() if is_open_state(c.state)]
        return sorted(cases, key=lambda c: c.case_id)


class SnowflakeCaseRepository:
    """Snowflake-backed case store writing to the normalized ``APP.CASE`` table.

    The physical table is the normalized layout from ``snowflake/ddl/03_app.sql``:
    ``case_id, entity_id, state, classification, risk_aggregation (VARIANT),
    freshness (VARIANT), correlated_alert_ids (ARRAY), created_at, updated_at``.
    There is deliberately no ``payload`` or ``correlation_key`` column — the
    :class:`Case` fields map directly onto typed columns so the governed semantic
    view and the dashboard can read case state without unpacking a JSON blob.

    VARIANT/ARRAY columns are bound as JSON-text scalars wrapped in
    ``PARSE_JSON(?)`` rather than as raw Python objects: Snowpark interprets a
    list/dict bind as a *multi-row batch* bind, so two array binds of differing
    length collide ("Batch size of N for bind variable K not the same as previous
    size of M"). A single JSON string keeps every bind a scalar (batch size 1) and
    still lands as a VARIANT/ARRAY.

    The triage service's ``correlation_key`` is produced as ``alert.correlation_key
    or entity_id`` (see the ingest router), i.e. the correlation group is the
    entity. The normalized table has no correlation-key column, so correlation
    lookups match on ``entity_id`` — collapsing alerts for the same entity into one
    case within the window (Req 4.5, Property 14).
    """

    def __init__(self, session: "Session", table: str = CASE_TABLE) -> None:
        self._session = session
        self._table = table

    def upsert(self, case: Case, correlation_key: str) -> None:
        # correlation_key is accepted for interface compatibility; the correlation
        # group is the entity (see class docstring), so it is not stored separately.
        self._session.sql(
            f"MERGE INTO {self._table} t "
            "USING (SELECT ? AS case_id, ? AS entity_id, ? AS state, ? AS classification, "
            "PARSE_JSON(?) AS risk_aggregation, PARSE_JSON(?) AS freshness, "
            "PARSE_JSON(?) AS correlated_alert_ids) s "
            "ON t.case_id = s.case_id "
            "WHEN MATCHED THEN UPDATE SET entity_id = s.entity_id, state = s.state, "
            "classification = s.classification, risk_aggregation = s.risk_aggregation, "
            "freshness = s.freshness, correlated_alert_ids = s.correlated_alert_ids, "
            "updated_at = CURRENT_TIMESTAMP() "
            "WHEN NOT MATCHED THEN INSERT (case_id, entity_id, state, classification, "
            "risk_aggregation, freshness, correlated_alert_ids, created_at, updated_at) "
            "VALUES (s.case_id, s.entity_id, s.state, s.classification, s.risk_aggregation, "
            "s.freshness, s.correlated_alert_ids, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())",
            params=[
                case.case_id,
                case.entity_id,
                case.state.value,
                case.classification,
                case.risk_aggregation.model_dump_json(),
                case.freshness.model_dump_json(),
                json.dumps(list(case.correlated_alert_ids)),
            ],
        ).collect()

    def get(self, case_id: str) -> Optional[Case]:
        rows = self._session.sql(
            f"SELECT {_CASE_COLUMNS} FROM {self._table} WHERE case_id = ? LIMIT 1",
            params=[case_id],
        ).collect()
        if not rows:
            return None
        return self._row_to_case(rows[0])

    def find_by_correlation_key(self, correlation_key: str) -> Optional[Case]:
        # The correlation group is the entity (correlation_key defaults to the
        # entity id at triage time), so a case is found by its entity_id. This
        # collapses alerts for the same entity into one case (Req 4.5, Property 14).
        rows = self._session.sql(
            f"SELECT {_CASE_COLUMNS} FROM {self._table} WHERE entity_id = ? "
            "ORDER BY case_id ASC LIMIT 1",
            params=[correlation_key],
        ).collect()
        if not rows:
            return None
        return self._row_to_case(rows[0])

    def save(self, case: Case) -> None:
        # Update the mutable lifecycle/state columns in place, keyed by case_id;
        # entity_id (the correlation group) is left untouched so the case stays in
        # its original group (Req 7.3).
        updated = self._session.sql(
            f"UPDATE {self._table} SET state = ?, classification = ?, "
            "risk_aggregation = PARSE_JSON(?), freshness = PARSE_JSON(?), "
            "correlated_alert_ids = PARSE_JSON(?), updated_at = CURRENT_TIMESTAMP() "
            "WHERE case_id = ?",
            params=[
                case.state.value,
                case.classification,
                case.risk_aggregation.model_dump_json(),
                case.freshness.model_dump_json(),
                json.dumps(list(case.correlated_alert_ids)),
                case.case_id,
            ],
        ).collect()
        # Snowpark returns a one-row result reporting the number of rows updated;
        # a transition presupposes an existing case, so a zero-row update is a
        # programming error (unknown case id).
        rows_updated = _rows_affected(updated)
        if rows_updated == 0:
            raise KeyError(case.case_id)

    def list_open_cases(self) -> list[Case]:
        placeholders = ", ".join("?" for _ in _TERMINAL_STATES)
        terminal = [s.value for s in _TERMINAL_STATES]
        rows = self._session.sql(
            f"SELECT {_CASE_COLUMNS} FROM {self._table} "
            f"WHERE state NOT IN ({placeholders}) ORDER BY case_id ASC",
            params=terminal,
        ).collect()
        return [self._row_to_case(row) for row in rows]

    @staticmethod
    def _row_to_case(row: object) -> Case:
        data = row.as_dict() if hasattr(row, "as_dict") else dict(row)  # type: ignore[arg-type]
        normalized = {str(k).lower(): v for k, v in data.items()}
        return Case(
            case_id=str(normalized["case_id"]),
            entity_id=str(normalized["entity_id"]),
            state=CaseState(str(normalized["state"])),
            classification=str(normalized["classification"]),  # type: ignore[arg-type]
            risk_aggregation=EntityRiskView.model_validate(
                _variant_to_obj(normalized.get("risk_aggregation"))
            ),
            freshness=FreshnessIndicator.model_validate(
                _variant_to_obj(normalized.get("freshness"))
            ),
            correlated_alert_ids=_variant_to_str_list(normalized.get("correlated_alert_ids")),
        )


# Column projection shared by every read so the row shape is consistent.
_CASE_COLUMNS = (
    "case_id, entity_id, state, classification, risk_aggregation, "
    "freshness, correlated_alert_ids"
)


def _variant_to_obj(value: object) -> object:
    """Coerce a Snowflake VARIANT read back into a Python object.

    Snowpark returns a VARIANT column either already-parsed (dict/list) or as a
    JSON string depending on driver/path; normalise both to the parsed object so
    the pydantic model validates against a mapping, not a string. ``None`` becomes
    an empty mapping so a NULL VARIANT falls back to the model's defaults.
    """
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return json.loads(value)
    return value


def _variant_to_str_list(value: object) -> list[str]:
    """Coerce a Snowflake ARRAY read back into a list of strings (NULL → empty)."""
    obj = _variant_to_obj(value)
    if isinstance(obj, (list, tuple)):
        return [str(v) for v in obj]
    if obj in ((), {}, None, ""):
        return []
    return [str(obj)]


def _rows_affected(result: object) -> int:
    """Best-effort extraction of the affected-row count from an UPDATE result.

    Snowpark's ``sql(...).collect()`` for an UPDATE returns a single row whose
    column reports the number of rows updated. The column name varies by driver
    version, so this reads the first numeric column value and falls back to
    treating the result as "updated" when the shape is unexpected (never masking
    a genuine zero-row update that the driver does surface).
    """
    try:
        rows = list(result)  # type: ignore[arg-type]
    except TypeError:
        return 1
    if not rows:
        return 0
    first = rows[0]
    data = first.as_dict() if hasattr(first, "as_dict") else None
    if data:
        for value in data.values():
            if isinstance(value, (int, float)):
                return int(value)
    return 1
