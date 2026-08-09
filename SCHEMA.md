# Database Schema

`tagger` keeps **one SQLite database per configured source directory**, plus
a single top-level **app config file** that lists known source directories
and points at their DB files. Both live under this project's own `data/`
directory (gitignored), never inside the source directory being tagged.

```
data/
  config.json          # app-level config: known sources
  sources/
    <source-id>.sqlite # one DB per source directory
```

## App config (`data/config.json`)

Not a database — a small JSON file, since it only needs to answer "what
source directories exist and where's their DB" before any per-source DB is
even opened (e.g. on the "pick a source directory" screen at launch).

```json
{
  "sources": [
    {
      "id": "b3f1c9...",              // stable id, see below
      "path": "/Users/sameer/Photos",
      "display_name": "Photos",
      "db_file": "sources/b3f1c9....sqlite",
      "created_at": "2026-08-09T12:00:00Z"
    }
  ]
}
```

- `id` is a random UUID4 generated when the source is first added — **not**
  derived from the path, so renaming/moving the source directory itself
  doesn't orphan its database (the user can just update `path`).
- `db_file` is relative to `data/`.

## Per-source database (`data/sources/<id>.sqlite`)

### `meta`

Single-row (or key/value) table for schema versioning and per-source
settings that belong with the data, not the app config.

| column                  | type | notes                                             |
|--------------------------|------|---------------------------------------------------|
| `key`                    | TEXT | PRIMARY KEY                                        |
| `value`                  | TEXT |                                                     |

Rows used at minimum:
- `schema_version` — integer string, for future migrations.
- `missing_retention_days` — default `"30"`; see rescan semantics in
  `CLAUDE.md`. Overridable per source.
- `last_scan_at` — ISO 8601 timestamp of the last completed rescan, or absent
  if never scanned.

### `files`

The core inventory table: every file ever seen under this source directory.

| column           | type    | notes                                                                 |
|-------------------|---------|------------------------------------------------------------------------|
| `id`              | INTEGER | PRIMARY KEY AUTOINCREMENT                                              |
| `content_hash`    | TEXT    | SHA-256 hex digest of full file contents. NOT NULL.                    |
| `relative_path`   | TEXT    | Path relative to the source directory root, `/`-separated. NOT NULL.   |
| `size_bytes`      | INTEGER | NOT NULL                                                                |
| `mtime`           | TEXT    | ISO 8601, file's mtime as of last time it was seen. NOT NULL.          |
| `status`          | TEXT    | `'active'` \| `'missing'`. NOT NULL, default `'active'`.               |
| `first_seen_at`   | TEXT    | ISO 8601, when this row was first created. NOT NULL.                   |
| `last_seen_at`    | TEXT    | ISO 8601, last rescan that found this file on disk. NOT NULL.          |
| `missing_since`   | TEXT    | ISO 8601, nullable. Set when status transitions to `missing`.          |

Indexes:
- `UNIQUE(relative_path)` — a source directory can't have two files at the
  same path simultaneously (only one `active` file per path at a time; when
  a path is reused after the original was purged, that's simply a new row).
- `INDEX(content_hash)` — required for the move-detection matching step in a
  rescan (`new_paths` vs `missing_paths` matched by hash).
- `INDEX(status)` — rescans filter by `status = 'active'` and cleanup
  filters by `status = 'missing'`.

Design notes:
- `content_hash` is **not** globally unique — two files with byte-identical
  content are legitimately two separate rows (e.g. a duplicate photo kept in
  two places), each with its own path and, potentially, its own tags. Move
  detection during rescan only needs hash lookups scoped to the *currently
  missing* rows, so duplicate hashes elsewhere don't cause false matches
  (see "Move-matching disambiguation" below).
- No foreign key to `sources` — the source is implicit (one DB per source),
  so there's no `source_id` column needed inside a per-source DB.

#### Move-matching disambiguation

If a rescan has multiple `missing_paths` and multiple `new_paths` that share
the same hash (e.g. 3 identical files, 2 moved + 1 deleted), match them
deterministically rather than arbitrarily:
1. Sort both the missing and new candidates (for a given hash) by their old
   `relative_path` / new relative path respectively.
2. Pair them off in that order, first-to-first.
3. Any leftovers on the "missing" side stay missing; any leftovers on the
   "new" side become new rows.

This is a pragmatic tie-break, not a correctness guarantee — duplicate
content is inherently ambiguous on "which specific file moved where." It's
documented here so the behavior is at least deterministic and testable.

### `tags`

| column         | type    | notes                                                        |
|-----------------|---------|----------------------------------------------------------------|
| `id`            | INTEGER | PRIMARY KEY AUTOINCREMENT                                       |
| `name`          | TEXT    | NOT NULL, `UNIQUE`. Case-insensitive uniqueness enforced at the application layer (store as-entered, compare lowercased). |
| `is_supertag`   | INTEGER | 0/1 boolean, NOT NULL, default 0.                               |
| `color`         | TEXT    | nullable, optional UI hint (hex string).                        |
| `created_at`    | TEXT    | ISO 8601, NOT NULL.                                             |

A tag doesn't stop being a normal, taggable tag when it becomes a supertag —
`is_supertag` just means it has rows in `supertag_members` and its expansion
rules apply during search/filter. A supertag can itself be tagged directly
on a file, same as any tag (via `file_tags`).

### `supertag_members`

Defines what a supertag implies. A supertag "is itself + a bunch of other
tags" — i.e. tagging a file with the supertag should behave, for search
purposes, as if all its member tags were also present.

| column           | type    | notes                                                              |
|-------------------|---------|----------------------------------------------------------------------|
| `supertag_id`     | INTEGER | FK -> `tags.id`, part of composite PK.                               |
| `member_tag_id`   | INTEGER | FK -> `tags.id`, part of composite PK.                               |

- `PRIMARY KEY (supertag_id, member_tag_id)`
- `CHECK (supertag_id != member_tag_id)` — a supertag can't be its own member.
- Nesting is allowed (a supertag's member can itself be a supertag), so
  expansion at query time must walk the membership graph transitively.
  **Cycle prevention is an application-layer responsibility**: when adding a
  `supertag_members` row, walk the prospective member's own transitive
  membership first and reject the write if it would reach `supertag_id`
  (i.e. reject the edge before it creates a cycle, rather than detecting
  cycles later at expansion time).
- `ON DELETE CASCADE` on both FKs — deleting a tag removes any
  `supertag_members` rows referencing it as supertag or member.

### `file_tags`

The many-to-many join between files and tags — the actual, directly-applied
tags on a file (not the supertag-expanded set; expansion happens at query
time, not by materializing extra rows here).

| column         | type    | notes                                                    |
|-----------------|---------|-------------------------------------------------------------|
| `file_id`       | INTEGER | FK -> `files.id` ON DELETE CASCADE, part of composite PK.    |
| `tag_id`        | INTEGER | FK -> `tags.id` ON DELETE CASCADE, part of composite PK.     |
| `tagged_at`     | TEXT    | ISO 8601, NOT NULL.                                          |

- `PRIMARY KEY (file_id, tag_id)`
- `INDEX(tag_id)` — search/filter is driven by "find files having tag X",
  so this is the hot lookup path (the PK already covers `file_id` first).

## Supertag expansion at query/search time

Given a search expression like `vacation and not private`, each tag name in
the expression resolves to a **set of tag ids that would satisfy it**: the
tag's own id, plus the id of any tag for which it is a (transitively)
implied member is *not* relevant here — expansion goes the other direction.

Concretely: if `roadtrip` is a supertag implying `travel` and `photos`, then
a file tagged only `roadtrip` should match a search for `travel` (the
supertag stands in for its members). So resolving the search term `travel`
must include: `travel` itself, plus every supertag that transitively implies
`travel`. This is computed by walking `supertag_members` upward
(`member_tag_id = travel` → find `supertag_id`s, then repeat) rather than
downward.

A file matches search term `T` if `file_tags` contains any tag id in that
resolved set for `T`. Boolean combination (`and`/`or`/`not`/parens) is then
just set algebra over per-term matching file-id sets, built by the parser
described in `CLAUDE.md`.

## Rescan bookkeeping

No dedicated "scan history" table for v1 — `meta.last_scan_at` is enough.
If a scan/audit log becomes useful later (e.g. "show me what changed in the
last rescan"), add a `scan_events` table then rather than speculatively now.

## Migrations

No migration framework for v1 (single-developer, pre-1.0, schema still
moving). `meta.schema_version` exists so a lightweight manual migration path
can be added later without redesigning storage. Do not add Alembic or
similar until the schema has stabilized enough to need it.
