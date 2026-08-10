"""Tag CRUD, bulk file tagging, and supertag membership/expansion.

A supertag "is itself + a bunch of other tags": tagging a file with a
supertag doesn't literally add rows for its members, but a search for a
member tag should also match files tagged only with a supertag that
transitively implies it. See SCHEMA.md ("Supertag expansion at query/search
time") for the full rationale.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from tagger import db
from tagger.models import Tag


def _row_to_tag(row: sqlite3.Row) -> Tag:
    return Tag(
        id=row["id"],
        name=row["name"],
        is_supertag=bool(row["is_supertag"]),
        color=row["color"],
        created_at=row["created_at"],
    )


def create_tag(
    conn: sqlite3.Connection,
    name: str,
    is_supertag: bool = False,
    color: str | None = None,
) -> Tag:
    if get_tag_by_name(conn, name) is not None:
        raise ValueError(f"Tag already exists: {name}")
    now = db.utcnow_iso()
    cur = conn.execute(
        "INSERT INTO tags (name, is_supertag, color, created_at) VALUES (?, ?, ?, ?)",
        (name, int(is_supertag), color, now),
    )
    conn.commit()
    tag_id = cur.lastrowid
    assert tag_id is not None
    return Tag(id=tag_id, name=name, is_supertag=is_supertag, color=color, created_at=now)


def get_tag(conn: sqlite3.Connection, tag_id: int) -> Tag | None:
    row = conn.execute("SELECT * FROM tags WHERE id = ?", (tag_id,)).fetchone()
    return _row_to_tag(row) if row is not None else None


def get_tag_by_name(conn: sqlite3.Connection, name: str) -> Tag | None:
    row = conn.execute("SELECT * FROM tags WHERE lower(name) = lower(?)", (name,)).fetchone()
    return _row_to_tag(row) if row is not None else None


def list_tags(conn: sqlite3.Connection) -> list[Tag]:
    rows = conn.execute("SELECT * FROM tags ORDER BY name COLLATE NOCASE").fetchall()
    return [_row_to_tag(row) for row in rows]


def rename_tag(conn: sqlite3.Connection, tag_id: int, new_name: str) -> None:
    existing = get_tag_by_name(conn, new_name)
    if existing is not None and existing.id != tag_id:
        raise ValueError(f"Tag already exists: {new_name}")
    conn.execute("UPDATE tags SET name = ? WHERE id = ?", (new_name, tag_id))
    conn.commit()


def delete_tag(conn: sqlite3.Connection, tag_id: int) -> None:
    """Deletes the tag, its file_tags rows, and any supertag_members rows
    referencing it (via ON DELETE CASCADE)."""
    conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    conn.commit()


def _downward_closure(conn: sqlite3.Connection, tag_id: int) -> set[int]:
    """All tags transitively implied as members of tag_id."""
    seen: set[int] = set()
    queue = [tag_id]
    while queue:
        current = queue.pop()
        rows = conn.execute(
            "SELECT member_tag_id FROM supertag_members WHERE supertag_id = ?", (current,)
        ).fetchall()
        for row in rows:
            member_id: int = row["member_tag_id"]
            if member_id not in seen:
                seen.add(member_id)
                queue.append(member_id)
    return seen


def _upward_closure(conn: sqlite3.Connection, tag_id: int) -> set[int]:
    """All supertags that transitively imply tag_id."""
    seen: set[int] = set()
    queue = [tag_id]
    while queue:
        current = queue.pop()
        rows = conn.execute(
            "SELECT supertag_id FROM supertag_members WHERE member_tag_id = ?", (current,)
        ).fetchall()
        for row in rows:
            supertag_id: int = row["supertag_id"]
            if supertag_id not in seen:
                seen.add(supertag_id)
                queue.append(supertag_id)
    return seen


def add_supertag_member(conn: sqlite3.Connection, supertag_id: int, member_tag_id: int) -> None:
    """Make supertag_id imply member_tag_id.

    Raises ValueError if this would make a tag its own (direct or
    transitive) member.
    """
    if supertag_id == member_tag_id:
        raise ValueError("A tag cannot be a member of itself")
    if supertag_id in _downward_closure(conn, member_tag_id):
        raise ValueError("This membership would create a supertag cycle")

    conn.execute(
        "INSERT OR IGNORE INTO supertag_members (supertag_id, member_tag_id) VALUES (?, ?)",
        (supertag_id, member_tag_id),
    )
    conn.execute("UPDATE tags SET is_supertag = 1 WHERE id = ?", (supertag_id,))
    conn.commit()


def remove_supertag_member(conn: sqlite3.Connection, supertag_id: int, member_tag_id: int) -> None:
    conn.execute(
        "DELETE FROM supertag_members WHERE supertag_id = ? AND member_tag_id = ?",
        (supertag_id, member_tag_id),
    )
    remaining = conn.execute(
        "SELECT COUNT(*) AS c FROM supertag_members WHERE supertag_id = ?", (supertag_id,)
    ).fetchone()
    if remaining["c"] == 0:
        conn.execute("UPDATE tags SET is_supertag = 0 WHERE id = ?", (supertag_id,))
    conn.commit()


def direct_members(conn: sqlite3.Connection, supertag_id: int) -> list[Tag]:
    rows = conn.execute(
        "SELECT t.* FROM tags t "
        "JOIN supertag_members sm ON t.id = sm.member_tag_id "
        "WHERE sm.supertag_id = ? ORDER BY t.name COLLATE NOCASE",
        (supertag_id,),
    ).fetchall()
    return [_row_to_tag(row) for row in rows]


def tag_files(conn: sqlite3.Connection, file_ids: Iterable[int], tag_ids: Iterable[int]) -> None:
    now = db.utcnow_iso()
    rows = [(file_id, tag_id, now) for file_id in file_ids for tag_id in tag_ids]
    if not rows:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO file_tags (file_id, tag_id, tagged_at) VALUES (?, ?, ?)", rows
    )
    conn.commit()


def untag_files(conn: sqlite3.Connection, file_ids: Iterable[int], tag_ids: Iterable[int]) -> None:
    rows = [(file_id, tag_id) for file_id in file_ids for tag_id in tag_ids]
    if not rows:
        return
    conn.executemany("DELETE FROM file_tags WHERE file_id = ? AND tag_id = ?", rows)
    conn.commit()


def tags_for_file(conn: sqlite3.Connection, file_id: int) -> list[Tag]:
    rows = conn.execute(
        "SELECT t.* FROM tags t "
        "JOIN file_tags ft ON t.id = ft.tag_id "
        "WHERE ft.file_id = ? ORDER BY t.name COLLATE NOCASE",
        (file_id,),
    ).fetchall()
    return [_row_to_tag(row) for row in rows]


def resolve_search_tag_ids(conn: sqlite3.Connection, tag_name: str) -> set[int]:
    """Tag ids that satisfy a search term of tag_name: the tag itself, plus
    every supertag that transitively implies it."""
    tag = get_tag_by_name(conn, tag_name)
    if tag is None:
        return set()
    return _upward_closure(conn, tag.id) | {tag.id}


def file_ids_with_any_tag(conn: sqlite3.Connection, tag_ids: set[int]) -> set[int]:
    if not tag_ids:
        return set()
    placeholders = ",".join("?" for _ in tag_ids)
    rows = conn.execute(
        f"SELECT DISTINCT file_id FROM file_tags WHERE tag_id IN ({placeholders})",
        tuple(tag_ids),
    ).fetchall()
    return {row["file_id"] for row in rows}


def untagged_file_ids(conn: sqlite3.Connection) -> set[int]:
    """Files with zero tags -- backs the reserved `untagged` search term."""
    rows = conn.execute(
        "SELECT id FROM files WHERE id NOT IN (SELECT DISTINCT file_id FROM file_tags)"
    ).fetchall()
    return {row["id"] for row in rows}
