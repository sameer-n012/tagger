"""Source directory picker: list/add/remove known source directories, and
trigger a rescan of one."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from tagger import config, db, scan_status, scanner
from tagger.routes.deps import get_source_or_404
from tagger.templating import templates

router = APIRouter(prefix="/sources", tags=["sources"])
logger = logging.getLogger(__name__)


def _run_scan_in_background(source: config.SourceConfig) -> None:
    """Runs on a daemon thread -- scan_status.start(source.id) has already
    been called synchronously by the triggering route so status is never
    momentarily absent, and this owns its own db connection since the
    request-scoped one is closed as soon as the response is sent."""
    conn = db.connect(config.resolve_db_path(source))
    try:
        def progress_cb(processed: int, total: int) -> None:
            scan_status.update(source.id, processed, total)

        scanner.rescan(conn, Path(source.path), progress_cb=progress_cb)
        scan_status.finish(source.id)
    except Exception as exc:
        logger.exception("background scan failed source_id=%s", source.id)
        scan_status.fail(source.id, str(exc))
    finally:
        conn.close()


class ScanStatusPayload(BaseModel):
    scanning: bool
    processed: int = 0
    total: int = 0
    error: str | None = None


@router.get("/{source_id}/scan-status")
def get_scan_status(source_id: str) -> ScanStatusPayload:
    get_source_or_404(source_id)
    status = scan_status.get(source_id)
    if status is None:
        return ScanStatusPayload(scanning=False)
    return ScanStatusPayload(
        scanning=status.state == "scanning",
        processed=status.processed,
        total=status.total,
        error=status.error,
    )


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

    logger.info("source added id=%s path=%s name=%s", source.id, source.path, source.display_name)

    # Initial scan runs in the background so this request (and the redirect
    # below) return immediately -- the browse page polls /scan-status and
    # shows a blocking overlay for however long the scan takes.
    scan_status.start(source.id)
    threading.Thread(target=_run_scan_in_background, args=(source,), daemon=True).start()

    return RedirectResponse(url=f"/sources/{source.id}/browse", status_code=303)


@router.post("/{source_id}/rescan")
def rescan_source(source_id: str):
    source = get_source_or_404(source_id)
    logger.info("rescan requested source_id=%s", source_id)
    if not scan_status.is_scanning(source_id):
        scan_status.start(source_id)
        threading.Thread(target=_run_scan_in_background, args=(source,), daemon=True).start()
    return RedirectResponse(url=f"/sources/{source_id}/browse", status_code=303)


@router.post("/{source_id}/delete")
def delete_source(source_id: str, delete_db: bool = Form(False)):
    get_source_or_404(source_id)
    config.remove_source(source_id, delete_db=delete_db)
    logger.info("source deleted id=%s delete_db=%s", source_id, delete_db)
    return RedirectResponse(url="/sources", status_code=303)
