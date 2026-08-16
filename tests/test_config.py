from pathlib import Path

import pytest

from app_store_updates_to_sqlite.config import ConfigError, load_config


def test_load_config_resolves_database_relative_to_config(tmp_path: Path) -> None:
    config_path = tmp_path / "settings" / "poller.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        'database = "data/releases.sqlite3"\napp_ids = [888422857, 284882215]\n'
    )

    config = load_config(config_path)

    assert config.database == config_path.parent / "data" / "releases.sqlite3"
    assert config.app_ids == (888422857, 284882215)


@pytest.mark.parametrize(
    ("config_text", "message"),
    [
        ('database = "updates.sqlite3"\n', "app_ids must be a nonempty list"),
        ('database = "updates.sqlite3"\napp_ids = []\n', "app_ids must be a nonempty list"),
        ('database = "updates.sqlite3"\napp_ids = [0]\n', "positive integers"),
        ('database = "updates.sqlite3"\napp_ids = [true]\n', "positive integers"),
        ('database = "updates.sqlite3"\napp_ids = [1, 1]\n', "must not contain duplicates"),
        ('app_ids = [1]\n', "database must be a nonempty path string"),
    ],
)
def test_load_config_rejects_invalid_values(
    tmp_path: Path, config_text: str, message: str
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)

    with pytest.raises(ConfigError, match=message):
        load_config(config_path)


def test_load_config_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="configuration file not found"):
        load_config(tmp_path / "missing.toml")


def test_load_config_reports_invalid_database_path(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('database = "\\u0000"\napp_ids = [1]\n')

    with pytest.raises(ConfigError, match="invalid database path"):
        load_config(config_path)
