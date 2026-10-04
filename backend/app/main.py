"""FastAPI application factory, startup credential guard, routers, and live updates.

This wires the Snowflake session factory, the hard startup credential guard
(task 2.1), the application-layer routers, the RBAC/purpose dependency layer, and
the WebSocket live-update endpoint (task 18.1) onto the app factory.

At startup the app runs ``run_startup_guard``: when ``validate_config()`` returns
a non-empty list, serving is aborted and **every** missing setting is named,
never echoing a secret value (Req 1.2, Req 1.1). The ``/health`` endpoint is
Snowflake-aware — it reports a classified, operation-identifying status without
crashing the process on connectivity/auth failure (Req 1.4).

Service wiring uses the dependency-injection seam in :mod:`app.api.dependencies`:

* In production, startup builds a fully-wired :class:`AppServices` from a live
  Snowpark session and places it on ``app.state.services`` (and a
  :class:`~app.api.live.ConnectionManager` on ``app.state.live``). All routers
  resolve the container from app state, so every service is connected end to end.
* In tests, no live session is needed. :func:`configure_services` lets a test
  place a container built with in-memory fakes (and a fake Cortex adapter) onto
  the app, so the API — including the Cortex-dependent investigate/sar routes —
  is exercised with the FastAPI TestClient without a live Snowflake/Cortex
  connection.

Startup never crashes the process on a Snowflake outage: if the credential guard
passes but the live session cannot be built, the error is logged and the app
still serves ``/health`` (which reports the degraded state) and any routes a test
has wired — matching the fail-safe posture of the session/health layer (Req 1.4).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.api.dependencies import AppServices, build_app_services_from_session
from app.api.live import ConnectionManager
from app.api.routers import approval as approval_router
from app.api.routers import audit as audit_router
from app.api.routers import auth as auth_router
from app.api.routers import cases as cases_router
from app.api.routers import ingest as ingest_router
from app.api.routers import investigate as investigate_router
from app.api.routers import kpi as kpi_router
from app.api.routers import sar as sar_router
from app.snowflake.session import (
    HealthStatus,
    StartupConfigError,
    get_session,
    health,
    run_startup_guard,
)

logger = logging.getLogger("governed_aml")

APP_TITLE = "SentinelAML Copilot"
APP_VERSION = "0.1.0"

# WebSocket path for live Command_Centre / approval-queue updates (Req 10.5, 11.1).
LIVE_STREAM_PATH = "/api/stream"


def _run_startup_guard() -> None:
    """Run the startup credential guard before the app serves requests (Req 1.2).

    Delegates to :func:`app.snowflake.session.run_startup_guard`, which raises
    ``StartupConfigError`` — naming every missing setting and never echoing a
    secret value (Req 1.1) — when configuration is incomplete. Raising from the
    startup hook prevents the application from serving requests.
    """
    run_startup_guard()


def configure_services(app: FastAPI, services: AppServices) -> None:
    """Attach a fully-wired :class:`AppServices` container to ``app``.

    This is the DI seam the routers resolve through (``app.state.services``) and
    the override point tests use: a test builds a container with in-memory fakes
    and a fake Cortex adapter (:func:`app.api.dependencies.build_app_services`)
    and calls this, so the whole API runs without a live Snowflake/Cortex
    connection. Also ensures a live-update :class:`ConnectionManager` is present.
    """
    app.state.services = services
    if getattr(app.state, "live", None) is None:
        app.state.live = ConnectionManager()


def _wire_live_services_from_session(app: FastAPI) -> None:
    """Build the production service graph from a live Snowpark session (fail-safe).

    Called at startup *after* the credential guard passes. Builds the Snowflake-
    backed :class:`AppServices` and attaches it via :func:`configure_services`. A
    connectivity/auth failure here is logged and swallowed so the process keeps
    running and ``/health`` can report the degraded state (Req 1.4); it never
    overwrites a container a test already supplied.
    """
    if getattr(app.state, "services", None) is not None:
        # A container was already configured (e.g. by a test). Do not replace it.
        return
    try:
        session = get_session()
        configure_services(app, build_app_services_from_session(session))
        logger.info("Application services wired from a live Snowflake session.")
    except Exception:  # noqa: BLE001 - never crash startup on a Snowflake outage (Req 1.4)
        logger.warning(
            "Could not wire services from a live Snowflake session at startup; "
            "the service will serve /health and report the degraded state."
        )


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application.

    Registers the application-layer routers (ingest, cases, investigate, sar,
    approval, audit, kpi, auth), the WebSocket live-update endpoint, the Snowflake
    startup guard, and the Snowflake-aware ``/health`` probe. A live-update
    connection manager is always present on ``app.state.live`` so the WebSocket
    endpoint and the router publishers work even before services are wired.
    """
    app = FastAPI(title=APP_TITLE, version=APP_VERSION)

    # CORS: allow the Next.js dashboard (localhost/127.0.0.1 on dev ports) to call
    # the API from the browser. Without this, the browser blocks every cross-origin
    # request (and its X-Role header) and the UI shows "unable to reach backend".
    # Allowing all origins is appropriate for a local demo; tighten for production.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Live-update manager is available immediately (routers publish through it).
    app.state.live = ConnectionManager()
    app.state.services = None

    @app.on_event("startup")
    async def _on_startup() -> None:  # pragma: no cover - thin wiring
        _run_startup_guard()
        _wire_live_services_from_session(app)

    @app.get("/health", tags=["system"])
    async def health_endpoint() -> dict[str, object]:
        """Snowflake-aware health probe (Req 1.4).

        Reports a classified, operation-identifying status. Connectivity/auth
        failures are surfaced as a non-``HEALTHY`` state without crashing the
        process, and the payload never contains a secret value (Req 1.1).
        """
        status: HealthStatus = health()
        payload: dict[str, object] = {
            "status": status.state.value,
            "service": APP_TITLE,
            "version": APP_VERSION,
            "failure_kind": status.failure_kind.value,
            "operation": status.operation,
            "detail": status.detail,
            "services_wired": getattr(app.state, "services", None) is not None,
            "live_clients": app.state.live.client_count,
        }
        if status.missing_settings:
            payload["missing_settings"] = status.missing_settings
        return payload

    @app.websocket(LIVE_STREAM_PATH)
    async def live_stream(websocket: WebSocket) -> None:
        """Live Command_Centre / approval-queue update stream (Req 10.5, 11.1).

        Registers the client with the connection manager and keeps the socket open
        to receive broadcasts. The server pushes :class:`~app.api.live.LiveEvent`
        messages (case/approval transitions) published by the routers; inbound
        client messages are drained (and ignored) purely to detect disconnects.
        """
        manager: ConnectionManager = app.state.live
        await manager.connect(websocket)
        try:
            while True:
                # Block on client input only to observe a disconnect; the stream
                # is server-push, so inbound payloads are not acted upon.
                await websocket.receive_text()
        except WebSocketDisconnect:
            await manager.disconnect(websocket)
        except Exception:  # noqa: BLE001 - ensure the client is always deregistered
            await manager.disconnect(websocket)

    # Register the application-layer routers (design "Application Layer").
    app.include_router(ingest_router.router)
    app.include_router(cases_router.router)
    app.include_router(investigate_router.router)
    app.include_router(sar_router.router)
    app.include_router(approval_router.router)
    app.include_router(audit_router.router)
    app.include_router(kpi_router.router)
    app.include_router(auth_router.router)

    return app


# Module-level ASGI app for ``uvicorn app.main:app``.
app = create_app()
