"""Workflow resilience, degradation, and step/compensation helpers.

This module is the small, cohesive mechanism that *unifies* the fail-safe
behaviour already present per-service (design §"Error Handling"; Req 13). It does
not re-implement any of the existing safeguards — the Cortex bounded retry +
fail-safe lives in :mod:`app.snowflake.cortex` (Req 13.1), the grounding gate that
routes low-confidence / low-groundedness / conflicted answers to human review
lives in :mod:`app.services.grounding_service` (Req 13.3), and the freshness
"no-data" UI state lives in :mod:`app.snowflake.freshness`. What this module adds
is the two cross-cutting pieces the services and API need to share:

* **Recorded graceful degradation (Req 13.2).** When a data source or a simulated
  downstream is unavailable, a step should *degrade* rather than crash or present
  a stale figure as current. :func:`record_degradation` writes an append-only
  audit record and returns a serializable :class:`DegradationState` carrying a
  defined :class:`UiState` (``healthy`` / ``degraded`` / ``unavailable``) that the
  API/UI surfaces. Degradation is always *recorded and surfaced, never silent*.

* **Transactional step + compensation (Req 13.4, Property 41).** Each workflow
  step must commit its persisted change together with its ``Audit_Record``, and a
  failure mid-step must leave the case in a valid lifecycle state with no partial,
  unaudited commit. :func:`commit_step` runs a ``persist_fn`` and, only if it
  succeeds, appends the paired audit record; if persistence fails nothing is
  committed and a *compensating* audit record is appended so the failure itself is
  audited (the case is never left in a partial, unaudited state). This mirrors the
  ``APPROVED → ACTION_FAILED`` compensation path already in the approval service
  (``ACTION_FAILED`` retains a recovery/retry back to ``AWAITING_APPROVAL``,
  Req 7.3), giving the other steps the same guarantee through one shared helper.

Everything here is **store-agnostic and deterministic**: persistence is supplied
as a callable (``persist_fn``) and audit goes through the injected
:class:`~app.services.audit_service.AuditService`, so the helpers are directly
unit-testable with in-memory fakes, consistent with the triage/approval/audit
services. Nothing logs or echoes a credential value (Req 1.1); degradation detail
is a short, operation-identifying, secret-free string.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable, Optional, Protocol, Sequence, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.services.audit_service import (
    SYSTEM_ACTOR,
    AuditService,
    AuditServiceImpl,
    build_audit_record,
)

# ---------------------------------------------------------------------------
# Canonical audit action names.
#
# Kept stable and package-private so the audit trail is queryable and the
# resilience tests (Property 41; degradation unit test) can assert them.
# ---------------------------------------------------------------------------

# Recorded when a data-source / downstream unavailability triggers graceful
# degradation (Req 13.2). The degradation is audited AND surfaced to the UI.
ACTION_DEGRADATION_RECORDED = "resilience.degradation_recorded"

# Recorded when a workflow step's persisted change + its audit record commit
# together successfully (Req 13.4). This is the "paired" audit record for the
# step; callers pass the step's own action via :func:`commit_step`.
# (No constant — the step action is caller-supplied.)

# Recorded when a step fails mid-commit and compensation runs, so the failure is
# itself audited and the case is left in a valid, fully-audited state
# (Req 13.4, Property 41).
ACTION_STEP_COMPENSATED = "resilience.step_compensated"


class UiState(str, Enum):
    """Defined health/degradation states the UI renders (Req 13.2).

    A single, serializable vocabulary shared by the API and the dashboard so a
    degraded data source or downstream is shown as a *defined* state rather than a
    crash or a silently-stale figure (design §"Data-source / downstream
    unavailability"). ``str`` mixin makes the enum serialize to its value for
    transport.

    * ``HEALTHY`` — the source/downstream responded normally; no degradation.
    * ``DEGRADED`` — partially available or serving stale/incomplete data; the UI
      shows the data behind an explicit stale/degraded indicator (Req 4.4) and no
      stale figure is presented as current.
    * ``UNAVAILABLE`` — the source/downstream could not be reached at all; the UI
      shows the defined empty/error state and no figure is presented.
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class DegradationState(BaseModel):
    """A recorded, serializable degradation the API/UI can surface (Req 13.2).

    Returned by :func:`record_degradation` after the degradation has been written
    to the audit trail, so a caller holds a value object it can place on a
    response/case view. It carries the defined :class:`UiState`, the logical
    ``operation`` that degraded (an operation-identifying, secret-free name, e.g.
    ``"snowflake.read_freshness"``), a short human-readable ``detail``, the
    ``audit_id`` of the recorded degradation (so the UI can link to the audit
    trail), and the optional case/entity references. The model is ``frozen`` — a
    recorded degradation is a fact and is not mutated in place.
    """

    model_config = ConfigDict(frozen=True)

    ui_state: UiState = Field(
        description="Defined UI state to render for this degradation (Req 13.2).",
    )
    operation: str = Field(
        min_length=1,
        description="Operation-identifying, secret-free name of what degraded (Req 1.1).",
    )
    detail: str = Field(
        default="",
        description="Short, secret-free human-readable reason for the degradation.",
    )
    audit_id: str = Field(
        description="Id of the append-only audit record for this degradation (Req 8.1).",
    )
    case_ref: Optional[str] = Field(
        default=None,
        description="Case this degradation relates to, when applicable.",
    )
    entity_ref: Optional[str] = Field(
        default=None,
        description="Entity this degradation relates to, when applicable.",
    )

    @property
    def is_degraded(self) -> bool:
        """Whether the state is anything other than healthy (degraded or unavailable)."""
        return self.ui_state is not UiState.HEALTHY


# A healthy, un-recorded state the API can use as the default when nothing has
# degraded. It carries no audit id because there is nothing to audit.
HEALTHY_STATE = DegradationState(
    ui_state=UiState.HEALTHY,
    operation="none",
    detail="",
    audit_id="",
)


def record_degradation(
    audit: AuditService,
    operation: str,
    detail: str = "",
    *,
    ui_state: UiState = UiState.UNAVAILABLE,
    case_ref: Optional[str] = None,
    entity_ref: Optional[str] = None,
    actor_id: str = SYSTEM_ACTOR,
) -> DegradationState:
    """Record a graceful degradation and return its surfaced :class:`DegradationState`.

    Writes an append-only audit record (action :data:`ACTION_DEGRADATION_RECORDED`,
    the degraded ``operation`` as an input ref and the resolved ``ui_state`` as an
    output ref) through ``audit``, then returns a serializable
    :class:`DegradationState` the API/UI surfaces. This is the single place a
    data-source / downstream unavailability turns into a *recorded and surfaced*
    degradation — never a silent failure (Req 13.2; design §"Data-source /
    downstream unavailability").

    The helper performs no I/O of its own beyond the audit append and never
    inspects or echoes a credential; ``operation`` and ``detail`` must be
    operation-identifying and secret-free (Req 1.1), matching how
    :mod:`app.snowflake.session` and :mod:`app.snowflake.freshness` attribute
    failures.

    Args:
        audit: Audit service the degradation is recorded through (Req 8.1).
        operation: Operation-identifying, secret-free name of what degraded
            (e.g. ``"snowflake.read_freshness"`` or ``"downstream.sar_filing"``).
        detail: Short, secret-free human-readable reason for the degradation.
        ui_state: The defined UI state to surface; defaults to
            :attr:`UiState.UNAVAILABLE` (the conservative fail-safe state). Pass
            :attr:`UiState.DEGRADED` for partial availability / stale data.
        case_ref: Case this degradation relates to, when applicable.
        entity_ref: Entity this degradation relates to, when applicable.
        actor_id: Actor recorded on the audit record; defaults to the system actor.

    Returns:
        A :class:`DegradationState` carrying the recorded ``audit_id`` and the
        defined ``ui_state`` for the UI to render.
    """
    record = build_audit_record(
        action=ACTION_DEGRADATION_RECORDED,
        actor_id=actor_id or SYSTEM_ACTOR,
        case_ref=case_ref,
        entity_ref=entity_ref,
        input_refs=[operation],
        output_refs=[ui_state.value],
    )
    audit_id = audit.append(record)
    return DegradationState(
        ui_state=ui_state,
        operation=operation,
        detail=detail,
        audit_id=audit_id,
        case_ref=case_ref,
        entity_ref=entity_ref,
    )


# Return type of a step's persistence function. A step may persist a new value
# (e.g. a transitioned Case) and return it, or persist in place and return None.
T = TypeVar("T")

# A step's persistence action: performs (only) the persisted change for the step
# and returns its result. It must raise on failure so :func:`commit_step` can
# compensate; it must NOT append the audit record itself (that pairing is
# commit_step's job, so persistence and audit commit together — Req 13.4).
PersistFn = Callable[[], T]


class StepCompensationError(RuntimeError):
    """Raised by :func:`commit_step` when a step fails and compensation has run.

    Signals that the step's persisted change did **not** commit; a compensating
    audit record (action :data:`ACTION_STEP_COMPENSATED`) has been appended so the
    failure is itself audited and the case is left in a valid, fully-audited state
    with no partial, unaudited commit (Req 13.4, Property 41). The original cause
    is chained so the caller/UI can surface a classified, secret-free reason while
    the underlying error is preserved for diagnostics.

    ``operation`` identifies the step; ``compensation_audit_id`` is the id of the
    appended compensating record (so the degraded case view can link to it).
    """

    def __init__(self, operation: str, compensation_audit_id: str, detail: str) -> None:
        self.operation = operation
        self.compensation_audit_id = compensation_audit_id
        self.detail = detail
        super().__init__(
            f"[{operation}] step failed and was compensated "
            f"(compensation audit {compensation_audit_id}): {detail}"
        )


class StepResult(BaseModel, frozen=True):
    """Outcome of a successful :func:`commit_step` (persist + audit committed together).

    Returned only when the step's persisted change *and* its paired audit record
    both committed (Req 13.4). ``audit_id`` is the id of the committed step audit
    record; ``value`` is whatever ``persist_fn`` returned (e.g. the transitioned
    case), typed by the caller.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    audit_id: str = Field(description="Id of the committed step audit record (Req 8.1).")
    value: object = Field(
        default=None,
        description="Result returned by the step's persist_fn (e.g. the transitioned case).",
    )


def commit_step(
    audit: AuditService,
    persist_fn: PersistFn,
    step_record,
    *,
    compensation_action: str = ACTION_STEP_COMPENSATED,
) -> StepResult:
    """Commit a workflow step's persisted change together with its audit record.

    This is the shared unit-of-work the workflow steps (triage, investigation,
    approval, …) use so each step's persisted change and its ``Audit_Record``
    commit *together*, and a failure mid-step leaves a valid, fully-audited state
    with no partial, unaudited commit (Req 13.4, Property 41; design §"Workflow /
    transactional failures"). The protocol is:

    1. Run ``persist_fn`` (which performs **only** the step's persisted change and
       must raise on failure). Nothing is audited yet, so a persistence failure
       leaves no orphan audit record.
    2. If persistence succeeded, append the paired ``step_record`` so the change
       and its audit record are committed together, and return a :class:`StepResult`.
    3. If persistence raised, run the compensation path: append a *compensating*
       audit record (``compensation_action``, defaulting to
       :data:`ACTION_STEP_COMPENSATED`, referencing the attempted step action) so
       the failure is itself audited, then raise :class:`StepCompensationError`.
       The step's own ``step_record`` is **not** appended, so there is no record of
       a change that did not happen.

    The audit append is treated as the commit of the pair: if persistence succeeds
    the step record is written; if persistence fails only the compensating record
    is written. Both outcomes leave every persisted change with a corresponding
    audit record and no persisted change without one (Property 41).

    Args:
        audit: Audit service the step/compensation records are written through.
        persist_fn: Zero-arg callable performing the step's persisted change and
            returning its result; must raise on failure and must not itself audit.
        step_record: The step's paired :class:`~app.models.audit.AuditRecord`,
            appended only when ``persist_fn`` succeeds. Built via
            :func:`~app.services.audit_service.build_audit_record` by the caller.
        compensation_action: Action name recorded on the compensating record when
            the step fails; defaults to :data:`ACTION_STEP_COMPENSATED`.

    Returns:
        A :class:`StepResult` with the committed step ``audit_id`` and the
        ``persist_fn`` result.

    Raises:
        StepCompensationError: When ``persist_fn`` raises. A compensating audit
            record has been appended before this is raised (Property 41).
    """
    try:
        value = persist_fn()
    except Exception as exc:  # noqa: BLE001 - classify + compensate, never leak secret
        compensation_audit_id = _compensate(
            audit, step_record, compensation_action, _sanitize_error(exc)
        )
        operation = getattr(step_record, "action", "workflow.step")
        raise StepCompensationError(
            operation, compensation_audit_id, _sanitize_error(exc)
        ) from exc

    # Persistence succeeded — commit the paired audit record so the change and its
    # record land together (Req 13.4).
    audit_id = audit.append(step_record)
    return StepResult(audit_id=audit_id, value=value)


def _compensate(
    audit: AuditService,
    step_record,
    compensation_action: str,
    detail: str,
) -> str:
    """Append a compensating audit record for a failed step (Req 13.4, Property 41).

    The compensating record copies the failed step's case/entity references so the
    compensation shows up in the right case's lineage, references the attempted
    step action as an input, and carries the sanitized failure ``detail`` as an
    output. Returns the appended record's id.
    """
    attempted_action = getattr(step_record, "action", "workflow.step")
    case_ref = getattr(step_record, "case_ref", None)
    entity_ref = getattr(step_record, "entity_ref", None)
    actor_id = getattr(step_record, "actor_id", SYSTEM_ACTOR) or SYSTEM_ACTOR
    record = build_audit_record(
        action=compensation_action,
        actor_id=actor_id,
        case_ref=case_ref,
        entity_ref=entity_ref,
        input_refs=[attempted_action],
        output_refs=[detail],
    )
    return audit.append(record)


def _sanitize_error(exc: BaseException) -> str:
    """Return a short, secret-free description of a step failure (Req 1.1).

    Only the exception type and a trimmed message are surfaced; this never
    includes credential values, consistent with the Cortex/session layers.
    """
    message = str(exc).strip()
    if len(message) > 200:
        message = message[:200] + "…"
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


__all__ = [
    "UiState",
    "DegradationState",
    "HEALTHY_STATE",
    "record_degradation",
    "commit_step",
    "StepResult",
    "StepCompensationError",
    "PersistFn",
    "ACTION_DEGRADATION_RECORDED",
    "ACTION_STEP_COMPENSATED",
]
