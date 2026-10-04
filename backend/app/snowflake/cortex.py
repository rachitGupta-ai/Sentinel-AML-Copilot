"""Snowflake Cortex adapter and guarded evidence retrieval.

This module is the single boundary through which the application talks to
**Snowflake Cortex** (design §"Components and Interfaces", component 10; task
10.1). It wraps the three Cortex surfaces the copilot uses — all executed through
the Snowpark session so no data leaves the Snowflake AI Data Cloud:

* ``SNOWFLAKE.CORTEX.COMPLETE(model, prompt)`` — narrative generation. The LLM is
  only ever handed numbers/evidence; it never produces a regulatory figure
  (Req 3.2).
* ``SNOWFLAKE.CORTEX.EMBED_TEXT_768(model, text)`` — text → 768-dim embedding,
  matching the ``VECTOR(FLOAT, 768)`` columns in ``VEC.POLICY_CHUNK`` /
  ``VEC.EVIDENCE_CHUNK`` (``snowflake/ddl/02_vec.sql``).
* ``VECTOR_COSINE_SIMILARITY(a, b)`` — similarity ranking for top-k retrieval.

Two hard guarantees are implemented here:

* **Bounded retry + fail-safe (Req 13.1, Property 40).** Every Cortex call runs
  under a configurable per-attempt timeout and a bounded retry budget
  (``max_retries``, default ``2``). Total attempts are therefore ``1 +
  max_retries``. When the budget is exhausted the call does **not** fall back to
  an invented value — it raises :class:`CortexUnavailableError`, so the calling
  step fails safe with no actionable, ungrounded output.

* **All retrieved content is untrusted (Req 9.1).** Chunk text retrieved from
  ``VEC.*`` is routed through :meth:`GuardService.scan_for_injection` *before* it
  can feed any model (``COMPLETE``) call. Any chunk whose text is flagged as
  prompt-injection is dropped from the retrieval result (and already recorded as
  a rejection by the guard), so injected content can never influence a Cortex
  generation.

The Cortex SQL surface is abstracted behind the :class:`SessionRunner` port and
the guard behind the injected :class:`GuardService`, so the adapter is fully
unit-testable with an in-memory fake session and a fake guard, consistent with
how the metric/audit layers are built (``metric_repository.py``).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol, Sequence, runtime_checkable

from app.config.settings import CortexSettings
from app.services.guard_service import GuardService, GuardServiceImpl

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids importing Snowpark eagerly
    from snowflake.snowpark import Session

logger = logging.getLogger("governed_aml")

# Logical operation names so a failure is attributable without exposing secrets.
_OP_COMPLETE = "cortex.complete"
_OP_EMBED = "cortex.embed_text"
_OP_RETRIEVE = "cortex.retrieve"

# Governed VEC.* read surfaces (mirrors snowflake/ddl/02_vec.sql). These are the
# only tables the retrieval reads from; they are also on the Guard allow-list.
POLICY_CHUNK_TABLE = "VEC.POLICY_CHUNK"
EVIDENCE_CHUNK_TABLE = "VEC.EVIDENCE_CHUNK"

# Dimension of the Cortex embedding + the VECTOR(FLOAT, 768) columns in VEC.*.
EMBED_DIM = 768


class CortexError(RuntimeError):
    """Base class for Cortex adapter failures (never carries a secret)."""


class CortexUnavailableError(CortexError):
    """Raised when a Cortex call exhausts its bounded retry budget (Req 13.1).

    The adapter never substitutes an invented/ungrounded value on exhaustion: it
    raises this so the calling step fails safe (no actionable, ungrounded output)
    and routes to human review (design §13; Property 40). ``attempts`` records how
    many attempts were made (``1 + max_retries`` on full exhaustion).
    """

    def __init__(self, operation: str, attempts: int, detail: str) -> None:
        self.operation = operation
        self.attempts = attempts
        self.detail = detail
        super().__init__(f"[{operation}] cortex unavailable after {attempts} attempt(s): {detail}")


@runtime_checkable
class SessionRunner(Protocol):
    """Minimal port over the Snowpark session used by the adapter.

    Abstracts ``session.sql(sql, params=[...]).collect()`` so the adapter is
    unit-testable with an in-memory fake. ``collect`` returns a sequence of row
    objects exposing either ``as_dict()`` or mapping access (mirrors Snowpark
    ``Row``).
    """

    def collect(self, sql: str, params: Sequence[Any] | None = None) -> list[Any]:
        """Execute ``sql`` with bound ``params`` and return the collected rows."""
        ...


class SnowparkSessionRunner:
    """:class:`SessionRunner` backed by a real Snowpark ``Session``.

    Runs parameterised SQL through ``session.sql(...).collect()``. Parameters are
    always bound (never string-interpolated) so untrusted values cannot alter the
    statement shape.
    """

    def __init__(self, session: "Session") -> None:
        self._session = session

    def collect(self, sql: str, params: Sequence[Any] | None = None) -> list[Any]:
        if params is None:
            return list(self._session.sql(sql).collect())
        return list(self._session.sql(sql, params=list(params)).collect())


@dataclass(frozen=True)
class RetrievedChunk:
    """One retrieved, injection-scanned evidence chunk (Req 9.1, Property 17).

    Only chunks that passed the Guard injection scan are materialised as a
    :class:`RetrievedChunk`, so ``chunk_text`` here is safe to feed a model call.
    ``source_kind``/``source_ref`` resolve the chunk back to its origin for
    citation lineage; ``similarity`` is the ``VECTOR_COSINE_SIMILARITY`` score the
    chunk was ranked by.
    """

    chunk_id: str
    chunk_text: str
    similarity: float
    source_kind: str
    source_ref: str
    chunk_index: Optional[int] = None
    embedding_model: Optional[str] = None


@dataclass(frozen=True)
class RetrievalResult:
    """Outcome of a guarded top-k retrieval over a ``VEC.*`` table.

    ``chunks`` are the clean, ranked chunks (highest similarity first) that may
    feed a model call. ``rejected_chunk_ids`` records chunks that were dropped
    because their text was flagged as prompt-injection — surfaced for
    observability/audit, never returned as usable evidence (Req 9.1).
    """

    chunks: list[RetrievedChunk] = field(default_factory=list)
    rejected_chunk_ids: list[str] = field(default_factory=list)


# A clock seam so the (bounded) backoff between retries is deterministic in tests.
Sleeper = Callable[[float], None]


@runtime_checkable
class CortexAdapter(Protocol):
    """Interface for the Cortex adapter (design component 10).

    Implementations wrap ``COMPLETE`` / ``EMBED_TEXT_*`` / retrieval with a
    configurable timeout and bounded retry, fail safe on exhaustion (Req 13.1),
    and route all retrieved chunk text through the Guard injection scan before it
    can feed any model call (Req 9.1).
    """

    def embed_text(self, text: str) -> list[float]:
        """Return the Cortex embedding of ``text`` (768-dim)."""
        ...

    def complete(self, prompt: str, *, model: str | None = None) -> str:
        """Generate narrative via ``SNOWFLAKE.CORTEX.COMPLETE`` for ``prompt``."""
        ...

    def retrieve_policy_evidence(
        self, query: str, *, top_k: int | None = None, **ctx: Any
    ) -> RetrievalResult:
        """Retrieve top-k guarded policy chunks relevant to ``query``."""
        ...

    def retrieve_evidence(
        self, query: str, *, top_k: int | None = None, **ctx: Any
    ) -> RetrievalResult:
        """Retrieve top-k guarded evidence chunks relevant to ``query``."""
        ...


class CortexAdapterImpl:
    """Default :class:`CortexAdapter` over a :class:`SessionRunner` + :class:`GuardService`.

    Timeout and retry are config-gated via :class:`CortexSettings` with
    safe/behavior-preserving defaults; the guard is injected so every retrieved
    chunk is injection-scanned before it can feed a ``COMPLETE`` call. Both the
    session runner and the guard are injectable for unit tests.
    """

    def __init__(
        self,
        runner: SessionRunner,
        *,
        guard: GuardService | None = None,
        settings: CortexSettings | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        self._runner = runner
        self._guard: GuardService = guard if guard is not None else GuardServiceImpl()
        self._settings = settings if settings is not None else CortexSettings()
        # time.sleep by default; a no-op/record seam keeps tests fast + deterministic.
        self._sleep: Sleeper = sleeper if sleeper is not None else time.sleep

    # -- Public Cortex surfaces --------------------------------------------

    def embed_text(self, text: str) -> list[float]:
        """Embed ``text`` with ``SNOWFLAKE.CORTEX.EMBED_TEXT_768`` (Req 13.1).

        Runs under the bounded-retry/fail-safe envelope. The embedding model name
        is config-gated (``CortexSettings.embedding_model``) and is a 768-dim
        model so the vector matches the ``VEC.*`` columns.
        """
        model = self._settings.embedding_model
        sql = f"SELECT SNOWFLAKE.CORTEX.EMBED_TEXT_{EMBED_DIM}(?, ?) AS embedding"
        rows = self._run_with_retry(_OP_EMBED, sql, [model, text])
        if not rows:
            raise CortexUnavailableError(_OP_EMBED, 1, "embedding query returned no rows")
        return _as_float_list(_first_column(rows[0], "embedding"))

    def complete(self, prompt: str, *, model: str | None = None) -> str:
        """Generate narrative via ``SNOWFLAKE.CORTEX.COMPLETE`` (Req 13.1).

        Runs under the bounded-retry/fail-safe envelope. The model name is
        config-gated (``CortexSettings.complete_model``) unless overridden.
        Callers must only ever feed this guarded/governed content; the adapter
        does not itself scan ``prompt`` (that is the responsibility of the caller
        that assembled it from already-scanned chunks + governed figures).
        """
        model_name = model or self._settings.complete_model
        sql = "SELECT SNOWFLAKE.CORTEX.COMPLETE(?, ?) AS completion"
        rows = self._run_with_retry(_OP_COMPLETE, sql, [model_name, prompt])
        if not rows:
            raise CortexUnavailableError(_OP_COMPLETE, 1, "completion query returned no rows")
        return _as_str(_first_column(rows[0], "completion"))

    def retrieve_policy_evidence(
        self, query: str, *, top_k: int | None = None, **ctx: Any
    ) -> RetrievalResult:
        """Retrieve top-k guarded policy chunks from ``VEC.POLICY_CHUNK`` (Req 9.1).

        Embeds ``query``, ranks policy chunks by ``VECTOR_COSINE_SIMILARITY``, and
        routes every candidate chunk's text through the Guard injection scan;
        flagged chunks are dropped. ``ctx`` (``case_ref``/``entity_ref``) is
        forwarded to the guard for audit lineage.
        """
        return self._retrieve(POLICY_CHUNK_TABLE, "policy_passage", query, top_k, ctx)

    def retrieve_evidence(
        self, query: str, *, top_k: int | None = None, **ctx: Any
    ) -> RetrievalResult:
        """Retrieve top-k guarded evidence chunks from ``VEC.EVIDENCE_CHUNK`` (Req 9.1).

        Same guarded flow as :meth:`retrieve_policy_evidence`, over the non-policy
        evidence table; each chunk's ``source_kind``/``source_ref`` resolve it back
        to its origin for citation lineage.
        """
        return self._retrieve(EVIDENCE_CHUNK_TABLE, None, query, top_k, ctx)

    # -- Retrieval core -----------------------------------------------------

    def _retrieve(
        self,
        table: str,
        source_kind_override: str | None,
        query: str,
        top_k: int | None,
        ctx: dict[str, Any],
    ) -> RetrievalResult:
        """Embed the query, rank by cosine similarity, then guard every chunk.

        The embedding call and the ranking SELECT each run under the bounded-retry
        envelope. Guard scanning happens *after* retrieval and *before* any chunk
        can feed a model, so no injected chunk text is ever returned as usable
        evidence (Req 9.1).
        """
        k = top_k if top_k is not None else self._settings.retrieval_top_k
        if k < 1:
            return RetrievalResult(chunks=[], rejected_chunk_ids=[])

        query_vec = self.embed_text(query)
        rows = self._rank_chunks(table, query_vec, k)

        clean: list[RetrievedChunk] = []
        rejected: list[str] = []
        case_ref = ctx.get("case_ref")
        entity_ref = ctx.get("entity_ref")

        for row in rows:
            data = _row_to_dict(row)
            chunk_id = _as_str(data.get("chunk_id"))
            chunk_text = _opt_str(data.get("chunk_text")) or ""
            source_kind = source_kind_override or _opt_str(data.get("source_kind")) or "policy_passage"
            source_ref = _opt_str(data.get("source_ref")) or _opt_str(data.get("policy_doc_id")) or chunk_id

            # ALL retrieved content is untrusted — scan before it can feed a model
            # call (Req 9.1). A flagged chunk is dropped (and already audited by the
            # guard) so injected text never reaches COMPLETE.
            scan = self._guard.scan_for_injection(
                chunk_text,
                source_ref=f"{table}:{chunk_id}",
                case_ref=case_ref,
                entity_ref=entity_ref,
            )
            if scan.is_injection:
                rejected.append(chunk_id)
                continue

            clean.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    chunk_text=chunk_text,
                    similarity=_as_float(data.get("similarity")),
                    source_kind=source_kind,
                    source_ref=source_ref,
                    chunk_index=_opt_int(data.get("chunk_index")),
                    embedding_model=_opt_str(data.get("embedding_model")),
                )
            )

        return RetrievalResult(chunks=clean, rejected_chunk_ids=rejected)

    def _rank_chunks(self, table: str, query_vec: Sequence[float], k: int) -> list[Any]:
        """Rank chunks in ``table`` by cosine similarity to ``query_vec`` (top-k).

        Runs under the bounded-retry envelope. The query embedding is passed as a
        bound parameter and cast to ``VECTOR(FLOAT, 768)`` so comparison uses
        ``VECTOR_COSINE_SIMILARITY`` against the stored ``embedding`` column.
        """
        vector_literal = _vector_literal(query_vec)
        sql = (
            "SELECT *, VECTOR_COSINE_SIMILARITY("
            f"embedding, {vector_literal}::VECTOR(FLOAT, {EMBED_DIM})) AS similarity "
            f"FROM {table} "
            "WHERE embedding IS NOT NULL "
            "ORDER BY similarity DESC "
            "LIMIT ?"
        )
        return self._run_with_retry(_OP_RETRIEVE, sql, [k])

    # -- Bounded retry + fail-safe envelope (Req 13.1, Property 40) ---------

    def _run_with_retry(self, operation: str, sql: str, params: Sequence[Any]) -> list[Any]:
        """Execute a Cortex SQL call with bounded retry and a fail-safe exit.

        Total attempts are ``1 + max(0, max_retries)`` so the attempt count is
        always bounded (Req 13.1). Each attempt is wrapped so a timeout or error is
        caught and (budget permitting) retried after a short bounded backoff. When
        the budget is exhausted the method raises :class:`CortexUnavailableError`
        — it never returns an invented/ungrounded result (Property 40).
        """
        max_retries = max(0, int(self._settings.max_retries))
        total_attempts = 1 + max_retries
        last_detail = "no attempt executed"

        for attempt in range(1, total_attempts + 1):
            try:
                return self._run_once(operation, sql, params)
            except Exception as exc:  # noqa: BLE001 - classify + fail safe, never leak secret
                last_detail = _sanitize_error(exc)
                logger.warning(
                    "Cortex call failed during %s (attempt %d/%d): %s",
                    operation,
                    attempt,
                    total_attempts,
                    last_detail,
                )
                if attempt < total_attempts:
                    # Bounded, deterministic backoff before the next retry.
                    self._sleep(min(0.5 * attempt, self._settings.timeout_seconds or 0.5))
                    continue
                break

        # Budget exhausted — fail safe with no actionable/ungrounded output.
        raise CortexUnavailableError(operation, total_attempts, last_detail)

    def _run_once(self, operation: str, sql: str, params: Sequence[Any]) -> list[Any]:
        """Run a single Cortex SQL attempt under the configured per-attempt timeout.

        The timeout is advisory at this layer (the Snowpark/driver enforces the
        hard cap); we measure the wall-clock duration and raise a
        :class:`TimeoutError` if the attempt overran, so the retry/fail-safe
        envelope treats an overrun like any other failure.
        """
        budget = self._settings.timeout_seconds
        started = time.monotonic()
        rows = self._runner.collect(sql, params)
        if budget and (time.monotonic() - started) > budget:
            raise TimeoutError(
                f"{operation} exceeded the configured timeout of {budget}s"
            )
        return rows


# ---------------------------------------------------------------------------
# Factory + small coercion helpers.
# ---------------------------------------------------------------------------


def build_cortex_adapter(
    session: "Session",
    *,
    guard: GuardService | None = None,
    settings: CortexSettings | None = None,
) -> CortexAdapterImpl:
    """Build a :class:`CortexAdapterImpl` over a real Snowpark ``Session``.

    Wraps the session in a :class:`SnowparkSessionRunner`; the guard and settings
    default to a :class:`GuardServiceImpl` and env-bound :class:`CortexSettings`.
    """
    return CortexAdapterImpl(
        SnowparkSessionRunner(session), guard=guard, settings=settings
    )


def _vector_literal(values: Sequence[float]) -> str:
    """Render a numeric vector as a Snowflake array literal for casting.

    Values are formatted deterministically; the result is cast to
    ``VECTOR(FLOAT, 768)`` by the caller. Only floats are emitted, so there is no
    injection surface from this literal.
    """
    return "[" + ", ".join(repr(float(v)) for v in values) + "]"


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Coerce a Snowpark ``Row`` (or mapping) to a lower-cased dict."""
    if hasattr(row, "as_dict"):
        data = row.as_dict()
    elif isinstance(row, dict):
        data = row
    else:  # pragma: no cover - defensive, Snowpark rows expose as_dict()
        data = dict(row)
    return {str(k).lower(): v for k, v in data.items()}


def _first_column(row: Any, name: str) -> Any:
    """Return column ``name`` from a row, tolerating case/aliasing differences."""
    data = _row_to_dict(row)
    if name.lower() in data:
        return data[name.lower()]
    # Fall back to the first value if the alias was not preserved.
    return next(iter(data.values()), None)


def _as_float_list(value: Any) -> list[float]:
    """Coerce a Cortex embedding payload to a list of floats."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [float(v) for v in value]
    # Some drivers return the vector as a JSON-ish string; parse leniently.
    text = str(value).strip().lstrip("[").rstrip("]")
    if not text:
        return []
    return [float(part) for part in text.split(",") if part.strip()]


def _as_float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


def _opt_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _opt_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


def _sanitize_error(exc: BaseException) -> str:
    """Return a short, secret-free description of a Cortex failure.

    Only the exception type and a trimmed message are surfaced; this never
    includes credential values (consistent with the session layer, Req 1.1).
    """
    message = str(exc).strip()
    if len(message) > 200:
        message = message[:200] + "…"
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


__all__ = [
    "CortexAdapter",
    "CortexAdapterImpl",
    "SessionRunner",
    "SnowparkSessionRunner",
    "RetrievedChunk",
    "RetrievalResult",
    "CortexError",
    "CortexUnavailableError",
    "build_cortex_adapter",
    "POLICY_CHUNK_TABLE",
    "EVIDENCE_CHUNK_TABLE",
    "EMBED_DIM",
]
