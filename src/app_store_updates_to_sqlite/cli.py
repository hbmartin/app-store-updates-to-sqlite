"""Command-line interface for one-shot App Store polling."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from .apple import AppleLookupError, LookupOutcome, fetch_releases
from .config import ConfigError, load_config
from .storage import StorageError, store_releases

Fetcher = Callable[[tuple[int, ...]], LookupOutcome]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Poll current iOS App Store release metadata into SQLite."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.toml"),
        help="TOML configuration path (default: config.toml)",
    )
    return parser


def run(
    argv: Sequence[str] | None = None,
    *,
    fetcher: Fetcher | None = None,
    clock: Callable[[], str] | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(f"error: {error}", file=stderr)
        return 2

    try:
        outcome = (fetcher or fetch_releases)(config.app_ids)
    except AppleLookupError as error:
        print(f"error: {error}", file=stderr)
        return 1

    observed_at = (clock or utc_now)()
    ordered_releases = [
        outcome.releases[app_id] for app_id in config.app_ids if app_id in outcome.releases
    ]
    try:
        summary = store_releases(config.database, ordered_releases, observed_at)
    except (OSError, sqlite3.Error, StorageError) as error:
        print(f"error: could not update SQLite database: {error}", file=stderr)
        return 1

    print(
        f"processed {summary.apps_processed} app(s); "
        f"created {summary.revisions_created} revision(s) and "
        f"{summary.events_created} version event(s)",
        file=stdout,
    )
    for app_id in config.app_ids:
        if app_id in outcome.errors:
            print(f"app {app_id}: {outcome.errors[app_id]}", file=stderr)

    return 1 if outcome.errors else 0


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> None:
    raise SystemExit(run())
