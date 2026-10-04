"""Synthetic source-data domain models (RAW zone).

These records model the fully synthetic source data ingested from Snowflake
(``RAW.ENTITY``, ``RAW.TRANSACTION_EVENT``, ``RAW.ALERT``). All data is
synthetic by mandate — no real customer/account/transaction data appears
anywhere (Req 2, Req 14).

Design invariants encoded here:

* ``TransactionEvent.event_id`` is the dedup key; ``is_duplicate_suppressed``
  defaults ``False`` and is set when ingestion suppresses a duplicate
  (Req 2.5, Property 4).
* Every ingested event carries ingestion provenance: ``ingested_at`` and
  ``source`` (Req 2.4, Property 3).
* ``Alert.correlation_key`` groups alerts for the same incident/entity so they
  collapse to one case within the configured window (Req 4.5, Property 14).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class Entity(BaseModel):
    """A synthetic customer/account under investigation (Req 2, Req 9.3).

    ``display_name_masked`` holds the role-masked rendering of the entity name;
    Snowflake masking policies control raw visibility for entitled roles
    (Req 9.3, Property 34). ``attributes`` carries additional synthetic
    attributes without binding the model to a fixed shape.
    """

    model_config = ConfigDict(frozen=True)

    entity_id: str = Field(description="Stable synthetic entity identifier.")
    display_name_masked: str = Field(
        description="Masked display name (raw value gated by masking policy, Req 9.3).",
    )
    attributes: dict[str, object] = Field(
        default_factory=dict,
        description="Additional synthetic entity attributes.",
    )


class TransactionEvent(BaseModel):
    """A synthetic transaction event ingested into the RAW zone (Req 2).

    ``event_id`` is the dedup key (Req 2.5). ``occurred_at`` is business time;
    ``ingested_at`` is the ingestion timestamp and ``source`` the ingestion
    source — both are ingestion provenance required on every stored row
    (Req 2.4, Property 3). ``is_duplicate_suppressed`` records whether this
    event was suppressed as a duplicate during ingestion and defaults ``False``
    (Req 2.5, Property 4).
    """

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(description="Dedup key; distinct per logical event (Req 2.5).")
    entity_id: str = Field(description="Entity the event belongs to.")
    amount: Decimal = Field(description="Transaction amount (exact decimal, never float).")
    currency: str = Field(description="ISO currency code of the amount.")
    occurred_at: datetime = Field(description="Business time the transaction occurred.")
    ingested_at: datetime = Field(
        description="Ingestion timestamp recorded on every stored row (Req 2.4).",
    )
    source: str = Field(description="Ingestion source, populated on every row (Req 2.4).")
    is_duplicate_suppressed: bool = Field(
        default=False,
        description="Set when ingestion suppressed this event as a duplicate (Req 2.5).",
    )


class Alert(BaseModel):
    """A suspicious-activity alert raised on an entity (Req 4).

    ``correlation_key`` groups alerts that belong to the same incident/entity so
    the triage service can collapse them into a single case within the
    configured correlation window (Req 4.5, Property 14).
    """

    model_config = ConfigDict(frozen=True)

    alert_id: str = Field(description="Stable alert identifier.")
    entity_id: str = Field(description="Entity the alert was raised on.")
    typology: str = Field(description="Suspicious-activity typology, e.g. 'structuring'.")
    raised_at: datetime = Field(description="When the alert was raised.")
    correlation_key: str = Field(
        description="Grouping key for dedup/correlation of related alerts (Req 4.5).",
    )
