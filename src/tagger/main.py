"""FastAPI app entrypoint."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from tagger.routes import files, sources, tags

_PACKAGE_DIR = Path(__file__).resolve().parent

DEFAULT_PORT = 3500

app = FastAPI(title="tagger")
app.mount("/static", StaticFiles(directory=str(_PACKAGE_DIR / "static")), name="static")

app.include_router(sources.router)
app.include_router(files.router)
app.include_router(tags.router)


@app.get("/")
def index() -> RedirectResponse:
    return RedirectResponse(url="/sources")


def main() -> None:
    import uvicorn

    uvicorn.run("tagger.main:app", host="127.0.0.1", port=DEFAULT_PORT, reload=True)
