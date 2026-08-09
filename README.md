# tagger

A local file tagging tool with a web GUI. Point it at one or more
directories, browse them like a file explorer, and tag files individually or
in bulk. Tags stick to files even when they're moved or renamed, because
files are tracked by content hash rather than path.

> Status: early scaffolding — see [CLAUDE.md](./CLAUDE.md) for the intended
> architecture and design decisions this project is being built against.

## Features (planned)

- **Tag files and folders** — attach one or more tags to any file, or to a
  multi-selection of files at once.
- **Move-tolerant** — each file is fingerprinted with a full-file SHA-256
  hash on scan, so tags survive renames and moves within a source directory.
- **Bulk tagging** — select many files (e.g. shift/ctrl-click, like a normal
  file explorer) and apply or remove tags in one action.
- **Search & filter** — find files with boolean tag expressions:
  `and`, `or`, `not`, and parentheses, e.g.
  `vacation and (2023 or 2024) and not private`.
- **Supertags** — define a tag that automatically implies a set of other
  tags (e.g. tagging something `#roadtrip` could imply `#travel` and
  `#photos`).
- **Multiple source directories** — choose which directory to browse/tag
  when you open the app; each source directory gets its own database.
- **File-explorer-style GUI** — browse the real folder tree of a source
  directory, with tagging controls alongside it, in a web UI served locally.

## How it works

- On first run (or when adding a new source), you pick a directory. `tagger`
  recursively scans it, hashing every file's contents.
- Rescans are **manual** (triggered from the UI, no background filesystem
  watcher). A rescan diffs the current directory contents against the
  database:
  - Files whose path is new but whose hash matches a file the DB thought was
    missing are treated as **moved** — the existing tags are kept.
  - Files that are genuinely new are added.
  - Files that were tracked but can no longer be found on disk are marked
    **missing** (not deleted outright), and are only purged from the
    database after they've stayed missing for a configurable retention
    period across a later rescan.
- All tag data, file records, and per-source databases live in this
  project's own `data/` directory — never inside the source directories
  themselves.

## Tech stack

- **Backend:** Python 3.12+, [FastAPI](https://fastapi.tiangolo.com/)
- **Frontend:** Jinja2 templates + [htmx](https://htmx.org/) (server-rendered,
  no SPA build step)
- **Storage:** SQLite — one database per configured source directory, plus a
  small local config file listing known sources
- **Package management:** [uv](https://github.com/astral-sh/uv)

## Getting started

```bash
# install dependencies
uv sync

# run the dev server
uv run fastapi dev src/tagger/main.py
```

Then open the printed local URL in a browser. On first launch you'll be
asked to choose (or add) a source directory to browse and tag.

## Development

```bash
# run tests
uv run pytest

# type-check (Pyright, strict mode)
uv run pyright
```

## License

MIT — see [LICENSE](./LICENSE).
