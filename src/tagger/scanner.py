"""Directory scanning, content hashing, and rescan diff logic.

Framework-agnostic: takes a raw ``sqlite3.Connection`` and a filesystem
path, has no FastAPI dependency. See SCHEMA.md ("Move-matching
disambiguation") for the algorithm this implements.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tagger import db
from tagger.models import FileStatus, ScanSummary

_HASH_CHUNK_SIZE = 1024 * 1024

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]
"""Called as progress_cb(files_processed, files_total) while hashing."""


def compute_file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_HASH_CHUNK_SIZE):
            hasher.update(chunk)
    return hasher.hexdigest()


@dataclass(slots=True)
class DiskFile:
    hash: str
    size: int
    mtime: str


def _count_files(root: Path) -> int:
    """A cheap (metadata-only, no hashing) pass to size the progress bar's
    denominator before the slow hashing pass starts."""
    return sum(1 for path in root.rglob("*") if path.is_file())


def walk_source(root: Path, progress_cb: ProgressCallback | None = None) -> dict[str, DiskFile]:
    """Recursively hash every regular file under root.

    Returns a mapping of POSIX-style relative path -> DiskFile. Files that
    disappear or become unreadable mid-walk are silently skipped for this
    pass -- a subsequent rescan will pick up whatever state settles.
    """
    total = _count_files(root) if progress_cb else 0
    result: dict[str, DiskFile] = {}
    processed = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            file_hash = compute_file_hash(path)
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        result[rel] = DiskFile(
            hash=file_hash,
            size=stat.st_size,
            mtime=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        )
        processed += 1
        if progress_cb is not None:
            progress_cb(processed, total)
    return result


def rescan(
    conn: sqlite3.Connection, root: Path, progress_cb: ProgressCallback | None = None
) -> ScanSummary:
    """Diff the current contents of root against the database and apply
    the new/moved/missing/purge transitions described in CLAUDE.md.
    """
    now = db.utcnow_iso()
    retention_days = db.get_missing_retention_days(conn)
    purged = _purge_stale_missing(conn, retention_days, now)

    rows = conn.execute("SELECT * FROM files").fetchall()
    db_active: dict[str, sqlite3.Row] = {}
    db_missing: dict[str, sqlite3.Row] = {}
    for row in rows:
        if row["status"] == FileStatus.ACTIVE.value:
            db_active[row["relative_path"]] = row
        else:
            db_missing[row["relative_path"]] = row

    on_disk = walk_source(root, progress_cb)

    unchanged_paths = {
        p for p, disk_file in on_disk.items()
        if p in db_active and db_active[p]["content_hash"] == disk_file.hash
    }
    for p in unchanged_paths:
        row = db_active[p]
        disk_file = on_disk[p]
        conn.execute(
            "UPDATE files SET size_bytes = ?, mtime = ?, last_seen_at = ? WHERE id = ?",
            (disk_file.size, disk_file.mtime, now, row["id"]),
        )

    # Candidates eligible to be "found" this scan: active rows whose path
    # vanished or whose content changed, plus rows already missing from a
    # prior scan (they might reappear, possibly under a new path).
    stale_active_paths = set(db_active) - unchanged_paths
    new_disk_paths = set(on_disk) - unchanged_paths

    missing_candidates: dict[str, sqlite3.Row] = {p: db_active[p] for p in stale_active_paths}
    missing_candidates.update(db_missing)
    new_candidates: dict[str, DiskFile] = {p: on_disk[p] for p in new_disk_paths}

    moved, unmatched_new, unmatched_missing = _match_by_hash(new_candidates, missing_candidates)

    summary = ScanSummary(purged_count=purged, unchanged_count=len(unchanged_paths))

    for old_path, new_path in moved:
        row = missing_candidates[old_path]
        disk_file = new_candidates[new_path]
        conn.execute(
            "UPDATE files SET relative_path = ?, content_hash = ?, size_bytes = ?, mtime = ?, "
            "status = ?, last_seen_at = ?, missing_since = NULL WHERE id = ?",
            (new_path, disk_file.hash, disk_file.size, disk_file.mtime,
             FileStatus.ACTIVE.value, now, row["id"]),
        )
        summary.moved_count += 1
        summary.moved_paths.append((old_path, new_path))

    for path in unmatched_new:
        disk_file = new_candidates[path]
        conn.execute(
            "INSERT INTO files "
            "(content_hash, relative_path, size_bytes, mtime, status, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (disk_file.hash, path, disk_file.size, disk_file.mtime,
             FileStatus.ACTIVE.value, now, now),
        )
        summary.new_count += 1
        summary.new_paths.append(path)

    for path in unmatched_missing:
        row = missing_candidates[path]
        if row["status"] == FileStatus.ACTIVE.value:
            conn.execute(
                "UPDATE files SET status = ?, missing_since = ? WHERE id = ?",
                (FileStatus.MISSING.value, now, row["id"]),
            )
            summary.missing_count += 1
            summary.missing_paths.append(path)
        # else: was already missing before this scan too -- no-op, still absent.

    db.set_meta(conn, "last_scan_at", now)
    conn.commit()

    logger.info(
        "scanned root=%s new=%d moved=%d missing=%d purged=%d unchanged=%d",
        root, summary.new_count, summary.moved_count, summary.missing_count,
        summary.purged_count, summary.unchanged_count,
    )
    return summary


def _match_by_hash(
    new_candidates: dict[str, DiskFile],
    missing_candidates: dict[str, sqlite3.Row],
) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """Pair missing paths with new paths sharing a content hash.

    Deterministic tie-break for duplicate-hash groups: sort both sides by
    path and pair off in order (see SCHEMA.md "Move-matching
    disambiguation"). Returns (moved_pairs, unmatched_new, unmatched_missing).
    """
    missing_by_hash: dict[str, list[str]] = {}
    for path, row in missing_candidates.items():
        missing_by_hash.setdefault(row["content_hash"], []).append(path)

    new_by_hash: dict[str, list[str]] = {}
    for path, disk_file in new_candidates.items():
        new_by_hash.setdefault(disk_file.hash, []).append(path)

    moved: list[tuple[str, str]] = []
    matched_missing: set[str] = set()
    matched_new: set[str] = set()

    for content_hash, missing_paths in missing_by_hash.items():
        new_paths = new_by_hash.get(content_hash)
        if not new_paths:
            continue
        for old_path, new_path in zip(sorted(missing_paths), sorted(new_paths), strict=False):
            moved.append((old_path, new_path))
            matched_missing.add(old_path)
            matched_new.add(new_path)

    unmatched_new = [p for p in new_candidates if p not in matched_new]
    unmatched_missing = [p for p in missing_candidates if p not in matched_missing]
    return moved, unmatched_new, unmatched_missing


def _purge_stale_missing(conn: sqlite3.Connection, retention_days: int, now: str) -> int:
    cutoff = (datetime.fromisoformat(now) - timedelta(days=retention_days)).isoformat()
    cur = conn.execute(
        "DELETE FROM files WHERE status = ? AND missing_since IS NOT NULL AND missing_since < ?",
        (FileStatus.MISSING.value, cutoff),
    )
    conn.commit()
    return cur.rowcount
