import json
import re

from mango.util.trace_viewer import main, render_html, write_html

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

    light = {
        p
        for p in _custom_properties(_block(page, ":root {"))
        if not p.startswith("--font-")
    }
    media = _block(
        page,
        '@media (prefers-color-scheme: dark) {\n  :root:not([data-theme="light"]) {',
    )
    forced = _block(page, ':root[data-theme="dark"] {')

    assert light
    assert light <= _custom_properties(media)
    assert light <= _custom_properties(forced)


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

    for placeholder in ("__TITLE__", "__DATA__", "__STYLE__", "__SCRIPT__"):
        assert placeholder not in page
    # only the data and the viewer script; no script file ends its element early
    assert page.count("</script>") == 2
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
