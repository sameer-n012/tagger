"""App-level configuration: known source directories.

Stored as JSON (per the project convention of avoiding YAML for config) at
``<data_dir>/config.json``. Each source directory gets a stable, randomly
generated id so renaming/moving the source directory itself never orphans
its database -- only the ``path`` field needs to be updated.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

_REPO_ROOT = Path(__file__).resolve().parents[2]


def get_data_dir() -> Path:
    """Root directory for all app-owned storage (config + per-source DBs).

    Overridable via ``TAGGER_DATA_DIR`` (used by tests to avoid touching the
    real project data directory).
    """
    override = os.environ.get("TAGGER_DATA_DIR")
    return Path(override).resolve() if override else _REPO_ROOT / "data"


def _config_path(data_dir: Path) -> Path:
    return data_dir / "config.json"


class SourceConfig(BaseModel):
    id: str
    path: str
    display_name: str
    db_file: str
    created_at: str


class AppConfig(BaseModel):
    sources: list[SourceConfig] = Field(default_factory=lambda: [])


def load_config(data_dir: Path | None = None) -> AppConfig:
    data_dir = data_dir or get_data_dir()
    path = _config_path(data_dir)
    if not path.exists():
        return AppConfig()
    return AppConfig.model_validate_json(path.read_text(encoding="utf-8"))


def save_config(config: AppConfig, data_dir: Path | None = None) -> None:
    data_dir = data_dir or get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "sources").mkdir(parents=True, exist_ok=True)
    path = _config_path(data_dir)
    path.write_text(config.model_dump_json(indent=2), encoding="utf-8")


def get_source(source_id: str, data_dir: Path | None = None) -> SourceConfig | None:
    config = load_config(data_dir)
    for source in config.sources:
        if source.id == source_id:
            return source
    return None


def add_source(
    path: Path,
    display_name: str | None = None,
    data_dir: Path | None = None,
) -> SourceConfig:
    """Register a new source directory and allocate it a fresh database file.

    Raises ``ValueError`` if the path doesn't exist, isn't a directory, or is
    already registered as a source.
    """
    resolved = path.resolve()
    if not resolved.is_dir():
        raise ValueError(f"Not a directory: {resolved}")

    data_dir = data_dir or get_data_dir()
    config = load_config(data_dir)
    for existing in config.sources:
        if Path(existing.path).resolve() == resolved:
            raise ValueError(f"Source directory already registered: {resolved}")

    source_id = uuid.uuid4().hex
    source = SourceConfig(
        id=source_id,
        path=str(resolved),
        display_name=display_name or resolved.name,
        db_file=f"sources/{source_id}.sqlite",
        created_at=datetime.now(UTC).isoformat(),
    )
    config.sources.append(source)
    save_config(config, data_dir)
    return source


def remove_source(
    source_id: str,
    data_dir: Path | None = None,
    delete_db: bool = False,
) -> None:
    """Unregister a source. Leaves its database file on disk unless delete_db=True."""
    data_dir = data_dir or get_data_dir()
    config = load_config(data_dir)
    remaining = [s for s in config.sources if s.id != source_id]
    if len(remaining) == len(config.sources):
        raise ValueError(f"Unknown source id: {source_id}")

    if delete_db:
        source = next(s for s in config.sources if s.id == source_id)
        db_path = data_dir / source.db_file
        db_path.unlink(missing_ok=True)

    config.sources = remaining
    save_config(config, data_dir)


def resolve_db_path(source: SourceConfig, data_dir: Path | None = None) -> Path:
    data_dir = data_dir or get_data_dir()
    return data_dir / source.db_file
