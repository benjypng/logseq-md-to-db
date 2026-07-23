#!/usr/bin/env python3
"""Migrate pages tagged #OldJournal into their corresponding journal pages.

For each page tagged #OldJournal:
  1. Parse the date from the page title (e.g. "Oct 19th, 2024").
  2. Find the journal page for that date. Create it if it does not exist.
  3. Append the page's blocks to the journal, preserving hierarchy.

Usage:
  python3 migrate_old_journals.py --graph <name> --dry-run
  python3 migrate_old_journals.py --graph <name> --execute

Exactly one of --dry-run or --execute is required. Dry run prints every
write command without running it. --execute creates a
graph backup first. Source pages are never modified; remove them yourself
after verifying the journals.

One caution: journals are always targeted by UUID, not title, because the
source pages share their titles with the journal pages. Appending by title
would be ambiguous.

Verify these flags against your CLI version before the first --execute
(flag names drift between releases; see `logseq example` too):
  - `logseq upsert page --help`  -> create_journal() assumes
      --title <t> --journal-day <yyyymmdd>
  - `logseq upsert block --help` -> append_block() assumes
      --content <text> with --page <uuid> or --parent <uuid>,
      and that the JSON output contains the new block's uuid.
"""

import argparse
import datetime
import json
import re
import subprocess


class LogseqCLI:
    def __init__(self, graph, dry_run=True):
        self.graph = graph
        self.dry_run = dry_run

    def _run(self, args, *, write, parse_json=True):
        cmd = ["logseq", *args, "--graph", self.graph, "--output", "json"]
        if write and self.dry_run:
            print(f"[dry-run] {' '.join(cmd)}")
            return None
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"logseq failed: {' '.join(cmd)}\n{r.stderr.strip()}")
        out = r.stdout.strip()
        return json.loads(out) if parse_json and out else out

    def query(self, edn, inputs=None):
        payload = "{:query " + edn
        if inputs is not None:
            payload += " :inputs " + json.dumps(inputs)
        payload += "}"
        return self._run(["query", payload], write=False)

    def backup(self, label):
        self._run(["graph", "backup", "create", "--name", label],
                  write=True, parse_json=False)


def _get(d, *keys):
    for k in keys:
        if isinstance(d, dict) and k in d:
            return d[k]
    return None


def _extract_uuid(result):
    if isinstance(result, dict):
        for k in ("uuid", "block/uuid", ":block/uuid"):
            if k in result:
                return result[k]
        for v in result.values():
            if (u := _extract_uuid(v)):
                return u
    elif isinstance(result, list):
        for item in result:
            if (u := _extract_uuid(item)):
                return u
    return None


# --- date parsing -----------------------------------------------------------

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})


def parse_journal_date(title):
    """Parse "Oct 19th, 2024" and common variants. None if unparseable."""
    m = re.fullmatch(r"([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})",
                     title.strip(), re.IGNORECASE)
    if m and m.group(1).lower() in _MONTHS:
        try:
            return datetime.date(int(m.group(3)),
                                 _MONTHS[m.group(1).lower()], int(m.group(2)))
        except ValueError:
            return None
    return None


# --- graph operations -------------------------------------------------------

def find_old_journal_pages(cli):
    edn = ("[:find (pull ?p [:block/uuid :block/title :block/name]) "
           ':where [?p :block/tags ?t] [?t :block/title "OldJournal"] '
           "[?p :block/name ?n]]")
    return [r[0] if isinstance(r, list) else r for r in (cli.query(edn) or [])]


def find_journal(cli, date):
    """Look up the journal page for a date; return its entity or None.

    Tries :block/journal-day as a yyyyMMdd integer first, then as a
    millisecond UTC timestamp, so it works whichever convention the DB
    version uses.
    """
    yyyymmdd = date.year * 10000 + date.month * 100 + date.day
    ms = int(datetime.datetime(date.year, date.month, date.day,
                               tzinfo=datetime.timezone.utc).timestamp() * 1000)
    edn = ("[:find (pull ?j [:block/uuid :block/title]) "
           ":in $ ?d :where [?j :block/journal-day ?d]]")
    for val in (yyyymmdd, ms):
        rows = cli.query(edn, inputs=[val]) or []
        if rows:
            return rows[0][0] if isinstance(rows[0], list) else rows[0]
    return None


def create_journal(cli, title, date):
    yyyymmdd = date.year * 10000 + date.month * 100 + date.day
    cli._run(["upsert", "page", "--title", title,
              "--journal-day", str(yyyymmdd)], write=True)


def fetch_page_blocks(cli, page_name):
    """Return the page's blocks as an ordered tree of {content, children}."""
    edn = ("[:find (pull ?b [:db/id :block/title :block/order "
           "{:block/parent [:db/id]} {:block/page [:db/id]}]) "
           ":in $ ?n :where [?b :block/page ?p] [?p :block/name ?n]]")
    rows = cli.query(edn, inputs=[page_name]) or []
    blocks = [r[0] if isinstance(r, list) else r for r in rows]

    by_id, children, page_ids = {}, {}, set()
    for b in blocks:
        bid = _get(b, "db/id", ":db/id")
        by_id[bid] = b
        pid = _get(_get(b, "block/parent", ":block/parent") or {}, "db/id", ":db/id")
        children.setdefault(pid, []).append(bid)
        pg = _get(_get(b, "block/page", ":block/page") or {}, "db/id", ":db/id")
        if pg is not None:
            page_ids.add(pg)

    def build(parent_id):
        kids = sorted(children.get(parent_id, []),
                      key=lambda i: _get(by_id[i], "block/order", ":block/order") or "")
        return [{"content": _get(by_id[i], "block/title", ":block/title") or "",
                 "children": build(i)} for i in kids]

    return [n for pid in page_ids for n in build(pid)]


def append_block(cli, content, *, page_uuid=None, parent_uuid=None):
    args = ["upsert", "block", "--content", content]
    args += ["--parent", parent_uuid] if parent_uuid else ["--page", page_uuid]
    result = cli._run(args, write=True)
    if result is None:
        return None
    uuid = _extract_uuid(result)
    if uuid is None:
        raise RuntimeError("No uuid in upsert output; adjust _extract_uuid(). "
                           f"Got: {json.dumps(result)[:300]}")
    return uuid


def append_tree(cli, nodes, *, page_uuid=None, parent_uuid=None):
    for node in nodes:
        new_uuid = append_block(cli, node["content"], page_uuid=page_uuid,
                                parent_uuid=parent_uuid)
        if node["children"]:
            if new_uuid is None:  # dry run
                print(f"[dry-run]   ({len(node['children'])} child block(s) "
                      "nested under the block above)")
                append_tree(cli, node["children"], page_uuid=page_uuid)
            else:
                append_tree(cli, node["children"], parent_uuid=new_uuid)


# --- main -------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="Print write commands without running them.")
    mode.add_argument("--execute", action="store_true",
                      help="Perform writes.")
    args = ap.parse_args()

    cli = LogseqCLI(args.graph, dry_run=args.dry_run)

    pages = find_old_journal_pages(cli)
    print(f"Processing {len(pages)} page(s) tagged #OldJournal.\n")
    if not pages:
        return

    if args.execute:
        label = "oldjournal-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        print(f"Creating backup '{label}' ...")
        cli.backup(label)

    migrated = skipped = 0
    for page in pages:
        title = _get(page, "block/title", ":block/title") or ""
        name = _get(page, "block/name", ":block/name") or ""
        src_uuid = _get(page, "block/uuid", ":block/uuid")

        date = parse_journal_date(title)
        if date is None:
            print(f"SKIP  '{title}': no date in title.")
            skipped += 1
            continue

        blocks = fetch_page_blocks(cli, name)
        if not blocks:
            print(f"SKIP  '{title}': page has no blocks.")
            skipped += 1
            continue

        journal = find_journal(cli, date)
        if journal is None:
            print(f"PAGE  '{title}': creating journal, then appending "
                  f"{len(blocks)} block(s).")
            create_journal(cli, title, date)
            if not cli.dry_run:
                journal = find_journal(cli, date)
                if journal is None:
                    print("      ERROR: journal not found after creation. "
                          "Check `logseq upsert page --help` flags. Skipping.")
                    skipped += 1
                    continue
        else:
            print(f"PAGE  '{title}': appending {len(blocks)} block(s) to "
                  "existing journal.")

        j_uuid = _get(journal or {}, "block/uuid", ":block/uuid")
        if j_uuid == src_uuid:
            print("      ERROR: journal lookup returned the source page "
                  "itself. Skipping.")
            skipped += 1
            continue

        append_tree(cli, blocks, page_uuid=j_uuid or "<new-journal-uuid>")
        migrated += 1

    print(f"\nDone. {migrated} migrated, {skipped} skipped."
          + (" (dry run; use --execute to write)" if cli.dry_run else ""))


if __name__ == "__main__":
    main()
