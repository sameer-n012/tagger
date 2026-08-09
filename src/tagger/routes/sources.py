"""Source directory picker: list/add/remove known source directories, and
trigger a rescan of one."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tagger import config, db, scanner
from tagger.routes.deps import get_source_or_404
from tagger.templating import templates

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("")
def list_sources(request: Request):
    app_config = config.load_config()
    return templates.TemplateResponse(
        request,
        "sources.html",
        {"sources": app_config.sources, "path_value": str(Path.home())},
    )


@router.post("")
def add_source(request: Request, path: str = Form(...), display_name: str = Form("")):
    try:
        source = config.add_source(Path(path), display_name.strip() or None)
    except ValueError as exc:
        app_config = config.load_config()
        return templates.TemplateResponse(
            request,
            "sources.html",
            {"sources": app_config.sources, "error": str(exc), "path_value": path},
            status_code=400,
        )

    # Initial scan so the browse view isn't empty on first visit.
    conn = db.connect(config.resolve_db_path(source))
    try:
        scanner.rescan(conn, Path(source.path))
    finally:
        conn.close()

    return RedirectResponse(url=f"/sources/{source.id}/browse", status_code=303)


@router.post("/{source_id}/rescan")
def rescan_source(source_id: str):
    source = get_source_or_404(source_id)
    conn = db.connect(config.resolve_db_path(source))
    try:
        scanner.rescan(conn, Path(source.path))
    finally:
        conn.close()
    return RedirectResponse(url=f"/sources/{source_id}/browse", status_code=303)


@router.post("/{source_id}/delete")
def delete_source(source_id: str, delete_db: bool = Form(False)):
    get_source_or_404(source_id)
    config.remove_source(source_id, delete_db=delete_db)
    return RedirectResponse(url="/sources", status_code=303)
