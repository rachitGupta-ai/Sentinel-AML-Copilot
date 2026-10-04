"""Guard Service (interface + ``Impl``).

The Guard Service is the single boundary between **untrusted** inputs and the
**governed/trusted** zone (design §9, "Trust boundaries"; Req 9.1, Req 9.4). It
enforces two invariants that nothing downstream is allowed to bypass:

* **Prompt-injection detection** — *all* retrieved document content and
  natural-language input is treated as untrusted. :meth:`GuardService.scan_for_injection`
  deterministically detects instruction-override / prompt-injection patterns
  *before* the content can influence a Cortex (model) call. Flagged content is
  rejected and the rejection is recorded in the audit trail; it never reaches a
  model call (Req 9.1, Property 32).
* **Generated-operation allow-list** — every generated SQL/action must match a
  governed allow-list before execution. :meth:`GuardService.validate_operation`
  permits an operation *if and only if* it is on the allow-list and carries no
  unsafe construct; a rejected operation is not executed and is audited
  (Req 9.4, Property 35).

Design-critical ordering: a rejection (injection *or* disallowed op) is appended
to the audit trail **before** this service returns, i.e. before any model or
execution path can run. The :class:`AuditService` is injected so this guarantee
holds against the real append-only ``AUDIT.AUDIT_RECORD`` store in production and
an in-memory store in tests (design §8, §9).

Detection is **deterministic and pattern-based**, consistent with the
deterministic-first design principle: the same content/operation always yields
the same verdict, with no wall-clock time or ambient randomness in the decision
path.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Protocol, Sequence, runtime_checkable

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
# Kept stable and package-private so rejection records are queryable and the
# security property tests (Property 32, Property 35) can assert them.
# ---------------------------------------------------------------------------

# Recorded when retrieved/NL content is flagged as prompt-injection and rejected
# before any model call (Req 9.1, Property 32).
ACTION_INJECTION_REJECTED = "guard.injection_rejected"

# Recorded when a generated SQL/action fails allow-list validation and is
# rejected before execution (Req 9.4, Property 35).
ACTION_OPERATION_REJECTED = "guard.operation_rejected"


# ---------------------------------------------------------------------------
# Deterministic detection patterns.
#
# Pattern-based, case-insensitive, and fixed at import time so detection is
# fully deterministic (same input -> same verdict). The patterns target the
# instruction-override / role-reset / exfiltration shapes that prompt-injection
# payloads use when smuggled inside retrieved document content or NL input
# (design "Trust boundaries"; Req 9.1).
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"\b(ignore|disregard|forget|override|bypass)\b[\s\S]{0,40}?"
            r"\b(previous|prior|above|earlier|all|any)\b[\s\S]{0,20}?"
            r"\b(instruction|instructions|prompt|prompts|rule|rules|context|directive|directives)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role_reassignment",
        re.compile(
            r"\byou\s+are\s+now\b|\bact\s+as\b|\bpretend\s+to\s+be\b|"
            r"\bfrom\s+now\s+on\b|\bnew\s+(instructions|persona|role)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_probe",
        re.compile(
            r"\b(system\s+prompt|developer\s+message|your\s+instructions|"
            r"reveal\s+(?:your\s+)?(?:prompt|instructions|rules))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "guardrail_disable",
        re.compile(
            r"\b(disable|turn\s+off|remove|lift|ignore)\b[\s\S]{0,30}?"
            r"\b(guardrail|guardrails|safety|safeguard|safeguards|filter|filters|restriction|restrictions)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "secret_exfiltration",
        re.compile(
            r"\b(print|reveal|show|exfiltrate|leak|dump|send)\b[\s\S]{0,30}?"
            r"\b(credential|credentials|secret|secrets|password|passwords|api[\s_-]?key|token|tokens|env|environment\s+variable)\b",
            re.IGNORECASE,
        ),
    ),
)


# ---------------------------------------------------------------------------
# Governed-operation allow-list.
#
# Only read-only access to the governed semantic/registry/vector surfaces and
# the append-only audit insert are permitted from generated operations. Figures
# always originate inside the governed zone via these read paths (Req 3.2); the
# LLM never writes or computes a regulatory figure, so no DML/DDL against RAW/APP
# is allow-listed here (design "Trust boundaries"; Req 9.4).
# ---------------------------------------------------------------------------

# Verbs a generated SQL statement is allowed to begin with.
_ALLOWED_SQL_VERBS: frozenset[str] = frozenset({"SELECT", "WITH"})

# Fully-qualified objects a generated SELECT is allowed to read from.
_ALLOWED_READ_OBJECTS: frozenset[str] = frozenset(
    {
        "SEM.ENTITY_RISK_METRICS",
        "SEM.METRIC_DEFINITION_REGISTRY",
        "VEC.POLICY_CHUNK",
        "VEC.EVIDENCE_CHUNK",
    }
)

# Named (non-SQL) governed actions the agent layer may request.
_ALLOWED_NAMED_ACTIONS: frozenset[str] = frozenset(
    {
        "aggregate_entity_risk",
        "get_metric",
        "resolve_term",
        "retrieve_policy_evidence",
        "submit_for_approval",
    }
)

# Unsafe SQL constructs that reject an operation outright, even if it otherwise
# starts with an allowed verb (e.g. a stacked/mutating statement). Deterministic,
# conservative, and fail-closed: anything matching here is rejected (Req 9.4).
_UNSAFE_SQL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r";\s*\S", re.IGNORECASE),  # stacked statements after a ';'
    re.compile(
        r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|"
        r"CALL|EXECUTE|COPY|PUT|REMOVE|UNDROP|USE)\b",
        re.IGNORECASE,
    ),
    re.compile(r"--|/\*|\*/"),  # inline/block comment (comment-smuggling)
)

_FQN_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b")


# ---------------------------------------------------------------------------
# Result value objects.
# ---------------------------------------------------------------------------


class ScanResult(BaseModel):
    """Outcome of scanning untrusted content for prompt-injection (Req 9.1).

    ``is_injection`` is the verdict the caller gates on: when true the content is
    rejected and must never reach a Cortex call. ``categories`` names the
    detector(s) that fired (deterministic, pattern-based), and ``audit_id`` is the
    id of the rejection record appended *before* this result was returned, so the
    block is provably audited ahead of any model path (Property 32).
    """

    model_config = ConfigDict(frozen=True)

    is_injection: bool = Field(
        description="True when instruction-override/injection content was detected."
    )
    categories: list[str] = Field(
        default_factory=list,
        description="Names of the injection detectors that matched (empty when clean).",
    )
    audit_id: str | None = Field(
        default=None,
        description="Audit id of the rejection record (set only when rejected).",
    )

    @property
    def is_clean(self) -> bool:
        """Whether the content passed and may proceed toward a model call."""
        return not self.is_injection


class OperationDecision(str, Enum):
    """Allow-list verdict for a generated SQL/action (Req 9.4)."""

    ALLOWED = "allowed"
    REJECTED = "rejected"


class OperationValidation(BaseModel):
    """Outcome of validating a generated operation against the allow-list (Req 9.4).

    ``decision`` is :attr:`OperationDecision.ALLOWED` *iff* the operation matches a
    governed allow-list entry and contains no unsafe construct; otherwise it is
    :attr:`OperationDecision.REJECTED` and the operation is not executed. On
    rejection ``reason`` explains why and ``audit_id`` references the audit record
    appended *before* this result was returned (Property 35).
    """

    model_config = ConfigDict(frozen=True)

    decision: OperationDecision = Field(description="ALLOWED or REJECTED.")
    reason: str | None = Field(
        default=None,
        description="Why the operation was rejected (None when allowed).",
    )
    audit_id: str | None = Field(
        default=None,
        description="Audit id of the rejection record (set only when rejected).",
    )

    @property
    def is_allowed(self) -> bool:
        """Whether the operation may be executed."""
        return self.decision is OperationDecision.ALLOWED


# ---------------------------------------------------------------------------
# Interface + Impl.
# ---------------------------------------------------------------------------


@runtime_checkable
class GuardService(Protocol):
    """Boundary guard: injection detection + generated-operation allow-list (Req 9.1, 9.4).

    Implementations treat all retrieved content as untrusted, reject detected
    prompt-injection before any model call, validate every generated SQL/action
    against a governed allow-list before execution, and record every rejection in
    the audit trail *before* returning (design §9).
    """

    def scan_for_injection(
        self,
        content: str,
        *,
        source_ref: str | None = None,
        case_ref: str | None = None,
        entity_ref: str | None = None,
    ) -> ScanResult:
        """Scan untrusted ``content`` for prompt-injection before any model call.

        Returns a :class:`ScanResult`; when injection is detected the result's
        ``is_injection`` is true and a rejection has already been appended to the
        audit trail (Req 9.1, Property 32).
        """
        ...

    def validate_operation(
        self,
        generated_sql_or_action: str,
        *,
        case_ref: str | None = None,
        entity_ref: str | None = None,
        actor_id: str = SYSTEM_ACTOR,
    ) -> OperationValidation:
        """Validate a generated SQL/action against the governed allow-list.

        Returns an :class:`OperationValidation`; a rejected operation is not
        executed and the rejection has already been audited (Req 9.4, Property 35).
        """
        ...


class GuardServiceImpl:
    """Default :class:`GuardService` with deterministic, pattern-based detection.

    The :class:`AuditService` is injected so every rejection — injection or
    disallowed operation — is appended to the audit trail *before* this service
    returns, i.e. before any model or execution path. When no audit service is
    supplied an :class:`AuditServiceImpl` over an in-memory store is used, which
    is convenient for local runs and unit tests (design §8, §9).
    """

    def __init__(self, audit_service: AuditService | None = None) -> None:
        self._audit: AuditService = (
            audit_service if audit_service is not None else AuditServiceImpl()
        )

    # -- Prompt-injection detection (Req 9.1, Property 32) -------------------

    def scan_for_injection(
        self,
        content: str,
        *,
        source_ref: str | None = None,
        case_ref: str | None = None,
        entity_ref: str | None = None,
    ) -> ScanResult:
        """See :meth:`GuardService.scan_for_injection`.

        Treats ``content`` as untrusted regardless of origin, matches it against
        the deterministic injection patterns, and — when any fire — appends a
        rejection audit record *before* returning so the block provably precedes
        any Cortex call (Property 32). Benign content returns a clean result and
        is **not** audited (only rejections are recorded, per Req 9.1).
        """
        categories = self._detect_injection_categories(content)
        if not categories:
            return ScanResult(is_injection=False, categories=[], audit_id=None)

        # Record the rejection BEFORE returning — nothing downstream may call a
        # model with this content (Req 9.1). ``source_ref`` ties the rejection to
        # the offending document/input for lineage.
        audit_id = self._audit.append(
            build_audit_record(
                action=ACTION_INJECTION_REJECTED,
                actor_id=SYSTEM_ACTOR,
                case_ref=case_ref,
                entity_ref=entity_ref,
                input_refs=[source_ref] if source_ref else [],
                output_refs=list(categories),
            )
        )
        return ScanResult(is_injection=True, categories=categories, audit_id=audit_id)

    @staticmethod
    def _detect_injection_categories(content: str) -> list[str]:
        """Return the names of the injection detectors matching ``content``.

        Deterministic and order-stable: patterns are evaluated in their declared
        order so the resulting category list is identical for identical input
        (deterministic-first principle). Non-string or empty content yields no
        matches.
        """
        if not content:
            return []
        return [name for name, pattern in _INJECTION_PATTERNS if pattern.search(content)]

    # -- Generated-operation allow-list (Req 9.4, Property 35) --------------

    def validate_operation(
        self,
        generated_sql_or_action: str,
        *,
        case_ref: str | None = None,
        entity_ref: str | None = None,
        actor_id: str = SYSTEM_ACTOR,
    ) -> OperationValidation:
        """See :meth:`GuardService.validate_operation`.

        Permits execution *iff* the operation is on the governed allow-list and
        carries no unsafe construct; otherwise rejects it, records the rejection
        *before* returning, and the caller must not execute it (Property 35).
        Fail-closed: an empty or unrecognised operation is rejected.
        """
        reason = self._disallowed_reason(generated_sql_or_action)
        if reason is None:
            return OperationValidation(
                decision=OperationDecision.ALLOWED, reason=None, audit_id=None
            )

        # Record the rejection BEFORE returning — a rejected op is never executed
        # (Req 9.4). The offending operation text is referenced via input_refs.
        audit_id = self._audit.append(
            build_audit_record(
                action=ACTION_OPERATION_REJECTED,
                actor_id=actor_id or SYSTEM_ACTOR,
                case_ref=case_ref,
                entity_ref=entity_ref,
                input_refs=[generated_sql_or_action],
                output_refs=[reason],
            )
        )
        return OperationValidation(
            decision=OperationDecision.REJECTED, reason=reason, audit_id=audit_id
        )

    @classmethod
    def _disallowed_reason(cls, operation: str) -> str | None:
        """Return a rejection reason, or ``None`` when the operation is allowed.

        A pure, deterministic classifier (no I/O, no clock): an operation is
        allowed only if it is a recognised governed named-action, or a read-only
        ``SELECT``/``WITH`` statement that contains no unsafe construct and reads
        only from allow-listed objects (Req 9.4).
        """
        if operation is None:
            return "empty operation"
        candidate = operation.strip()
        if not candidate:
            return "empty operation"

        # Named (non-SQL) governed actions: exact-match against the allow-list.
        if candidate in _ALLOWED_NAMED_ACTIONS:
            return None

        upper = candidate.upper()
        first_token = upper.split(None, 1)[0] if upper.split() else ""

        # Anything that is not a recognised read verb and not a named action is
        # out of scope and rejected (fail-closed).
        if first_token not in _ALLOWED_SQL_VERBS:
            return f"operation not on governed allow-list: '{first_token or candidate}'"

        # Unsafe constructs reject even an otherwise-read statement (stacked
        # statements, DML/DDL keywords, comment-smuggling).
        for pattern in _UNSAFE_SQL_PATTERNS:
            if pattern.search(candidate):
                return "operation contains an unsafe or non-read construct"

        # Every fully-qualified object the statement references must be an
        # allow-listed governed read surface.
        referenced = cls._referenced_objects(candidate)
        disallowed = sorted(referenced - _ALLOWED_READ_OBJECTS)
        if disallowed:
            return f"operation reads non-governed object(s): {', '.join(disallowed)}"
        if not referenced:
            return "operation references no governed object"

        return None

    @staticmethod
    def _referenced_objects(sql: str) -> set[str]:
        """Extract the fully-qualified ``SCHEMA.OBJECT`` references from ``sql``.

        Upper-cased so comparison against the allow-list is case-insensitive.
        Only ``SCHEMA.OBJECT`` shapes are considered (the governed read surfaces
        are all two-part names); bare identifiers and aliases are ignored.
        """
        return {match.group(1).upper() for match in _FQN_PATTERN.finditer(sql)}


__all__ = [
    "GuardService",
    "GuardServiceImpl",
    "ScanResult",
    "OperationValidation",
    "OperationDecision",
    "ACTION_INJECTION_REJECTED",
    "ACTION_OPERATION_REJECTED",
]
