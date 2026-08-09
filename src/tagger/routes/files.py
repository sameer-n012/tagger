"""File-explorer browsing, search, and (bulk) tag application on files."""

from __future__ import annotations

import sqlite3
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from tagger import search as search_module
from tagger import tags as tags_module
from tagger.routes.deps import get_conn, get_source_or_404
from tagger.templating import templates

router = APIRouter(prefix="/sources/{source_id}", tags=["files"])

Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


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


def _search_files(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    expr = search_module.parse(query)
    universe = {
        row["id"] for row in conn.execute("SELECT id FROM files WHERE status = 'active'")
    }

    def resolver(tag_name: str) -> set[int]:
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
        except search_module.SearchSyntaxError as exc:
            search_error = str(exc)
            file_rows = []
    else:
        subdirs, file_rows = _list_directory(conn, path)

    files_view = [
        {"file": row, "tags": tags_module.tags_for_file(conn, row["id"])} for row in file_rows
    ]
    all_tags = tags_module.list_tags(conn)

    return templates.TemplateResponse(
        request,
        "browse.html",
        {
            "source": source,
            "path": path,
            "breadcrumbs": _breadcrumbs(path),
            "subdirs": subdirs,
            "files": files_view,
            "all_tags": all_tags,
            "q": q,
            "search_error": search_error,
        },
    )


def _back_to_browse_url(source_id: str, path: str, q: str) -> str:
    if q:
        return f"/sources/{source_id}/browse?q={quote(q)}"
    if path:
        return f"/sources/{source_id}/browse?path={quote(path)}"
    return f"/sources/{source_id}/browse"


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

    return RedirectResponse(url=_back_to_browse_url(source_id, path, q), status_code=303)


@router.post("/files/{file_id}/tags")
def add_file_tag(
    source_id: str,
    file_id: int,
    conn: Conn,
    tag_name: str = Form(...),
    path: str = Form(""),
    q: str = Form(""),
):
    name = tag_name.strip()
    if name:
        tag = tags_module.get_tag_by_name(conn, name)
        if tag is None:
            tag = tags_module.create_tag(conn, name)
        tags_module.tag_files(conn, [file_id], [tag.id])
    return RedirectResponse(url=_back_to_browse_url(source_id, path, q), status_code=303)


@router.post("/files/{file_id}/tags/{tag_id}/delete")
def remove_file_tag(
    source_id: str,
    file_id: int,
    tag_id: int,
    conn: Conn,
    path: str = Form(""),
    q: str = Form(""),
):
    tags_module.untag_files(conn, [file_id], [tag_id])
    return RedirectResponse(url=_back_to_browse_url(source_id, path, q), status_code=303)
