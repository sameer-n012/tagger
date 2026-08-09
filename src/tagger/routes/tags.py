"""Tag management: create/rename/delete tags and edit supertag membership."""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from tagger import tags as tags_module
from tagger.routes.deps import browse_url, get_conn, get_source_or_404
from tagger.templating import templates

router = APIRouter(prefix="/sources/{source_id}/tags", tags=["tags"])

Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def _safe_redirect(next_url: str, default: str) -> str:
    """Only ever redirect to a same-origin path, never an attacker-supplied
    absolute/protocol-relative URL (open-redirect guard)."""
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return default


@router.get("")
def list_tags_page(request: Request, source_id: str, conn: Conn, path: str = "", q: str = ""):
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
            "back_url": browse_url(source_id, path, q),
        },
    )


@router.post("")
def create_tag(source_id: str, conn: Conn, name: str = Form(...), next: str = Form("")):
    name = name.strip()
    if name and tags_module.get_tag_by_name(conn, name) is None:
        tags_module.create_tag(conn, name)
    return RedirectResponse(
        url=_safe_redirect(next, f"/sources/{source_id}/tags"), status_code=303
    )


@router.post("/{tag_id}/rename")
def rename_tag(source_id: str, tag_id: int, conn: Conn, new_name: str = Form(...)):
    new_name = new_name.strip()
    if new_name:
        tags_module.rename_tag(conn, tag_id, new_name)
    return RedirectResponse(url=f"/sources/{source_id}/tags", status_code=303)


@router.post("/{tag_id}/delete")
def delete_tag(source_id: str, tag_id: int, conn: Conn):
    tags_module.delete_tag(conn, tag_id)
    return RedirectResponse(url=f"/sources/{source_id}/tags", status_code=303)


@router.post("/{tag_id}/members")
def add_member(source_id: str, tag_id: int, conn: Conn, member_tag_id: int = Form(...)):
    try:
        tags_module.add_supertag_member(conn, tag_id, member_tag_id)
    except ValueError:
        pass  # self-membership or cycle -- silently ignored, no member added
    return RedirectResponse(url=f"/sources/{source_id}/tags", status_code=303)


@router.post("/{tag_id}/members/{member_id}/delete")
def remove_member(source_id: str, tag_id: int, member_id: int, conn: Conn):
    tags_module.remove_supertag_member(conn, tag_id, member_id)
    return RedirectResponse(url=f"/sources/{source_id}/tags", status_code=303)
