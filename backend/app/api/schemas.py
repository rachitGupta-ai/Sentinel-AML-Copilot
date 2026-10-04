"""Request/response DTOs for the FastAPI routers (task 18).

Most *responses* reuse the domain models directly — a router returns a
:class:`~app.models.case.Case`, :class:`~app.models.answer.Answer`,
:class:`~app.models.answer.SARDraft`, :class:`~app.models.audit.Lineage`, or
:class:`~app.models.telemetry.KpiSnapshot` and FastAPI serialises it. This module
holds the *request* bodies the routers accept and a few thin response wrappers
where the domain layer has no single aggregate to return (e.g. the ranked open
cases list, an authorization check result, an ingest acknowledgement).

The DTOs are intentionally minimal and carry no governed figures of their own —
figures only ever originate inside the governed services (Req 3.2); these models
just move request parameters in and governed results out.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.auth_service import ProtectedAction, Role

# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


class AlertIngestRequest(BaseModel):
    """An inbound suspicious-activity alert to triage into a case (Req 4.1).

    Mirrors :class:`~app.models.source.Alert`. ``alert_id`` and ``correlation_key``
    default to deterministic-enough values when omitted so a minimal demo call
    still triages, while a real stream supplies them. ``raised_at`` defaults to
    now (UTC) when the caller does not provide a business time.
    """

    model_config = ConfigDict(frozen=True)

    alert_id: str = Field(min_length=1, description="Stable alert identifier.")
    entity_id: str = Field(min_length=1, description="Entity the alert was raised on.")
    typology: str = Field(default="structuring", description="Suspicious-activity typology.")
    correlation_key: Optional[str] = Field(
        default=None,
        description="Correlation group key; defaults to the entity id when omitted (Req 4.5).",
    )
    raised_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the alert was raised (defaults to now).",
    )


class IngestResponse(BaseModel):
    """Acknowledgement of an ingested alert and the case it landed in (Req 4.1, 4.5).

    ``is_new_case`` distinguishes opening a fresh case from correlating the alert
    into an existing one; ``correlated_alert_ids`` is the case's full collapsed
    alert set so the caller/UI can see the correlation outcome (Property 14).
    """

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(description="Case the alert was triaged into.")
    entity_id: str = Field(description="Entity under investigation for the case.")
    state: str = Field(description="Case lifecycle state after triage.")
    is_new_case: bool = Field(description="True when a new case was opened for this alert.")
    correlated_alert_ids: list[str] = Field(
        default_factory=list,
        description="All alert ids collapsed into the case (Req 4.5).",
    )


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


class CaseRankingView(BaseModel):
    """One open case positioned in the governed-risk ranking (Req 4.2).

    Response shape for the Command_Centre ranked list; mirrors the triage
    service's ``CaseRanking`` (the governed composite the ranking is ordered by).
    """

    model_config = ConfigDict(frozen=True)

    rank: int = Field(ge=1, description="1-based position in the governed-risk ranking.")
    case_id: str = Field(description="The ranked case.")
    entity_id: str = Field(description="Entity under investigation.")
    aggregate_score: Decimal = Field(description="Governed composite risk score (Req 4.2).")


# ---------------------------------------------------------------------------
# Investigation
# ---------------------------------------------------------------------------


class InvestigateRequest(BaseModel):
    """A natural-language investigation question against a case (Req 5)."""

    model_config = ConfigDict(frozen=True)

    question: str = Field(min_length=1, description="The analyst's natural-language question.")


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


class ApproveRequest(BaseModel):
    """An approval decision carrying the reviewer identity (Req 7.5, Property 28).

    ``reviewer_id`` must be non-empty — a decision without a reviewer identity is
    rejected by the approval service (Req 7.6).
    """

    model_config = ConfigDict(frozen=True)

    reviewer_id: str = Field(min_length=1, description="Non-empty reviewer identity (Req 7.5).")


class RejectRequest(BaseModel):
    """A rejection decision carrying the reviewer identity and a reason (Req 7.4)."""

    model_config = ConfigDict(frozen=True)

    reviewer_id: str = Field(min_length=1, description="Non-empty reviewer identity (Req 7.5).")
    reason: str = Field(min_length=1, description="Non-empty rejection reason (Req 7.4).")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class AuthCheckRequest(BaseModel):
    """A request to check whether a role may perform a protected action (Req 9.2)."""

    model_config = ConfigDict(frozen=True)

    role: Optional[Role] = Field(
        default=None,
        description="Role to check; None/unknown is entitled to nothing (fail-closed).",
    )
    action: ProtectedAction = Field(description="The protected action to check.")


class AuthCheckResponse(BaseModel):
    """The verdict of an RBAC check, including the audit id of any denial (Req 9.2).

    ``decision`` is ``permitted`` or ``denied``; a denial carries the ``reason``
    and the ``audit_id`` of the record the auth service appended before returning,
    so the denial is provably audited (Property 33).
    """

    model_config = ConfigDict(frozen=True)

    decision: Literal["permitted", "denied"] = Field(description="RBAC verdict.")
    action: ProtectedAction = Field(description="The action that was checked.")
    role: Optional[Role] = Field(default=None, description="The resolved role.")
    reason: Optional[str] = Field(default=None, description="Why access was denied, if denied.")
    audit_id: Optional[str] = Field(
        default=None,
        description="Audit id of the denial record (set only when denied).",
    )
