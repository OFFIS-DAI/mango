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
