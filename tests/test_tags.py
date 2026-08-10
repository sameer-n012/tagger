import sqlite3
from pathlib import Path

import pytest

from tagger import db, tags


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return db.connect(tmp_path / "test.sqlite")


def _make_file(conn: sqlite3.Connection, path: str) -> int:
    now = db.utcnow_iso()
    cur = conn.execute(
        "INSERT INTO files "
        "(content_hash, relative_path, size_bytes, mtime, status, first_seen_at, last_seen_at) "
        "VALUES ('h', ?, 1, ?, 'active', ?, ?)",
        (path, now, now, now),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def test_create_and_lookup_tag_case_insensitive(conn: sqlite3.Connection) -> None:
    tag = tags.create_tag(conn, "Vacation")
    found = tags.get_tag_by_name(conn, "vacation")
    assert found is not None
    assert found.id == tag.id
    with pytest.raises(ValueError):
        tags.create_tag(conn, "vacation")


def test_supertag_expansion_matches_search(conn: sqlite3.Connection) -> None:
    travel = tags.create_tag(conn, "travel")
    photos = tags.create_tag(conn, "photos")
    roadtrip = tags.create_tag(conn, "roadtrip")
    tags.add_supertag_member(conn, roadtrip.id, travel.id)
    tags.add_supertag_member(conn, roadtrip.id, photos.id)

    file_id = _make_file(conn, "a.jpg")
    tags.tag_files(conn, [file_id], [roadtrip.id])

    resolved = tags.resolve_search_tag_ids(conn, "travel")
    assert roadtrip.id in resolved
    matches = tags.file_ids_with_any_tag(conn, resolved)
    assert file_id in matches


def test_nested_supertag_expansion(conn: sqlite3.Connection) -> None:
    a = tags.create_tag(conn, "a")
    b = tags.create_tag(conn, "b")
    c = tags.create_tag(conn, "c")
    # c implies b, b implies a -> searching "a" should also match files tagged "c"
    tags.add_supertag_member(conn, b.id, a.id)
    tags.add_supertag_member(conn, c.id, b.id)

    file_id = _make_file(conn, "f.txt")
    tags.tag_files(conn, [file_id], [c.id])

    resolved = tags.resolve_search_tag_ids(conn, "a")
    matches = tags.file_ids_with_any_tag(conn, resolved)
    assert file_id in matches


def test_supertag_direct_cycle_rejected(conn: sqlite3.Connection) -> None:
    a = tags.create_tag(conn, "a")
    b = tags.create_tag(conn, "b")
    tags.add_supertag_member(conn, a.id, b.id)
    with pytest.raises(ValueError):
        tags.add_supertag_member(conn, b.id, a.id)


def test_supertag_transitive_cycle_rejected(conn: sqlite3.Connection) -> None:
    a = tags.create_tag(conn, "a")
    b = tags.create_tag(conn, "b")
    c = tags.create_tag(conn, "c")
    tags.add_supertag_member(conn, a.id, b.id)
    tags.add_supertag_member(conn, b.id, c.id)
    with pytest.raises(ValueError):
        tags.add_supertag_member(conn, c.id, a.id)


def test_self_membership_rejected(conn: sqlite3.Connection) -> None:
    a = tags.create_tag(conn, "a")
    with pytest.raises(ValueError):
        tags.add_supertag_member(conn, a.id, a.id)


def test_bulk_tag_and_untag_files(conn: sqlite3.Connection) -> None:
    tag = tags.create_tag(conn, "bulk")
    f1 = _make_file(conn, "a.txt")
    f2 = _make_file(conn, "b.txt")
    tags.tag_files(conn, [f1, f2], [tag.id])

    assert {t.id for t in tags.tags_for_file(conn, f1)} == {tag.id}
    assert {t.id for t in tags.tags_for_file(conn, f2)} == {tag.id}

    tags.untag_files(conn, [f1], [tag.id])
    assert tags.tags_for_file(conn, f1) == []
    assert {t.id for t in tags.tags_for_file(conn, f2)} == {tag.id}


def test_untagged_file_ids(conn: sqlite3.Connection) -> None:
    tag = tags.create_tag(conn, "keepsake")
    tagged = _make_file(conn, "a.txt")
    untagged = _make_file(conn, "b.txt")
    tags.tag_files(conn, [tagged], [tag.id])

    assert tags.untagged_file_ids(conn) == {untagged}


def test_remove_last_supertag_member_clears_flag(conn: sqlite3.Connection) -> None:
    super_tag = tags.create_tag(conn, "super")
    member = tags.create_tag(conn, "member")
    tags.add_supertag_member(conn, super_tag.id, member.id)
    refreshed = tags.get_tag(conn, super_tag.id)
    assert refreshed is not None
    assert refreshed.is_supertag is True

    tags.remove_supertag_member(conn, super_tag.id, member.id)
    refreshed = tags.get_tag(conn, super_tag.id)
    assert refreshed is not None
    assert refreshed.is_supertag is False
