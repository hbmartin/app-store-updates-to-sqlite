import io
import sqlite3
from pathlib import Path

from app_store_updates_to_sqlite.apple import AppleLookupError, LookupOutcome
from app_store_updates_to_sqlite.cli import run, utc_now
from app_store_updates_to_sqlite.models import ReleaseMetadata


def release(app_id: int) -> ReleaseMetadata:
    return ReleaseMetadata(
        app_id=app_id,
        version="1.0",
        release_date="2026-01-01T00:00:00Z",
        release_notes=None,
        app_store_url=f"https://apps.apple.com/us/app/id{app_id}",
    )


def write_config(tmp_path: Path, app_ids: str = "[10]") -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'database = "updates.sqlite3"\napp_ids = {app_ids}\n')
    return config_path


def test_cli_saves_valid_apps_and_returns_nonzero_for_partial_errors(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, "[10, 20]")
    stdout = io.StringIO()
    stderr = io.StringIO()

    def fetcher(app_ids: tuple[int, ...]) -> LookupOutcome:
        assert app_ids == (10, 20)
        return LookupOutcome(
            releases={10: release(10)},
            errors={20: "Apple returned no result"},
        )

    exit_code = run(
        ["--config", str(config_path)],
        fetcher=fetcher,
        clock=lambda: "2026-01-02T00:00:00Z",
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert "processed 1 app(s)" in stdout.getvalue()
    assert "app 20: Apple returned no result" in stderr.getvalue()
    with sqlite3.connect(tmp_path / "updates.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM version_events").fetchone()[0] == 1


def test_cli_request_wide_failure_does_not_create_database(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    stderr = io.StringIO()

    def failing_fetcher(_app_ids: tuple[int, ...]) -> LookupOutcome:
        raise AppleLookupError("network unavailable")

    exit_code = run(
        ["--config", str(config_path)],
        fetcher=failing_fetcher,
        stderr=stderr,
    )

    assert exit_code == 1
    assert "network unavailable" in stderr.getvalue()
    assert not (tmp_path / "updates.sqlite3").exists()


def test_cli_invalid_config_returns_usage_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('database = "updates.sqlite3"\napp_ids = []\n')
    stderr = io.StringIO()

    exit_code = run(["--config", str(config_path)], stderr=stderr)

    assert exit_code == 2
    assert "app_ids must be a nonempty list" in stderr.getvalue()


def test_utc_now_uses_utc_iso_8601() -> None:
    timestamp = utc_now()

    assert timestamp.endswith("Z")
    assert "+00:00" not in timestamp
