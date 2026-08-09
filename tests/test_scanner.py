import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tagger import db, scanner


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return db.connect(tmp_path / "test.sqlite")


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_new_files_are_inserted(tmp_path: Path, conn: sqlite3.Connection) -> None:
    root = tmp_path / "source"
    _write(root / "a.txt", b"hello")
    _write(root / "sub" / "b.txt", b"world")

    summary = scanner.rescan(conn, root)

    assert summary.new_count == 2
    assert sorted(summary.new_paths) == ["a.txt", "sub/b.txt"]
    rows = conn.execute(
        "SELECT relative_path, status FROM files ORDER BY relative_path"
    ).fetchall()
    assert [(r["relative_path"], r["status"]) for r in rows] == [
        ("a.txt", "active"),
        ("sub/b.txt", "active"),
    ]


def test_unchanged_files_are_not_reinserted(tmp_path: Path, conn: sqlite3.Connection) -> None:
    root = tmp_path / "source"
    _write(root / "a.txt", b"hello")
    scanner.rescan(conn, root)

    summary = scanner.rescan(conn, root)

    assert summary.new_count == 0
    assert summary.unchanged_count == 1
    count = conn.execute("SELECT COUNT(*) AS c FROM files").fetchone()["c"]
    assert count == 1


def test_moved_file_preserves_id_and_tags(tmp_path: Path, conn: sqlite3.Connection) -> None:
    root = tmp_path / "source"
    file_path = root / "a.txt"
    _write(file_path, b"hello")
    scanner.rescan(conn, root)

    file_id = conn.execute(
        "SELECT id FROM files WHERE relative_path = 'a.txt'"
    ).fetchone()["id"]
    tag_cur = conn.execute(
        "INSERT INTO tags (name, is_supertag, created_at) VALUES ('keep-me', 0, ?)",
        (db.utcnow_iso(),),
    )
    tag_id = tag_cur.lastrowid
    conn.execute(
        "INSERT INTO file_tags (file_id, tag_id, tagged_at) VALUES (?, ?, ?)",
        (file_id, tag_id, db.utcnow_iso()),
    )
    conn.commit()

    file_path.rename(root / "renamed.txt")
    summary = scanner.rescan(conn, root)

    assert summary.moved_count == 1
    assert summary.moved_paths == [("a.txt", "renamed.txt")]

    row = conn.execute("SELECT id, relative_path, status FROM files").fetchone()
    assert row["id"] == file_id
    assert row["relative_path"] == "renamed.txt"
    assert row["status"] == "active"

    tag_count = conn.execute(
        "SELECT COUNT(*) AS c FROM file_tags WHERE file_id = ?", (file_id,)
    ).fetchone()["c"]
    assert tag_count == 1


def test_deleted_file_marked_missing_then_purged(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    root = tmp_path / "source"
    file_path = root / "a.txt"
    _write(file_path, b"hello")
    scanner.rescan(conn, root)

    file_path.unlink()
    summary = scanner.rescan(conn, root)
    assert summary.missing_count == 1
    row = conn.execute("SELECT status, missing_since FROM files").fetchone()
    assert row["status"] == "missing"
    assert row["missing_since"] is not None

    old_time = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    conn.execute("UPDATE files SET missing_since = ?", (old_time,))
    conn.commit()

    summary2 = scanner.rescan(conn, root)
    assert summary2.purged_count == 1
    count = conn.execute("SELECT COUNT(*) AS c FROM files").fetchone()["c"]
    assert count == 0


def test_reused_path_while_old_missing_row_pending(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """A path can be reused by unrelated new content before the old missing
    row for that same path has been purged (partial-unique-index case)."""
    root = tmp_path / "source"
    file_path = root / "a.txt"
    _write(file_path, b"original")
    scanner.rescan(conn, root)

    file_path.unlink()
    scanner.rescan(conn, root)  # a.txt now missing

    _write(file_path, b"totally different content")
    summary = scanner.rescan(conn, root)

    assert summary.new_count == 1
    rows = conn.execute(
        "SELECT relative_path, status FROM files WHERE relative_path = 'a.txt'"
    ).fetchall()
    statuses = sorted(r["status"] for r in rows)
    assert statuses == ["active", "missing"]


def test_duplicate_hash_move_matching_is_deterministic(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    root = tmp_path / "source"
    _write(root / "a.txt", b"same")
    _write(root / "b.txt", b"same")
    scanner.rescan(conn, root)

    (root / "a.txt").rename(root / "z_a.txt")
    (root / "b.txt").rename(root / "z_b.txt")
    summary = scanner.rescan(conn, root)

    assert summary.moved_count == 2
    assert sorted(summary.moved_paths) == [("a.txt", "z_a.txt"), ("b.txt", "z_b.txt")]
