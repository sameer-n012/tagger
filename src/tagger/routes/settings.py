"""App-wide settings (currently just the color theme). Global, not
per-source, since there's a single local instance of the app."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tagger import config
from tagger.routes.deps import safe_redirect, with_query_param
from tagger.templating import templates

router = APIRouter(prefix="/settings", tags=["settings"])
logger = logging.getLogger(__name__)


@router.get("")
def show_settings(request: Request, error: str = "", next: str = ""):
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "current_theme": config.get_theme(),
            "themes": config.THEMES,
            "error": error,
            "next": next,
            "back_url": safe_redirect(next, "/sources"),
        },
    )


@router.post("")
def update_settings(theme: str = Form(...), next: str = Form("")):
    target = with_query_param("/settings", "next", next) if next else "/settings"
    try:
        config.set_theme(theme)
        logger.info("theme set to=%s", theme)
    except ValueError as exc:
        target = with_query_param(target, "error", str(exc))
    return RedirectResponse(url=target, status_code=303)
