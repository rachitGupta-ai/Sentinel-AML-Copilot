"""Snowflake masking / row-access policy helpers.

These helpers *reference and apply* the Snowflake-native masking and row-access
policies defined in ``snowflake/policies/05_policies.sql`` (design "Authorization
(auth)"; Req 9.3). The SQL file is the source of truth for the policy bodies and
column bindings; this module gives the backend deterministic, non-secret handles
to those objects and a way to (re)apply them through a Snowpark session when
provisioning programmatically.

Separation of concerns:

* **Req 9.2 (RBAC on protected actions)** is enforced in the application layer by
  :mod:`app.services.auth_service`.
* **Req 9.3 (sensitive-field masking / row access)** is enforced *inside
  Snowflake* by the policies referenced here, so visibility is governed by the
  effective role at query time (Property 34) — the backend cannot accidentally
  leak a masked value because the engine applies the policy, not the app.

The role names exposed here mirror :class:`app.services.auth_service.Role` so a
single logical role maps 1:1 between the app layer and the Snowflake policies.
Nothing in this module logs or echoes a credential value (Req 1.1); errors
identify the *operation* and the policy object by its non-secret name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from app.services.auth_service import Role

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids importing Snowpark eagerly
    from snowflake.snowpark import Session

logger = logging.getLogger("governed_aml")

# Logical operation name for attributable, secret-free errors (mirrors session.py).
_OP_APPLY_POLICIES = "snowflake.apply_policies"

# Path to the authoritative policy SQL, relative to the repository root
# (backend/app/snowflake/policies.py -> repo root is three parents up).
_REPO_ROOT = Path(__file__).resolve().parents[3]
POLICY_SQL_PATH = _REPO_ROOT / "snowflake" / "policies" / "05_policies.sql"


# ---------------------------------------------------------------------------
# Policy object names (the single Python-side reference to the SQL objects).
# Kept in sync with snowflake/policies/05_policies.sql.
# ---------------------------------------------------------------------------

# Masking policies.
MASKING_POLICY_DISPLAY_NAME = "MASK_DISPLAY_NAME"
MASKING_POLICY_ACCOUNT_IDENTIFIER = "MASK_ACCOUNT_IDENTIFIER"

# Row-access policy.
ROW_ACCESS_POLICY_ENTITY = "RAP_ENTITY_RESTRICTED"

# Snowflake entitlement role names, keyed by the application :class:`Role`. These
# are the roles the masking/row-access policies check with ``IS_ROLE_IN_SESSION``.
SNOWFLAKE_ROLE_BY_APP_ROLE: dict[Role, str] = {
    Role.COMPLIANCE_OFFICER: "AML_COMPLIANCE_OFFICER",
    Role.METRIC_STEWARD: "AML_METRIC_STEWARD",
    Role.ANALYST: "AML_ANALYST",
    Role.VIEWER: "AML_VIEWER",
}

# Roles entitled to see sensitive synthetic fields unmasked (Req 9.3). Mirrors the
# ``IS_ROLE_IN_SESSION`` entitlement checks in the masking policies so the backend
# can answer "would this role see the raw value?" without a round-trip.
_ENTITLED_TO_SENSITIVE: frozenset[Role] = frozenset(
    {Role.COMPLIANCE_OFFICER, Role.METRIC_STEWARD}
)


def snowflake_role_for(role: Role) -> str:
    """Return the Snowflake role name backing an application :class:`Role`.

    The returned name is the role the masking/row-access policies evaluate with
    ``IS_ROLE_IN_SESSION`` (Req 9.3). Deterministic and total over the enum.
    """
    return SNOWFLAKE_ROLE_BY_APP_ROLE[role]


def is_entitled_to_sensitive(role: Role | None) -> bool:
    """Whether ``role`` sees sensitive synthetic fields unmasked (Req 9.3).

    Mirrors the masking-policy entitlement exactly: compliance officers and metric
    stewards are entitled; analysts, viewers, and an unknown/``None`` role are not
    (fail-closed — Property 34). This lets the backend predict masking behaviour
    (e.g. for UI state) consistently with what Snowflake will actually return.
    """
    return role in _ENTITLED_TO_SENSITIVE


@dataclass(frozen=True)
class PolicyApplicationResult:
    """Outcome of (re)applying the policy script, without crashing the caller.

    Attributes:
        applied: True when every statement executed successfully.
        statements_executed: Count of SQL statements run from the script.
        operation: The logical operation attempted (for attribution).
        detail: A human-readable, secret-free description of the outcome.
    """

    applied: bool
    statements_executed: int
    operation: str = _OP_APPLY_POLICIES
    detail: str = ""


def apply_policies(
    session: "Session",
    *,
    db: str | None = None,
    sql_path: Path | None = None,
) -> PolicyApplicationResult:
    """Apply the masking / row-access policies from the SQL script (Req 9.3).

    Reads ``snowflake/policies/05_policies.sql`` and executes its statements
    through the Snowpark ``session`` so the policies and their column bindings are
    provisioned programmatically (an alternative to running the file via the CoCo
    CLI). The script is idempotent (``CREATE ... IF NOT EXISTS`` and benign
    re-binds), so this is safe to re-run.

    Never raises on a Snowflake error: failures are caught and surfaced in the
    returned :class:`PolicyApplicationResult` with an operation-identifying,
    secret-free ``detail`` (consistent with the health-check philosophy in
    :mod:`app.snowflake.session`).

    Args:
        session: Connected Snowpark session with privileges to create policies.
        db: Optional database name to bind ``$db``; defaults to the connection's
            database when omitted.
        sql_path: Override the policy SQL path (defaults to :data:`POLICY_SQL_PATH`).

    Returns:
        A :class:`PolicyApplicationResult` describing success/failure.
    """
    path = sql_path or POLICY_SQL_PATH
    try:
        script = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("Could not read policy SQL during %s: %s", _OP_APPLY_POLICIES, path)
        return PolicyApplicationResult(
            applied=False,
            statements_executed=0,
            detail=f"Policy SQL not readable at {path}: {type(exc).__name__}.",
        )

    statements = _split_sql_statements(script)
    executed = 0
    try:
        if db:
            session.sql("SET db = ?", params=[db]).collect()
        for statement in statements:
            session.sql(statement).collect()
            executed += 1
    except Exception as exc:  # noqa: BLE001 - never crash the caller; surface a classified detail
        logger.error(
            "Policy application failed during %s after %d statement(s): %s",
            _OP_APPLY_POLICIES,
            executed,
            type(exc).__name__,
        )
        return PolicyApplicationResult(
            applied=False,
            statements_executed=executed,
            detail=(
                f"Policy application failed after {executed} statement(s) "
                f"({type(exc).__name__}); see server logs."
            ),
        )

    return PolicyApplicationResult(
        applied=True,
        statements_executed=executed,
        detail=f"Applied {executed} policy statement(s) from {path.name}.",
    )


def _split_sql_statements(script: str) -> list[str]:
    """Split a SQL script into executable statements for the Snowpark driver.

    Snowpark's ``session.sql`` executes a single statement at a time, so the
    multi-statement policy script is split on top-level semicolons. Full-line
    comments (``-- ...``) are stripped first so a trailing comment does not become
    an empty statement; inline SQL in the policy bodies contains no semicolons, so
    a simple split is sufficient and deterministic for this curated file.
    """
    lines = [line for line in script.splitlines() if not line.lstrip().startswith("--")]
    cleaned = "\n".join(lines)
    return [stmt.strip() for stmt in cleaned.split(";") if stmt.strip()]


__all__ = [
    "POLICY_SQL_PATH",
    "MASKING_POLICY_DISPLAY_NAME",
    "MASKING_POLICY_ACCOUNT_IDENTIFIER",
    "ROW_ACCESS_POLICY_ENTITY",
    "SNOWFLAKE_ROLE_BY_APP_ROLE",
    "snowflake_role_for",
    "is_entitled_to_sensitive",
    "PolicyApplicationResult",
    "apply_policies",
]
