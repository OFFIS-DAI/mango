import json
import re

import pytest

from mango.util.trace_viewer import _bundle, main, render_html, write_html
from mango.util.tracing import CATEGORIES, VIEWER_SCHEMA

RECORDS = [
    {"event": "message.received", "category": "message", "id": "message-1"},
    {"event": "user.log", "cause": "message-1", "note": "</script><b>x</b>"},
]


def embedded_records(page: str) -> list[dict]:
    data = re.search(
        r'<script type="application/json" id="trace-data">(.*?)</script>',
        page,
        re.S,
    ).group(1)
    return json.loads(data)


def test_render_embeds_records_safely():
    page = render_html(RECORDS, title="<run>")

    assert page.startswith("<!doctype html>")
    assert "<title>&lt;run&gt;</title>" in page
    # the record cannot close the data script element early
    assert "</script><b>" not in page
    assert embedded_records(page) == RECORDS


def test_write_html_next_to_trace(tmp_path):
    trace = tmp_path / "run.jsonl"
    trace.write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n")

    out = write_html(trace)

    assert out == tmp_path / "run.html"
    page = out.read_text()
    assert "<title>run.jsonl</title>" in page
    assert embedded_records(page) == RECORDS


def test_cli(tmp_path, capsys):
    trace = tmp_path / "run.jsonl"
    trace.write_text(json.dumps(RECORDS[0]) + "\n")
    out = tmp_path / "view.html"

    assert main([str(trace), "-o", str(out), "--title", "Run 1"]) == 0

    assert "<title>Run 1</title>" in out.read_text()
    assert str(out) in capsys.readouterr().out


def _block(page: str, start: str) -> str:
    begin = page.index(start) + len(start)
    return page[begin : page.index("}", begin)]


def _custom_properties(block: str) -> set[str]:
    return set(re.findall(r"(--[\w-]+)\s*:", block))


def test_page_loads_nothing_from_elsewhere():
    page = render_html(RECORDS)

    assert not re.search(r"<script[^>]*\ssrc\s*=", page)
    urls = set(re.findall(r"https?://[^\s\"'<>)]+", page))
    external = {u for u in urls if not u.startswith("http://www.w3.org/")}
    assert all(u.startswith("https://fonts.googleapis.com/") for u in external)
    linked = re.findall(r"""(?:href|src)\s*=\s*["']?(https?:[^"'\s>]+)""", page)
    assert linked and all(
        u.startswith("https://fonts.googleapis.com/css2") for u in linked
    )


def test_theme_tokens_have_dark_values():
    page = render_html(RECORDS)
    root = _block(page, ":root {")
    colours = {
        name: value
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", root)
        if not name.startswith("--font-")
    }

    assert colours
    assert "color-scheme: light dark;" in root
    assert all(value.startswith("light-dark(") for value in colours.values())


def test_page_has_views_and_topology():
    page = render_html(RECORDS)

    for marker in [
        'id="tab-records"',
        'id="tab-lanes"',
        'role="tablist"',
        'id="lanes"',
        'id="topo"',
        'id="panel"',
        'id="rail"',
    ]:
        assert marker in page


def test_placeholders_replaced():
    page = render_html(RECORDS, title="run")

    for placeholder in (
        "__TITLE__",
        "__DATA__",
        "__SCHEMA__",
        "__STYLE__",
        "__SCRIPT__",
    ):
        assert placeholder not in page
    # only the schema, the data and the viewer script; no module ends its element early
    assert page.count("</script>") == 3
    assert page.isascii()


def test_records_without_agent_or_time_embed():
    records = [
        {"event": "trace.started", "category": "run"},
        {"event": "note", "level": "info", "timestamp": "2026-10-07T18:15:08.959153Z"},
        {"event": "message.sent", "category": "message", "id": "msg-1", "sender": "a"},
        {
            "event": "message.received",
            "category": "message",
            "agent": "b",
            "cause": "msg-1",
            "sim_time": 5.0,
        },
    ]

    page = render_html(records)

    assert embedded_records(page) == records


def test_viewer_has_a_colour_for_every_category():
    page = render_html(RECORDS)

    assert all(f"--c-{category}:" in page for category in CATEGORIES)


def test_page_carries_the_record_schema():
    page = render_html(RECORDS)
    schema = re.search(
        r'<script type="application/json" id="trace-schema">(.*?)</script>', page, re.S
    ).group(1)

    assert json.loads(schema) == VIEWER_SCHEMA


def _write(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_bundle_joins_modules_dependencies_first(tmp_path):
    _write(
        tmp_path,
        {
            "main.js": 'import { b } from "./ui/b.js";\nimport { a } from "./a.js";\nb(a);\n',
            "a.js": "export const a = 1;\n",
            "ui/b.js": 'import { a } from "../a.js";\nexport function b(x) {\n  return x + a;\n}\n',
        },
    )

    script = _bundle(tmp_path)

    assert (
        script.index("// a.js")
        < script.index("// ui/b.js")
        < script.index("// main.js")
    )
    assert "import" not in script and "export" not in script


def test_bundle_rejects_cycles_and_clashing_names(tmp_path):
    _write(
        tmp_path,
        {
            "main.js": 'import { a } from "./a.js";\n',
            "a.js": 'import { m } from "./main.js";\nexport const a = 1;\n',
        },
    )
    with pytest.raises(ValueError, match="import cycle: main.js -> a.js -> main.js"):
        _bundle(tmp_path)

    _write(
        tmp_path,
        {
            "a.js": "export const a = 1;\nconst x = 1;\n",
            "main.js": 'import { a } from "./a.js";\nconst x = a;\n',
        },
    )
    with pytest.raises(ValueError, match="x is declared in a.js and main.js"):
        _bundle(tmp_path)
