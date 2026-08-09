# CLAUDE.md

Guidance for Claude Code (and other agents) working in this repository.

## Project Summary

**tagger** is a local-first file tagging tool with a web GUI. It lets a user
point the app at one or more source directories, browse those directories in
a file-explorer-style interface, and attach tags to files (individually or in
bulk). Tags survive files being moved or renamed within the source directory
because files are identified by content hash, not by path. Tags can be
combined into **supertags** (a tag that implies a set of other tags), and
files can be filtered/searched using a boolean expression over tags
(`and`, `or`, `not`, parentheses).

This is a local desktop-style tool (single user, run on localhost), not a
multi-tenant web service.

## Architecture

- **Backend:** Python, FastAPI. Package/dependency management via `uv`.
- **Frontend:** Server-rendered Jinja2 templates + htmx (+ a small amount of
  vanilla JS/CSS where htmx isn't enough, e.g. multi-select). No SPA
  framework, no separate frontend build step.
- **Storage:** SQLite. One database file per configured source directory,
  plus a top-level app config file that lists known source directories. All
  storage lives in this project's own data directory (e.g. `./data/`), never
  inside the user's source directories.
- **Python version:** 3.12+.

## Directory Layout (target)

```
tagger/
  pyproject.toml
  uv.lock
  src/tagger/
    __init__.py
    main.py              # FastAPI app entrypoint
    config.py            # app-level config (known source dirs) load/save
    db.py                # SQLite connection/schema management per source dir
    models.py            # typed dataclasses / pydantic models
    scanner.py           # directory scan + hash + diff-against-db logic
    tags.py              # tag CRUD, supertag expansion
    search.py            # boolean tag-expression parser + query builder
    routes/              # FastAPI routers (files, tags, search, sources)
    templates/            # Jinja2 templates
    static/               # CSS/JS assets
  data/                   # gitignored: per-source-dir SQLite DBs + config
  tests/
```

## Core Data Model (guideline, not gospel — adjust as implementation evolves)

- `sources`: known source directories (id, path, display name, last scan time).
- `files`: id, source_id, content_hash (SHA-256, full file), current relative
  path, size, mtime, status (`active` / `missing`), first_seen_at,
  missing_since (nullable).
- `tags`: id, name, is_supertag (bool).
- `supertag_members`: supertag_id -> member_tag_id (a supertag directly
  implies its member tags; expansion should handle nesting).
- `file_tags`: file_id -> tag_id (many-to-many).

Tags attach to a `content_hash`, not a path — if the same content is scanned
from two different source dirs, that's a modeling edge case to be explicit
about (default assumption: tags are per source-dir DB, so this doesn't cross
databases).

## Rescan / Sync Semantics (important — implement exactly this way)

Rescanning is **manual**, triggered by the user (no background filesystem
watcher). On a rescan of a source directory:

1. Walk the source directory (recursively) and hash every file found
   (full-file SHA-256). Call this set **on-disk**.
2. Load all `active` files for this source from the DB. Call this set
   **db-active**.
3. Compute:
   - `new_paths` = on-disk paths not present in db-active paths.
   - `missing_paths` = db-active paths not found on disk.
4. Match `new_paths` against `missing_paths` **by content hash**:
   - If a new path's hash matches a missing path's hash → treat as a
     **move/rename**: update that file record's path in place, keep its id
     and all existing tags. Do not create a new row.
   - Any `new_paths` left unmatched → truly new files: insert new rows
     (status `active`).
   - Any `missing_paths` left unmatched → truly missing: mark status
     `missing` and set `missing_since = now()` (do not delete the row or its
     tags yet).
5. **Cleanup pass:** files that have been `missing` for longer than a
   configured retention period (default: 30 days) are purged (row + its
   `file_tags` rows deleted) at the start of the *next* rescan after that
   period elapses. This is a hard delete, not a soft one — treat it as
   destructive and don't run it outside of an explicit rescan.

Do not build a live filesystem watcher (e.g. `watchdog`) unless the user
explicitly asks for one later — this was a deliberate choice to keep the
background process model simple.

## Tag Search / Filter Semantics

Search is a boolean expression over tag names: `and`, `or`, `not`, and
parentheses for grouping, e.g. `photos and (vacation or family) and not
private`. Supertags expand to include their member tags when matching (a
file tagged only with a supertag's member should still match a search for
the supertag... confirm this direction with the user if ambiguous — the
inverse, matching a supertag search to files tagged with the supertag
itself, is the non-negotiable baseline behavior).

Write the parser as a small recursive-descent or Pratt parser — don't pull in
a parser-generator dependency for this.

## Coding Conventions

- Strong typing throughout; assume Pyright strict mode. Run type checks
  (`uv run pyright`) after any multi-file change.
- Use `uv` for all dependency management (`uv add`, `uv sync`, `uv run`).
  Never edit `pyproject.toml` dependency lists by hand without also
  regenerating the lockfile via `uv`.
- Keep the scanning/hashing logic (`scanner.py`) and the tag-expression
  parser (`search.py`) framework-agnostic and unit-testable — they should not
  import FastAPI.
- No global mutable state beyond a single app config object; each source
  directory's DB connection is scoped per-request or per-source-id, not
  process-global.
- Follow the global engineering standards in `~/.claude/CLAUDE.md` (typing,
  defensive error handling, no silent fallbacks, grep before deleting
  imports, post-change git workflow, etc.) for all work in this repo.

## Testing

- `tests/` mirrors `src/tagger/`. Prioritize unit tests for `scanner.py`
  (move/new/missing detection) and `search.py` (boolean expression parsing
  and supertag expansion) since those are the trickiest correctness-critical
  pieces.
- Use `uv run pytest`.

## Status

Project scaffolding stage — see `README.md` for the current feature/status
summary and `TODO.md` (once created) for outstanding work.
