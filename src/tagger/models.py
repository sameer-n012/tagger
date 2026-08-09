"""Typed data structures shared across tagger modules.

Deliberately framework-agnostic: no FastAPI or sqlite3 imports here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class FileStatus(StrEnum):
    ACTIVE = "active"
    MISSING = "missing"


@dataclass(slots=True)
class FileRecord:
    id: int
    content_hash: str
    relative_path: str
    size_bytes: int
    mtime: str
    status: FileStatus
    first_seen_at: str
    last_seen_at: str
    missing_since: str | None = None


@dataclass(slots=True)
class Tag:
    id: int
    name: str
    is_supertag: bool
    color: str | None
    created_at: str


@dataclass(slots=True)
class ScanSummary:
    """Result of a single rescan pass, for reporting back to the caller."""

    new_count: int = 0
    moved_count: int = 0
    missing_count: int = 0
    purged_count: int = 0
    unchanged_count: int = 0
    new_paths: list[str] = field(default_factory=lambda: [])
    moved_paths: list[tuple[str, str]] = field(default_factory=lambda: [])
    missing_paths: list[str] = field(default_factory=lambda: [])
