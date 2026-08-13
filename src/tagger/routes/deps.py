"""Shared FastAPI dependencies: resolving a source and opening its DB
connection scoped to a single request (never a process-global connection)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException, Request

from tagger import config, db


def get_source_or_404(source_id: str) -> config.SourceConfig:
    source = config.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Unknown source: {source_id}")
    return source


def browse_url(source_id: str, path: str = "", q: str = "") -> str:
    """The browse URL for a given folder/search state -- shared by every
    route that needs to send the user "back to files" without losing their
    place (bulk-tag, per-file tag edits, the tags-management page link).

    Both are kept together (searches are scoped to `path` and its
    subdirectories -- see files.py's _search_files) rather than letting `q`
    take over the URL alone, which would silently widen the search scope
    back out to the whole source on return."""
    params: list[str] = []
    if path:
        params.append(f"path={quote(path)}")
    if q:
        params.append(f"q={quote(q)}")
    if not params:
        return f"/sources/{source_id}/browse"
    return f"/sources/{source_id}/browse?{'&'.join(params)}"


def with_query_param(url: str, key: str, value: str) -> str:
    """Set/replace a single query param on url, preserving whatever else is
    there. Used to flash a one-off error onto a redirect target (e.g.
    "next") without clobbering the path/q it already carries."""
    scheme, netloc, path, query, fragment = urlsplit(url)
    params = dict(parse_qsl(query))
    params[key] = value
    return urlunsplit((scheme, netloc, path, urlencode(params), fragment))


def wants_partial(request: Request) -> bool:
    """True when the request came from htmx (see hx-* attributes) rather
    than a plain browser navigation -- callers use this to choose between
    returning a swappable HTML fragment and a redirect."""
    return request.headers.get("hx-request") == "true"


def get_conn(source_id: str) -> Iterator[sqlite3.Connection]:
    source = get_source_or_404(source_id)
    conn = db.connect(config.resolve_db_path(source))
    try:
        yield conn
    finally:
        conn.close()
