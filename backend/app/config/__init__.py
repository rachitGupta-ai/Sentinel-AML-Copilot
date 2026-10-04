"""Configuration package.

pydantic-settings ``Settings`` and the startup credential guard
(``validate_config``). See ``app.config.settings`` and design.md.
"""

from app.config.settings import REQUIRED_SETTINGS, Settings, validate_config

__all__ = ["REQUIRED_SETTINGS", "Settings", "validate_config"]
