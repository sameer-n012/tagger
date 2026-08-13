"""SQLite connection and schema management for a single source directory's
database. See SCHEMA.md for the full schema design and rationale.

Each source directory gets its own database file; this module only ever
operates on one connection at a time and has no notion of "which source" --
callers resolve the db path via ``tagger.config``.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = "1"

DEFAULT_MISSING_RETENTION_DAYS = "30"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash   TEXT NOT NULL,
    relative_path  TEXT NOT NULL,
    size_bytes     INTEGER NOT NULL,
    mtime          TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'missing')),
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    missing_since  TEXT
);

-- Partial index: uniqueness only enforced among *active* files. A path can
-- be reused by a new file while an old row for that same path still lingers
-- with status='missing' (awaiting either a hash-match "move" or retention
-- purge) -- see scanner.py.
CREATE UNIQUE INDEX IF NOT EXISTS idx_files_relative_path_active
    ON files (relative_path) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_files_content_hash ON files (content_hash);
CREATE INDEX IF NOT EXISTS idx_files_status ON files (status);

CREATE TABLE IF NOT EXISTS tags (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    is_supertag  INTEGER NOT NULL DEFAULT 0 CHECK (is_supertag IN (0, 1)),
    color        TEXT,
    description  TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supertag_members (
    supertag_id   INTEGER NOT NULL REFERENCES tags (id) ON DELETE CASCADE,
    member_tag_id INTEGER NOT NULL REFERENCES tags (id) ON DELETE CASCADE,
    PRIMARY KEY (supertag_id, member_tag_id),
    CHECK (supertag_id != member_tag_id)
);

CREATE TABLE IF NOT EXISTS file_tags (
    file_id    INTEGER NOT NULL REFERENCES files (id) ON DELETE CASCADE,
    tag_id     INTEGER NOT NULL REFERENCES tags (id) ON DELETE CASCADE,
    tagged_at  TEXT NOT NULL,
    PRIMARY KEY (file_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_file_tags_tag_id ON file_tags (tag_id);
"""


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the database at db_path with schema applied.

    check_same_thread=False: FastAPI may resolve a sync dependency (which
    opens this connection) on a worker thread while the endpoint itself
    runs as a coroutine on the event loop thread. The connection is still
    scoped to a single request/response lifecycle either way -- there is no
    concurrent cross-request sharing -- so relaxing sqlite3's same-thread
    check here is safe.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_SQL)
    # CREATE TABLE IF NOT EXISTS only applies to brand-new databases -- a
    # database created before the `description` column existed needs it
    # added explicitly.
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tags)")}
    if "description" not in existing_columns:
        conn.execute("ALTER TABLE tags ADD COLUMN description TEXT")
    if get_meta(conn, "schema_version") is None:
        set_meta(conn, "schema_version", SCHEMA_VERSION)
    if get_meta(conn, "missing_retention_days") is None:
        set_meta(conn, "missing_retention_days", DEFAULT_MISSING_RETENTION_DAYS)
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def get_missing_retention_days(conn: sqlite3.Connection) -> int:
    value = get_meta(conn, "missing_retention_days")
    return int(value) if value is not None else int(DEFAULT_MISSING_RETENTION_DAYS)
