"""Application configuration and the startup credential guard.

SentinelAML Copilot is Snowflake-native and **config-only** for credentials: every
Snowflake connection setting (account, user, authentication, role, warehouse,
database, schema) is read from the environment / configuration — never embedded
in source, prompts, or logs (Req 1.1). ``Settings`` binds those keys via
``pydantic-settings`` and deliberately provides **no defaults that embed secrets**.

``validate_config()`` is the startup credential guard's inspection helper: it
returns the names of the required settings that are missing or empty. The caller
(``app.main``, and the hard abort in task 2.1) fails startup — naming every
missing setting — if and only if that list is non-empty (Req 1.2).

Secrets must never leak. The authentication secret is held as a
``pydantic.SecretStr`` so that its value is not rendered by ``repr``/``str`` or by
default logging, and this module does not define any ``__repr__``/``__str__`` that
echoes secret values (Req 1.1, design Property 2).
"""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Logical names of the required Snowflake connection settings (Req 1.1). These are
# the canonical identifiers reported by ``validate_config`` when missing/empty.
# They correspond to env keys ``SNOWFLAKE_ACCOUNT``, ``SNOWFLAKE_USER``, etc.
REQUIRED_SETTINGS: tuple[str, ...] = (
    "account",
    "user",
    "authentication",
    "role",
    "warehouse",
    "database",
    "schema",
)


class Settings(BaseSettings):
    """Snowflake connection settings, bound to environment keys.

    Each required setting is bound to a ``SNOWFLAKE_<NAME>`` environment variable
    (e.g. ``SNOWFLAKE_ACCOUNT``). There are intentionally **no defaults that embed
    secrets** (Req 1.1): the credential-bearing fields default to empty so that a
    missing configuration is detected by ``validate_config`` rather than silently
    substituted. The ``authentication`` secret is stored as ``SecretStr`` so its
    value is not exposed by ``repr``/``str``/logging (Req 1.1, Property 2).
    """

    model_config = SettingsConfigDict(
        env_prefix="SNOWFLAKE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Account identifier (e.g. "orgname-accountname"). Non-secret but still
    # config-only — no default value is embedded.
    account: str = Field(default="", description="Snowflake account identifier.")

    # Login user name. Config-only; no embedded default.
    user: str = Field(default="", description="Snowflake user name.")

    # Authentication credential (password / key-pair passphrase / token). Held as
    # SecretStr so it is never rendered by repr/str/logging (Req 1.1).
    authentication: SecretStr = Field(
        default=SecretStr(""),
        description="Snowflake authentication secret (password / key / token).",
    )

    # Role to assume for all AI/semantic/persistence operations.
    role: str = Field(default="", description="Snowflake role.")

    # Warehouse used for compute.
    warehouse: str = Field(default="", description="Snowflake warehouse.")

    # Target database.
    database: str = Field(default="", description="Snowflake database.")

    # Target schema. Bound to ``SNOWFLAKE_SCHEMA``; aliased because ``schema`` is a
    # reserved attribute name on pydantic models.
    db_schema: str = Field(
        default="",
        alias="SNOWFLAKE_SCHEMA",
        description="Snowflake schema.",
    )

    def is_missing(self, name: str) -> bool:
        """Return whether the required setting ``name`` is missing or empty.

        A setting is considered missing when its configured value is empty after
        trimming surrounding whitespace. The ``authentication`` secret is unwrapped
        via ``SecretStr.get_secret_value()`` purely to test emptiness — the value is
        never returned, logged, or otherwise exposed (Req 1.1).
        """
        if name == "authentication":
            return not self.authentication.get_secret_value().strip()
        if name == "schema":
            return not self.db_schema.strip()
        value = getattr(self, name, "")
        return not str(value).strip()


class CortexSettings(BaseSettings):
    """Config-gated behaviour for the Snowflake Cortex adapter (Req 13.1).

    These are **operational** settings (timeout and bounded retry), not
    credentials, and are bound to ``CORTEX_*`` environment keys with
    safe/behavior-preserving defaults. They are deliberately separate from
    :class:`Settings` so the credential guard's required-settings set is
    unchanged and so Cortex tuning never embeds a secret.

    * ``timeout_seconds`` — per-attempt wall-clock budget for a Cortex SQL call.
    * ``max_retries`` — the maximum number of *retries* after the first attempt;
      total attempts are therefore ``1 + max_retries``. Defaults to ``2`` and is
      clamped to a non-negative value so the retry budget is always bounded
      (Req 13.1, Property 40).
    * ``embedding_model`` — the Cortex ``EMBED_TEXT_*`` model name; the default
      ``snowflake-arctic-embed-m-v1.5`` is a 768-dim model matching the
      ``VECTOR(FLOAT, 768)`` columns in ``VEC.*`` (see ``snowflake/ddl/02_vec.sql``).
    * ``complete_model`` — the Cortex ``COMPLETE`` model name used for narrative.
      Defaults to ``claude-sonnet-5-5`` (strong quality-per-credit for regulated
      narrative; verified available). Set ``CORTEX_COMPLETE_MODEL=claude-opus-5``
      for a max-quality showcase run at higher per-token cost.
    * ``retrieval_top_k`` — default number of chunks returned by a retrieval.
    """

    model_config = SettingsConfigDict(
        env_prefix="CORTEX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    timeout_seconds: float = Field(
        default=30.0,
        ge=0.0,
        description="Per-attempt timeout (seconds) for a Cortex call.",
    )
    max_retries: int = Field(
        default=2,
        ge=0,
        description="Max retries after the first attempt; total attempts = 1 + max_retries.",
    )
    embedding_model: str = Field(
        default="snowflake-arctic-embed-m-v1.5",
        description="Cortex EMBED_TEXT_* model (768-dim to match VEC.* columns).",
    )
    complete_model: str = Field(
        default="claude-sonnet-5-5",
        description="Cortex COMPLETE model used for narrative generation.",
    )
    retrieval_top_k: int = Field(
        default=5,
        ge=1,
        description="Default top-k chunks returned by a similarity retrieval.",
    )


class TriageSettings(BaseSettings):
    """Config-gated behaviour for the alert triage & case service (Req 4.4, 4.5).

    These are **operational** tuning knobs (not credentials), bound to ``TRIAGE_*``
    environment keys with safe, behaviour-preserving defaults, and kept separate
    from :class:`Settings` so the credential guard's required-settings set is
    unchanged.

    * ``correlation_window_seconds`` — the configurable window within which
      alerts sharing a correlation group collapse into a single case
      (Req 4.5, Property 14). A non-positive window degenerates to "same instant
      only"; the default is one hour.
    * ``freshness_threshold_seconds`` — the configurable staleness threshold: a
      case's data is flagged stale when the age of its latest event, measured
      against the triage reference time, exceeds this value (Req 4.4,
      Property 13). The default is 24 hours.
    """

    model_config = SettingsConfigDict(
        env_prefix="TRIAGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    correlation_window_seconds: float = Field(
        default=3600.0,
        ge=0.0,
        description="Window (seconds) within which same-group alerts collapse to one case (Req 4.5).",
    )
    freshness_threshold_seconds: float = Field(
        default=86400.0,
        ge=0.0,
        description="Data age (seconds) beyond which a case is flagged stale (Req 4.4).",
    )


def validate_config(settings: Settings | None = None) -> list[str]:
    """Return the names of required settings that are missing or empty.

    This is the inspection half of the startup credential guard (Req 1.2). It
    returns **exactly** the set of required setting names (from
    ``REQUIRED_SETTINGS``) whose configured value is absent or empty, preserving
    the canonical ordering. An empty list means configuration is complete; a
    non-empty list is the authoritative set the caller names when aborting
    startup. The returned names never include any secret value (Req 1.1).

    Args:
        settings: Optional pre-built ``Settings`` instance. When omitted, settings
            are read from the environment / ``.env``.

    Returns:
        The missing/empty required setting names, in canonical order.
    """
    if settings is None:
        settings = Settings()
    return [name for name in REQUIRED_SETTINGS if settings.is_missing(name)]
