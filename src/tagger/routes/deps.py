"""Shared FastAPI dependencies: resolving a source and opening its DB
connection scoped to a single request (never a process-global connection)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import HTTPException

from tagger import config, db


def get_source_or_404(source_id: str) -> config.SourceConfig:
    source = config.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Unknown source: {source_id}")
    return source


def get_conn(source_id: str) -> Iterator[sqlite3.Connection]:
    source = get_source_or_404(source_id)
    conn = db.connect(config.resolve_db_path(source))
    try:
        yield conn
    finally:
        conn.close()
