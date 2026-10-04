"""Snowflake-native access layer.

Session factory, startup credential guard, semantic-view accessors,
stream/task definitions, Cortex adapter, policy helpers. See design.md.
"""

from __future__ import annotations

from app.snowflake.cortex import (
    EMBED_DIM,
    EVIDENCE_CHUNK_TABLE,
    POLICY_CHUNK_TABLE,
    CortexAdapter,
    CortexAdapterImpl,
    CortexError,
    CortexUnavailableError,
    RetrievalResult,
    RetrievedChunk,
    SessionRunner,
    SnowparkSessionRunner,
    build_cortex_adapter,
)
from app.snowflake.freshness import (
    DATA_FRESHNESS_VIEW,
    NO_DATA,
    read_freshness,
)
from app.snowflake.session import (
    FailureKind,
    HealthState,
    HealthStatus,
    SnowflakeConnectionError,
    StartupConfigError,
    close_session,
    get_session,
    health,
    run_startup_guard,
)

__all__ = [
    # session + startup guard
    "get_session",
    "health",
    "run_startup_guard",
    "close_session",
    "HealthStatus",
    "HealthState",
    "FailureKind",
    "StartupConfigError",
    "SnowflakeConnectionError",
    # cortex adapter
    "CortexAdapter",
    "CortexAdapterImpl",
    "SessionRunner",
    "SnowparkSessionRunner",
    "RetrievedChunk",
    "RetrievalResult",
    "CortexError",
    "CortexUnavailableError",
    "build_cortex_adapter",
    "POLICY_CHUNK_TABLE",
    "EVIDENCE_CHUNK_TABLE",
    "EMBED_DIM",
    # data-freshness reader
    "read_freshness",
    "DATA_FRESHNESS_VIEW",
    "NO_DATA",
]
