import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app_store_updates_to_sqlite.models import ReleaseMetadata
from app_store_updates_to_sqlite.storage import StorageError, store_releases


def release(
    app_id: int = 888422857,
    version: str = "1.0",
    notes: str | None = "Initial notes",
) -> ReleaseMetadata:
    return ReleaseMetadata(
        app_id=app_id,
        version=version,
        release_date=f"2026-01-{version.split('.')[0].zfill(2)}T00:00:00Z",
        release_notes=notes,
        app_store_url=f"https://apps.apple.com/us/app/id{app_id}",
    )


def rows(database: Path, query: str) -> list[sqlite3.Row]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(query).fetchall()
    finally:
        connection.close()


def test_first_observation_creates_revision_state_event_and_view(tmp_path: Path) -> None:
    database = tmp_path / "nested" / "updates.sqlite3"
    observed = release()

    summary = store_releases(database, [observed], "2026-01-02T00:00:00Z")

    assert summary.apps_processed == 1
    assert summary.revisions_created == 1
    assert summary.events_created == 1
    event = rows(database, "SELECT * FROM app_store_update_events")[0]
    assert dict(event) == {
        "event_id": 1,
        "app_id": observed.app_id,
        "previous_version": None,
        "version": "1.0",
        "release_date": "2026-01-01T00:00:00Z",
        "release_notes": "Initial notes",
        "app_store_url": f"https://apps.apple.com/us/app/id{observed.app_id}",
        "content_hash": observed.content_hash,
        "detected_at": "2026-01-02T00:00:00Z",
        "first_seen_at": "2026-01-02T00:00:00Z",
        "last_seen_at": "2026-01-02T00:00:00Z",
    }
    assert rows(database, "PRAGMA user_version")[0][0] == 1


def test_unchanged_edits_reversion_version_change_and_rollback(tmp_path: Path) -> None:
    database = tmp_path / "updates.sqlite3"
    original = release()
    edited = release(notes="Edited notes")
    version_two = release(version="2.0", notes="Version two")

    store_releases(database, [original], "2026-01-01T01:00:00Z")
    unchanged = store_releases(database, [original], "2026-01-01T02:00:00Z")
    edit = store_releases(database, [edited], "2026-01-01T03:00:00Z")
    reversion = store_releases(database, [original], "2026-01-01T04:00:00Z")
    upgrade = store_releases(database, [version_two], "2026-02-01T01:00:00Z")
    rollback = store_releases(database, [original], "2026-02-01T02:00:00Z")

    assert (unchanged.revisions_created, unchanged.events_created) == (0, 0)
    assert (edit.revisions_created, edit.events_created) == (1, 0)
    assert (reversion.revisions_created, reversion.events_created) == (0, 0)
    assert (upgrade.revisions_created, upgrade.events_created) == (1, 1)
    assert (rollback.revisions_created, rollback.events_created) == (0, 1)

    revisions = rows(
        database,
        "SELECT version, release_notes, first_seen_at, last_seen_at "
        "FROM release_revisions ORDER BY id",
    )
    assert len(revisions) == 3
    assert dict(revisions[0]) == {
        "version": "1.0",
        "release_notes": "Initial notes",
        "first_seen_at": "2026-01-01T01:00:00Z",
        "last_seen_at": "2026-02-01T02:00:00Z",
    }
    assert revisions[1]["release_notes"] == "Edited notes"

    events = rows(
        database,
        "SELECT previous_version, version FROM app_store_update_events ORDER BY event_id",
    )
    assert [tuple(row) for row in events] == [(None, "1.0"), ("1.0", "2.0"), ("2.0", "1.0")]
    state = rows(database, "SELECT * FROM app_state")[0]
    assert state["current_version"] == "1.0"
    assert state["current_content_hash"] == original.content_hash
    assert state["first_seen_at"] == "2026-01-01T01:00:00Z"
    assert state["last_seen_at"] == "2026-02-01T02:00:00Z"


def test_multiple_apps_are_stored_in_one_call(tmp_path: Path) -> None:
    database = tmp_path / "updates.sqlite3"

    summary = store_releases(
        database,
        [release(app_id=10), release(app_id=20)],
        "2026-01-01T00:00:00Z",
    )

    assert summary.apps_processed == 2
    assert summary.events_created == 2
    assert [row[0] for row in rows(database, "SELECT app_id FROM app_state ORDER BY app_id")] == [
        10,
        20,
    ]


def test_concurrent_initial_observations_create_one_event(tmp_path: Path) -> None:
    database = tmp_path / "updates.sqlite3"
    observed = release()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(store_releases, database, [observed], "2026-01-01T00:00:00Z")
            for _ in range(2)
        ]
        summaries = [future.result() for future in futures]

    assert sum(summary.revisions_created for summary in summaries) == 1
    assert sum(summary.events_created for summary in summaries) == 1
    assert len(rows(database, "SELECT id FROM release_revisions")) == 1
    assert len(rows(database, "SELECT id FROM version_events")) == 1


def test_rejects_unknown_schema_version(tmp_path: Path) -> None:
    database = tmp_path / "updates.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(StorageError, match="unsupported database schema version 99"):
        store_releases(database, [release()], "2026-01-01T00:00:00Z")
