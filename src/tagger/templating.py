"""Shared Jinja2Templates instance, imported by main.py and every router."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi.templating import Jinja2Templates

from tagger import config

_STATIC_DIR = Path(__file__).resolve().parent / "static"

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _static_version() -> str:
    """mtime of style.css, used as a cache-busting query param on its
    <link> tag -- without this, browsers may serve a stale cached copy on
    ordinary in-app navigation (only a hard/forced reload reliably
    revalidates), silently reviving old CSS bugs after they're fixed."""
    return str(int((_STATIC_DIR / "style.css").stat().st_mtime))


_globals = cast("dict[str, Any]", templates.env.globals)
_globals["static_version"] = _static_version
_globals["theme"] = config.get_theme
