# pyright: basic
"""End-to-end route tests against the FastAPI app (no browser -- these hit
the ASGI app in-process via TestClient). Complements the pure-logic unit
tests in test_scanner.py / test_tags.py / test_search.py by exercising the
actual HTTP layer: redirects, htmx partial responses, and the raw-file
path-traversal guard.

Pinned to basic type-checking mode (rather than the project-wide strict
default): httpx's TestClient response methods use a sentinel-default
pattern that pyright can't fully resolve even in upstream httpx, producing
~30 reportUnknownMemberType/reportUnknownVariableType false positives
unrelated to this file's own code.
"""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tagger import config, db
from tagger.main import app


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "data"
    monkeypatch.setenv("TAGGER_DATA_DIR", str(d))
    return d


@pytest.fixture
def client(data_dir: Path) -> TestClient:
    return TestClient(app)


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "photos").mkdir(parents=True)
    (root / "a.txt").write_text("hello")
    (root / "photos" / "b.png").write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")
    return root


def _wait_for_scan(client: TestClient, source_id: str, timeout: float = 5.0) -> None:
    """Scans now run on a background thread (see scan_status.py) so the
    add-source/rescan requests return immediately; tests that need the scan
    to have actually finished poll the same /scan-status endpoint the
    browser's overlay polls."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/sources/{source_id}/scan-status")
        if not r.json()["scanning"]:
            return
        time.sleep(0.01)
    raise AssertionError(f"scan for source {source_id} did not finish within {timeout}s")


def _add_source(client: TestClient, source_dir: Path) -> str:
    r = client.post(
        "/sources",
        data={"path": str(source_dir), "display_name": "Demo"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    source_id = r.headers["location"].split("/")[2]
    _wait_for_scan(client, source_id)
    return source_id


def _file_id(data_dir: Path, source_id: str, relative_path: str) -> int:
    source = config.get_source(source_id, data_dir=data_dir)
    assert source is not None
    conn = db.connect(config.resolve_db_path(source, data_dir))
    row = conn.execute(
        "SELECT id FROM files WHERE relative_path = ?", (relative_path,)
    ).fetchone()
    conn.close()
    assert row is not None
    return row["id"]


def test_root_redirects_to_sources(client: TestClient) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/sources"


def test_add_source_scans_and_shows_files(client: TestClient, source_dir: Path) -> None:
    source_id = _add_source(client, source_dir)

    r = client.get(f"/sources/{source_id}/browse")
    assert r.status_code == 200
    assert "a.txt" in r.text
    assert "photos" in r.text
    assert 'class="file-checkbox"' not in r.text


def test_add_source_redirects_before_scan_completes(
    client: TestClient, source_dir: Path
) -> None:
    """The redirect (and the scan-status endpoint reporting "scanning") must
    both be available immediately, before the background scan finishes --
    that's what lets the browse page's overlay show up without a race."""
    r = client.post(
        "/sources",
        data={"path": str(source_dir), "display_name": "Demo"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    source_id = r.headers["location"].split("/")[2]

    r = client.get(f"/sources/{source_id}/scan-status")
    assert r.status_code == 200
    assert r.json()["scanning"] is True

    _wait_for_scan(client, source_id)
    r = client.get(f"/sources/{source_id}/scan-status")
    assert r.json() == {"scanning": False, "processed": 0, "total": 0, "error": None}


def test_browse_page_renders_overlay_visible_while_scanning(
    client: TestClient, source_dir: Path
) -> None:
    # _add_source waits for the (fast, real) background scan to finish, so
    # to deterministically exercise the "still scanning" render path (which
    # would otherwise race a background thread over a two-file fixture) we
    # flip scan_status directly rather than relying on scan timing.
    from tagger import scan_status

    source_id = _add_source(client, source_dir)

    r = client.get(f"/sources/{source_id}/browse")
    assert 'data-scanning="false"' in r.text
    assert 'id="scan-overlay" class="scan-overlay" hidden' in r.text

    scan_status.start(source_id)
    try:
        r = client.get(f"/sources/{source_id}/browse")
        assert 'data-scanning="true"' in r.text
        assert 'id="scan-overlay" class="scan-overlay" ' in r.text  # not hidden
    finally:
        scan_status.finish(source_id)


def test_browse_page_shows_supertag_caret_and_implied_tags(
    client: TestClient, source_dir: Path, data_dir: Path
) -> None:
    source_id = _add_source(client, source_dir)
    for name in ("trip", "travel"):
        client.post(f"/sources/{source_id}/tags", data={"name": name})

    source = config.get_source(source_id, data_dir=data_dir)
    assert source is not None
    conn = db.connect(config.resolve_db_path(source, data_dir))
    trip_id = conn.execute("SELECT id FROM tags WHERE name = 'trip'").fetchone()["id"]
    travel_id = conn.execute("SELECT id FROM tags WHERE name = 'travel'").fetchone()["id"]
    conn.close()
    client.post(f"/sources/{source_id}/tags/{trip_id}/members", data={"member_tag_id": travel_id})

    r = client.get(f"/sources/{source_id}/browse")
    assert r.status_code == 200
    assert "tag-caret" in r.text
    assert "implied-sublist" in r.text
    assert "travel" in r.text


def test_tag_then_search_matches(client: TestClient, source_dir: Path, data_dir: Path) -> None:
    source_id = _add_source(client, source_dir)
    file_id = _file_id(data_dir, source_id, "a.txt")

    r = client.post(
        f"/sources/{source_id}/files/{file_id}/tags",
        data={"tag_name": "keepsake", "path": "", "q": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303

    r = client.get(f"/sources/{source_id}/browse", params={"q": "keepsake"})
    assert r.status_code == 200
    assert "a.txt" in r.text

    r = client.get(f"/sources/{source_id}/browse", params={"q": "not keepsake"})
    assert r.status_code == 200
    assert "b.png" in r.text
    assert "a.txt" not in r.text


def test_htmx_tag_add_returns_partial_not_full_page(
    client: TestClient, source_dir: Path, data_dir: Path
) -> None:
    source_id = _add_source(client, source_dir)
    file_id = _file_id(data_dir, source_id, "a.txt")

    r = client.post(
        f"/sources/{source_id}/files/{file_id}/tags",
        data={"tag_name": "quick", "path": "", "q": ""},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "quick" in r.text
    assert "<html" not in r.text.lower()


def test_preview_and_raw_image_serving(
    client: TestClient, source_dir: Path, data_dir: Path
) -> None:
    source_id = _add_source(client, source_dir)
    image_id = _file_id(data_dir, source_id, "photos/b.png")

    r = client.get(f"/sources/{source_id}/files/{image_id}/preview", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "<html" not in r.text.lower()
    assert f"/files/{image_id}/raw" in r.text

    r = client.get(f"/sources/{source_id}/files/{image_id}/raw")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


def test_preview_and_raw_video_serving(
    client: TestClient, source_dir: Path, data_dir: Path
) -> None:
    (source_dir / "clip.mp4").write_bytes(b"fake-mp4-bytes")
    source_id = _add_source(client, source_dir)
    video_id = _file_id(data_dir, source_id, "clip.mp4")

    r = client.get(f"/sources/{source_id}/files/{video_id}/preview", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "<video" in r.text
    assert f"/files/{video_id}/raw" in r.text

    r = client.get(f"/sources/{source_id}/files/{video_id}/raw")
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"

    r = client.get(f"/sources/{source_id}/browse")
    assert "🎬" in r.text


def test_missing_file_appears_and_can_be_purged(
    client: TestClient, source_dir: Path, data_dir: Path
) -> None:
    source_id = _add_source(client, source_dir)

    (source_dir / "a.txt").unlink()
    r = client.post(f"/sources/{source_id}/rescan", follow_redirects=False)
    assert r.status_code == 303
    _wait_for_scan(client, source_id)

    r = client.get(f"/sources/{source_id}/browse")
    assert "missing since" in r.text

    file_id = _file_id(data_dir, source_id, "a.txt")
    r = client.post(f"/sources/{source_id}/files/{file_id}/purge", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Nothing missing" in r.text


def test_raw_file_rejects_path_traversal(
    client: TestClient, source_dir: Path, data_dir: Path
) -> None:
    source_id = _add_source(client, source_dir)
    file_id = _file_id(data_dir, source_id, "a.txt")

    source = config.get_source(source_id, data_dir=data_dir)
    assert source is not None
    conn = db.connect(config.resolve_db_path(source, data_dir))
    conn.execute("UPDATE files SET relative_path = ? WHERE id = ?", ("../outside.txt", file_id))
    conn.commit()
    conn.close()

    r = client.get(f"/sources/{source_id}/files/{file_id}/raw")
    assert r.status_code == 400


def test_supertag_workflow_via_tags_page(
    client: TestClient, source_dir: Path, data_dir: Path
) -> None:
    source_id = _add_source(client, source_dir)

    for name in ("trip", "travel"):
        r = client.post(f"/sources/{source_id}/tags", data={"name": name}, follow_redirects=False)
        assert r.status_code == 303

    source = config.get_source(source_id, data_dir=data_dir)
    assert source is not None
    conn = db.connect(config.resolve_db_path(source, data_dir))
    trip_id = conn.execute("SELECT id FROM tags WHERE name = 'trip'").fetchone()["id"]
    travel_id = conn.execute("SELECT id FROM tags WHERE name = 'travel'").fetchone()["id"]
    conn.close()

    r = client.post(
        f"/sources/{source_id}/tags/{trip_id}/members",
        data={"member_tag_id": travel_id},
        follow_redirects=False,
    )
    assert r.status_code == 303

    file_id = _file_id(data_dir, source_id, "a.txt")
    r = client.post(
        f"/sources/{source_id}/files/{file_id}/tags",
        data={"tag_name": "trip", "path": "", "q": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303

    r = client.get(f"/sources/{source_id}/browse", params={"q": "travel"})
    assert r.status_code == 200
    assert "a.txt" in r.text


def test_sources_page_defaults_path_to_home(client: TestClient) -> None:
    r = client.get("/sources")
    assert r.status_code == 200
    assert str(Path.home()) in r.text


def test_create_tag_preserves_current_folder_via_next(
    client: TestClient, source_dir: Path
) -> None:
    source_id = _add_source(client, source_dir)
    next_url = f"/sources/{source_id}/browse?path=photos"

    r = client.post(
        f"/sources/{source_id}/tags",
        data={"name": "newtag", "next": next_url},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == next_url

    # An untrusted absolute "next" must not be honored (open-redirect guard).
    r = client.post(
        f"/sources/{source_id}/tags",
        data={"name": "othertag", "next": "https://evil.example/"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/sources/{source_id}/tags"


def test_browse_page_new_tag_form_targets_current_folder(
    client: TestClient, source_dir: Path
) -> None:
    source_id = _add_source(client, source_dir)
    r = client.get(f"/sources/{source_id}/browse", params={"path": "photos"})
    assert r.status_code == 200
    assert f"/sources/{source_id}/browse?path=photos" in r.text


def test_preview_lists_implied_tags_under_supertag(
    client: TestClient, source_dir: Path, data_dir: Path
) -> None:
    source_id = _add_source(client, source_dir)

    for name in ("trip", "travel"):
        client.post(f"/sources/{source_id}/tags", data={"name": name})

    source = config.get_source(source_id, data_dir=data_dir)
    assert source is not None
    conn = db.connect(config.resolve_db_path(source, data_dir))
    trip_id = conn.execute("SELECT id FROM tags WHERE name = 'trip'").fetchone()["id"]
    travel_id = conn.execute("SELECT id FROM tags WHERE name = 'travel'").fetchone()["id"]
    conn.close()

    client.post(f"/sources/{source_id}/tags/{trip_id}/members", data={"member_tag_id": travel_id})

    file_id = _file_id(data_dir, source_id, "a.txt")
    client.post(
        f"/sources/{source_id}/files/{file_id}/tags",
        data={"tag_name": "trip", "path": "", "q": ""},
    )

    r = client.get(f"/sources/{source_id}/files/{file_id}/preview", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "implies" in r.text.lower()
    assert "travel" in r.text
