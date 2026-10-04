"""Data-freshness reader for the UI (Req 2.6).

Reads the Snowflake-native freshness indicator ``RAW.DATA_FRESHNESS``
(provisioned by ``snowflake/streams/31_data_freshness.sql``) and maps it to the
:class:`app.models.governed.FreshnessIndicator` domain model the UI consumes.

The view is the source of truth: it computes the latest ingested event time over
the governed, deduplicated ``RAW.TRANSACTION_EVENT`` and reports a defined
"no data" state when nothing has been ingested (Req 2.6, Property 5 — the
indicator equals the maximum event time over the set, and the empty set reports
the no-data state).

This module intentionally does **no** figure computation of its own: it only
projects the governed view row into the model, so freshness shown in the UI is
exactly what Snowflake reports. Nothing here logs or echoes a credential value
(Req 1.1); errors identify the *operation* by its non-secret name, consistent
with :mod:`app.snowflake.session`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from app.models.governed import FreshnessIndicator

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids importing Snowpark eagerly
    from snowflake.snowpark import Session

logger = logging.getLogger("governed_aml")

# Logical operation name for attributable, secret-free errors (mirrors session.py).
_OP_READ_FRESHNESS = "snowflake.read_freshness"

# Fully-qualified governed freshness view (schema-qualified; database is bound by
# the session's current database, matching the $db convention in the SQL).
DATA_FRESHNESS_VIEW = "RAW.DATA_FRESHNESS"

# The defined "no data" indicator (Req 2.6): no latest event time, has_data False.
NO_DATA = FreshnessIndicator(latest_event_time=None, has_data=False, is_stale=False)


def _coerce_datetime(value: Any) -> Optional[datetime]:
    """Return ``value`` as a ``datetime`` or ``None`` (defensive, never raises)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    # Snowpark may return ISO strings for timestamps depending on config.
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def read_freshness(session: "Session", *, view: str = DATA_FRESHNESS_VIEW) -> FreshnessIndicator:
    """Read the data-freshness indicator from Snowflake (Req 2.6).

    Queries the governed ``RAW.DATA_FRESHNESS`` view and maps the single row to a
    :class:`FreshnessIndicator`. When the view reports no data (empty governed
    store) the defined "no data" state is returned (``has_data=False``,
    ``latest_event_time=None`` — Property 5).

    Never raises on a Snowflake error: on failure it logs an operation-identifying,
    secret-free message and returns the :data:`NO_DATA` state so the UI degrades
    to the defined empty state rather than crashing (consistent with the
    health-check philosophy in :mod:`app.snowflake.session`).

    Args:
        session: Connected Snowpark session.
        view: Override the freshness view name (defaults to
            :data:`DATA_FRESHNESS_VIEW`).

    Returns:
        A :class:`FreshnessIndicator`; the defined no-data state on empty data or
        on any read error.
    """
    try:
        rows = session.sql(
            "SELECT latest_event_time, has_data, is_stale "
            f"FROM {view}"
        ).collect()
    except Exception:  # noqa: BLE001 - never crash the UI path; degrade to no-data
        logger.error(
            "Freshness read failed during %s for view %s; degrading to no-data state.",
            _OP_READ_FRESHNESS,
            view,
        )
        return NO_DATA

    if not rows:
        # The view always returns one row; an empty result is treated as no data.
        return NO_DATA

    row = rows[0]
    # Snowpark Row supports mapping-style and attribute access depending on
    # version; read defensively by key with a fallback to positional.
    try:
        latest = row["LATEST_EVENT_TIME"]
        has_data = row["HAS_DATA"]
        is_stale = row["IS_STALE"]
    except (TypeError, KeyError, IndexError):
        latest, has_data, is_stale = (row[0], row[1], row[2])

    latest_dt = _coerce_datetime(latest)
    # Trust the view's has_data flag, but keep the model self-consistent: no
    # timestamp implies no data.
    resolved_has_data = bool(has_data) and latest_dt is not None

    if not resolved_has_data:
        return NO_DATA

    return FreshnessIndicator(
        latest_event_time=latest_dt,
        has_data=True,
        is_stale=bool(is_stale),
    )


__all__ = [
    "DATA_FRESHNESS_VIEW",
    "NO_DATA",
    "read_freshness",
]
