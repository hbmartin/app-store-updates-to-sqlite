# App Store Updates to SQLite

Poll the current public version of one or more iOS apps and retain the observed release
history in SQLite. This is useful because Apple's public lookup endpoint returns the current
release metadata, not a durable version-history stream.

The poller always requests the United States storefront (`country=us`). It stores the version,
release date, release notes, App Store URL, an exact-content SHA-256 hash, and first/last-seen
timestamps. Version transitions are append-only events; edits to metadata for an unchanged
version are retained as distinct revisions.

## Setup

The project requires Python 3.12 or newer and uses
[`uv`](https://docs.astral.sh/uv/) for environment management.

```console
uv sync
cp config.example.toml config.toml
```

Edit `config.toml`:

```toml
database = "app-store-updates.sqlite3"
app_ids = [888422857]
```

The database path is resolved relative to the configuration file. App IDs must be unique,
positive integers.

## Poll once

```console
uv run app-store-updates-to-sqlite --config config.toml
```

The first successful poll records the currently surfaced version as an initial event. Later
polls update `last_seen_at`, create revisions when Apple changes surfaced metadata, and create
events whenever the version changes—including a rollback to a version seen before.

A request-wide network or JSON error leaves observations unchanged and exits nonzero. If only
some IDs are missing or malformed, valid apps are saved and the command still exits nonzero so
a scheduler can report the partial failure.

## Schedule hourly

The CLI intentionally performs one poll and exits. Use cron, launchd, or another process
scheduler. For cron, replace `/absolute/path/to/project` below with the checkout path:

```cron
0 * * * * cd /absolute/path/to/project && /absolute/path/to/project/.venv/bin/app-store-updates-to-sqlite --config /absolute/path/to/project/config.toml >> /absolute/path/to/project/poller.log 2>&1
```

## Query the database

`app_store_update_events` exposes one row for every observed version transition with all release
metadata:

```sql
SELECT
    app_id,
    previous_version,
    version,
    release_date,
    release_notes,
    app_store_url,
    detected_at
FROM app_store_update_events
ORDER BY detected_at DESC;
```

Use `release_revisions` to inspect same-version metadata edits and their first/last-seen times:

```sql
SELECT
    app_id,
    version,
    content_hash,
    release_notes,
    first_seen_at,
    last_seen_at
FROM release_revisions
ORDER BY app_id, first_seen_at;
```

The `app_state` table contains the latest successfully observed version and revision for each app.

## Development

Tests mock the Apple endpoint and never require the live service.

```console
uv run lizard src tests
uv run pyrefly check
uv run ty check
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run pytest --cov=app_store_updates_to_sqlite
```
