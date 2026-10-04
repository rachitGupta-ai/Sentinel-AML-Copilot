"""Persistence port for per-answer AI-quality telemetry.

The :class:`~app.services.kpi_service.KpiService` records and reads
:class:`~app.models.telemetry.AnswerTelemetry` through a ``TelemetryRepository``
*port* rather than touching Snowflake directly, mirroring the audit layer. This
keeps the KPI service's recording and deterministic-aggregation logic independent
of the physical store and unit-testable with the in-memory adapter, while the
Snowflake adapter persists telemetry for the system-health / AI-quality view
(design §10; Req 10.2).

Two adapters are provided:

* :class:`InMemoryTelemetryRepository` — a deterministic, insertion-ordered store
  for tests and local runs.
* :class:`SnowflakeTelemetryRepository` — writes through the Snowpark session
  using INSERT + SELECT only, so recorded telemetry is persisted for later KPI
  computation and UI surfacing.

The port intentionally exposes only an append and read surface: telemetry is a
record of what the system produced and is not mutated after the fact.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.models.telemetry import AnswerTelemetry

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids importing Snowpark eagerly
    from snowflake.snowpark import Session


# Fully-qualified physical table backing per-answer telemetry (design §10).
ANSWER_TELEMETRY_TABLE = "APP.ANSWER_TELEMETRY"


def _is_missing_table(exc: Exception) -> bool:
    """Whether ``exc`` is Snowflake's "object does not exist" compilation error.

    Snowflake raises SQL compilation error 002003 (SQLSTATE 42S02) with a message
    containing "does not exist" when a table is absent. Matching on that signature
    (rather than the exception type, to avoid importing Snowpark eagerly) lets the
    telemetry read degrade to an empty record set only for a genuinely missing
    table, never masking a different failure.
    """
    text = str(exc).lower()
    return "002003" in text or "does not exist" in text


@runtime_checkable
class TelemetryRepository(Protocol):
    """Append/read persistence port for :class:`AnswerTelemetry` (Req 10.2).

    Implementations persist one record per produced answer and return the full
    recorded set so KPI aggregations are a pure function of what was recorded
    (Property 38).
    """

    def record(self, telemetry: AnswerTelemetry) -> None:
        """Append one per-answer telemetry record."""
        ...

    def list_all(self) -> list[AnswerTelemetry]:
        """Return every recorded telemetry record in insertion order."""
        ...


class InMemoryTelemetryRepository:
    """In-memory, insertion-ordered telemetry store for tests and local runs.

    Reads return records in the order recorded so KPI aggregations are
    deterministic and reproducible (Property 38).
    """

    def __init__(self) -> None:
        self._records: list[AnswerTelemetry] = []
        self._lock = threading.Lock()

    def record(self, telemetry: AnswerTelemetry) -> None:
        with self._lock:
            self._records.append(telemetry)

    def list_all(self) -> list[AnswerTelemetry]:
        with self._lock:
            return list(self._records)


class SnowflakeTelemetryRepository:
    """Snowflake-backed telemetry store writing to ``APP.ANSWER_TELEMETRY``.

    Uses the Snowpark session for INSERT + SELECT only. Records are read back in
    ``recorded_at`` order so KPI aggregation over the Snowflake-backed store
    matches the in-memory adapter's deterministic ordering (Property 38).
    """

    def __init__(self, session: "Session", table: str = ANSWER_TELEMETRY_TABLE) -> None:
        self._session = session
        self._table = table

    def record(self, telemetry: AnswerTelemetry) -> None:
        self._session.sql(
            f"INSERT INTO {self._table} ("
            "case_id, answer_id, groundedness_score, dual_grounded, refusal, "
            "citation_count, refusal_correct, recorded_at) "
            "SELECT ?, ?, ?, ?, ?, ?, ?, ?",
            params=[
                telemetry.case_id,
                telemetry.answer_id,
                telemetry.groundedness_score,
                telemetry.dual_grounded,
                telemetry.refusal,
                telemetry.citation_count,
                telemetry.refusal_correct,
                telemetry.recorded_at,
            ],
        ).collect()

    def list_all(self) -> list[AnswerTelemetry]:
        try:
            rows = self._session.sql(
                f"SELECT case_id, answer_id, groundedness_score, dual_grounded, refusal, "
                f"citation_count, refusal_correct, recorded_at "
                f"FROM {self._table} ORDER BY recorded_at ASC"
            ).collect()
        except Exception as exc:  # noqa: BLE001 - degrade on any read failure
            # No telemetry has been recorded yet when the backing table is absent
            # (it is created lazily on first write). Treat "no table" as "no
            # records" so KPI aggregation stays available and reports an empty
            # sample rather than failing the whole /api/kpi read (graceful
            # degradation; design "every retrieval path has a safe fallback").
            if _is_missing_table(exc):
                logger.warning(
                    "answer_telemetry_table_absent",
                    extra={"table": self._table},
                )
                return []
            raise
        return [self._row_to_telemetry(row) for row in rows]

    @staticmethod
    def _row_to_telemetry(row: object) -> AnswerTelemetry:
        """Map a Snowpark ``Row`` to an :class:`AnswerTelemetry`."""
        data = row.as_dict() if hasattr(row, "as_dict") else dict(row)
        normalized = {str(k).lower(): v for k, v in data.items()}
        return AnswerTelemetry(
            case_id=normalized["case_id"],
            answer_id=normalized.get("answer_id"),
            groundedness_score=float(normalized["groundedness_score"]),
            dual_grounded=bool(normalized["dual_grounded"]),
            refusal=bool(normalized["refusal"]),
            citation_count=int(normalized["citation_count"]),
            refusal_correct=normalized.get("refusal_correct"),
            recorded_at=normalized["recorded_at"],
        )
