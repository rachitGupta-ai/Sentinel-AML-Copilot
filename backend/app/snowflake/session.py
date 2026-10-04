"""Snowflake session factory, startup credential guard, and health check.

SentinelAML Copilot is Snowflake-native and **config-only** for credentials
(Req 1.1): the session is built exclusively from :class:`app.config.settings.Settings`,
which binds the connection parameters from the environment / configuration. No
credential value is embedded in source, prompts, or logs.

This module provides the three interfaces named in the design (design §1,
"Snowflake Session & Startup Guard"):

* ``get_session()`` — build (and cache) a Snowpark ``Session`` usable by all AI,
  semantic, and persistence operations (Req 1.4).
* ``health() -> HealthStatus`` — probe connectivity/auth and surface a clear,
  **operation-identifying** error WITHOUT crashing the process (Req 1.4).
* ``run_startup_guard()`` — the hard startup abort: when ``validate_config()``
  returns a non-empty list, raise :class:`StartupConfigError` naming **every**
  missing setting and never echoing a secret value (Req 1.2, Req 1.1).

Prohibited everywhere here: logging or echoing credential values. Errors
reference the *operation* that failed and the connection *target* by non-secret
coordinates (account/user/role/warehouse/database/schema names), never the
authentication secret (Req 1.1, design Property 2).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from app.config.settings import Settings, validate_config

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids importing Snowpark eagerly
    from snowflake.snowpark import Session

logger = logging.getLogger("governed_aml")

# Logical name of the operation each error identifies, so failures are
# attributable without exposing any secret (Req 1.4).
_OP_BUILD_SESSION = "snowflake.get_session"
_OP_HEALTH = "snowflake.health"
_OP_STARTUP_GUARD = "snowflake.startup_guard"


class StartupConfigError(RuntimeError):
    """Raised to abort startup when required configuration is missing (Req 1.2).

    Carries the exact set of missing setting *names* (never values) so callers
    and operators can see every setting that must be provided before the service
    will serve requests.
    """

    def __init__(self, missing: list[str]) -> None:
        self.missing = list(missing)
        super().__init__(
            "Startup aborted: missing required Snowflake configuration settings: "
            + ", ".join(self.missing)
        )


class HealthState(str, Enum):
    """Coarse health classification surfaced to callers and the UI.

    ``HEALTHY`` — a session was established and a trivial probe query succeeded.
    ``MISCONFIGURED`` — required configuration is missing; we never attempted a
        connection (so no secret is involved).
    ``UNAVAILABLE`` — connection/auth/probe failed; the error is classified and
        operation-identifying, and the process keeps running (Req 1.4).
    """

    HEALTHY = "healthy"
    MISCONFIGURED = "misconfigured"
    UNAVAILABLE = "unavailable"


class FailureKind(str, Enum):
    """Classification of a connectivity/auth failure (Req 1.4).

    These let the UI and logs identify *what kind* of problem occurred without
    revealing any secret: a configuration gap, an authentication rejection, a
    network/connectivity fault, or any other operational error.
    """

    NONE = "none"
    CONFIG = "config"
    AUTH = "auth"
    CONNECTIVITY = "connectivity"
    OPERATIONAL = "operational"


@dataclass(frozen=True)
class HealthStatus:
    """Result of :func:`health`: a non-crashing, operation-identifying report.

    Attributes:
        state: Coarse :class:`HealthState`.
        failure_kind: Classified :class:`FailureKind` (``NONE`` when healthy).
        operation: The logical operation that was attempted (for attribution).
        detail: A human-readable, **secret-free** description of the outcome.
        missing_settings: When ``MISCONFIGURED``, the names of missing settings.
    """

    state: HealthState
    failure_kind: FailureKind = FailureKind.NONE
    operation: str = _OP_HEALTH
    detail: str = ""
    missing_settings: list[str] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        """Whether the Snowflake backing store is usable."""
        return self.state is HealthState.HEALTHY


# Module-level session cache. A single Snowpark session is reused across the
# process; building is guarded so concurrent callers do not race (the service
# runs multi-worker, but each worker builds at most one session here).
_session: Optional["Session"] = None
_session_lock = threading.Lock()


class SnowflakeConnectionError(RuntimeError):
    """Operation-identifying connectivity/auth error (never carries a secret).

    Raised by :func:`get_session` when a session cannot be established. The
    message names the failing operation and the non-secret connection target so
    the fault is attributable, while the underlying driver error is chained for
    diagnostics. The authentication secret is never included (Req 1.1, 1.4).
    """

    def __init__(self, operation: str, kind: FailureKind, detail: str) -> None:
        self.operation = operation
        self.kind = kind
        self.detail = detail
        super().__init__(f"[{operation}] {kind.value}: {detail}")


def _target_coordinates(settings: Settings) -> str:
    """Describe the connection target using non-secret coordinates only.

    Includes account/user/role/warehouse/database/schema *names* so an error is
    attributable, and deliberately omits the authentication secret (Req 1.1).
    """
    return (
        f"account={settings.account or '<unset>'} "
        f"user={settings.user or '<unset>'} "
        f"role={settings.role or '<unset>'} "
        f"warehouse={settings.warehouse or '<unset>'} "
        f"database={settings.database or '<unset>'} "
        f"schema={settings.db_schema or '<unset>'}"
    )


def _classify_error(exc: BaseException) -> FailureKind:
    """Map a driver/runtime exception to a :class:`FailureKind`.

    Classification is best-effort and based on exception type/name and a
    secret-free scan of the message. We never surface the raw message to callers
    verbatim; only the classification and a sanitized detail are exposed.
    """
    name = type(exc).__name__.lower()
    text = str(exc).lower()

    # Authentication / authorization problems.
    auth_markers = (
        "auth",
        "password",
        "incorrect username",
        "invalid credential",
        "token",
        "login",
        "250001",  # Snowflake: incorrect username or password
    )
    if any(marker in name for marker in ("auth", "programmingerror")) and any(
        m in text for m in auth_markers
    ):
        return FailureKind.AUTH
    if any(m in text for m in auth_markers):
        return FailureKind.AUTH

    # Connectivity / network problems.
    conn_markers = (
        "timeout",
        "timed out",
        "connection",
        "could not connect",
        "network",
        "unreachable",
        "name or service not known",
        "getaddrinfo",
        "ssl",
        "operationalerror",
    )
    if "operationalerror" in name or any(m in text for m in conn_markers):
        return FailureKind.CONNECTIVITY

    return FailureKind.OPERATIONAL


def _build_connection_parameters(settings: Settings) -> dict[str, Any]:
    """Assemble Snowpark connection parameters from config only (Req 1.1).

    The authentication secret is unwrapped from ``SecretStr`` solely to hand it
    to the driver; it is never logged or returned anywhere else.
    """
    return {
        "account": settings.account,
        "user": settings.user,
        "password": settings.authentication.get_secret_value(),
        "role": settings.role,
        "warehouse": settings.warehouse,
        "database": settings.database,
        "schema": settings.db_schema,
    }


def get_session(settings: Settings | None = None, *, force_new: bool = False) -> "Session":
    """Return a Snowpark ``Session`` built from configuration (Req 1.4).

    The session is cached at module scope and reused; pass ``force_new=True`` to
    rebuild (e.g. after a connectivity failure). Credentials come only from
    :class:`Settings` (Req 1.1).

    Args:
        settings: Optional pre-built settings; read from the environment when
            omitted.
        force_new: Rebuild the session even if one is cached.

    Returns:
        A connected Snowpark ``Session``.

    Raises:
        StartupConfigError: If required settings are missing (no connection is
            attempted, so no secret is involved).
        SnowflakeConnectionError: If a session cannot be established; the error
            identifies the operation and classifies the failure, without echoing
            any secret value (Req 1.1, 1.4).
    """
    global _session

    if settings is None:
        settings = Settings()

    missing = validate_config(settings)
    if missing:
        # Configuration gap: do not attempt a connection (keeps secrets out of it).
        raise StartupConfigError(missing)

    if not force_new and _session is not None:
        return _session

    with _session_lock:
        if not force_new and _session is not None:
            return _session

        # Import Snowpark lazily so unit tests and the config guard do not require
        # the driver to be importable, and so an import failure is itself classified.
        try:
            from snowflake.snowpark import Session as SnowparkSession
        except ImportError as exc:  # pragma: no cover - exercised only without the driver
            raise SnowflakeConnectionError(
                _OP_BUILD_SESSION,
                FailureKind.OPERATIONAL,
                "Snowpark driver is not available in this environment.",
            ) from exc

        params = _build_connection_parameters(settings)
        try:
            session = SnowparkSession.builder.configs(params).create()
        except Exception as exc:  # noqa: BLE001 - classify, never crash the caller silently
            kind = _classify_error(exc)
            # Log attribution with non-secret coordinates only (Req 1.1).
            logger.error(
                "Snowflake session build failed (%s) during %s for %s",
                kind.value,
                _OP_BUILD_SESSION,
                _target_coordinates(settings),
            )
            raise SnowflakeConnectionError(
                _OP_BUILD_SESSION,
                kind,
                f"Could not establish a Snowflake session for {_target_coordinates(settings)}.",
            ) from exc

        _session = session
        return _session


def close_session() -> None:
    """Close and clear the cached session, if any (best-effort, never raises)."""
    global _session
    with _session_lock:
        if _session is not None:
            try:
                _session.close()
            except Exception:  # noqa: BLE001 - cleanup must not raise
                logger.debug("Ignoring error while closing Snowflake session.")
            finally:
                _session = None


def health(settings: Settings | None = None) -> HealthStatus:
    """Probe Snowflake and return a :class:`HealthStatus` without crashing (Req 1.4).

    Behaviour:

    * Missing configuration → ``MISCONFIGURED`` with the missing setting names
      (no connection attempted, so no secret is touched).
    * Connectivity/auth/probe failure → ``UNAVAILABLE`` with a classified,
      operation-identifying, secret-free detail. The exception is caught here so
      the process keeps running (Req 1.4).
    * Otherwise → ``HEALTHY``.
    """
    if settings is None:
        settings = Settings()

    missing = validate_config(settings)
    if missing:
        return HealthStatus(
            state=HealthState.MISCONFIGURED,
            failure_kind=FailureKind.CONFIG,
            operation=_OP_HEALTH,
            detail=(
                "Missing required Snowflake configuration settings: "
                + ", ".join(missing)
            ),
            missing_settings=missing,
        )

    try:
        session = get_session(settings)
    except StartupConfigError as exc:
        # Defensive: should be caught by the validate_config check above.
        return HealthStatus(
            state=HealthState.MISCONFIGURED,
            failure_kind=FailureKind.CONFIG,
            operation=_OP_HEALTH,
            detail=str(exc),
            missing_settings=exc.missing,
        )
    except SnowflakeConnectionError as exc:
        return HealthStatus(
            state=HealthState.UNAVAILABLE,
            failure_kind=exc.kind,
            operation=exc.operation,
            detail=exc.detail,
        )

    # Session established; run a trivial, side-effect-free probe query.
    try:
        session.sql("SELECT 1").collect()
    except Exception as exc:  # noqa: BLE001 - never crash the health check
        kind = _classify_error(exc)
        logger.error(
            "Snowflake health probe failed (%s) during %s for %s",
            kind.value,
            _OP_HEALTH,
            _target_coordinates(settings),
        )
        return HealthStatus(
            state=HealthState.UNAVAILABLE,
            failure_kind=kind,
            operation=_OP_HEALTH,
            detail=(
                "Snowflake connectivity probe failed for "
                f"{_target_coordinates(settings)}."
            ),
        )

    return HealthStatus(
        state=HealthState.HEALTHY,
        failure_kind=FailureKind.NONE,
        operation=_OP_HEALTH,
        detail="Snowflake session established and probe query succeeded.",
    )


def run_startup_guard(settings: Settings | None = None) -> None:
    """Hard startup abort when configuration is incomplete (Req 1.2, 1.1).

    Inspects configuration via ``validate_config`` and, if any required setting
    is missing or empty, raises :class:`StartupConfigError` naming **every**
    missing setting. The error never echoes a secret value — only setting names.

    Args:
        settings: Optional pre-built settings; read from the environment when
            omitted.

    Raises:
        StartupConfigError: If one or more required settings are missing/empty.
    """
    if settings is None:
        settings = Settings()

    missing = validate_config(settings)
    if missing:
        logger.error(
            "Startup aborted during %s: missing required settings: %s",
            _OP_STARTUP_GUARD,
            ", ".join(missing),
        )
        raise StartupConfigError(missing)
