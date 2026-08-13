"""FastAPI app entrypoint."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from tagger.logging_config import configure_logging
from tagger.routes import files, settings, sources, tags

_PACKAGE_DIR = Path(__file__).resolve().parent

DEFAULT_PORT = 3500

configure_logging()
_request_logger = logging.getLogger("tagger.request")

app = FastAPI(title="tagger")
app.mount("/static", StaticFiles(directory=str(_PACKAGE_DIR / "static")), name="static")

app.include_router(sources.router)
app.include_router(files.router)
app.include_router(tags.router)
app.include_router(settings.router)


@app.middleware("http")
async def log_requests(request: Request, call_next: RequestResponseEndpoint) -> Response:
    started = time.monotonic()
    response = await call_next(request)
    elapsed_ms = (time.monotonic() - started) * 1000
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    _request_logger.info(
        "%s %s -> %s (%.1fms)", request.method, target, response.status_code, elapsed_ms
    )
    return response


@app.get("/")
def index() -> RedirectResponse:
    return RedirectResponse(url="/sources")


def main() -> None:
    import uvicorn

    uvicorn.run("tagger.main:app", host="127.0.0.1", port=DEFAULT_PORT, reload=True)
