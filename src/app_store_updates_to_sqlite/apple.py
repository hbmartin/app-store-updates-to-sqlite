"""Client and response parser for Apple's public iTunes lookup endpoint."""

from __future__ import annotations

import json
from dataclasses import dataclass
from http.client import HTTPException
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import ReleaseMetadata

LOOKUP_ENDPOINT = "https://itunes.apple.com/lookup"
STOREFRONT = "us"
DEFAULT_TIMEOUT_SECONDS = 30.0


class AppleLookupError(RuntimeError):
    """Raised when a lookup request fails as a whole."""


@dataclass(frozen=True, slots=True)
class LookupOutcome:
    releases: dict[int, ReleaseMetadata]
    errors: dict[int, str]


def build_lookup_url(app_ids: tuple[int, ...]) -> str:
    if not app_ids:
        raise ValueError("app_ids must not be empty")
    query = urlencode({"id": ",".join(str(app_id) for app_id in app_ids), "country": STOREFRONT})
    return f"{LOOKUP_ENDPOINT}?{query}"


def fetch_releases(
    app_ids: tuple[int, ...], *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> LookupOutcome:
    """Fetch and parse the currently surfaced release for each requested app."""
    request = Request(
        build_lookup_url(app_ids),
        headers={"User-Agent": "app-store-updates-to-sqlite/0.1"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read())
    except (
        HTTPException,
        OSError,
        URLError,
        TimeoutError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise AppleLookupError(f"Apple lookup request failed: {error}") from error

    return parse_lookup_response(app_ids, payload)


def parse_lookup_response(app_ids: tuple[int, ...], payload: Any) -> LookupOutcome:
    """Validate a lookup response while isolating errors to individual app IDs."""
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise AppleLookupError("Apple lookup response did not contain a results array")

    expected_ids = set(app_ids)
    releases: dict[int, ReleaseMetadata] = {}
    errors: dict[int, str] = {}

    for result in payload["results"]:
        if not isinstance(result, dict):
            continue
        app_id = result.get("trackId")
        if isinstance(app_id, bool) or not isinstance(app_id, int) or app_id not in expected_ids:
            continue
        if app_id in releases or app_id in errors:
            releases.pop(app_id, None)
            errors[app_id] = "Apple returned multiple results for this app ID"
            continue

        try:
            releases[app_id] = _parse_result(app_id, result)
        except ValueError as error:
            errors[app_id] = str(error)

    for app_id in app_ids:
        if app_id not in releases and app_id not in errors:
            errors[app_id] = "Apple returned no result for this app ID in the US storefront"

    return LookupOutcome(releases=releases, errors=errors)


def _parse_result(app_id: int, result: dict[str, Any]) -> ReleaseMetadata:
    version = _required_string(result, "version")
    release_date = _required_string(result, "currentVersionReleaseDate")
    app_store_url = _required_string(result, "trackViewUrl")
    release_notes = result.get("releaseNotes")
    if release_notes is not None and not isinstance(release_notes, str):
        raise ValueError("releaseNotes must be a string or null")

    return ReleaseMetadata(
        app_id=app_id,
        version=version,
        release_date=release_date,
        release_notes=release_notes,
        app_store_url=app_store_url,
    )


def _required_string(result: dict[str, Any], key: str) -> str:
    value = result.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a nonempty string")
    return value
