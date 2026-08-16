"""SQLite schema management and atomic release observation persistence."""

from __future__ import annotations

import sqlite3
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from .models import ReleaseMetadata

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS release_revisions (
    id INTEGER PRIMARY KEY,
    app_id INTEGER NOT NULL CHECK (app_id > 0),
    version TEXT NOT NULL,
    release_date TEXT NOT NULL,
    release_notes TEXT,
    app_store_url TEXT NOT NULL,
    content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE (app_id, version, content_hash)
) STRICT;

CREATE TABLE IF NOT EXISTS version_events (
    id INTEGER PRIMARY KEY,
    app_id INTEGER NOT NULL CHECK (app_id > 0),
    previous_version TEXT,
    revision_id INTEGER NOT NULL REFERENCES release_revisions(id),
    detected_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS app_state (
    app_id INTEGER PRIMARY KEY CHECK (app_id > 0),
    current_version TEXT NOT NULL,
    current_content_hash TEXT NOT NULL CHECK (length(current_content_hash) = 64),
    current_revision_id INTEGER NOT NULL REFERENCES release_revisions(id),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS release_revisions_app_version
    ON release_revisions(app_id, version);
CREATE INDEX IF NOT EXISTS version_events_app_detected
    ON version_events(app_id, detected_at);

CREATE VIEW IF NOT EXISTS app_store_update_events AS
SELECT
    events.id AS event_id,
    events.app_id AS app_id,
    events.previous_version AS previous_version,
    revisions.version AS version,
    revisions.release_date AS release_date,
    revisions.release_notes AS release_notes,
    revisions.app_store_url AS app_store_url,
    revisions.content_hash AS content_hash,
    events.detected_at AS detected_at,
    revisions.first_seen_at AS first_seen_at,
    revisions.last_seen_at AS last_seen_at
FROM version_events AS events
JOIN release_revisions AS revisions ON revisions.id = events.revision_id;
"""


class StorageError(RuntimeError):
    """Raised when the SQLite database cannot support the requested operation."""


@dataclass(frozen=True, slots=True)
class StorageSummary:
    apps_processed: int
    revisions_created: int
    events_created: int


def store_releases(
    database: Path,
    releases: Collection[ReleaseMetadata],
    observed_at: str,
) -> StorageSummary:
    """Atomically persist a collection of successful app observations."""
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=5.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        _initialize_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            summary = _store_in_transaction(connection, releases, observed_at)
        except BaseException:
            connection.rollback()
            raise
        connection.commit()
        return summary
    finally:
        connection.close()


def _initialize_schema(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        connection.executescript(_SCHEMA)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif version != SCHEMA_VERSION:
        raise StorageError(
            f"unsupported database schema version {version}; expected {SCHEMA_VERSION}"
        )


def _store_in_transaction(
    connection: sqlite3.Connection,
    releases: Collection[ReleaseMetadata],
    observed_at: str,
) -> StorageSummary:
    revisions_created = 0
    events_created = 0

    for release in releases:
        revision_id, created = _upsert_revision(connection, release, observed_at)
        revisions_created += int(created)

        state = connection.execute(
            "SELECT current_version FROM app_state WHERE app_id = ?",
            (release.app_id,),
        ).fetchone()
        previous_version = state["current_version"] if state is not None else None
        version_changed = state is None or previous_version != release.version

        if state is None:
            connection.execute(
                """
                INSERT INTO app_state (
                    app_id,
                    current_version,
                    current_content_hash,
                    current_revision_id,
                    first_seen_at,
                    last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    release.app_id,
                    release.version,
                    release.content_hash,
                    revision_id,
                    observed_at,
                    observed_at,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE app_state
                SET current_version = ?,
                    current_content_hash = ?,
                    current_revision_id = ?,
                    last_seen_at = ?
                WHERE app_id = ?
                """,
                (
                    release.version,
                    release.content_hash,
                    revision_id,
                    observed_at,
                    release.app_id,
                ),
            )

        if version_changed:
            connection.execute(
                """
                INSERT INTO version_events (app_id, previous_version, revision_id, detected_at)
                VALUES (?, ?, ?, ?)
                """,
                (release.app_id, previous_version, revision_id, observed_at),
            )
            events_created += 1

    return StorageSummary(
        apps_processed=len(releases),
        revisions_created=revisions_created,
        events_created=events_created,
    )


def _upsert_revision(
    connection: sqlite3.Connection,
    release: ReleaseMetadata,
    observed_at: str,
) -> tuple[int, bool]:
    existing = connection.execute(
        """
        SELECT id
        FROM release_revisions
        WHERE app_id = ? AND version = ? AND content_hash = ?
        """,
        (release.app_id, release.version, release.content_hash),
    ).fetchone()
    if existing is not None:
        connection.execute(
            "UPDATE release_revisions SET last_seen_at = ? WHERE id = ?",
            (observed_at, existing["id"]),
        )
        return int(existing["id"]), False

    cursor = connection.execute(
        """
        INSERT INTO release_revisions (
            app_id,
            version,
            release_date,
            release_notes,
            app_store_url,
            content_hash,
            first_seen_at,
            last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            release.app_id,
            release.version,
            release.release_date,
            release.release_notes,
            release.app_store_url,
            release.content_hash,
            observed_at,
            observed_at,
        ),
    )
    if cursor.lastrowid is None:
        raise StorageError("SQLite did not return an ID for the inserted release revision")
    return cursor.lastrowid, True
