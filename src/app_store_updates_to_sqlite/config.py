"""TOML configuration loading and validation."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when the configuration file cannot be loaded or validated."""


@dataclass(frozen=True, slots=True)
class Config:
    database: Path
    app_ids: tuple[int, ...]


def load_config(path: Path) -> Config:
    """Load a configuration file, resolving its database path relative to the file."""
    config_path = path.expanduser().resolve()
    try:
        with config_path.open("rb") as file:
            data = tomllib.load(file)
    except FileNotFoundError as error:
        raise ConfigError(f"configuration file not found: {config_path}") from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"could not read configuration file {config_path}: {error}") from error

    database = _database_path(data.get("database"), config_path)
    app_ids = _app_ids(data.get("app_ids"))
    return Config(database=database, app_ids=app_ids)


def _database_path(value: Any, config_path: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("database must be a nonempty path string")

    database = Path(value).expanduser()
    if not database.is_absolute():
        database = config_path.parent / database
    return database.resolve()


def _app_ids(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError("app_ids must be a nonempty list of positive integers")

    app_ids: list[int] = []
    for app_id in value:
        if isinstance(app_id, bool) or not isinstance(app_id, int) or app_id <= 0:
            raise ConfigError("app_ids must contain only positive integers")
        app_ids.append(app_id)

    if len(set(app_ids)) != len(app_ids):
        raise ConfigError("app_ids must not contain duplicates")
    return tuple(app_ids)
