#!/usr/bin/env python3
"""Migrate pages tagged #OldJournal into their corresponding journal pages.

For each page tagged #OldJournal:
  1. Parse the date from the page title (e.g. "Oct 19th, 2024").
  2. Upsert the journal page named with the ISO date (e.g. "2024-10-19").
     This creates the journal if it does not exist and is a no-op if it does.
  3. Append the page's blocks to the journal as one nested EDN tree via
     `upsert block --target-page <iso-date> --blocks-file <file.edn>`.

Usage:
  python3 migrate_old_journals.py --graph <name> --dry-run
  python3 migrate_old_journals.py --graph <name> --execute

Dry run prints every write command (and each page's block EDN) without
running anything. --execute creates a graph backup first. Source pages are
never modified; remove them yourself after verifying the journals.
CLI conventions follow https://github.com/benjypng/logseq-md-to-db.
"""

import argparse
import datetime
import json
import re
import subprocess
import sys
import tempfile
import uuid as uuidlib
from pathlib import Path


class CliError(Exception):
    pass


class LogseqCli:
    def __init__(self, graph, dry_run, timeout_ms=120000):
        self.graph = graph
        self.dry_run = dry_run
        self.timeout_ms = timeout_ms

    def run(self, *args, write=False, timeout_ms=None):
        ms = timeout_ms or self.timeout_ms
        cmd = ["logseq", *args, "--graph", self.graph,
               "--timeout-ms", str(ms), "--output", "json"]
        if write and self.dry_run:
            print(f"[dry-run] {' '.join(cmd)}")
            return None
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=ms / 1000 + 30)
        except subprocess.TimeoutExpired:
            raise CliError(f"process timeout after {ms}ms: {' '.join(cmd)}")
        raw = (r.stdout or "").strip()
        if r.returncode != 0:
            raise CliError(raw or (r.stderr or "").strip()
                           or f"exit code {r.returncode}")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise CliError(f"unparseable CLI output: {raw[:500]}")
        if payload.get("status") != "ok":
            raise CliError(json.dumps(payload.get("error", payload)))
        return payload.get("data", {})

    def query(self, edn, inputs=None):
        args = ["query", "--query", edn]
        if inputs is not None:
            args += ["--inputs", json.dumps(inputs)]
        data = self.run(*args)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("result") or data.get("rows") or []
        return []


# --- date parsing -----------------------------------------------------------

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})


def parse_journal_date(title):
    """Parse "Oct 19th, 2024" and close variants. None if unparseable."""
    m = re.fullmatch(r"([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})",
                     title.strip(), re.IGNORECASE)
    if m and m.group(1).lower() in _MONTHS:
        try:
            return datetime.date(int(m.group(3)),
                                 _MONTHS[m.group(1).lower()], int(m.group(2)))
        except ValueError:
            return None
    return None


# --- graph reads ------------------------------------------------------------

def _get(d, *keys):
    for k in keys:
        if isinstance(d, dict) and k in d:
            return d[k]
    return None


def _rows(result):
    return [r[0] if isinstance(r, list) else r for r in (result or [])]


def find_old_journal_pages(cli):
    edn = ("[:find (pull ?p [:block/uuid :block/title :block/name]) "
           ':where [?p :block/tags ?t] [?t :block/title "OldJournal"] '
           "[?p :block/name ?n]]")
    return _rows(cli.query(edn))


def fetch_page_blocks(cli, page_name):
    """Return the page's blocks as an ordered tree of {content, children}.

    Siblings sort lexicographically on :block/order (fractional indexing).
    """
    edn = ("[:find (pull ?b [:db/id :block/title :block/order "
           "{:block/parent [:db/id]} {:block/page [:db/id]}]) "
           ":in $ ?n :where [?b :block/page ?p] [?p :block/name ?n]]")
    blocks = _rows(cli.query(edn, inputs=[page_name]))

    by_id, children, page_ids = {}, {}, set()
    for b in blocks:
        bid = _get(b, "db/id", ":db/id")
        by_id[bid] = b
        pid = _get(_get(b, "block/parent", ":block/parent") or {},
                   "db/id", ":db/id")
        children.setdefault(pid, []).append(bid)
        pg = _get(_get(b, "block/page", ":block/page") or {},
                  "db/id", ":db/id")
        if pg is not None:
            page_ids.add(pg)

    def build(parent_id):
        kids = sorted(children.get(parent_id, []),
                      key=lambda i: _get(by_id[i], "block/order",
                                         ":block/order") or "")
        return [{"content": _get(by_id[i], "block/title",
                                 ":block/title") or "",
                 "children": build(i)} for i in kids]

    return [n for pid in page_ids for n in build(pid)]


# --- EDN construction -------------------------------------------------------

def edn_str(s):
    return '"' + (s.replace("\\", "\\\\").replace('"', '\\"')
                   .replace("\n", "\\n").replace("\r", "")
                   .replace("\t", "\\t")) + '"'


def block_edn(node):
    parts = [f":block/title {edn_str(node['content'])}",
             f':block/uuid #uuid "{uuidlib.uuid4()}"']
    if node["children"]:
        parts.append(":block/children ["
                     + " ".join(block_edn(c) for c in node["children"]) + "]")
    return "{" + " ".join(parts) + "}"


def blocks_edn(nodes):
    return "[" + " ".join(block_edn(n) for n in nodes) + "]"


# --- migration --------------------------------------------------------------

def migrate_page(cli, title, page_name, iso, blocks):
    # Upsert the journal page: creates it if missing, no-op if it exists.
    cli.run("upsert", "page", "--page", iso, write=True)

    edn = blocks_edn(blocks)
    if cli.dry_run:
        print(f"[dry-run] logseq upsert block --target-page {iso} "
              f"--blocks-file <tmp.edn> --graph {cli.graph}")
        print(f"[dry-run]   blocks EDN: {edn[:200]}"
              + ("..." if len(edn) > 200 else ""))
        return
    with tempfile.NamedTemporaryFile("w", suffix=".edn", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(edn)
        tmp = fh.name
    try:
        cli.run("upsert", "block", "--target-page", iso,
                "--blocks-file", tmp, write=True, timeout_ms=300000)
    finally:
        Path(tmp).unlink()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="Print write commands without running them.")
    mode.add_argument("--execute", action="store_true",
                      help="Perform writes.")
    args = ap.parse_args()

    cli = LogseqCli(args.graph, dry_run=args.dry_run)

    try:
        pages = find_old_journal_pages(cli)
    except CliError as e:
        sys.stderr.write(f"Failed to list #OldJournal pages: {e}\n")
        return 1
    print(f"Processing {len(pages)} page(s) tagged #OldJournal.\n")
    if not pages:
        return 0

    if args.execute:
        label = ("oldjournal-"
                 + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        print(f"Creating backup '{label}' ...")
        cli.run("graph", "backup", "create", "--name", label, write=True)
        print()

    migrated = skipped = failed = 0
    for page in pages:
        title = _get(page, "block/title", ":block/title") or ""
        name = _get(page, "block/name", ":block/name") or ""

        date = parse_journal_date(title)
        if date is None:
            print(f"SKIP  '{title}': no date in title.")
            skipped += 1
            continue
        iso = date.isoformat()

        try:
            blocks = fetch_page_blocks(cli, name)
        except CliError as e:
            print(f"FAIL  '{title}': could not read blocks: {e}")
            failed += 1
            continue
        if not blocks:
            print(f"SKIP  '{title}': page has no blocks.")
            skipped += 1
            continue

        print(f"PAGE  '{title}' -> {iso} "
              f"({len(blocks)} top-level block(s))")
        try:
            migrate_page(cli, title, name, iso, blocks)
            migrated += 1
        except CliError as e:
            print(f"FAIL  '{title}': {e}")
            failed += 1

    print(f"\nDone. {migrated} migrated, {skipped} skipped, {failed} failed."
          + (" (dry run)" if cli.dry_run else ""))
    if not cli.dry_run:
        print("Source pages were left in place. Verify a few journals in "
              "the app before removing them.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
