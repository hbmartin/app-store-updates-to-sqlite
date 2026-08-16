"""Command-line interface for one-shot App Store polling."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Never, Protocol, TextIO

from .apple import AppleLookupError, LookupOutcome, fetch_releases
from .config import ConfigError, load_config
from .storage import StorageError, store_releases

Fetcher = Callable[[tuple[int, ...]], LookupOutcome]


class _SupportsWrite(Protocol):
    def write(self, data: str, /) -> object: ...


class _ParserExit(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


class _ArgumentParser(argparse.ArgumentParser):
    output_stdout: TextIO
    output_stderr: TextIO

    def _print_message(self, message: str, file: _SupportsWrite | None = None) -> None:
        if file is None or file is sys.stdout:
            file = self.output_stdout
        elif file is sys.stderr:
            file = self.output_stderr
        file.write(message)

    def exit(self, status: int = 0, message: str | None = None) -> Never:
        if message is not None:
            self._print_message(message, self.output_stderr)
        raise _ParserExit(status)


def build_parser(
    *, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr
) -> argparse.ArgumentParser:
    parser = _ArgumentParser(description="Poll current iOS App Store release metadata into SQLite.")
    parser.output_stdout = stdout
    parser.output_stderr = stderr
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
    try:
        args = build_parser(stdout=stdout, stderr=stderr).parse_args(argv)
    except _ParserExit as error:
        return error.status

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
    if not ordered_releases:
        _print_app_errors(config.app_ids, outcome.errors, stderr)
        return 1 if outcome.errors else 0

    try:
        summary = store_releases(config.database, ordered_releases, observed_at)
    except (OSError, sqlite3.Error, StorageError) as error:
        print(f"error: could not update SQLite database: {error}", file=stderr)
        return 1

    _print_app_errors(config.app_ids, outcome.errors, stderr)
    print(
        f"processed {summary.apps_processed} app(s); "
        f"created {summary.revisions_created} revision(s) and "
        f"{summary.events_created} version event(s)",
        file=stdout,
    )

    return 1 if outcome.errors else 0


def _print_app_errors(app_ids: tuple[int, ...], errors: dict[int, str], stderr: TextIO) -> None:
    for app_id in app_ids:
        if app_id in errors:
            print(f"app {app_id}: {errors[app_id]}", file=stderr)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> None:
    raise SystemExit(run())
