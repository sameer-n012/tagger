"""Tag management: create/rename/delete tags and edit supertag membership."""

from __future__ import annotations

import logging
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from tagger import tags as tags_module
from tagger.routes.deps import browse_url, get_conn, get_source_or_404, with_query_param
from tagger.templating import templates

router = APIRouter(prefix="/sources/{source_id}/tags", tags=["tags"])
logger = logging.getLogger(__name__)

Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def _safe_redirect(next_url: str, default: str) -> str:
    """Only ever redirect to a same-origin path, never an attacker-supplied
    absolute/protocol-relative URL (open-redirect guard)."""
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return default


@router.get("")
def list_tags_page(
    request: Request,
    source_id: str,
    conn: Conn,
    path: str = "",
    q: str = "",
    error: str = "",
    info: str = "",
):
    source = get_source_or_404(source_id)
    all_tags = tags_module.list_tags(conn)
    members_by_supertag = {
        tag.id: tags_module.direct_members(conn, tag.id) for tag in all_tags if tag.is_supertag
    }
    return templates.TemplateResponse(
        request,
        "tags.html",
        {
            "source": source,
            "tags": all_tags,
            "members_by_supertag": members_by_supertag,
            "tag_counts": tags_module.tag_file_counts(conn),
            "back_url": browse_url(source_id, path, q),
            "error": error,
            "info": info,
        },
    )


@router.post("")
def create_tag(source_id: str, conn: Conn, name: str = Form(...), next: str = Form("")):
    name = name.strip()
    target = _safe_redirect(next, f"/sources/{source_id}/tags")
    if name:
        try:
            tags_module.create_tag(conn, name)
            logger.info("tag created source_id=%s name=%s", source_id, name)
        except ValueError as exc:
            target = with_query_param(target, "error", str(exc))
    return RedirectResponse(url=target, status_code=303)


@router.post("/{tag_id}/rename")
def rename_tag(source_id: str, tag_id: int, conn: Conn, new_name: str = Form(...)):
    new_name = new_name.strip()
    target = f"/sources/{source_id}/tags"
    if new_name:
        try:
            tags_module.rename_tag(conn, tag_id, new_name)
            logger.info(
                "tag renamed source_id=%s tag_id=%s new_name=%s", source_id, tag_id, new_name
            )
        except ValueError as exc:
            target = with_query_param(target, "error", str(exc))
    return RedirectResponse(url=target, status_code=303)


@router.post("/{tag_id}/delete")
def delete_tag(source_id: str, tag_id: int, conn: Conn):
    tags_module.delete_tag(conn, tag_id)
    logger.info("tag deleted source_id=%s tag_id=%s", source_id, tag_id)
    return RedirectResponse(url=f"/sources/{source_id}/tags", status_code=303)


@router.post("/{tag_id}/members")
def add_member(source_id: str, tag_id: int, conn: Conn, member_tag_id: int = Form(...)):
    try:
        tags_module.add_supertag_member(conn, tag_id, member_tag_id)
        logger.info(
            "supertag member added source_id=%s supertag_id=%s member_tag_id=%s",
            source_id, tag_id, member_tag_id,
        )
    except ValueError:
        pass  # self-membership or cycle -- silently ignored, no member added
    return RedirectResponse(url=f"/sources/{source_id}/tags", status_code=303)


@router.post("/{tag_id}/members/{member_id}/delete")
def remove_member(source_id: str, tag_id: int, member_id: int, conn: Conn):
    tags_module.remove_supertag_member(conn, tag_id, member_id)
    logger.info(
        "supertag member removed source_id=%s supertag_id=%s member_tag_id=%s",
        source_id, tag_id, member_id,
    )
    return RedirectResponse(url=f"/sources/{source_id}/tags", status_code=303)


@router.post("/clean-unused")
def clean_unused(source_id: str, conn: Conn):
    removed = tags_module.clean_unused_tags(conn)
    logger.info("cleaned unused tags source_id=%s removed=%d", source_id, removed)
    message = f"Removed {removed} unused tag{'' if removed == 1 else 's'}."
    target = with_query_param(f"/sources/{source_id}/tags", "info", message)
    return RedirectResponse(url=target, status_code=303)


@router.post("/merge-implied")
def merge_implied(source_id: str, conn: Conn):
    removed = tags_module.merge_implied_tags(conn)
    logger.info("merged implied tags source_id=%s removed=%d", source_id, removed)
    message = f"Removed {removed} redundant tag application{'' if removed == 1 else 's'}."
    target = with_query_param(f"/sources/{source_id}/tags", "info", message)
    return RedirectResponse(url=target, status_code=303)
