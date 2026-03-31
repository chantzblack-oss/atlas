"""Atlas configuration with TOML support and JSON backward compatibility.

Supports loading from (in priority order):
1. Explicit path passed to load_config()
2. ./atlas.toml  (project-local)
3. ~/.atlas/config.toml  (user-global)
4. ~/.atlas/config.json  (legacy JSON -- backward compat)
5. Built-in defaults

Uses tomllib (Python 3.11+) with tomli fallback for 3.10.
Ref: https://docs.python.org/3/library/tomllib.html
Ref: https://github.com/hukkin/tomli
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .exceptions import ConfigError

# tomllib landed in 3.11 (PEP 680); tomli is the API-identical backport
try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

logger = logging.getLogger("atlas.config")

ATLAS_DIR = Path.home() / ".atlas"
LEGACY_CONFIG = ATLAS_DIR / "config.json"

_TOML_SEARCH: list[Path] = [
    Path("atlas.toml"),
    ATLAS_DIR / "config.toml",
]

# Legacy JSON defaults (kept for backward compat with existing config.json)
_LEGACY_DEFAULTS = {
    "model": "opus",
    "voice": "andrew",
    "listen": False,
}


# -- Typed config dataclasses -------------------------------------------


@dataclass(frozen=True)
class EngineConfig:
    """Engine-level tunables."""
    model: str = "opus"
    timeout: int = 300
    max_retries: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 30.0
    max_prompt_length: int = 50_000
    max_input_length: int = 2_000


@dataclass(frozen=True)
class HistoryConfig:
    """History storage tunables."""
    max_entries: int = 500
    archive_threshold: int = 200
    max_recent_context: int = 20
    connection_threshold: float = 0.15
    page_size: int = 30


@dataclass(frozen=True)
class AtlasConfig:
    """Top-level typed configuration container."""
    engine: EngineConfig = field(default_factory=EngineConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    config_path: str | None = None


# -- Merge helpers -------------------------------------------------------


def _defaults_for(cls: type) -> dict[str, Any]:
    """Extract {name: default} from a dataclass with all-default fields."""
    return {f.name: f.default for f in fields(cls)}


def _merge_section(defaults: dict[str, Any], overrides: dict[str, Any],
                   section: str) -> dict[str, Any]:
    """Merge user overrides into defaults with type checking."""
    merged = dict(defaults)
    for key, value in overrides.items():
        if key not in defaults:
            logger.warning(
                "Unknown config key '%s' in [%s] -- ignored", key, section,
            )
            continue
        expected_type = type(defaults[key])
        if not isinstance(value, expected_type):
            # Allow int -> float promotion (TOML has no float-only syntax)
            if expected_type is float and isinstance(value, int):
                value = float(value)
            else:
                raise ConfigError(
                    f"Config key '{section}.{key}' expected "
                    f"{expected_type.__name__}, got {type(value).__name__}",
                )
        merged[key] = value
    return merged


# -- TOML config loading ------------------------------------------------


def load_config(path: str | Path | None = None) -> AtlasConfig:
    """Load Atlas configuration from TOML, falling back to defaults.

    Args:
        path: Explicit config file path.  If None, searches standard
              locations (./atlas.toml, ~/.atlas/config.toml).

    Returns:
        Frozen AtlasConfig dataclass.

    Raises:
        ConfigError: If the specified file cannot be read or parsed.
    """
    if tomllib is None:
        logger.debug("No TOML parser available; using built-in defaults")
        return AtlasConfig()

    # Determine which file to load
    config_path: Path | None = None
    if path is not None:
        config_path = Path(path)
        if not config_path.is_file():
            raise ConfigError(
                f"Config file not found: {config_path}", str(config_path),
            )
    else:
        for candidate in _TOML_SEARCH:
            if candidate.is_file():
                config_path = candidate
                break

    if config_path is None:
        logger.debug("No TOML config file found; using built-in defaults")
        return AtlasConfig()

    logger.info("Loading config from %s", config_path)

    try:
        with open(config_path, "rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"Invalid TOML in {config_path}: {exc}", str(config_path),
        ) from exc
    except OSError as exc:
        raise ConfigError(
            f"Cannot read {config_path}: {exc}", str(config_path),
        ) from exc

    engine_merged = _merge_section(
        _defaults_for(EngineConfig), raw.get("engine", {}), "engine",
    )
    history_merged = _merge_section(
        _defaults_for(HistoryConfig), raw.get("history", {}), "history",
    )

    return AtlasConfig(
        engine=EngineConfig(**engine_merged),
        history=HistoryConfig(**history_merged),
        config_path=str(config_path),
    )


# -- Legacy JSON config (backward compat) --------------------------------


def load() -> dict:
    """Load legacy JSON config with defaults.  Kept for CLI backward compat."""
    if LEGACY_CONFIG.exists():
        try:
            data = json.loads(LEGACY_CONFIG.read_text(encoding="utf-8"))
            return {**_LEGACY_DEFAULTS, **data}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_LEGACY_DEFAULTS)


def save(cfg: dict) -> None:
    """Save legacy JSON config."""
    ATLAS_DIR.mkdir(exist_ok=True)
    LEGACY_CONFIG.write_text(
        json.dumps(cfg, indent=2), encoding="utf-8",
    )


def get(key: str) -> Any:
    """Get a single legacy config value."""
    return load().get(key, _LEGACY_DEFAULTS.get(key))


def set_value(key: str, value: Any) -> None:
    """Set a single legacy config value."""
    cfg = load()
    cfg[key] = value
    save(cfg)
