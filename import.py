#!/usr/bin/env python3
"""Import this Logseq 1.0 file-based graph into a new Logseq DB graph using logseq-cli.

Place this script in the root of the graph (next to pages/ and journals/) and run:
    python3 import.py
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
import uuid as uuidlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"

STATUS_MAP = {
    "TODO": "todo",
    "LATER": "todo",
    "DOING": "doing",
    "NOW": "doing",
    "IN-PROGRESS": "doing",
    "DONE": "done",
    "CANCELED": "canceled",
    "CANCELLED": "canceled",
    "WAITING": "backlog",
}
PRIORITY_MAP = {"A": "high", "B": "medium", "C": "low"}
STATUS_IDENTS = {
    "todo": ":logseq.property/status.todo",
    "doing": ":logseq.property/status.doing",
    "done": ":logseq.property/status.done",
    "canceled": ":logseq.property/status.canceled",
    "backlog": ":logseq.property/status.backlog",
}
PRIORITY_IDENTS = {
    "high": ":logseq.property/priority.high",
    "medium": ":logseq.property/priority.medium",
    "low": ":logseq.property/priority.low",
}
TASK_CLASS_IDENT = ":logseq.class/Task"
QUERY_CLASS_IDENT = ":logseq.class/Query"

BULLET_RE = re.compile(r"^([\t ]*)-(?: (.*))?$")
PROP_RE = re.compile(r"^([A-Za-z0-9_.-]+):: ?(.*)$")
UUID_VALUE_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
BLOCK_REF_RE = re.compile(r"\(\(([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\)\)")
TASK_MARKER_RE = re.compile(r"^(TODO|DOING|DONE|LATER|NOW|WAITING|CANCELED|CANCELLED|IN-PROGRESS)(?:\s+|$)")
PRIORITY_MARKER_RE = re.compile(r"^\[#([ABC])\]\s*")
SCHEDULE_RE = re.compile(r"^(SCHEDULED|DEADLINE):\s*<(\d{4})-(\d{1,2})-(\d{1,2})(?:[^>\d]*(\d{1,2}):(\d{2}))?[^>]*>")
TAG_RE = re.compile(r"(^|[\s(])#(?:\[\[([^\]\n]+)\]\]|([A-Za-z0-9_][\w/-]*))")
ASSET_LINK_RE = re.compile(r"\(\.?\.?/?assets/[^)]+\)|\(\.?\.?/?draws/[^)]+\)")
EMBED_RE = re.compile(r"\{\{embed[\s(]")
NODE_EMBED_PAGE_RE = re.compile(r"\{\{embed\s+\[\[([^\]]+)\]\]\s*\}\}")
NODE_EMBED_BLOCK_RE = re.compile(r"\{\{embed\s+\(\(([0-9a-fA-F-]{36})\)\)\s*\}\}")
QUERY_RE = re.compile(r"\{\{query[\s(]")
JOURNAL_FILE_RE = re.compile(r"^(\d{4})_(\d{2})_(\d{2})\.md$")


@dataclass
class Options:
    convert_tags: bool = True
    import_properties: bool = True


@dataclass
class Issue:
    kind: str
    file: str
    line: int
    detail: str


@dataclass
class RawBlock:
    depth: int
    lines: list
    line_no: int


@dataclass
class Block:
    content: str = ""
    uuid: str = ""
    line: int = 0
    had_id: bool = False
    tags: list = field(default_factory=list)
    properties: dict = field(default_factory=dict)
    status: str = ""
    priority: str = ""
    scheduled: str = ""
    deadline: str = ""
    query: str = ""
    link_page: str = ""
    link_uuid: str = ""
    children: list = field(default_factory=list)


@dataclass
class Page:
    name: str
    file: str
    is_journal: bool = False
    tags: list = field(default_factory=list)
    properties: dict = field(default_factory=dict)
    blocks: list = field(default_factory=list)


@dataclass
class GraphModel:
    pages: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    counters: Counter = field(default_factory=Counter)
    uuids: dict = field(default_factory=dict)
    block_refs: list = field(default_factory=list)
    tag_names: dict = field(default_factory=dict)
    prop_names: dict = field(default_factory=dict)


def page_name_from_filename(filename):
    name = filename[:-3] if filename.endswith(".md") else filename
    name = urllib.parse.unquote(name)
    return name.replace("___", "/")


def journal_page_name(filename):
    m = JOURNAL_FILE_RE.match(filename)
    if not m:
        return None
    return "{}-{}-{}".format(m.group(1), m.group(2), m.group(3))


def indent_depth(indent):
    tabs = indent.count("\t")
    if tabs or not indent:
        return tabs
    return len(indent) // 2


def split_page_text(text):
    preamble = []
    raws = []
    for line_no, line in enumerate(text.splitlines(), 1):
        m = BULLET_RE.match(line)
        if m:
            raws.append(RawBlock(depth=indent_depth(m.group(1)), lines=[m.group(2) or ""], line_no=line_no))
        elif raws:
            raws[-1].lines.append(line)
        elif line.strip():
            preamble.append(line)
    return preamble, raws


def strip_continuation(line, depth):
    i = 0
    while i < len(line) and i < depth and line[i] == "\t":
        i += 1
    if i == 0:
        while i < len(line) and i < 2 * depth and line[i] == " ":
            i += 1
    line = line[i:]
    if line.startswith("  "):
        return line[2:]
    if line.startswith("\t"):
        return line[1:]
    return line


def build_tree(pairs):
    roots = []
    stack = []
    for depth, block in pairs:
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if stack:
            stack[-1][1].children.append(block)
        else:
            roots.append(block)
        stack.append((depth, block))
    return roots


@dataclass
class ParseContext:
    file: str
    opts: Options
    model: GraphModel


def parse_tags_value(value):
    tags = []
    for part in value.split(","):
        name = part.strip().strip("#")
        if name.startswith("[[") and name.endswith("]]"):
            name = name[2:-2].strip()
        if name:
            tags.append(name)
    return tags


def register_tag(name, model):
    key = name.lower()
    if key not in model.tag_names:
        model.tag_names[key] = name


def handle_property(key, value, holder, ctx, line_no, allow_id=True):
    lower = key.lower()
    if lower == "id" and allow_id:
        if UUID_VALUE_RE.match(value):
            uid = value.lower()
            if uid in ctx.model.uuids:
                ctx.model.issues.append(Issue("duplicate-uuid", ctx.file, line_no, uid))
                return None
            ctx.model.uuids[uid] = (ctx.file, line_no)
            return uid
        ctx.model.issues.append(Issue("invalid-id", ctx.file, line_no, value))
        return None
    if lower == "collapsed":
        ctx.model.counters["collapsed_dropped"] += 1
        return None
    if lower == "tags":
        for name in parse_tags_value(value):
            holder.tags.append(name)
            register_tag(name, ctx.model)
        return None
    if not value.strip():
        return None
    if ctx.opts.import_properties:
        holder.properties[key] = value
        if lower not in ctx.model.prop_names:
            ctx.model.prop_names[lower] = key
    else:
        ctx.model.counters["properties_dropped"] += 1
    return None


def iso_datetime(m):
    hour = int(m.group(5)) if m.group(5) else 0
    minute = int(m.group(6)) if m.group(6) else 0
    return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:00.000Z".format(
        int(m.group(2)), int(m.group(3)), int(m.group(4)), hour, minute
    )


def process_block(raw, ctx):
    block = Block(line=raw.line_no)
    first = raw.lines[0]
    tm = TASK_MARKER_RE.match(first)
    if tm:
        block.status = STATUS_MAP[tm.group(1)]
        first = first[tm.end():]
    pm = PRIORITY_MARKER_RE.match(first)
    if pm:
        block.priority = PRIORITY_MAP[pm.group(1)]
        first = first[pm.end():]
    content_lines = []
    in_logbook = False
    in_query = False
    query_lines = []
    lines = [first] + [strip_continuation(l, raw.depth) for l in raw.lines[1:]]
    for offset, line in enumerate(lines):
        stripped = line.strip()
        if stripped == ":LOGBOOK:":
            in_logbook = True
            ctx.model.counters["logbook_dropped"] += 1
            continue
        if in_logbook:
            if stripped == ":END:":
                in_logbook = False
            continue
        if in_query:
            if stripped.upper() == "#+END_QUERY":
                joined = "\n".join(query_lines).lstrip()
                if block.query:
                    ctx.model.issues.append(Issue(
                        "multiple-queries", ctx.file, raw.line_no,
                        "block has more than one #+BEGIN_QUERY drawer; only the first was converted",
                    ))
                else:
                    block.query = joined
                in_query = False
                query_lines = []
            else:
                query_lines.append(line)
            continue
        if stripped.upper() == "#+BEGIN_QUERY":
            in_query = True
            query_lines = []
            continue
        sm = SCHEDULE_RE.match(stripped)
        if sm:
            if sm.group(1) == "SCHEDULED":
                block.scheduled = iso_datetime(sm)
            else:
                block.deadline = iso_datetime(sm)
            continue
        prop = PROP_RE.match(line)
        if prop:
            uid = handle_property(prop.group(1), prop.group(2).strip(), block, ctx, raw.line_no + offset)
            if uid:
                block.uuid = uid
                block.had_id = True
            continue
        content_lines.append(line)
    if in_logbook:
        ctx.model.issues.append(Issue("unclosed-logbook", ctx.file, raw.line_no, "content after :LOGBOOK: was dropped"))
    if in_query:
        block.query = "\n".join(query_lines).lstrip()
        ctx.model.issues.append(Issue("unclosed-query", ctx.file, raw.line_no, "#+BEGIN_QUERY never closed with #+END_QUERY"))
    while content_lines and not content_lines[-1].strip():
        content_lines.pop()
    block.content = "\n".join(content_lines)
    if not block.uuid:
        block.uuid = str(uuidlib.uuid4())
    return block


def apply_inline_tags(content, ctx):
    found = []

    def replace(m):
        name = m.group(2) or m.group(3)
        found.append(name)
        if ctx.opts.convert_tags:
            return m.group(1) + "[[" + name + "]]"
        register_tag(name, ctx.model)
        return m.group(0)

    new_content = TAG_RE.sub(replace, content)
    if ctx.opts.convert_tags:
        return new_content, found
    return content, found


def scan_content_features(block, ctx):
    for uid in BLOCK_REF_RE.findall(block.content):
        ctx.model.block_refs.append((uid.lower(), ctx.file, block.line))
    for m in ASSET_LINK_RE.finditer(block.content):
        ctx.model.issues.append(Issue("asset-link", ctx.file, block.line, m.group(0)))
    if block.link_page or block.link_uuid:
        ctx.model.counters["node_embeds"] += 1
        ctx.model.issues.append(Issue("node-embed", ctx.file, block.line, block.content.strip()))
    else:
        for m in EMBED_RE.finditer(block.content):
            ctx.model.counters["embeds"] += 1
            ctx.model.issues.append(Issue("embed", ctx.file, block.line, m.group(0)))
    for m in QUERY_RE.finditer(block.content):
        ctx.model.counters["queries"] += 1
        ctx.model.issues.append(Issue("query", ctx.file, block.line, m.group(0)))
    if block.query:
        ctx.model.counters["advanced_queries"] += 1
        first_line = block.query.split("\n", 1)[0][:80]
        ctx.model.issues.append(Issue("advanced-query", ctx.file, block.line, first_line))


def block_is_empty(block):
    return (
        not block.content.strip()
        and not block.children
        and not block.tags
        and not block.properties
        and not block.status
        and not block.scheduled
        and not block.deadline
        and not block.query
    )


def finalize_block(block, ctx):
    new_content, found = apply_inline_tags(block.content, ctx)
    block.content = new_content
    if not ctx.opts.convert_tags:
        for name in found:
            if name not in block.tags:
                block.tags.append(name)
    stripped = block.content.strip()
    page_m = NODE_EMBED_PAGE_RE.fullmatch(stripped)
    if page_m:
        block.link_page = page_m.group(1).strip()
    else:
        block_m = NODE_EMBED_BLOCK_RE.fullmatch(stripped)
        if block_m:
            block.link_uuid = block_m.group(1).lower()
    scan_content_features(block, ctx)
    ctx.model.counters["blocks"] += 1
    kept_children = []
    for child in block.children:
        finalize_block(child, ctx)
        if block_is_empty(child):
            ctx.model.counters["blocks"] -= 1
        else:
            kept_children.append(child)
    block.children = kept_children
    if block.status:
        ctx.model.counters["tasks"] += 1


def parse_page_text(text, page_name, rel_file, is_journal, opts, model):
    ctx = ParseContext(file=rel_file, opts=opts, model=model)
    page = Page(name=page_name, file=rel_file, is_journal=is_journal)
    preamble, raws = split_page_text(text)
    leftover = []
    for line_no, line in enumerate(preamble, 1):
        prop = PROP_RE.match(line)
        if prop:
            handle_property(prop.group(1), prop.group(2).strip(), page, ctx, line_no, allow_id=False)
        else:
            leftover.append(line)
    for name in page.tags:
        register_tag(name, model)
    blocks = [process_block(r, ctx) for r in raws]
    if leftover:
        preamble_block = Block(content="\n".join(leftover), uuid=str(uuidlib.uuid4()), line=1)
        blocks.insert(0, preamble_block)
        raws.insert(0, RawBlock(depth=0, lines=[], line_no=1))
        model.counters["preamble_content_blocks"] += 1
    pairs = [(raws[i].depth, blocks[i]) for i in range(len(blocks))]
    roots = build_tree(pairs)
    kept = []
    for root in roots:
        finalize_block(root, ctx)
        if block_is_empty(root):
            model.counters["blocks"] -= 1
        else:
            kept.append(root)
    page.blocks = kept
    return page


def scan_graph(root, opts):
    model = GraphModel()
    for sub, is_journal in (("pages", False), ("journals", True)):
        directory = root / sub
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            rel = "{}/{}".format(sub, path.name)
            if is_journal:
                name = journal_page_name(path.name)
                if name is None:
                    model.issues.append(Issue("bad-journal-filename", rel, 0, path.name))
                    name = page_name_from_filename(path.name)
                    page_is_journal = False
                else:
                    page_is_journal = True
            else:
                name = page_name_from_filename(path.name)
                page_is_journal = False
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                model.issues.append(Issue("unreadable-file", rel, 0, str(exc)))
                continue
            page = parse_page_text(text, name, rel, page_is_journal, opts, model)
            model.pages.append(page)
            if name.startswith("hls__"):
                model.issues.append(Issue("hls-page", rel, 0, name))
    return model


def validate(model):
    for uid, file, line in model.block_refs:
        if uid not in model.uuids:
            model.issues.append(Issue("unresolved-ref", file, line, "(({}))".format(uid)))
    by_name = {}
    for page in model.pages:
        by_name.setdefault(page.name.casefold(), []).append(page)
    for name, pages in by_name.items():
        if len(pages) > 1:
            files = ", ".join(p.file for p in pages)
            model.issues.append(Issue("page-name-collision", pages[0].file, 0, files))


ISSUE_EXPLANATIONS = {
    "unresolved-ref": "block ref points at a uuid that exists nowhere in this graph; it will not resolve after import",
    "duplicate-uuid": "same id:: appears on more than one block; later ones get a fresh uuid",
    "invalid-id": "id:: value is not a valid uuid; a fresh uuid was generated",
    "page-name-collision": "multiple files map to the same page name; their content will merge into one page",
    "asset-link": "assets/ and draws/ are not imported; this link will be broken in the new graph",
    "bad-journal-filename": "journal filename is not YYYY_MM_DD.md; imported as a normal page instead",
    "hls-page": "PDF-highlight page; imported as an ordinary page",
    "unreadable-file": "file could not be read as UTF-8 and was skipped",
    "unclosed-logbook": ":LOGBOOK: drawer never closed with :END:; the rest of that block's lines were dropped",
    "embed": "inline {{embed}} macros are kept as literal text; only whole-block embeds are converted to node embeds",
    "query": "{{query ...}} uses file-graph query syntax and will likely need rewriting for the DB version",
    "advanced-query": "converted to a #Query block; file-graph Datalog often uses attributes that do not exist in DB graphs - verify the query still runs",
    "unclosed-query": "#+BEGIN_QUERY never closed with #+END_QUERY; the query was captured to the end of the block",
    "multiple-queries": "block has more than one #+BEGIN_QUERY drawer; only the first was converted",
    "node-embed": "converted to a DB node embed (block/link); verify the embedded node renders",
}


def write_report(model, path, mode, options_desc, failures=(), notes=()):
    lines = []
    lines.append("Logseq 1.0 import report ({})".format(mode))
    lines.append("Options: {}".format(options_desc))
    lines.append("")
    lines.append("Counts")
    lines.append("  pages: {}".format(sum(1 for p in model.pages if not p.is_journal)))
    lines.append("  journals: {}".format(sum(1 for p in model.pages if p.is_journal)))
    for key in ("blocks", "tasks", "embeds", "node_embeds", "queries", "advanced_queries", "logbook_dropped", "collapsed_dropped", "properties_dropped", "preamble_content_blocks"):
        lines.append("  {}: {}".format(key, model.counters[key]))
    lines.append("  distinct tags: {}".format(len(model.tag_names)))
    lines.append("  distinct properties: {}".format(len(model.prop_names)))
    lines.append("")
    if failures:
        lines.append("{} FAILURES during import".format(len(failures)))
        for context, message in failures:
            lines.append("  {}: {}".format(context, message))
        lines.append("")
    if notes:
        lines.append("{} NOTES".format(len(notes)))
        for context, message in notes:
            lines.append("  {}: {}".format(context, message))
        lines.append("")
    by_kind = {}
    for issue in model.issues:
        by_kind.setdefault(issue.kind, []).append(issue)
    lines.append("Issues: {} total".format(len(model.issues)))
    for kind in sorted(by_kind):
        issues = by_kind[kind]
        lines.append("")
        lines.append("[{}] {} occurrence(s)".format(kind, len(issues)))
        lines.append("  {}".format(ISSUE_EXPLANATIONS.get(kind, "")))
        for issue in issues:
            location = "{}:{}".format(issue.file, issue.line) if issue.line else issue.file
            lines.append("  {} {}".format(location, issue.detail))
    text = "\n".join(lines) + "\n"
    Path(path).write_text(text, encoding="utf-8")
    return text


def edn_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "").replace("\t", "\\t") + '"'


def props_edn(properties, prop_idents):
    parts = []
    for key, value in properties.items():
        ident = prop_idents.get(key.lower())
        if ident:
            parts.append("{} {}".format(ident, edn_str(value)))
    return "{" + " ".join(parts) + "}"


def resolve_link_target(block, links):
    if not links:
        return None
    if block.link_page:
        page_id = links.get("page_ids", {}).get(block.link_page.casefold())
        if page_id is not None:
            return str(page_id)
        return None
    if block.link_uuid:
        if block.link_uuid in links.get("imported_uuids", set()):
            return '[:block/uuid #uuid "{}"]'.format(block.link_uuid)
        return None
    return None


def block_edn(block, tag_ids, prop_idents, links=None, pending=None):
    link_target = resolve_link_target(block, links)
    if link_target is not None:
        parts = [
            ':block/title ""',
            ':block/uuid #uuid "{}"'.format(block.uuid),
            ":block/link {}".format(link_target),
        ]
        if block.children:
            parts.append(":block/children [{}]".format(" ".join(block_edn(c, tag_ids, prop_idents, links, pending) for c in block.children)))
        return "{" + " ".join(parts) + "}"
    if (block.link_page or block.link_uuid) and pending is not None:
        pending.append(block)
    parts = [
        ":block/title {}".format(edn_str(block.content)),
        ':block/uuid #uuid "{}"'.format(block.uuid),
    ]
    tag_refs = []
    for name in block.tags:
        tag_id = tag_ids.get(name.lower())
        if tag_id is not None:
            tag_refs.append(str(tag_id))
    if block.status:
        tag_refs.append(TASK_CLASS_IDENT)
        parts.append(":logseq.property/status {}".format(STATUS_IDENTS[block.status]))
    if block.priority:
        parts.append(":logseq.property/priority {}".format(PRIORITY_IDENTS[block.priority]))
    if block.query:
        tag_refs.append(QUERY_CLASS_IDENT)
        parts.append(":logseq.property/query {}".format(edn_str(block.query)))
    if tag_refs:
        parts.insert(2, ":block/tags [{}]".format(" ".join(tag_refs)))
    for key, value in block.properties.items():
        ident = prop_idents.get(key.lower())
        if ident:
            parts.append("{} {}".format(ident, edn_str(value)))
    if block.children:
        parts.append(":block/children [{}]".format(" ".join(block_edn(c, tag_ids, prop_idents, links, pending) for c in block.children)))
    return "{" + " ".join(parts) + "}"


def blocks_edn(blocks, tag_ids, prop_idents, links=None, pending=None):
    return "[" + " ".join(block_edn(b, tag_ids, prop_idents, links, pending) for b in blocks) + "]"


class CliError(Exception):
    def __init__(self, command, message):
        super().__init__(message)
        self.command = command
        self.message = message


class LogseqCli:
    def __init__(self, graph, binary="logseq", timeout_ms=120000):
        self.graph = graph
        self.binary = binary
        self.timeout_ms = timeout_ms

    def run(self, *args, timeout_ms=None):
        ms = timeout_ms or self.timeout_ms
        cmd = [self.binary] + list(args) + ["--graph", self.graph, "--timeout-ms", str(ms), "--output", "json"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=ms / 1000 + 30)
        except subprocess.TimeoutExpired:
            raise CliError(cmd, "process timeout after {}ms".format(ms))
        raw = (result.stdout or "").strip()
        if result.returncode != 0:
            raise CliError(cmd, raw or (result.stderr or "").strip() or "exit code {}".format(result.returncode))
        try:
            payload = json.loads(raw)
        except ValueError:
            raise CliError(cmd, "unparseable CLI output: {}".format(raw[:500]))
        if payload.get("status") != "ok":
            raise CliError(cmd, json.dumps(payload.get("error", payload)))
        return payload.get("data", {})


def print_error(context, message):
    sys.stderr.write("{}✗ {}: {}{}\n".format(RED, context, message, RESET))


def print_step(message):
    sys.stdout.write("{}\n".format(message))
    sys.stdout.flush()


class Importer:
    def __init__(self, model, graph_name, cli):
        self.model = model
        self.graph = graph_name
        self.cli = cli
        self.failures = []
        self.failed_pages = set()
        self.page_ids = {}
        self.imported_uuids = set()
        self.link_pending = []
        self.notes = []

    def attempt(self, context, fn):
        try:
            return fn()
        except CliError as exc:
            print_error(context, exc.message)
            self.failures.append((context, exc.message))
            return None

    def run(self):
        self.create_graph()
        print_step("== Creating {} tags".format(len(self.model.tag_names)))
        tag_ids = self.create_tags()
        print_step("== Creating {} properties".format(len(self.model.prop_names)))
        prop_idents = self.create_properties()
        print_step("== Importing {} pages".format(len(self.model.pages)))
        self.import_pages(tag_ids, prop_idents)
        self.task_followups()
        self.query_followups()
        self.link_followups()
        return self.failures

    def create_graph(self):
        try:
            data = self.cli.run("graph", "list")
        except CliError as exc:
            print_error("graph list", exc.message)
            sys.stderr.write("{}Aborting: could not check existing graphs before creating '{}'{}\n".format(RED, self.graph, RESET))
            raise SystemExit(2)
        if self.graph in (data or {}).get("graphs", []):
            sys.stderr.write("{}Aborting: graph '{}' already exists. This script never writes into an existing graph.{}\n".format(RED, self.graph, RESET))
            raise SystemExit(2)
        try:
            self.cli.run("graph", "create")
            print_step("== Created graph {}".format(self.graph))
        except CliError as exc:
            print_error("graph create", exc.message)
            sys.stderr.write("{}Aborting: could not create graph '{}' (does it already exist?){}\n".format(RED, self.graph, RESET))
            raise SystemExit(2)

    def create_tags(self):
        tag_ids = {}
        for lower, display in sorted(self.model.tag_names.items()):
            data = self.attempt("tag {}".format(display), lambda d=display: self.cli.run("upsert", "tag", "--name", d))
            if data and data.get("result"):
                tag_ids[lower] = data["result"][0]
        return tag_ids

    def create_properties(self):
        for lower, display in sorted(self.model.prop_names.items()):
            self.attempt(
                "property {}".format(display),
                lambda d=display: self.cli.run("upsert", "property", "--name", d, "--type", "default", "--cardinality", "one"),
            )
        prop_idents = {}
        data = self.attempt("list property", lambda: self.cli.run("list", "property", "--with-type"))
        for item in (data or {}).get("items", []):
            title = str(item.get("block/title", "")).lower()
            ident = item.get("db/ident", "")
            if title in self.model.prop_names and ident.startswith("user.property/"):
                prop_idents[title] = ":" + ident
        missing = set(self.model.prop_names) - set(prop_idents)
        for name in sorted(missing):
            print_error("property {}".format(name), "no ident resolved; its values will be skipped")
            self.failures.append(("property {}".format(name), "no ident resolved"))
        return prop_idents

    def import_pages(self, tag_ids, prop_idents):
        total = len(self.model.pages)
        for index, page in enumerate(self.model.pages, 1):
            print_step("[{}/{}] {}".format(index, total, page.file))
            self.upsert_page_record(page, tag_ids, prop_idents)
        for page in self.model.pages:
            if page.file in self.failed_pages:
                continue
            self.upsert_page_blocks(page, tag_ids, prop_idents)

    def upsert_page_record(self, page, tag_ids, prop_idents):
        args = ["upsert", "page", "--page", page.name]
        kept = [t for t in page.tags if t.lower() in tag_ids]
        if kept:
            args += ["--update-tags", "[{}]".format(" ".join(edn_str(t) for t in kept))]
        if page.properties:
            edn_map = props_edn(page.properties, prop_idents)
            if edn_map != "{}":
                args += ["--update-properties", edn_map]
        data = self.attempt(page.file, lambda: self.cli.run(*args))
        if data is None:
            self.failed_pages.add(page.file)
            return
        result = (data or {}).get("result")
        page_id = result[0] if result else None
        if page_id is not None:
            self.page_ids[page.name.casefold()] = page_id

    def upsert_page_blocks(self, page, tag_ids, prop_idents):
        if not page.blocks:
            return
        links = {"page_ids": self.page_ids, "imported_uuids": self.imported_uuids}
        pending = []
        edn = blocks_edn(page.blocks, tag_ids, prop_idents, links, pending)
        with tempfile.NamedTemporaryFile("w", suffix=".edn", delete=False, encoding="utf-8") as handle:
            handle.write(edn)
            temp_path = handle.name
        try:
            result = self.attempt(
                page.file,
                lambda: self.cli.run("upsert", "block", "--target-page", page.name, "--blocks-file", temp_path, timeout_ms=300000),
            )
            if result is None:
                self.failed_pages.add(page.file)
        finally:
            Path(temp_path).unlink()
        if page.file in self.failed_pages:
            return
        self.collect_uuids(page.blocks)
        self.link_pending.extend(pending)

    def collect_uuids(self, blocks):
        for block in blocks:
            self.imported_uuids.add(block.uuid)
            self.collect_uuids(block.children)

    def task_followups(self):
        followups = []

        def walk(block):
            if block.scheduled or block.deadline:
                followups.append(block)
            for child in block.children:
                walk(child)

        for page in self.model.pages:
            if page.file in self.failed_pages:
                continue
            for block in page.blocks:
                walk(block)
        if not followups:
            return
        print_step("== Applying {} scheduled/deadline follow-ups".format(len(followups)))
        for block in followups:
            args = ["upsert", "task", "--uuid", block.uuid]
            if block.scheduled:
                args += ["--scheduled", block.scheduled]
            if block.deadline:
                args += ["--deadline", block.deadline]
            self.attempt("task followup {}".format(block.uuid), lambda a=args: self.cli.run(*a))

    def query_followups(self):
        followups = []

        def walk(block):
            if block.query:
                followups.append(block)
            for child in block.children:
                walk(child)

        for page in self.model.pages:
            if page.file in self.failed_pages:
                continue
            for block in page.blocks:
                walk(block)
        if not followups:
            return
        print_step("== Applying {} query follow-ups".format(len(followups)))
        for block in followups:
            context = "query {}".format(block.uuid)
            data = self.attempt(context, lambda b=block: self.cli.run("debug", "pull", "--uuid", b.uuid))
            if data is None:
                continue
            entity = (data or {}).get("entity", {}) or {}
            query_ref = entity.get("logseq.property/query")
            value_id = query_ref.get("db/id") if isinstance(query_ref, dict) else None
            if value_id is None:
                print_error(context, "could not resolve query property value entity id")
                self.failures.append((context, "could not resolve query property value entity id"))
                continue
            self.attempt(
                context,
                lambda vid=value_id: self.cli.run(
                    "upsert", "block", "--id", str(vid),
                    "--update-properties", '{:logseq.property.node/display-type :code :logseq.property.code/lang "clojure"}',
                ),
            )
            self.attempt(
                context,
                lambda vid=value_id: self.cli.run("upsert", "block", "--id", str(vid), "--update-tags", '["Code"]'),
            )

    def link_followups(self):
        if not self.link_pending:
            return
        print_step("== Applying {} embed link follow-ups".format(len(self.link_pending)))
        for block in self.link_pending:
            context = "embed {}".format(block.uuid)
            target_id = self.resolve_link_followup_target(block, context)
            if target_id is None:
                continue
            result = self.attempt(
                context,
                lambda b=block, tid=target_id: self.cli.run(
                    "upsert", "block", "--uuid", b.uuid,
                    "--update-properties", "{{:block/link {}}}".format(tid),
                ),
            )
            if result is None:
                continue
            self.notes.append((context, "linked via fallback; verify the block renders correctly"))

    def resolve_link_followup_target(self, block, context):
        if block.link_page:
            key = block.link_page.casefold()
            target_id = self.page_ids.get(key)
            if target_id is not None:
                return target_id
            data = self.attempt(context, lambda p=block.link_page: self.cli.run("upsert", "page", "--page", p))
            if data is None:
                return None
            result = (data or {}).get("result")
            target_id = result[0] if result else None
            if target_id is not None:
                self.page_ids[key] = target_id
            else:
                print_error(context, "embed target page could not be resolved")
                self.failures.append((context, "embed target page could not be resolved"))
            return target_id
        if block.link_uuid:
            data = self.attempt(context, lambda u=block.link_uuid: self.cli.run("debug", "pull", "--uuid", u))
            if data is None:
                return None
            entity = (data or {}).get("entity", {}) or {}
            target_id = entity.get("db/id")
            if target_id is None:
                print_error(context, "embed target not found")
                self.failures.append((context, "embed target not found"))
            return target_id
        return None


def script_root():
    return Path(__file__).resolve().parent


def ask_yes_no(question):
    while True:
        answer = input("{} [y/n] ".format(question)).strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def resolve_bool(flag_value, question):
    if flag_value is not None:
        return flag_value
    return ask_yes_no(question)


def check_graph_root(root):
    has_content = (root / "pages").is_dir() or (root / "journals").is_dir()
    has_config = (root / "logseq" / "config.edn").is_file()
    return has_content and has_config


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import this Logseq 1.0 graph into a new Logseq DB graph via logseq-cli.")
    parser.add_argument("--convert-tags", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--import-properties", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--graph", default=None)
    args = parser.parse_args(argv)
    root = script_root()
    if not check_graph_root(root):
        sys.stderr.write("{}This directory does not look like a Logseq 1.0 graph (need pages/ or journals/, plus logseq/config.edn).{}\n".format(RED, RESET))
        return 2
    convert_tags = resolve_bool(args.convert_tags, "Convert #tags to [[page references]]? (otherwise they become DB tags)")
    import_properties = resolve_bool(args.import_properties, "Import prop:: value properties?")
    dry_run = resolve_bool(args.dry_run, "Dry run? (scan and report only, no import)")
    opts = Options(convert_tags=convert_tags, import_properties=import_properties)
    options_desc = "convert_tags={} import_properties={}".format(convert_tags, import_properties)
    print_step("== Scanning graph at {}".format(root))
    model = scan_graph(root, opts)
    validate(model)
    print_step("== Parsed {} pages, {} blocks, {} tasks, {} issues".format(
        len(model.pages), model.counters["blocks"], model.counters["tasks"], len(model.issues)))
    report_path = root / "import-report.txt"
    if dry_run:
        write_report(model, report_path, "dry-run", options_desc)
        print_step("== Dry run complete. Report written to {}".format(report_path))
        print_step("== Review the issues, fix what matters in the markdown, then re-run without dry run.")
        return 0
    from shutil import which
    if which("logseq") is None:
        sys.stderr.write("{}logseq CLI not found on PATH.{}\n".format(RED, RESET))
        return 2
    graph_name = args.graph or input("Name for the new DB graph: ").strip()
    if not graph_name:
        sys.stderr.write("{}A graph name is required.{}\n".format(RED, RESET))
        return 2
    cli = LogseqCli(graph_name)
    importer = Importer(model, graph_name, cli)
    failures = importer.run()
    write_report(model, report_path, "import", options_desc, failures=failures, notes=importer.notes)
    if failures:
        sys.stderr.write("{}Import finished with {} failure(s). See {}{}\n".format(YELLOW, len(failures), report_path, RESET))
        return 1
    print_step("{}== Import complete with no failures. Report written to {}{}".format(GREEN, report_path, RESET))
    return 0


if __name__ == "__main__":
    sys.exit(main())
