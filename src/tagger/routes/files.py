"""File-explorer browsing, search, (bulk) tag application, previewing, and
missing-file bookkeeping."""

from __future__ import annotations

import logging
import mimetypes
import sqlite3
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from tagger import config, scan_status
from tagger import search as search_module
from tagger import tags as tags_module
from tagger.config import SourceConfig
from tagger.routes.deps import browse_url, get_conn, get_source_or_404
from tagger.templating import templates

router = APIRouter(prefix="/sources/{source_id}", tags=["files"])
logger = logging.getLogger(__name__)

Conn = Annotated[sqlite3.Connection, Depends(get_conn)]

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".mpg", ".mpeg", ".avi", ".m4v"}

# python's stdlib mimetypes table is missing (or has stale) entries for a
# few of these -- register them explicitly so FileResponse (in file_raw)
# sends a content-type the browser's <video> element will actually play.
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("video/webm", ".webm")
mimetypes.add_type("video/x-matroska", ".mkv")
mimetypes.add_type("video/quicktime", ".mov")
mimetypes.add_type("video/mpeg", ".mpg")
mimetypes.add_type("video/mpeg", ".mpeg")
mimetypes.add_type("video/x-msvideo", ".avi")
mimetypes.add_type("video/x-m4v", ".m4v")


def _is_image(relative_path: str) -> bool:
    return Path(relative_path).suffix.lower() in _IMAGE_EXTENSIONS


def _is_video(relative_path: str) -> bool:
    return Path(relative_path).suffix.lower() in _VIDEO_EXTENSIONS


def _breadcrumbs(path: str) -> list[tuple[str, str]]:
    """[(segment_name, path_up_to_and_including_segment), ...]"""
    if not path:
        return []
    parts = path.strip("/").split("/")
    crumbs: list[tuple[str, str]] = []
    accumulated = ""
    for part in parts:
        accumulated = f"{accumulated}/{part}" if accumulated else part
        crumbs.append((part, accumulated))
    return crumbs


def _list_directory(
    conn: sqlite3.Connection, prefix: str
) -> tuple[list[str], list[sqlite3.Row]]:
    """Direct subdirectory names and direct active files under prefix
    (prefix='' means the source root)."""
    prefix_norm = f"{prefix.strip('/')}/" if prefix else ""
    like_pattern = f"{prefix_norm}%" if prefix_norm else "%"
    rows = conn.execute(
        "SELECT * FROM files WHERE status = 'active' AND relative_path LIKE ? "
        "ORDER BY relative_path",
        (like_pattern,),
    ).fetchall()

    subdirs: set[str] = set()
    direct_files: list[sqlite3.Row] = []
    for row in rows:
        rel: str = row["relative_path"]
        if not rel.startswith(prefix_norm):
            continue
        remainder = rel[len(prefix_norm) :]
        if "/" in remainder:
            subdirs.add(remainder.split("/", 1)[0])
        else:
            direct_files.append(row)
    return sorted(subdirs), direct_files


_UNTAGGED_TERM = "untagged"


def _search_files(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    expr = search_module.parse(query)
    universe = {
        row["id"] for row in conn.execute("SELECT id FROM files WHERE status = 'active'")
    }

    def resolver(tag_name: str) -> set[int]:
        # Reserved keyword for "no tags at all" -- unless the user has
        # actually created a real tag named "untagged", in which case that
        # takes precedence and behaves like any other tag search.
        if (
            tag_name.strip().lower() == _UNTAGGED_TERM
            and tags_module.get_tag_by_name(conn, tag_name) is None
        ):
            return tags_module.untagged_file_ids(conn)
        tag_ids = tags_module.resolve_search_tag_ids(conn, tag_name)
        return tags_module.file_ids_with_any_tag(conn, tag_ids)

    matched_ids = search_module.evaluate(expr, resolver, universe)
    if not matched_ids:
        return []
    placeholders = ",".join("?" for _ in matched_ids)
    return conn.execute(
        f"SELECT * FROM files WHERE id IN ({placeholders}) ORDER BY relative_path",
        tuple(matched_ids),
    ).fetchall()


def _missing_files(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM files WHERE status = 'missing' ORDER BY missing_since"
    ).fetchall()


def _get_file_or_404(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown file")
    return row


@router.get("/browse")
def browse(
    request: Request,
    source_id: str,
    conn: Conn,
    path: str = "",
    q: str = "",
):
    source = get_source_or_404(source_id)

    search_error: str | None = None
    subdirs: list[str] = []
    file_rows: list[sqlite3.Row]

    if q.strip():
        try:
            file_rows = _search_files(conn, q)
            logger.info(
                "search source_id=%s query=%r matches=%d", source_id, q, len(file_rows)
            )
        except search_module.SearchSyntaxError as exc:
            search_error = str(exc)
            file_rows = []
            logger.info("search source_id=%s query=%r error=%s", source_id, q, search_error)
    else:
        subdirs, file_rows = _list_directory(conn, path)

    files_view = [
        {
            "file": row,
            "tags": tags_module.tags_for_file(conn, row["id"]),
            "is_image": _is_image(row["relative_path"]),
            "is_video": _is_video(row["relative_path"]),
        }
        for row in file_rows
    ]
    all_tags = tags_module.list_tags(conn)
    members_by_supertag = {
        tag.id: tags_module.direct_members(conn, tag.id) for tag in all_tags if tag.is_supertag
    }
    current_scan = scan_status.get(source_id)

    return templates.TemplateResponse(
        request,
        "browse.html",
        {
            "source": source,
            "sources": config.load_config().sources,
            "path": path,
            "breadcrumbs": _breadcrumbs(path),
            "subdirs": subdirs,
            "files": files_view,
            "all_tags": all_tags,
            "members_by_supertag": members_by_supertag,
            "missing_files": _missing_files(conn),
            "q": q,
            "search_error": search_error,
            "scanning": current_scan is not None and current_scan.state == "scanning",
            "back_url": browse_url(source_id, path, q),
        },
    )


def _wants_partial(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def _render_file_panel(
    request: Request,
    source: SourceConfig,
    conn: sqlite3.Connection,
    file_id: int,
    path: str,
    q: str,
):
    file_row = _get_file_or_404(conn, file_id)
    file_tags = tags_module.tags_for_file(conn, file_id)
    members_by_supertag = {
        tag.id: tags_module.direct_members(conn, tag.id) for tag in file_tags if tag.is_supertag
    }
    return templates.TemplateResponse(
        request,
        "_file_panel.html",
        {
            "source": source,
            "file": file_row,
            "tags": file_tags,
            "members_by_supertag": members_by_supertag,
            "is_image": _is_image(file_row["relative_path"]),
            "is_video": _is_video(file_row["relative_path"]),
            "path": path,
            "q": q,
        },
    )


@router.get("/files/{file_id}/preview")
def file_preview(
    request: Request,
    source_id: str,
    file_id: int,
    conn: Conn,
    path: str = "",
    q: str = "",
):
    source = get_source_or_404(source_id)
    return _render_file_panel(request, source, conn, file_id, path, q)


@router.get("/files/{file_id}/raw")
def file_raw(source_id: str, file_id: int, conn: Conn):
    source = get_source_or_404(source_id)
    file_row = _get_file_or_404(conn, file_id)
    if file_row["status"] != "active":
        raise HTTPException(status_code=404, detail="File is missing on disk")

    root = Path(source.path).resolve()
    candidate = (root / file_row["relative_path"]).resolve()
    if not candidate.is_relative_to(root):
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(candidate)


@router.post("/files/{file_id}/purge")
def purge_file(request: Request, source_id: str, file_id: int, conn: Conn):
    source = get_source_or_404(source_id)
    row = conn.execute("SELECT status FROM files WHERE id = ?", (file_id,)).fetchone()
    if row is not None and row["status"] == "missing":
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        conn.commit()
        logger.info("file purged source_id=%s file_id=%s", source_id, file_id)
    return templates.TemplateResponse(
        request,
        "_missing_list.html",
        {"source": source, "missing_files": _missing_files(conn)},
    )


@router.post("/bulk-tag")
async def bulk_tag(source_id: str, request: Request, conn: Conn):
    get_source_or_404(source_id)
    form = await request.form()
    file_ids = [int(v) for v in form.getlist("file_ids") if isinstance(v, str)]
    action = str(form.get("action", "add"))
    path = str(form.get("path", ""))
    q = str(form.get("q", ""))
    raw_names = str(form.get("tag_names", ""))
    names = [n.strip() for n in raw_names.split(",") if n.strip()]

    tag_ids: list[int] = []
    for name in names:
        tag = tags_module.get_tag_by_name(conn, name)
        if tag is None:
            if action == "remove":
                continue
            tag = tags_module.create_tag(conn, name)
        tag_ids.append(tag.id)

    if file_ids and tag_ids:
        if action == "remove":
            tags_module.untag_files(conn, file_ids, tag_ids)
        else:
            tags_module.tag_files(conn, file_ids, tag_ids)
        logger.info(
            "bulk tag source_id=%s action=%s file_count=%d tags=%s",
            source_id, action, len(file_ids), names,
        )

    return RedirectResponse(url=browse_url(source_id, path, q), status_code=303)


@router.post("/files/{file_id}/tags")
def add_file_tag(
    request: Request,
    source_id: str,
    file_id: int,
    conn: Conn,
    tag_name: str = Form(...),
    path: str = Form(""),
    q: str = Form(""),
):
    source = get_source_or_404(source_id)
    name = tag_name.strip()
    if name:
        tag = tags_module.get_tag_by_name(conn, name)
        if tag is None:
            tag = tags_module.create_tag(conn, name)
        tags_module.tag_files(conn, [file_id], [tag.id])
        logger.info("tag added source_id=%s file_id=%s tag=%s", source_id, file_id, name)

    if _wants_partial(request):
        return _render_file_panel(request, source, conn, file_id, path, q)
    return RedirectResponse(url=browse_url(source_id, path, q), status_code=303)


@router.post("/files/{file_id}/tags/{tag_id}/delete")
def remove_file_tag(
    request: Request,
    source_id: str,
    file_id: int,
    tag_id: int,
    conn: Conn,
    path: str = Form(""),
    q: str = Form(""),
):
    source = get_source_or_404(source_id)
    tags_module.untag_files(conn, [file_id], [tag_id])
    logger.info("tag removed source_id=%s file_id=%s tag_id=%s", source_id, file_id, tag_id)

    if _wants_partial(request):
        return _render_file_panel(request, source, conn, file_id, path, q)
    return RedirectResponse(url=browse_url(source_id, path, q), status_code=303)
