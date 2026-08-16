"""Shared data types for App Store release observations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReleaseMetadata:
    """The current public release metadata surfaced by Apple for one app."""

    app_id: int
    version: str
    release_date: str
    release_notes: str | None
    app_store_url: str

    @property
    def content_hash(self) -> str:
        """Return a stable SHA-256 hash of the exact surfaced metadata values."""
        payload = {
            "app_store_url": self.app_store_url,
            "release_date": self.release_date,
            "release_notes": self.release_notes,
            "version": self.version,
        }
        canonical_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical_json.encode()).hexdigest()
