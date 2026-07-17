# Logseq OG (Markdown) to Logseq DB importer

A single-file Python script that imports a Logseq 1.0 file-based (Markdown) graph into a new Logseq DB graph, using the official `logseq` CLI.

This is a starting point for users who want to control the import approach themselves. It is not a turnkey migrator. The intended workflow is to run the dry run, read the report, fix the Markdown or change the script, and repeat until the import matches what you want. The script is one file with no dependencies beyond the Python standard library, so it can be read and modified in full. `CLAUDE.md` documents the internals for this purpose, including loading the project into an LLM and having it make the changes for you.

## Requirements

- Python 3.9 or later
- The Logseq DB version, with the `logseq` CLI on your PATH
- A Logseq 1.0 file-based graph: a folder containing `pages/`, `journals/` and `logseq/config.edn`

## Usage

Copy `import.py` into the root of the graph you want to import, then run:

```bash
python3 import.py
```

The script asks, in order:

1. Convert `#tags` to `[[page references]]`? If not, they become DB tags.
2. Import `prop:: value` properties?
3. Dry run?
4. On a real run only: a name for the new DB graph.

Each prompt has a matching flag, so a second run needs no retyping:

```bash
python3 import.py --dry-run --no-convert-tags --import-properties
python3 import.py --no-dry-run --no-convert-tags --import-properties --graph my-new-graph
```

Run the dry run first. It parses everything without touching the CLI and writes a report you can work through before committing to an import. During a real run, each error prints in red, the failing transaction is skipped, and the import continues; the script never writes into an existing graph, and aborts if the graph name is already taken.

## The report

Both modes write `import-report.txt` next to the script: the dry run so you can fix problems before importing, the real run so you have a record of what happened. The report contains:

- The mode (dry run or import) and the options chosen.
- Counts: pages, journals, blocks, tasks, distinct tags and properties, advanced queries and node embeds converted, and how many `LOGBOOK` drawers, `collapsed::` markers and property lines were dropped.
- Issues grouped by kind. Each kind carries a one-line explanation of what will happen in the new graph, followed by every occurrence with its file and line number. The kinds cover unresolved `((uuid))` block references, duplicate and invalid `id::` values, page-name collisions (which merge into one page), asset and whiteboard links that will break, converted queries and embeds to verify, unclosed drawers, PDF-highlight pages, and journal files whose names do not parse as dates.

A real run adds two sections. FAILURES lists every transaction that was skipped, with the file or item and the CLI error, so you can decide what to do about each. NOTES lists embeds that were linked in a second pass rather than at creation; these render correctly in most cases but are worth checking in the app.

## What it supports

| Feature | How it imports |
|---|---|
| Pages and journals | `pages/*.md` and `journals/YYYY_MM_DD.md` (journals become real DB journal pages); filenames URL-decoded, `___` becomes `/` |
| Block hierarchy | Full nesting preserved; tab or two-space indentation |
| Block UUIDs | `id:: <uuid>` is preserved, so `((uuid))` block references keep resolving in the app |
| Page references | `[[Page Name]]` in content becomes a real reference; missing pages are created |
| Tags | `#tag` and `#[[multi word]]` either rewritten to `[[refs]]` or attached as DB tags, per your choice; `tags::` always becomes DB tags |
| Properties | Page properties (leading `key:: value` lines) and block properties become DB user properties of text type; `[[page]]` values resolve to references |
| Tasks | `TODO`/`LATER` to todo, `DOING`/`NOW` to doing, `DONE` to done, `CANCELED` to canceled, `WAITING` to backlog, as `#Task` blocks; `[#A/#B/#C]` to high/medium/low priority |
| Advanced queries | `#+BEGIN_QUERY … #+END_QUERY` drawers become `#Query` blocks with the query stored as a Clojure code block, the DB version's native advanced-query shape |
| Node embeds | Whole-block `{{embed [[Page]]}}` and `{{embed ((uuid))}}` become native DB node embeds |

## What it does not support

| Not imported | What happens instead |
|---|---|
| Assets (`assets/`: images, PDFs, audio) | Skipped; every asset link is listed in the report for manual migration |
| Whiteboards (`draws/*.excalidraw`) | Skipped; the DB version uses a different whiteboard format |
| `LOGBOOK` clocking history | Dropped, with the count recorded in the report |
| Inline embeds (`{{embed …}}` mixed with other text in a block) | Kept as literal text; the DB model has no inline-embed representation |
| Simple query macros (`{{query …}}`) | Kept as literal text |
| `alias::` | Imported as a plain property; DB-native aliases are not wired up |
| Typed properties | All properties import as text; dates, numbers and checkboxes are not detected |
| PDF highlight pages (`hls__*`) | Imported as ordinary pages; the annotation linkage is lost |
| Query Datalog rewriting | Queries are converted structurally, but file-graph Datalog often uses attributes that do not exist in DB graphs, such as `:block/properties`; each converted query is listed in the report for you to verify and rewrite |
| Drawers in page preamble | A query or logbook drawer placed before the first bullet of a file is kept as plain text |

## Partially supported

Verify these after import.

- `SCHEDULED:` and `DEADLINE:` on tasks: the date and optional time carry over, but times are read as UTC, and org-mode repeaters such as `.+1w` are dropped.
- Linked-references panels: `((uuid))` block references resolve when opened, but the importer does not create reference index entries for them; the app rebuilds these as blocks are touched.
- Node embeds whose target imports later in the run: these are linked in a second pass and listed in the report's NOTES section.

## Safety

The importer only ever creates a new graph and aborts if the name is taken. Even so, import into a throwaway graph first and open it in Logseq before you import for your real workflow.
