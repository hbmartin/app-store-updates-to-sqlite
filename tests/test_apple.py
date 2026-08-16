import json
from http.client import IncompleteRead
from typing import Self
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

import pytest

from app_store_updates_to_sqlite import apple
from app_store_updates_to_sqlite.apple import (
    AppleLookupError,
    build_lookup_url,
    parse_lookup_response,
)
from app_store_updates_to_sqlite.models import ReleaseMetadata


def result_for(app_id: int, **overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "trackId": app_id,
        "version": "1.2.3",
        "releaseNotes": "Fixed a bug.\nKept whitespace. ",
        "currentVersionReleaseDate": "2026-08-11T20:54:10Z",
        "trackViewUrl": f"https://apps.apple.com/us/app/id{app_id}",
    }
    result.update(overrides)
    return result


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def test_build_lookup_url_uses_all_ids_and_us_storefront() -> None:
    parsed = urlparse(build_lookup_url((888422857, 284882215)))

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://itunes.apple.com/lookup"
    assert parse_qs(parsed.query) == {"id": ["888422857,284882215"], "country": ["us"]}


def test_build_lookup_url_rejects_empty_ids() -> None:
    with pytest.raises(ValueError, match="app_ids must not be empty"):
        build_lookup_url(())


def test_fetch_releases_uses_http_wrapper_and_parses_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps({"results": [result_for(10)]}).encode()

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        assert request.full_url == build_lookup_url((10,))
        assert request.get_header("User-agent") == "app-store-updates-to-sqlite/0.1"
        assert timeout == 4.0
        return FakeResponse(payload)

    monkeypatch.setattr(apple, "urlopen", fake_urlopen)

    outcome = apple.fetch_releases((10,), timeout=4.0)

    assert set(outcome.releases) == {10}
    assert outcome.errors == {}


@pytest.mark.parametrize(
    "error",
    [URLError("offline"), json.JSONDecodeError("bad JSON", "{", 1)],
)
def test_fetch_releases_wraps_request_wide_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def failing_urlopen(_request: Request, timeout: float) -> FakeResponse:
        assert timeout == 30.0
        raise error

    monkeypatch.setattr(apple, "urlopen", failing_urlopen)

    with pytest.raises(AppleLookupError, match="Apple lookup request failed"):
        apple.fetch_releases((10,))


def test_fetch_releases_wraps_truncated_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    class TruncatedResponse(FakeResponse):
        def read(self) -> bytes:
            raise IncompleteRead(b"partial")

    def fake_urlopen(_request: Request, timeout: float) -> FakeResponse:
        assert timeout == 30.0
        return TruncatedResponse(b"")

    monkeypatch.setattr(apple, "urlopen", fake_urlopen)

    with pytest.raises(AppleLookupError, match="Apple lookup request failed"):
        apple.fetch_releases((10,))


def test_parse_lookup_response_matches_results_by_track_id_and_allows_missing_notes() -> None:
    payload = {
        "resultCount": 2,
        "results": [result_for(20, releaseNotes=None), result_for(10)],
    }

    outcome = parse_lookup_response((10, 20), payload)

    assert set(outcome.releases) == {10, 20}
    assert outcome.releases[10].release_notes == "Fixed a bug.\nKept whitespace. "
    assert outcome.releases[20].release_notes is None
    assert outcome.errors == {}


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"version": ""}, "version"),
        ({"currentVersionReleaseDate": None}, "currentVersionReleaseDate"),
        ({"trackViewUrl": 123}, "trackViewUrl"),
        ({"releaseNotes": ["bad"]}, "releaseNotes"),
    ],
)
def test_parse_lookup_response_isolates_malformed_app_results(
    overrides: dict[str, object], message: str
) -> None:
    payload = {"results": [result_for(10, **overrides), result_for(20)]}

    outcome = parse_lookup_response((10, 20), payload)

    assert set(outcome.releases) == {20}
    assert message in outcome.errors[10]


def test_parse_lookup_response_reports_missing_and_duplicate_apps() -> None:
    payload = {"results": [result_for(10), result_for(10), result_for(30)]}

    outcome = parse_lookup_response((10, 20), payload)

    assert outcome.releases == {}
    assert "multiple results" in outcome.errors[10]
    assert "no result" in outcome.errors[20]


def test_parse_lookup_response_rejects_invalid_envelope() -> None:
    with pytest.raises(AppleLookupError, match="results array"):
        parse_lookup_response((10,), {"results": {}})


def test_content_hash_is_deterministic_and_sensitive_to_exact_values() -> None:
    release = ReleaseMetadata(
        app_id=10,
        version="1.0",
        release_date="2026-01-01T00:00:00Z",
        release_notes="Notes ",
        app_store_url="https://apps.apple.com/us/app/id10",
    )
    same_content_other_app = ReleaseMetadata(
        app_id=20,
        version=release.version,
        release_date=release.release_date,
        release_notes=release.release_notes,
        app_store_url=release.app_store_url,
    )
    changed_whitespace = ReleaseMetadata(
        app_id=10,
        version=release.version,
        release_date=release.release_date,
        release_notes="Notes",
        app_store_url=release.app_store_url,
    )

    assert len(release.content_hash) == 64
    assert release.content_hash == same_content_other_app.content_hash
    assert release.content_hash != changed_whitespace.content_hash
