"""Ingest router: accept a suspicious-activity alert and triage it (Req 4).

This is the entry point that connects the ingestion edge to the triage/case
service end to end: an inbound :class:`~app.api.schemas.AlertIngestRequest` is
turned into an :class:`~app.models.source.Alert`, handed to
:meth:`TriageService.on_alert` (which creates-or-correlates a case, reading
governed risk solely from governed metrics and emitting an audit record), and the
resulting case is acknowledged. On success a live Command_Centre update is
broadcast so the dashboard refreshes without a poll (Req 10.5, 11.1).

Ingesting an alert and opening/triaging a case is a privileged write; it is
guarded by the ``VIEW_CASE`` entitlement so an unknown/viewer-only caller cannot
inject alerts, and the authorization decision is audited (Req 9.2).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from app.api.dependencies import AppServices, get_services, require_action
from app.api.live import LiveEvent, LiveEventType, publish_event
from app.api.schemas import AlertIngestRequest, IngestResponse
from app.models.source import Alert
from app.services.auth_service import ProtectedAction

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.post(
    "",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_action(ProtectedAction.VIEW_CASE))],
)
async def ingest_alert(
    body: AlertIngestRequest,
    request: Request,
    services: AppServices = Depends(get_services),
) -> IngestResponse:
    """Triage an inbound alert into a (new or correlated) case (Req 4.1, 4.5).

    The alert is correlated first (to learn whether it opened a new case or joined
    an existing one), then the resulting case is read back. A ``case_opened`` or
    ``alert_correlated`` live event is broadcast so the Command_Centre updates in
    real time (Req 10.5, 11.1).
    """
    alert = Alert(
        alert_id=body.alert_id,
        entity_id=body.entity_id,
        typology=body.typology,
        raised_at=body.raised_at,
        correlation_key=body.correlation_key or body.entity_id,
    )

    correlation = services.triage_service.correlate(alert)
    case = services.case_repository.get(correlation.case_id)
    # correlate() has just upserted the case, so this is defensive only.
    state = case.state.value if case is not None else "received"

    event_type = (
        LiveEventType.CASE_OPENED if correlation.is_new_case else LiveEventType.ALERT_CORRELATED
    )
    publish_event(
        getattr(request.app.state, "live", None),
        LiveEvent(
            type=event_type,
            case_id=correlation.case_id,
            entity_id=alert.entity_id,
            state=state,
            detail=f"Alert {alert.alert_id} ({alert.typology}) triaged.",
        ),
    )

    return IngestResponse(
        case_id=correlation.case_id,
        entity_id=alert.entity_id,
        state=state,
        is_new_case=correlation.is_new_case,
        correlated_alert_ids=correlation.correlated_alert_ids,
    )
