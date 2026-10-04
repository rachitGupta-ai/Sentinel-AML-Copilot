"""Auth router: role introspection and RBAC checks (Req 9.2).

Exposes the Authorization Service so the frontend can drive role-aware UI and the
"same answer, provably for two roles" demonstration:

* ``GET /api/auth/whoami`` — the caller's resolved role (from the role header) and
  the actions it is entitled to.
* ``GET /api/auth/roles`` — the entitlement matrix (which roles may perform which
  protected action), for building unauthorised-state UI.
* ``POST /api/auth/check`` — check whether a given role may perform a protected
  action; a denial is recorded in the audit trail before the response is returned,
  so the check itself exercises the audited RBAC path (Req 9.2, Property 33).

These routes are introspection/health-style and are not themselves gated — they
reveal only the entitlement model, never governed data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import AppServices, get_role, get_services
from app.api.schemas import AuthCheckRequest, AuthCheckResponse
from app.services.auth_service import (
    AuthorizationDecision,
    ProtectedAction,
    Role,
    permitted_roles,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _entitled_actions(role: Role | None) -> list[str]:
    """Return the protected actions ``role`` is entitled to (empty for None)."""
    if role is None:
        return []
    return [
        action.value for action in ProtectedAction if role in permitted_roles(action)
    ]


@router.get("/whoami")
async def whoami(role: Role | None = Depends(get_role)) -> dict[str, object]:
    """Return the caller's resolved role and entitled actions (fail-closed).

    An absent/unknown role resolves to ``None`` and an empty entitlement list, so
    the UI renders the unauthorised state rather than assuming access.
    """
    return {
        "role": role.value if role is not None else None,
        "entitled_actions": _entitled_actions(role),
    }


@router.get("/roles")
async def list_roles() -> dict[str, list[str]]:
    """Return the entitlement matrix: action -> permitted role names (Req 9.2)."""
    return {
        action.value: sorted(r.value for r in permitted_roles(action))
        for action in ProtectedAction
    }


@router.post("/check", response_model=AuthCheckResponse)
async def check_access(
    body: AuthCheckRequest,
    services: AppServices = Depends(get_services),
) -> AuthCheckResponse:
    """Check whether ``role`` may perform ``action``; a denial is audited (Req 9.2).

    Uses the non-raising :meth:`AuthService.check`, which appends a denial audit
    record before returning when access is refused (Property 33). The response
    carries the verdict and, on denial, the reason and the audit id of the record.
    """
    result = services.auth_service.check(body.role, body.action)
    decision = "permitted" if result.decision is AuthorizationDecision.PERMITTED else "denied"
    return AuthCheckResponse(
        decision=decision,
        action=result.action,
        role=result.role,
        reason=result.reason,
        audit_id=result.audit_id,
    )
