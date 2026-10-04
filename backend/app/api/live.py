"""WebSocket live updates for the Command_Centre and approval queue.

The dashboard's Command_Centre (open cases + KPIs) and human-approval queue need
to update live as the workflow progresses — a new case is triaged, a case is
submitted for approval, an approval decision is taken, an action completes
(Req 10.5, Req 11.1). This module provides the small, framework-level mechanism
for that: a :class:`ConnectionManager` that tracks connected WebSocket clients
and broadcasts :class:`LiveEvent` messages to all of them.

The adaptation mirrors the StreamContract.AI ``websocket_manager`` reuse noted in
the design: a connection manager owns the set of live clients and fans an event
out to each, dropping any client whose send fails so a dead socket never blocks
the others. Routers publish events through the manager after a successful state
transition (e.g. the ingest router publishes a ``case_opened`` event, the
approval router publishes ``case_submitted_for_approval`` / ``case_approved`` /
``action_completed``), so the push path is wired end to end with the services.

Events are deliberately lightweight and **free of sensitive data**: they carry
the event type, the case/entity id, the new state, and a short detail — enough
for the UI to refresh the relevant row and pull detail through the governed REST
routes (which apply RBAC/masking). No governed figure or PII is pushed over the
socket.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from fastapi import WebSocket
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("governed_aml")


class LiveEventType(str, Enum):
    """Types of live update broadcast to the Command_Centre / approval queue.

    Each corresponds to a workflow transition a dashboard view reacts to
    (Req 10.5, Req 11.1). ``str`` mixin serialises the enum to its value for
    transport.
    """

    CASE_OPENED = "case_opened"
    ALERT_CORRELATED = "alert_correlated"
    CASE_SUBMITTED_FOR_APPROVAL = "case_submitted_for_approval"
    CASE_APPROVED = "case_approved"
    CASE_REJECTED = "case_rejected"
    ACTION_COMPLETED = "action_completed"
    ACTION_FAILED = "action_failed"


class LiveEvent(BaseModel):
    """A lightweight, sensitive-data-free live update pushed to UI clients.

    Carries only what the Command_Centre / approval queue needs to refresh a row:
    the event ``type``, the ``case_id``/``entity_id`` it concerns, the resulting
    case ``state`` where applicable, and a short ``detail`` string. Governed
    figures and PII are never placed on the socket — the UI pulls detail through
    the RBAC/masking-governed REST routes (design "Experience Layer").
    """

    model_config = ConfigDict(frozen=True)

    type: LiveEventType = Field(description="The kind of live update (Req 10.5, 11.1).")
    case_id: Optional[str] = Field(default=None, description="Case the event concerns.")
    entity_id: Optional[str] = Field(default=None, description="Entity the event concerns.")
    state: Optional[str] = Field(default=None, description="Resulting case lifecycle state.")
    detail: str = Field(default="", description="Short, sensitive-data-free description.")
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp the event was emitted.",
    )


class ConnectionManager:
    """Tracks connected WebSocket clients and broadcasts :class:`LiveEvent`s.

    A single manager instance is held on ``app.state.live`` for the life of the
    app. :meth:`connect`/:meth:`disconnect` manage the client set and
    :meth:`broadcast` fans an event out to every connected client, dropping any
    client whose send raises so one dead socket never blocks the rest. Access to
    the client set is guarded by an :class:`asyncio.Lock` because connects,
    disconnects, and broadcasts can interleave on the event loop.
    """

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register ``websocket`` as a live client."""
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)
        logger.debug("Live client connected; %d connected.", len(self._clients))

    async def disconnect(self, websocket: WebSocket) -> None:
        """Deregister ``websocket`` (idempotent; never raises)."""
        async with self._lock:
            self._clients.discard(websocket)
        logger.debug("Live client disconnected; %d connected.", len(self._clients))

    async def broadcast(self, event: LiveEvent) -> None:
        """Send ``event`` to every connected client, dropping failed sockets.

        Serialises the event to JSON and sends it to each client. A client whose
        send raises (closed/broken socket) is collected and removed afterwards so
        the broadcast never fails as a whole and a dead client is cleaned up
        (Req 10.5, 11.1).
        """
        payload = event.model_dump(mode="json")
        async with self._lock:
            clients = list(self._clients)

        dead: list[WebSocket] = []
        for client in clients:
            try:
                await client.send_json(payload)
            except Exception:  # noqa: BLE001 - a broken socket must not break the fan-out
                dead.append(client)

        if dead:
            async with self._lock:
                for client in dead:
                    self._clients.discard(client)
            logger.debug("Dropped %d dead live client(s).", len(dead))

    @property
    def client_count(self) -> int:
        """Number of currently-connected clients (for health/diagnostics)."""
        return len(self._clients)


def publish_event(manager: Optional[ConnectionManager], event: LiveEvent) -> None:
    """Fire-and-forget publish of ``event`` through ``manager`` from any context.

    Routers call this after a successful transition. It schedules the async
    :meth:`ConnectionManager.broadcast` without the caller having to await it, so
    publishing a live update never blocks (or fails) the REST response:

    * when called on the event loop (the normal request path) the broadcast is
      scheduled as a task;
    * when called outside a running loop (e.g. a synchronous unit test) the
      broadcast is run to completion;
    * when there is no manager (not wired, or a test that does not care about live
      updates) it is a no-op.

    Broadcasting is best-effort by design — a live-update failure must never
    affect the governed workflow result (design "live updates").
    """
    if manager is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (sync context): run the broadcast to completion.
        asyncio.run(manager.broadcast(event))
        return
    loop.create_task(manager.broadcast(event))
