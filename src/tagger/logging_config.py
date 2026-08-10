"""File-based logging setup, shared by the app entrypoint and (indirectly)
every module that calls ``logging.getLogger("tagger.<module>")``.

All ``tagger.*`` loggers propagate up to the "tagger" logger configured
here, so attaching a single FileHandler to it is enough to capture request
traffic and mutating actions (tags/sources/scans/searches) from anywhere in
the app -- see the per-module ``logger.info(...)`` calls in scanner.py and
the route modules.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def default_log_path() -> Path:
    """Overridable via ``TAGGER_LOG_DIR`` (used by tests to avoid writing
    into the real project's logs/ directory)."""
    override = os.environ.get("TAGGER_LOG_DIR")
    base = Path(override).resolve() if override else _REPO_ROOT / "logs"
    return base / "app.log"


def configure_logging(log_path: Path | None = None) -> None:
    log_path = log_path or default_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("tagger")
    logger.setLevel(logging.INFO)

    # uvicorn --reload re-imports the app module in the same process on
    # occasion; guard against stacking duplicate handlers.
    if any(
        isinstance(h, logging.FileHandler) and h.baseFilename == str(log_path)
        for h in logger.handlers
    ):
        return

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.addHandler(handler)
