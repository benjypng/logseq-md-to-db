import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "lsimport", Path(__file__).parent / "import.py"
)
ls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ls)


def test_page_name_decodes_url_escapes():
    assert ls.page_name_from_filename("What would you take from us%3F.md") == "What would you take from us?"
    assert ls.page_name_from_filename("%22%22.md") == '""'
    assert ls.page_name_from_filename("plain page.md") == "plain page"


def test_page_name_decodes_namespace_separator():
    assert ls.page_name_from_filename("parent___child.md") == "parent/child"


def test_journal_page_name():
    assert ls.journal_page_name("2021_03_09.md") == "2021-03-09"
    assert ls.journal_page_name("1872_10_04.md") == "1872-10-04"
    assert ls.journal_page_name("notes.md") is None


def test_split_page_text_preamble_and_blocks():
    text = "retrieved:: [[04-11-2022]]\nauthor:: [[X]]\n\n- first\n\t- child\n\t  cont:: 1\n- second\n"
    preamble, raws = ls.split_page_text(text)
    assert preamble == ["retrieved:: [[04-11-2022]]", "author:: [[X]]"]
    assert [(r.depth, r.lines[0], r.line_no) for r in raws] == [
        (0, "first", 4),
        (1, "child", 5),
        (0, "second", 7),
    ]
    assert raws[1].lines == ["child", "\t  cont:: 1"]


def test_indent_depth_tabs_and_spaces():
    assert ls.indent_depth("\t\t") == 2
    assert ls.indent_depth("") == 0
    assert ls.indent_depth("    ") == 2
    assert ls.indent_depth("  ") == 1


def test_strip_continuation():
    assert ls.strip_continuation("\t  on:: [[x]]", 1) == "on:: [[x]]"
    assert ls.strip_continuation("\t\t  text", 2) == "text"
    assert ls.strip_continuation("  plain", 0) == "plain"
    assert ls.strip_continuation("\t\t    indented code", 2) == "  indented code"


def test_strip_continuation_space_indented_graph():
    assert ls.strip_continuation("    b cont", 1) == "b cont"
    assert ls.strip_continuation("      deep", 2) == "deep"


def test_build_tree_nesting_and_depth_jump():
    b = [ls.Block(content=str(i)) for i in range(5)]
    pairs = [(0, b[0]), (1, b[1]), (3, b[2]), (1, b[3]), (0, b[4])]
    roots = ls.build_tree(pairs)
    assert [r.content for r in roots] == ["0", "4"]
    assert [c.content for c in b[0].children] == ["1", "3"]
    assert [c.content for c in b[1].children] == ["2"]


def make_ctx(convert_tags=True, import_properties=True):
    return ls.ParseContext(
        file="pages/t.md",
        opts=ls.Options(convert_tags=convert_tags, import_properties=import_properties),
        model=ls.GraphModel(),
    )


def test_process_block_task_marker_and_priority():
    ctx = make_ctx()
    raw = ls.RawBlock(depth=0, lines=["TODO [#A] ship the release"], line_no=1)
    b = ls.process_block(raw, ctx)
    assert b.content == "ship the release"
    assert b.status == "todo"
    assert b.priority == "high"


def test_process_block_id_consumed_as_uuid():
    ctx = make_ctx()
    raw = ls.RawBlock(depth=0, lines=["Next Actions", "  id:: 63d3ac1e-4690-422f-ae0d-85a0d7e07761"], line_no=1)
    b = ls.process_block(raw, ctx)
    assert b.uuid == "63d3ac1e-4690-422f-ae0d-85a0d7e07761"
    assert b.had_id is True
    assert b.content == "Next Actions"
    assert "63d3ac1e-4690-422f-ae0d-85a0d7e07761" in ctx.model.uuids


def test_process_block_generates_uuid_when_no_id():
    ctx = make_ctx()
    b = ls.process_block(ls.RawBlock(depth=0, lines=["hello"], line_no=1), ctx)
    assert len(b.uuid) == 36
    assert b.had_id is False


def test_process_block_properties_toggle():
    lines = ["location:: [114](kindle://x)", "  on:: [[30-09-2020]]", "  tags:: ", "  The first is ignorance."]
    b = ls.process_block(ls.RawBlock(depth=1, lines=lines, line_no=1), make_ctx())
    assert b.properties == {"location": "[114](kindle://x)", "on": "[[30-09-2020]]"}
    assert b.content == "The first is ignorance."
    ctx2 = make_ctx(import_properties=False)
    b2 = ls.process_block(ls.RawBlock(depth=1, lines=lines, line_no=1), ctx2)
    assert b2.properties == {}
    assert ctx2.model.counters["properties_dropped"] == 2


def test_process_block_tags_property_always_becomes_tags():
    ctx = make_ctx(import_properties=False)
    raw = ls.RawBlock(depth=0, lines=["some text", "  tags:: [[teaching-resources]], delegated"], line_no=1)
    b = ls.process_block(raw, ctx)
    assert b.tags == ["teaching-resources", "delegated"]


def test_process_block_drops_logbook_and_collapsed():
    lines = ["DONE task", "  collapsed:: true", "  :LOGBOOK:", "  CLOCK: [2023-01-01]", "  :END:", "  trailing note"]
    ctx = make_ctx()
    b = ls.process_block(ls.RawBlock(depth=0, lines=lines, line_no=1), ctx)
    assert b.content == "task\ntrailing note"
    assert b.status == "done"
    assert "collapsed" not in b.properties
    assert ctx.model.counters["logbook_dropped"] == 1
    assert ctx.model.counters["collapsed_dropped"] == 1


def test_process_block_scheduled_and_deadline():
    lines = ["TODO review", "  SCHEDULED: <2023-8-24 Thu>", "  DEADLINE: <2023-08-30 Wed 14:30>"]
    b = ls.process_block(ls.RawBlock(depth=0, lines=lines, line_no=1), make_ctx())
    assert b.scheduled == "2023-08-24T00:00:00.000Z"
    assert b.deadline == "2023-08-30T14:30:00.000Z"
    assert "SCHEDULED" not in b.content


def test_process_block_duplicate_uuid_reports_issue():
    ctx = make_ctx()
    lines = ["a", "  id:: 63d3ac1e-4690-422f-ae0d-85a0d7e07761"]
    ls.process_block(ls.RawBlock(depth=0, lines=lines, line_no=1), ctx)
    ls.process_block(ls.RawBlock(depth=0, lines=list(lines), line_no=5), ctx)
    assert any(i.kind == "duplicate-uuid" for i in ctx.model.issues)


def test_process_block_unclosed_logbook_reports_issue():
    lines = ["DONE task", "  :LOGBOOK:", "  CLOCK: [2023-01-01]", "  trailing note never closed"]
    ctx = make_ctx()
    b = ls.process_block(ls.RawBlock(depth=0, lines=lines, line_no=4), ctx)
    assert b.content == "task"
    assert any(i.kind == "unclosed-logbook" and i.line == 4 for i in ctx.model.issues)


def test_process_block_query_drawer_captured_with_surrounding_content():
    lines = [
        "before text",
        "  #+BEGIN_QUERY",
        "      {:title [:h3 \"Q\"]",
        "       :query [:find ?b]}",
        "  #+END_QUERY",
        "  after text",
    ]
    ctx = make_ctx()
    b = ls.process_block(ls.RawBlock(depth=0, lines=lines, line_no=1), ctx)
    assert b.content == "before text\nafter text"
    assert b.query == '{:title [:h3 "Q"]\n     :query [:find ?b]}'
    assert b.query.startswith("{")
    assert not any(i.kind in ("unclosed-query", "multiple-queries") for i in ctx.model.issues)


def test_process_block_query_drawer_lowercase_markers():
    lines = ["#+begin_query", "  {:title 1}", "#+end_query"]
    ctx = make_ctx()
    b = ls.process_block(ls.RawBlock(depth=0, lines=lines, line_no=1), ctx)
    assert b.query == "{:title 1}"
    assert b.content == ""


def test_process_block_query_drawer_unclosed_reports_issue():
    lines = ["text", "  #+BEGIN_QUERY", "    {:title [:h3 \"Q\"]}"]
    ctx = make_ctx()
    b = ls.process_block(ls.RawBlock(depth=0, lines=lines, line_no=9), ctx)
    assert b.content == "text"
    assert b.query == '{:title [:h3 "Q"]}'
    issues = [i for i in ctx.model.issues if i.kind == "unclosed-query"]
    assert len(issues) == 1
    assert issues[0].line == 9
    assert issues[0].file == "pages/t.md"


def test_process_block_query_drawer_second_drawer_discarded():
    lines = [
        "#+BEGIN_QUERY",
        "  {:title 1}",
        "#+END_QUERY",
        "#+BEGIN_QUERY",
        "  {:title 2}",
        "#+END_QUERY",
    ]
    ctx = make_ctx()
    b = ls.process_block(ls.RawBlock(depth=0, lines=lines, line_no=3), ctx)
    assert b.query == "{:title 1}"
    issues = [i for i in ctx.model.issues if i.kind == "multiple-queries"]
    assert len(issues) == 1


def test_block_is_empty_false_when_query_set():
    b = ls.Block(query="{:title 1}")
    assert ls.block_is_empty(b) is False


def test_finalize_block_whole_page_embed_sets_link_and_counts_node_embed():
    ctx = make_ctx()
    block = ls.Block(content="{{embed [[Target Page]]}}", line=3)
    ls.finalize_block(block, ctx)
    assert block.link_page == "Target Page"
    assert block.link_uuid == ""
    assert block.content == "{{embed [[Target Page]]}}"
    assert ctx.model.counters["node_embeds"] == 1
    assert ctx.model.counters["embeds"] == 0
    node_issues = [i for i in ctx.model.issues if i.kind == "node-embed"]
    assert len(node_issues) == 1
    assert node_issues[0].line == 3
    assert node_issues[0].file == "pages/t.md"
    assert not any(i.kind == "embed" for i in ctx.model.issues)


def test_finalize_block_whole_block_embed_sets_link_uuid():
    ctx = make_ctx()
    uid = "63D3AC1E-4690-422F-AE0D-85A0D7E07799"
    block = ls.Block(content="{{embed ((" + uid + "))}}", line=5)
    ls.finalize_block(block, ctx)
    assert block.link_uuid == uid.lower()
    assert block.link_page == ""
    assert ctx.model.counters["node_embeds"] == 1
    assert ctx.model.counters["embeds"] == 0


def test_finalize_block_whole_block_embed_surrounding_whitespace_still_matches():
    ctx = make_ctx()
    uid = "63d3ac1e-4690-422f-ae0d-85a0d7e07799"
    block = ls.Block(content="  {{embed ((" + uid + "))}}  ", line=6)
    ls.finalize_block(block, ctx)
    assert block.link_uuid == uid


def test_finalize_block_inline_embed_not_converted():
    ctx = make_ctx()
    block = ls.Block(content="intro {{embed [[Target]]}} outro", line=2)
    ls.finalize_block(block, ctx)
    assert block.link_page == ""
    assert block.link_uuid == ""
    assert ctx.model.counters["node_embeds"] == 0
    assert ctx.model.counters["embeds"] == 1
    assert any(i.kind == "embed" for i in ctx.model.issues)
    assert not any(i.kind == "node-embed" for i in ctx.model.issues)


def test_two_page_embeds_in_one_block_stay_literal():
    ctx = make_ctx()
    b = ls.Block(content="{{embed [[A]]}} {{embed [[B]]}}", uuid="11111111-1111-4111-8111-111111111111", line=1)
    ls.finalize_block(b, ctx)
    assert b.link_page == ""
    assert b.link_uuid == ""
    assert "{{embed [[A]]}}" in b.content


def test_finalize_block_page_embed_with_whitespace_in_brackets():
    ctx = make_ctx()
    b = ls.Block(content="{{embed [[ Target ]]}}", uuid="11111111-1111-4111-8111-111111111111", line=1)
    ls.finalize_block(b, ctx)
    assert b.link_page == "Target"
    assert b.link_uuid == ""


def test_scan_content_features_node_embed_page():
    ctx = make_ctx()
    b = ls.Block(content="{{embed [[Target]]}}", line=4)
    b.link_page = "Target"
    ls.scan_content_features(b, ctx)
    assert ctx.model.counters["node_embeds"] == 1
    assert ctx.model.counters["embeds"] == 0
    issues = [i for i in ctx.model.issues if i.kind == "node-embed"]
    assert len(issues) == 1
    assert issues[0].detail == "{{embed [[Target]]}}"
    assert not any(i.kind == "embed" for i in ctx.model.issues)


def test_scan_content_features_node_embed_block():
    ctx = make_ctx()
    uid = "63d3ac1e-4690-422f-ae0d-85a0d7e07799"
    b = ls.Block(content="{{embed ((" + uid + "))}}", line=9)
    b.link_uuid = uid
    ls.scan_content_features(b, ctx)
    assert ctx.model.counters["node_embeds"] == 1
    assert ctx.model.counters["embeds"] == 0
    issues = [i for i in ctx.model.issues if i.kind == "node-embed"]
    assert len(issues) == 1


def test_inline_tags_convert_mode():
    ctx = make_ctx(convert_tags=True)
    content, found = ls.apply_inline_tags("saw #inbox and #[[alex culture]] today", ctx)
    assert content == "saw [[inbox]] and [[alex culture]] today"
    assert found == ["inbox", "alex culture"]
    assert ctx.model.tag_names == {}


def test_inline_tags_keep_mode_registers_tags():
    ctx = make_ctx(convert_tags=False)
    content, found = ls.apply_inline_tags("saw #inbox today", ctx)
    assert content == "saw #inbox today"
    assert found == ["inbox"]
    assert ctx.model.tag_names == {"inbox": "inbox"}


def test_inline_tags_ignore_priority_and_urls():
    ctx = make_ctx(convert_tags=True)
    content, found = ls.apply_inline_tags("[#A] see https://x.com/#anchor", ctx)
    assert found == []
    assert content == "[#A] see https://x.com/#anchor"


def test_scan_content_features():
    ctx = make_ctx()
    b = ls.Block(
        content="ref ((6429252a-e423-4f7b-8885-055f7c8259ae)) ![img](../assets/pic.png) {{embed ((x))}} {{query (todo)}}",
        line=7,
    )
    ls.scan_content_features(b, ctx)
    assert ctx.model.block_refs == [("6429252a-e423-4f7b-8885-055f7c8259ae", "pages/t.md", 7)]
    assert ctx.model.counters["embeds"] == 1
    assert ctx.model.counters["queries"] == 1
    assert any(i.kind == "asset-link" for i in ctx.model.issues)
    embed_issues = [i for i in ctx.model.issues if i.kind == "embed"]
    assert len(embed_issues) == 1
    assert embed_issues[0].file == "pages/t.md"
    assert embed_issues[0].line == 7
    assert embed_issues[0].detail == "{{embed "
    query_issues = [i for i in ctx.model.issues if i.kind == "query"]
    assert len(query_issues) == 1
    assert query_issues[0].file == "pages/t.md"
    assert query_issues[0].line == 7
    assert query_issues[0].detail == "{{query "


def test_scan_content_features_advanced_query():
    ctx = make_ctx()
    b = ls.Block(query='{:title [:h3 "Random Quote"]\n :query [:find ?b]}', line=12)
    ls.scan_content_features(b, ctx)
    assert ctx.model.counters["advanced_queries"] == 1
    issues = [i for i in ctx.model.issues if i.kind == "advanced-query"]
    assert len(issues) == 1
    assert issues[0].file == "pages/t.md"
    assert issues[0].line == 12
    assert issues[0].detail == '{:title [:h3 "Random Quote"]'


def test_scan_content_features_advanced_query_truncates_detail():
    ctx = make_ctx()
    long_first_line = "{" + "x" * 200
    b = ls.Block(query=long_first_line + "\nrest", line=3)
    ls.scan_content_features(b, ctx)
    issue = next(i for i in ctx.model.issues if i.kind == "advanced-query")
    assert issue.detail == long_first_line[:80]
    assert len(issue.detail) == 80


def test_scan_content_features_no_advanced_query_when_unset():
    ctx = make_ctx()
    b = ls.Block(content="plain", line=1)
    ls.scan_content_features(b, ctx)
    assert ctx.model.counters["advanced_queries"] == 0
    assert not any(i.kind == "advanced-query" for i in ctx.model.issues)


PAGE_TEXT = """retrieved:: [[04-11-2022]]
tags:: teaching-resources

- first block #inbox
  id:: 63d3ac1e-4690-422f-ae0d-85a0d7e07761
\t- TODO child task
-
"""


def test_parse_page_text_full():
    model = ls.GraphModel()
    opts = ls.Options(convert_tags=False, import_properties=True)
    page = ls.parse_page_text(PAGE_TEXT, "todo framework", "pages/todo framework.md", False, opts, model)
    assert page.properties == {"retrieved": "[[04-11-2022]]"}
    assert page.tags == ["teaching-resources"]
    assert len(page.blocks) == 1
    root = page.blocks[0]
    assert root.content == "first block #inbox"
    assert root.tags == ["inbox"]
    assert root.uuid == "63d3ac1e-4690-422f-ae0d-85a0d7e07761"
    assert [c.content for c in root.children] == ["child task"]
    assert root.children[0].status == "todo"


def test_parse_page_text_preamble_content_becomes_block():
    model = ls.GraphModel()
    page = ls.parse_page_text("just a line of text\n- real block\n", "x", "pages/x.md", False, ls.Options(), model)
    assert [b.content for b in page.blocks] == ["just a line of text", "real block"]
    assert model.counters["preamble_content_blocks"] == 1


def test_scan_graph(tmp_path):
    (tmp_path / "pages").mkdir()
    (tmp_path / "journals").mkdir()
    (tmp_path / "pages" / "hello%3Aworld.md").write_text("- hi ((6429252a-e423-4f7b-8885-055f7c8259ae))\n", encoding="utf-8")
    (tmp_path / "journals" / "2021_03_09.md").write_text("- journal entry\n", encoding="utf-8")
    (tmp_path / "journals" / "badname.md").write_text("- x\n", encoding="utf-8")
    model = ls.scan_graph(tmp_path, ls.Options())
    names = {p.name: p for p in model.pages}
    assert "hello:world" in names
    assert "2021-03-09" in names
    assert names["2021-03-09"].is_journal is True
    assert any(i.kind == "bad-journal-filename" for i in model.issues)
    assert model.counters["blocks"] == 3
    assert len(model.block_refs) == 1


def test_validate_unresolved_refs_and_collisions():
    model = ls.GraphModel()
    model.uuids["63d3ac1e-4690-422f-ae0d-85a0d7e07761"] = ("pages/a.md", 1)
    model.block_refs = [
        ("63d3ac1e-4690-422f-ae0d-85a0d7e07761", "pages/b.md", 3),
        ("00000000-0000-0000-0000-000000000000", "pages/b.md", 9),
    ]
    model.pages = [
        ls.Page(name="Foo", file="pages/Foo.md"),
        ls.Page(name="foo", file="pages/foo.md"),
        ls.Page(name="bar", file="pages/bar.md"),
    ]
    ls.validate(model)
    kinds = [i.kind for i in model.issues]
    assert kinds.count("unresolved-ref") == 1
    assert kinds.count("page-name-collision") == 1


def test_write_report(tmp_path):
    model = ls.GraphModel()
    model.pages = [ls.Page(name="a", file="pages/a.md")]
    model.counters["blocks"] = 5
    model.issues = [ls.Issue("asset-link", "pages/a.md", 2, "(../assets/x.png)")]
    out = tmp_path / "r.txt"
    text = ls.write_report(model, out, "dry-run", "convert_tags=True import_properties=True")
    assert out.read_text(encoding="utf-8") == text
    assert "asset-link" in text
    assert "pages/a.md:2" in text
    assert "blocks" in text


def test_write_report_import_failures(tmp_path):
    model = ls.GraphModel()
    text = ls.write_report(model, tmp_path / "r.txt", "import", "x", failures=[("pages/a.md", "boom")])
    assert "boom" in text
    assert "1 FAILURE" in text.upper()


def test_edn_str_escaping():
    assert ls.edn_str('say "hi"\nline2\\end') == '"say \\"hi\\"\\nline2\\\\end"'


def test_block_edn_full():
    child = ls.Block(content="child", uuid="22222222-2222-4222-8222-222222222222")
    b = ls.Block(
        content="parent",
        uuid="11111111-1111-4111-8111-111111111111",
        tags=["inbox"],
        properties={"source": "[[kindle]]"},
        status="todo",
        priority="high",
        children=[child],
    )
    edn = ls.block_edn(b, {"inbox": 197}, {"source": ":user.property/source-abc"})
    assert ':block/title "parent"' in edn
    assert ':block/uuid #uuid "11111111-1111-4111-8111-111111111111"' in edn
    assert ":block/tags [197 :logseq.class/Task]" in edn
    assert ":logseq.property/status :logseq.property/status.todo" in edn
    assert ":logseq.property/priority :logseq.property/priority.high" in edn
    assert ':user.property/source-abc "[[kindle]]"' in edn
    assert ':block/children [{' in edn
    assert ':block/title "child"' in edn


def test_block_edn_skips_unknown_tags_and_props():
    b = ls.Block(content="x", uuid="11111111-1111-4111-8111-111111111111", tags=["ghost"], properties={"gone": "v"})
    edn = ls.block_edn(b, {}, {})
    assert ":block/tags" not in edn
    assert "gone" not in edn


def test_block_edn_query_block_emits_class_and_property():
    b = ls.Block(
        content="",
        uuid="33333333-3333-4333-8333-333333333333",
        query='{:title "say \\"hi\\""\nline2}',
    )
    edn = ls.block_edn(b, {}, {})
    assert ":logseq.class/Query" in edn
    assert ":block/tags [:logseq.class/Query]" in edn
    assert ":logseq.property/query {}".format(ls.edn_str(b.query)) in edn


def test_block_edn_query_block_combines_with_task_tag():
    b = ls.Block(
        content="",
        uuid="33333333-3333-4333-8333-333333333333",
        query="{:title 1}",
        status="todo",
    )
    edn = ls.block_edn(b, {}, {})
    assert ":block/tags [:logseq.class/Task :logseq.class/Query]" in edn


def test_block_edn_page_link_resolved_at_creation_time():
    b = ls.Block(content="{{embed [[Target]]}}", uuid="11111111-1111-4111-8111-111111111111")
    b.link_page = "Target"
    links = {"page_ids": {"target": 4483}, "imported_uuids": set()}
    edn = ls.block_edn(b, {}, {}, links)
    assert ':block/title ""' in edn
    assert ":block/link 4483" in edn
    assert "{{embed" not in edn


def test_block_edn_block_link_resolved_at_creation_time():
    uid = "63d3ac1e-4690-422f-ae0d-85a0d7e07799"
    b = ls.Block(content="{{embed ((" + uid + "))}}", uuid="11111111-1111-4111-8111-111111111111")
    b.link_uuid = uid
    links = {"page_ids": {}, "imported_uuids": {uid}}
    edn = ls.block_edn(b, {}, {}, links)
    assert ':block/title ""' in edn
    assert ':block/link [:block/uuid #uuid "{}"]'.format(uid) in edn


def test_block_edn_link_unresolved_emits_literal_and_records_pending():
    uid = "63d3ac1e-4690-422f-ae0d-85a0d7e07799"
    b = ls.Block(content="{{embed ((" + uid + "))}}", uuid="11111111-1111-4111-8111-111111111111")
    b.link_uuid = uid
    links = {"page_ids": {}, "imported_uuids": set()}
    pending = []
    edn = ls.block_edn(b, {}, {}, links, pending)
    assert "{{embed ((" in edn
    assert ":block/link" not in edn
    assert pending == [b]


def test_block_edn_page_link_unresolved_when_page_missing_from_map():
    b = ls.Block(content="{{embed [[Ghost]]}}", uuid="11111111-1111-4111-8111-111111111111")
    b.link_page = "Ghost"
    links = {"page_ids": {}, "imported_uuids": set()}
    pending = []
    edn = ls.block_edn(b, {}, {}, links, pending)
    assert "{{embed [[Ghost]]}}" in edn
    assert ":block/link" not in edn
    assert pending == [b]


def test_block_edn_no_links_arg_keeps_existing_behavior():
    b = ls.Block(content="{{embed [[Target]]}}", uuid="11111111-1111-4111-8111-111111111111")
    b.link_page = "Target"
    edn = ls.block_edn(b, {}, {})
    assert "{{embed [[Target]]}}" in edn
    assert ":block/link" not in edn


def test_write_report_notes_section(tmp_path):
    model = ls.GraphModel()
    text = ls.write_report(
        model, tmp_path / "r.txt", "import", "x",
        notes=[("embed 11111111-1111-4111-8111-111111111111", "linked via fallback; verify the block renders correctly")],
    )
    assert "NOTES" in text
    assert "linked via fallback" in text


def test_cli_run_parses_json_and_errors(monkeypatch):
    import subprocess as sp

    calls = []

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        class R:
            returncode = 0
            stdout = '{"status":"ok","data":{"result":[42]}}'
            stderr = ""
        return R()

    monkeypatch.setattr(sp, "run", fake_run)
    cli = ls.LogseqCli("test-graph")
    data = cli.run("upsert", "page", "--page", "X")
    assert data == {"result": [42]}
    assert "--graph" in calls[0] and "test-graph" in calls[0]
    assert "--output" in calls[0] and "json" in calls[0]

    def fake_fail(cmd, capture_output, text, timeout):
        class R:
            returncode = 1
            stdout = "Failure(tag not found)"
            stderr = ""
        return R()

    monkeypatch.setattr(sp, "run", fake_fail)
    try:
        cli.run("upsert", "block")
        assert False
    except ls.CliError as e:
        assert "tag not found" in e.message


class FakeCli:
    def __init__(self):
        self.calls = []
        self.fail_on = None
        self.graph = "fake"

    def run(self, *args, timeout_ms=None):
        self.calls.append(args)
        joined = " ".join(args)
        if self.fail_on and self.fail_on in joined:
            raise ls.CliError(args, "boom")
        if args[:2] == ("graph", "list"):
            return {"graphs": []}
        if args[:2] == ("upsert", "tag"):
            return {"result": [100 + len(self.calls)]}
        if args[:2] == ("list", "property"):
            return {"items": [
                {"block/title": "source", "db/ident": "user.property/source-xYz"},
                {"block/title": "retrieved", "db/ident": "user.property/retrieved-aBc"},
            ]}
        if args[:2] == ("debug", "pull"):
            return {"entity": {"db/id": 777, "logseq.property/query": {"db/id": 555}}}
        return {"result": [1]}


def make_model_for_import():
    model = ls.GraphModel()
    model.tag_names = {"inbox": "inbox"}
    model.prop_names = {"source": "source"}
    block = ls.Block(
        content="hello",
        uuid="11111111-1111-4111-8111-111111111111",
        tags=["inbox"],
        properties={"source": "[[kindle]]"},
        status="todo",
        scheduled="2023-08-24T00:00:00.000Z",
    )
    page = ls.Page(name="P1", file="pages/P1.md", tags=["inbox"], properties={"source": "x"}, blocks=[block])
    model.pages = [page]
    return model


def test_importer_happy_path():
    model = make_model_for_import()
    cli = FakeCli()
    importer = ls.Importer(model, "newgraph", cli)
    failures = importer.run()
    assert failures == []
    joined = [" ".join(c) for c in cli.calls]
    assert any(c.startswith("graph create") for c in joined)
    assert any(c.startswith("upsert tag --name inbox") for c in joined)
    assert any(c.startswith("upsert property --name source") for c in joined)
    assert any(c.startswith("list property") for c in joined)
    assert any(c.startswith("upsert page --page P1") for c in joined)
    blocks_calls = [c for c in cli.calls if c[:2] == ("upsert", "block")]
    assert len(blocks_calls) == 1
    assert "--blocks-file" in blocks_calls[0]
    task_calls = [c for c in cli.calls if c[:2] == ("upsert", "task")]
    assert len(task_calls) == 1
    assert "--uuid" in task_calls[0] and "--scheduled" in task_calls[0]


def test_importer_blocks_file_content(tmp_path, monkeypatch):
    model = make_model_for_import()
    cli = FakeCli()
    captured = {}

    class SpyCli(FakeCli):
        def run(self, *args, timeout_ms=None):
            if args[:2] == ("upsert", "block") and "--blocks-file" in args:
                path = args[args.index("--blocks-file") + 1]
                captured["edn"] = Path(path).read_text(encoding="utf-8")
            return FakeCli.run(self, *args, timeout_ms=timeout_ms)

    cli = SpyCli()
    ls.Importer(model, "newgraph", cli).run()
    assert ':block/uuid #uuid "11111111-1111-4111-8111-111111111111"' in captured["edn"]
    assert ":user.property/source-xYz" in captured["edn"]


def test_importer_cleans_temp_file_when_blocks_upsert_fails():
    model = make_model_for_import()
    captured = {}

    class FailingSpyCli(FakeCli):
        def run(self, *args, timeout_ms=None):
            if args[:2] == ("upsert", "block") and "--blocks-file" in args:
                captured["path"] = args[args.index("--blocks-file") + 1]
                raise ls.CliError(args, "boom")
            return FakeCli.run(self, *args, timeout_ms=timeout_ms)

    failures = ls.Importer(model, "newgraph", FailingSpyCli()).run()
    assert any("pages/P1.md" == context for context, message in failures)
    assert not Path(captured["path"]).exists()


def test_importer_cleans_temp_file_even_when_exception_propagates():
    model = make_model_for_import()
    captured = {}

    class ExplodingSpyCli(FakeCli):
        def run(self, *args, timeout_ms=None):
            if args[:2] == ("upsert", "block") and "--blocks-file" in args:
                captured["path"] = args[args.index("--blocks-file") + 1]
                raise RuntimeError("boom")
            return FakeCli.run(self, *args, timeout_ms=timeout_ms)

    importer = ls.Importer(model, "newgraph", ExplodingSpyCli())
    raised = False
    try:
        importer.run()
    except RuntimeError:
        raised = True
    assert raised
    assert not Path(captured["path"]).exists()


def test_importer_page_failure_skips_and_continues(capsys):
    model = make_model_for_import()
    model.pages.append(ls.Page(name="P2", file="pages/P2.md", blocks=[ls.Block(content="x", uuid="22222222-2222-4222-8222-222222222222")]))
    cli = FakeCli()
    cli.fail_on = "upsert page --page P1"
    importer = ls.Importer(model, "newgraph", cli)
    failures = importer.run()
    assert len(failures) == 1
    assert failures[0][0] == "pages/P1.md"
    joined = [" ".join(c) for c in cli.calls]
    assert any(c.startswith("upsert page --page P2") for c in joined)
    p1_block_calls = [c for c in cli.calls if c[:2] == ("upsert", "block") and "P1" in c]
    assert p1_block_calls == []
    p2_block_calls = [c for c in cli.calls if c[:2] == ("upsert", "block") and "P2" in c]
    assert len(p2_block_calls) == 1


def test_importer_aborts_when_graph_create_fails():
    model = make_model_for_import()
    cli = FakeCli()
    cli.fail_on = "graph create"
    try:
        ls.Importer(model, "newgraph", cli).run()
        assert False
    except SystemExit:
        pass


def test_importer_aborts_when_graph_already_exists():
    model = make_model_for_import()

    class ExistingGraphCli(FakeCli):
        def run(self, *args, timeout_ms=None):
            if args[:2] == ("graph", "list"):
                self.calls.append(args)
                return {"graphs": ["newgraph"]}
            return FakeCli.run(self, *args, timeout_ms=timeout_ms)

    cli = ExistingGraphCli()
    try:
        ls.Importer(model, "newgraph", cli).run()
        assert False
    except SystemExit:
        pass
    assert not any(c[:2] == ("graph", "create") for c in cli.calls)


def test_task_followups_skip_pages_that_failed_to_upsert():
    model = make_model_for_import()
    cli = FakeCli()
    cli.fail_on = "upsert page --page P1"
    importer = ls.Importer(model, "newgraph", cli)
    importer.run()
    assert not any(c[:2] == ("upsert", "task") for c in cli.calls)


def test_import_page_omits_tags_missing_from_tag_ids():
    model = ls.GraphModel()
    model.tag_names = {"ghost": "ghost"}
    model.prop_names = {}
    page = ls.Page(name="P1", file="pages/P1.md", tags=["ghost"], blocks=[])
    model.pages = [page]
    cli = FakeCli()
    cli.fail_on = "upsert tag --name ghost"
    importer = ls.Importer(model, "newgraph", cli)
    failures = importer.run()
    page_upsert_calls = [c for c in cli.calls if c[:2] == ("upsert", "page")]
    assert len(page_upsert_calls) == 1
    assert "ghost" not in " ".join(page_upsert_calls[0])
    assert len(failures) == 1
    assert failures[0][0] == "tag ghost"


def make_model_with_query_block():
    model = ls.GraphModel()
    model.tag_names = {}
    model.prop_names = {}
    block = ls.Block(
        content="",
        uuid="44444444-4444-4444-8444-444444444444",
        query='{:title "Q"}',
    )
    page = ls.Page(name="P1", file="pages/P1.md", blocks=[block])
    model.pages = [page]
    return model


def test_query_followups_pulls_and_updates_value_entity_in_two_calls():
    model = make_model_with_query_block()
    cli = FakeCli()
    importer = ls.Importer(model, "newgraph", cli)
    failures = importer.run()
    assert failures == []
    debug_calls = [c for c in cli.calls if c[:2] == ("debug", "pull")]
    assert len(debug_calls) == 1
    assert "--uuid" in debug_calls[0]
    assert "44444444-4444-4444-8444-444444444444" in debug_calls[0]
    id_calls = [c for c in cli.calls if c[:2] == ("upsert", "block") and "--id" in c]
    assert len(id_calls) == 2
    props_calls = [c for c in id_calls if "--update-properties" in c]
    tags_calls = [c for c in id_calls if "--update-tags" in c]
    assert len(props_calls) == 1
    assert len(tags_calls) == 1
    assert "555" in props_calls[0]
    assert "555" in tags_calls[0]
    assert "--update-tags" not in props_calls[0]
    assert "--update-properties" not in tags_calls[0]


def test_query_followups_skip_pages_that_failed_to_upsert():
    model = make_model_with_query_block()
    cli = FakeCli()
    cli.fail_on = "upsert page --page P1"
    importer = ls.Importer(model, "newgraph", cli)
    importer.run()
    assert not any(c[:2] == ("debug", "pull") for c in cli.calls)
    assert not any(c[:2] == ("upsert", "block") and "--id" in c for c in cli.calls)


def test_import_pages_page_prepass_before_any_blocks_upsert():
    model = make_model_for_import()
    model.pages.append(ls.Page(name="P2", file="pages/P2.md", blocks=[
        ls.Block(content="x2", uuid="22222222-2222-4222-8222-222222222222"),
    ]))
    cli = FakeCli()
    importer = ls.Importer(model, "newgraph", cli)
    importer.run()
    page_upsert_indexes = [i for i, c in enumerate(cli.calls) if c[:2] == ("upsert", "page")]
    block_upsert_indexes = [i for i, c in enumerate(cli.calls) if c[:2] == ("upsert", "block") and "--blocks-file" in c]
    assert len(page_upsert_indexes) == 2
    assert len(block_upsert_indexes) == 2
    assert max(page_upsert_indexes) < min(block_upsert_indexes)


def test_imported_uuids_grows_per_page():
    model = make_model_for_import()
    model.pages.append(ls.Page(name="P2", file="pages/P2.md", blocks=[
        ls.Block(content="x2", uuid="22222222-2222-4222-8222-222222222222"),
    ]))
    cli = FakeCli()
    importer = ls.Importer(model, "newgraph", cli)
    importer.run()
    assert importer.imported_uuids == {
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    }


def make_model_with_forward_block_embed():
    model = ls.GraphModel()
    model.tag_names = {}
    model.prop_names = {}
    target_uuid = "22222222-2222-4222-8222-222222222222"
    embed_block = ls.Block(
        content="{{embed ((" + target_uuid + "))}}",
        uuid="11111111-1111-4111-8111-111111111111",
    )
    embed_block.link_uuid = target_uuid
    target_block = ls.Block(content="target content", uuid=target_uuid)
    page1 = ls.Page(name="P1", file="pages/P1.md", blocks=[embed_block])
    page2 = ls.Page(name="P2", file="pages/P2.md", blocks=[target_block])
    model.pages = [page1, page2]
    return model


def test_link_followups_resolves_forward_reference_block_embed():
    model = make_model_with_forward_block_embed()
    cli = FakeCli()
    importer = ls.Importer(model, "newgraph", cli)
    failures = importer.run()
    assert failures == []
    pull_calls = [c for c in cli.calls if c[:2] == ("debug", "pull")]
    assert len(pull_calls) == 1
    assert "22222222-2222-4222-8222-222222222222" in pull_calls[0]
    link_updates = [
        c for c in cli.calls
        if c[:2] == ("upsert", "block") and "--uuid" in c and "11111111-1111-4111-8111-111111111111" in c
    ]
    assert len(link_updates) == 1
    assert ":block/link 777" in " ".join(link_updates[0])
    assert importer.notes == [
        ("embed 11111111-1111-4111-8111-111111111111", "linked via fallback; verify the block renders correctly"),
    ]


def test_link_followups_skip_when_page_blocks_upsert_fails():
    model = make_model_with_forward_block_embed()
    cli = FakeCli()
    cli.fail_on = "upsert block --target-page P1"
    importer = ls.Importer(model, "newgraph", cli)
    failures = importer.run()
    assert any(context == "pages/P1.md" for context, _ in failures)
    assert not any(c[:2] == ("debug", "pull") for c in cli.calls)
    assert importer.notes == []


def make_model_with_missing_page_target():
    model = ls.GraphModel()
    model.tag_names = {}
    model.prop_names = {}
    embed_block = ls.Block(
        content="{{embed [[Ghost Page]]}}",
        uuid="33333333-3333-4333-8333-333333333333",
    )
    embed_block.link_page = "Ghost Page"
    page = ls.Page(name="P1", file="pages/P1.md", blocks=[embed_block])
    model.pages = [page]
    return model


def test_link_followups_creates_missing_page_target():
    model = make_model_with_missing_page_target()
    cli = FakeCli()
    importer = ls.Importer(model, "newgraph", cli)
    failures = importer.run()
    assert failures == []
    ghost_calls = [c for c in cli.calls if c[:2] == ("upsert", "page") and "Ghost Page" in c]
    assert len(ghost_calls) == 1
    link_updates = [
        c for c in cli.calls
        if c[:2] == ("upsert", "block") and "33333333-3333-4333-8333-333333333333" in c
    ]
    assert len(link_updates) == 1
    assert ":block/link 1" in " ".join(link_updates[0])


def test_main_dry_run_on_fixture(tmp_path, monkeypatch, capsys):
    (tmp_path / "pages").mkdir()
    (tmp_path / "logseq").mkdir()
    (tmp_path / "logseq" / "config.edn").write_text("{}", encoding="utf-8")
    (tmp_path / "pages" / "a.md").write_text("- hello #inbox\n- TODO do it\n", encoding="utf-8")
    monkeypatch.setattr(ls, "script_root", lambda: tmp_path)
    rc = ls.main(["--dry-run", "--convert-tags", "--import-properties"])
    assert rc == 0
    report = (tmp_path / "import-report.txt").read_text(encoding="utf-8")
    assert "blocks: 2" in report
    assert "tasks: 1" in report


def test_main_rejects_non_graph_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "script_root", lambda: tmp_path)
    rc = ls.main(["--dry-run"])
    assert rc == 2


def test_ask_yes_no(monkeypatch):
    answers = iter(["x", "Y"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert ls.ask_yes_no("Continue?") is True
    answers = iter(["n"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert ls.ask_yes_no("Continue?") is False
