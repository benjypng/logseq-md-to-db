# Logseq OG (Markdown) → Logseq DB importer

A single-file Python script that imports a Logseq 1.0 file-based (Markdown) graph into a new Logseq DB graph, driving the official `logseq` CLI.

**This is a starting point, not a turnkey migrator.** It is for users who want to *control* their own migration: run the dry run, read the report, decide what to fix in your Markdown or change in the script, and iterate until the import matches what you want. The script is deliberately small (one file, standard library only) so you can read and modify it — see `CLAUDE.md` for a guide written for exactly that, including loading this project into an LLM to make changes for you.

## Requirements

- Python 3.9+
- [Logseq DB version](https://github.com/logseq/logseq) with the `logseq` CLI on your PATH
- A Logseq 1.0 file-based graph (a folder with `pages/`, `journals/`, `logseq/config.edn`)

## Usage

Copy `import.py` into the root of the graph you want to import, then:

```bash
python3 import.py
```

The script asks, in order:

1. Convert `#tags` to `[[page references]]`? (otherwise they become DB tags)
2. Import `prop:: value` properties?
3. Dry run?
4. (Real run only) Name for the new DB graph.

Every prompt has a mirror flag so a re-run needs no retyping:

```bash
python3 import.py --dry-run --no-convert-tags --import-properties
python3 import.py --no-dry-run --no-convert-tags --import-properties --graph my-new-graph
```

**Always dry-run first.** The dry run parses everything and writes `import-report.txt` with counts and per-issue `file:line` locations, so you can fix the source Markdown before importing. The real run writes the same report plus any failures; errors print in red, the failing transaction is skipped, and the import continues.

The script **never writes into an existing graph** — it checks first and aborts if the graph name is taken.

## What it supports

| Feature | How it imports |
|---|---|
| Pages and journals | `pages/*.md` and `journals/YYYY_MM_DD.md` (journals become real DB journal pages); filenames URL-decoded, `___` → `/` |
| Block hierarchy | Full nesting preserved; tab or 2-space indentation |
| Block UUIDs | `id:: <uuid>` is preserved, so `((uuid))` block refs keep resolving in the app |
| Page references | `[[Page Name]]` in content becomes a real reference; missing pages are auto-created |
| Tags | `#tag` / `#[[multi word]]` either rewritten to `[[refs]]` or attached as real DB tags (your choice); `tags::` always becomes DB tags |
| Properties | Page properties (leading `key:: value` lines) and block properties become DB user properties (text type); `[[page]]` values resolve to references |
| Tasks | `TODO`/`LATER` → todo, `DOING`/`NOW` → doing, `DONE` → done, `CANCELED` → canceled, `WAITING` → backlog, as `#Task` blocks; `[#A/#B/#C]` → high/medium/low priority |
| Advanced queries | `#+BEGIN_QUERY … #+END_QUERY` drawers become `#Query` blocks with the query stored as a Clojure code block, matching the DB version's native advanced-query shape |
| Node embeds | Whole-block `{{embed [[Page]]}}` and `{{embed ((uuid))}}` become native DB node embeds (`:block/link`) |
| Validation | Dry-run report flags unresolved block refs, duplicate UUIDs, page-name collisions, broken asset links, and everything it could not convert — each with file and line |

## What it does NOT support

| Not imported | What happens instead |
|---|---|
| **Assets** (`assets/` — images, PDFs, audio) | Skipped entirely; every asset link is listed in the report so you can migrate them manually |
| **Whiteboards** (`draws/*.excalidraw`) | Skipped; the DB version uses a different whiteboard format |
| **LOGBOOK** (`:LOGBOOK: … :END:` clocking history) | Dropped (counted in the report) |
| Inline embeds (`{{embed …}}` mixed with other text in the same block) | Kept as literal text; the DB model has no inline-embed representation |
| Simple query macros (`{{query …}}`) | Kept as literal text |
| `alias::` | Imported as a plain property; DB-native aliases are not wired up |
| Typed properties | All properties import as text; dates/numbers/checkboxes are not detected |
| PDF highlight pages (`hls__*`) | Imported as ordinary pages; annotation linkage is lost |
| Query Datalog rewriting | Queries are converted structurally, but file-graph Datalog often uses attributes that do not exist in DB graphs (e.g. `:block/properties`) — each converted query is listed in the report for you to verify and rewrite |
| Drawers in page preamble | A query/logbook drawer placed before the first bullet of a file is kept as plain text |

## Partially supported — verify after import

- **`SCHEDULED:` / `DEADLINE:` on tasks**: the date (and optional time) is carried over onto the task, but times are interpreted as UTC, and org-mode repeaters (e.g. `.+1w`) are silently dropped. Check your scheduled tasks after importing.
- **Linked-references panels**: `((uuid))` block refs resolve when opened, but the importer does not create reference index entries for them; the app rebuilds these as blocks are touched.
- **Node embeds whose target imports later**: linked in a second pass; the report's NOTES section lists them so you can confirm they render.

## A note on safety

Test against a throwaway graph first (`--graph test-something`), open it in Logseq, and eyeball the result before importing over your real workflow. The importer only ever creates a brand-new graph, but your time is worth the rehearsal.
