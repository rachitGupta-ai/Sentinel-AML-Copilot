"""Authorization Service (interface + ``Impl``).

The Authorization Service is the single decision point for **role-based access**
to the three protected operations named in the design (design "Authorization
(auth)"; Req 9.2):

* **view a case** — only entitled roles may open/read a case,
* **approve an action** — only entitled roles may pass an Approval_Gate,
* **change a Governed_Metric definition** — only entitled roles may create a new
  Metric_Definition_Version.

The authorization contract is deterministic and total (Property 33): for any
``(role, protected action)`` pair, access is granted *if and only if* the role is
in that action's permitted-role set. Entitlements are declared explicitly below
(no implicit grants, no wildcard), so the same inputs always yield the same
verdict — consistent with the deterministic-first design principle.

Every **denial** is recorded in the immutable audit trail *before* this service
returns, so an unauthorized attempt is both refused and auditable (Req 9.2,
Property 33). The :class:`~app.services.audit_service.AuditService` is injected so
the guarantee holds against the real append-only ``AUDIT.AUDIT_RECORD`` store in
production and an in-memory store in tests (design §8, §9), mirroring the
:class:`~app.services.guard_service.GuardServiceImpl` pattern.

Masking / row-access enforcement for sensitive synthetic fields (Req 9.3) is a
*Snowflake-native* control applied by the policies in ``snowflake/policies/`` and
referenced from :mod:`app.snowflake.policies`; this service governs the
application-layer action checks (Req 9.2). The two together satisfy Req 9.2/9.3.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.services.audit_service import (
    SYSTEM_ACTOR,
    AuditService,
    AuditServiceImpl,
    build_audit_record,
)

# ---------------------------------------------------------------------------
# Canonical audit action name.
#
# Kept stable and package-private so denial records are queryable and the RBAC
# property test (Property 33) can assert them.
# ---------------------------------------------------------------------------

# Recorded when a role is denied a protected action (Req 9.2, Property 33).
ACTION_ACCESS_DENIED = "auth.access_denied"


class Role(str, Enum):
    """Application roles that drive entitlement decisions (Req 9.2).

    Declared explicitly so authorization is deterministic and auditable. These
    map onto the Snowflake roles used by the masking / row-access policies in
    ``snowflake/policies/`` (compliance officers and metric stewards are entitled
    to see sensitive synthetic fields; analysts and viewers are not — Req 9.3).

    * ``ANALYST`` — fraud analyst: investigates cases (view), but cannot approve
      material actions or change governed metric definitions.
    * ``COMPLIANCE_OFFICER`` — reviews full lineage and approves/rejects material
      actions; may view cases.
    * ``METRIC_STEWARD`` — owns Governed_Metric definitions; may change a metric
      definition (minting a new Metric_Definition_Version) and view cases.
    * ``VIEWER`` — read-only observer: may view a case but take no action.
    """

    ANALYST = "analyst"
    COMPLIANCE_OFFICER = "compliance_officer"
    METRIC_STEWARD = "metric_steward"
    VIEWER = "viewer"

    @classmethod
    def from_string(cls, value: str | None) -> "Role | None":
        """Resolve a role name to a :class:`Role`, or ``None`` when unrecognised.

        Case-insensitive and whitespace-tolerant. An unknown or missing role
        resolves to ``None`` so the caller fails closed (an unknown role is
        entitled to nothing — Property 33).
        """
        if not value:
            return None
        normalized = value.strip().lower()
        for role in cls:
            if role.value == normalized:
                return role
        return None


class ProtectedAction(str, Enum):
    """The protected operations guarded by RBAC (design "Authorization"; Req 9.2).

    Exactly the three actions named in the design: viewing a case, approving an
    action, and changing a Governed_Metric definition.
    """

    VIEW_CASE = "view_case"
    APPROVE_ACTION = "approve_action"
    CHANGE_METRIC_DEFINITION = "change_metric_definition"


# ---------------------------------------------------------------------------
# Entitlement matrix.
#
# The single source of truth for "which roles may perform which action". Declared
# explicitly (no wildcards, no inheritance magic) so the check is deterministic
# and the permitted-role set for each action is auditable (Property 33). Any role
# not listed for an action is denied that action; any unknown role is entitled to
# nothing (fail-closed).
# ---------------------------------------------------------------------------

_ENTITLEMENTS: dict[ProtectedAction, frozenset[Role]] = {
    # Viewing a case is the broadest entitlement: every known role may read.
    ProtectedAction.VIEW_CASE: frozenset(
        {Role.ANALYST, Role.COMPLIANCE_OFFICER, Role.METRIC_STEWARD, Role.VIEWER}
    ),
    # Only a compliance officer passes an Approval_Gate (Req 7, Req 9.2).
    ProtectedAction.APPROVE_ACTION: frozenset({Role.COMPLIANCE_OFFICER}),
    # Only a metric steward may mint a new Metric_Definition_Version (Req 3.4, 9.2).
    ProtectedAction.CHANGE_METRIC_DEFINITION: frozenset({Role.METRIC_STEWARD}),
}


def permitted_roles(action: ProtectedAction) -> frozenset[Role]:
    """Return the exact set of roles entitled to ``action`` (Property 33).

    Exposed so the API layer and tests can introspect the entitlement matrix
    without duplicating it. An action with no explicit entry permits no role
    (fail-closed).
    """
    return _ENTITLEMENTS.get(action, frozenset())


class AuthorizationDecision(str, Enum):
    """RBAC verdict for a ``(role, action)`` pair (Req 9.2)."""

    PERMITTED = "permitted"
    DENIED = "denied"


class AuthorizationResult(BaseModel):
    """Outcome of an authorization check (Req 9.2, Property 33).

    ``decision`` is :attr:`AuthorizationDecision.PERMITTED` *iff* the role is in
    the action's permitted-role set; otherwise it is
    :attr:`AuthorizationDecision.DENIED`. On denial ``reason`` explains why and
    ``audit_id`` references the audit record appended *before* this result was
    returned, so the denial is provably audited (Property 33). A permitted result
    is **not** audited here (only denials are recorded by this service; the
    performed action is audited by the service that carries it out).
    """

    model_config = ConfigDict(frozen=True)

    decision: AuthorizationDecision = Field(description="PERMITTED or DENIED.")
    action: ProtectedAction = Field(description="The protected action that was checked.")
    role: Role | None = Field(
        default=None,
        description="The resolved role (None when the supplied role was unrecognised).",
    )
    reason: str | None = Field(
        default=None,
        description="Why access was denied (None when permitted).",
    )
    audit_id: str | None = Field(
        default=None,
        description="Audit id of the denial record (set only when denied).",
    )

    @property
    def is_permitted(self) -> bool:
        """Whether the action may proceed."""
        return self.decision is AuthorizationDecision.PERMITTED


class AuthorizationError(PermissionError):
    """Raised by :meth:`AuthService.authorize` when access is denied (Req 9.2).

    Carries the denial :class:`AuthorizationResult` (including the ``audit_id`` of
    the recorded denial) so an API layer can translate it into an *unauthorised*
    response while the denial is already captured in the audit trail.
    """

    def __init__(self, result: AuthorizationResult) -> None:
        self.result = result
        super().__init__(
            f"Access denied for role "
            f"'{result.role.value if result.role else '<unknown>'}' "
            f"on action '{result.action.value}': {result.reason}"
        )


# ---------------------------------------------------------------------------
# Interface + Impl.
# ---------------------------------------------------------------------------


@runtime_checkable
class AuthService(Protocol):
    """Role-based access control for protected operations (design "Authorization"; Req 9.2).

    Implementations grant an action *iff* the role is entitled, deny otherwise,
    and record every denial in the audit trail *before* returning (Property 33).
    """

    def check(
        self,
        role: Role | str | None,
        action: ProtectedAction,
        *,
        actor_id: str = SYSTEM_ACTOR,
        case_ref: str | None = None,
        entity_ref: str | None = None,
    ) -> AuthorizationResult:
        """Return an :class:`AuthorizationResult` for ``(role, action)``.

        Non-raising form: callers inspect ``result.is_permitted``. On denial the
        rejection has already been appended to the audit trail (Req 9.2,
        Property 33).
        """
        ...

    def authorize(
        self,
        role: Role | str | None,
        action: ProtectedAction,
        *,
        actor_id: str = SYSTEM_ACTOR,
        case_ref: str | None = None,
        entity_ref: str | None = None,
    ) -> AuthorizationResult:
        """Like :meth:`check` but raise :class:`AuthorizationError` on denial.

        Convenience for call sites that want to abort on an unauthorised request;
        the denial is audited before the exception is raised (Req 9.2).
        """
        ...


class AuthServiceImpl:
    """Default :class:`AuthService` driven by the explicit entitlement matrix.

    The check is pure and deterministic: a role is permitted an action *iff* it is
    in :data:`_ENTITLEMENTS` for that action (Property 33). The injected
    :class:`AuditService` records every denial *before* this service returns, so
    an unauthorized attempt is refused and auditable in one step (Req 9.2). When no
    audit service is supplied an :class:`AuditServiceImpl` over an in-memory store
    is used, convenient for local runs and unit tests (design §8, §9).
    """

    def __init__(self, audit_service: AuditService | None = None) -> None:
        self._audit: AuditService = (
            audit_service if audit_service is not None else AuditServiceImpl()
        )

    def check(
        self,
        role: Role | str | None,
        action: ProtectedAction,
        *,
        actor_id: str = SYSTEM_ACTOR,
        case_ref: str | None = None,
        entity_ref: str | None = None,
    ) -> AuthorizationResult:
        """See :meth:`AuthService.check`.

        Resolves the role (an unrecognised role is entitled to nothing), consults
        the entitlement matrix, and — on denial — appends a denial audit record
        *before* returning so the block provably precedes any guarded operation
        (Property 33).
        """
        resolved = role if isinstance(role, Role) else Role.from_string(role)

        if resolved is not None and resolved in permitted_roles(action):
            return AuthorizationResult(
                decision=AuthorizationDecision.PERMITTED,
                action=action,
                role=resolved,
                reason=None,
                audit_id=None,
            )

        # Denied: build a secret-free reason, then record the denial BEFORE
        # returning — nothing downstream may perform the action (Req 9.2).
        if resolved is None:
            raw = role if isinstance(role, str) else ("<none>" if role is None else str(role))
            reason = f"unrecognised role '{raw}' is not entitled to '{action.value}'"
        else:
            reason = f"role '{resolved.value}' is not entitled to '{action.value}'"

        audit_id = self._audit.append(
            build_audit_record(
                action=ACTION_ACCESS_DENIED,
                actor_id=actor_id or SYSTEM_ACTOR,
                case_ref=case_ref,
                entity_ref=entity_ref,
                input_refs=[action.value, resolved.value if resolved else "<unknown_role>"],
                output_refs=[reason],
            )
        )
        return AuthorizationResult(
            decision=AuthorizationDecision.DENIED,
            action=action,
            role=resolved,
            reason=reason,
            audit_id=audit_id,
        )

    def authorize(
        self,
        role: Role | str | None,
        action: ProtectedAction,
        *,
        actor_id: str = SYSTEM_ACTOR,
        case_ref: str | None = None,
        entity_ref: str | None = None,
    ) -> AuthorizationResult:
        """See :meth:`AuthService.authorize`.

        Delegates to :meth:`check` (so the denial is audited) and raises
        :class:`AuthorizationError` carrying the denial result when not permitted.
        """
        result = self.check(
            role,
            action,
            actor_id=actor_id,
            case_ref=case_ref,
            entity_ref=entity_ref,
        )
        if not result.is_permitted:
            raise AuthorizationError(result)
        return result


__all__ = [
    "AuthService",
    "AuthServiceImpl",
    "Role",
    "ProtectedAction",
    "AuthorizationDecision",
    "AuthorizationResult",
    "AuthorizationError",
    "permitted_roles",
    "ACTION_ACCESS_DENIED",
]
