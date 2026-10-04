"""Immutable audit-record domain model.

Every auditable operation — state transition, AI call, metric computation,
evidence retrieval, approval, action — appends a complete, append-only
``AuditRecord``. Records are immutable: attempts to modify or delete one are
rejected and themselves audited, and a case's full lineage is replayable from
its audit records alone (Req 8, Property 29, Property 30, Property 31).

Design invariants encoded here:

* ``AuditRecord`` carries all required fields: a UTC timestamp, case/entity
  references, actor id, action, input/output references, and the
  ``metric_definition_version`` where applicable (Req 8.2, Property 29).
* The record is ``frozen`` to reflect its append-only, immutable nature — the
  model itself does not permit in-place mutation (Req 8.3).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AuditRecord(BaseModel):
    """An append-only, immutable audit-trail record (Req 8.1, Req 8.2).

    Produced for every auditable operation. ``timestamp_utc`` is UTC; ``case_ref``
    and ``entity_ref`` tie the record to the workflow; ``actor_id`` identifies the
    user or system actor; ``action`` names the operation; ``input_refs`` /
    ``output_refs`` reference the data in and out; and
    ``metric_definition_version`` is populated wherever a governed figure is
    involved (Req 8.2, Property 29). The model is ``frozen`` to reflect its
    immutable, append-only contract — mutation is rejected and itself audited at
    the service/store layer (Req 8.3, Property 30).
    """

    model_config = ConfigDict(frozen=True)

    audit_id: str = Field(description="Stable, unique audit-record identifier.")
    timestamp_utc: datetime = Field(description="UTC timestamp of the audited operation.")
    case_ref: str | None = Field(
        default=None,
        description="Case this record relates to, when applicable.",
    )
    entity_ref: str | None = Field(
        default=None,
        description="Entity this record relates to, when applicable.",
    )
    actor_id: str = Field(description="Identity of the user or system actor.")
    action: str = Field(description="Name of the audited operation/transition.")
    input_refs: list[str] = Field(
        default_factory=list,
        description="References to the inputs of the operation.",
    )
    output_refs: list[str] = Field(
        default_factory=list,
        description="References to the outputs of the operation.",
    )
    metric_definition_version: str | None = Field(
        default=None,
        description="Metric-definition version where a governed figure is involved (Req 8.2).",
    )


class Lineage(BaseModel):
    """A case's full, replayable lineage reconstructed from audit records alone.

    A ``Lineage`` is the ordered sequence of :class:`AuditRecord`s for a single
    case, from the first recorded step (e.g. the alert / ``RECEIVED`` transition)
    through to the outcome (e.g. the simulated action). It is assembled *only*
    from append-only audit records — no other store is consulted — so the chain
    *question → semantic metric → generated query → source rows → calculation →
    AI narrative → human approval → outcome* can be reconstructed and verified
    after the fact (Req 8.4, Property 31).

    Records are ordered by ``timestamp_utc`` ascending; ties preserve insertion
    order so the reconstructed chain is stable and deterministic.
    """

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(description="Case whose lineage this reconstructs.")
    records: list[AuditRecord] = Field(
        default_factory=list,
        description="Append-only audit records for the case, in chronological order (Req 8.4).",
    )

    @property
    def is_empty(self) -> bool:
        """Whether the case has no audit records yet (nothing to replay)."""
        return not self.records

    @property
    def actions(self) -> list[str]:
        """The ordered action names forming the replayable chain (Req 8.4)."""
        return [record.action for record in self.records]
