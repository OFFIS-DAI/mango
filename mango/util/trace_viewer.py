"""
Viewer for traces written with :mod:`mango.util.tracing`.

Turns a JSON-lines trace into one self-contained HTML page (no server, no
network needed) that lists all records and lets you

* filter by category, agent, free text or ``field=value`` on any field,
* select a record to highlight what started it (its ``cause`` chain) and
  everything it led to,
* jump to failures (``*.failed`` records).

Command line::

    mango-trace trace.jsonl              # writes trace.html next to it
    mango-trace trace.jsonl -o out.html --open

From Python::

    from mango.util.trace_viewer import write_html
    write_html("trace.jsonl", open_browser=True)
"""

from __future__ import annotations

import argparse
import html
import json
import os
import webbrowser
from collections.abc import Iterable
from pathlib import Path

from ._trace_viewer_template import PAGE
from .tracing import read_trace

_SKELETON = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body>
{page}
</body>
</html>
"""


def _render_page(records: Iterable[dict], title: str) -> str:
    # escape "<" so no record can close the embedding <script> element
    data = json.dumps(list(records), default=str).replace("<", "\\u003c")
    return PAGE.replace("__TITLE__", html.escape(title)).replace("__DATA__", data)


def render_html(records: Iterable[dict], *, title: str = "mango trace") -> str:
    """Return the viewer as a complete HTML document for *records*."""
    return _SKELETON.format(page=_render_page(records, title))


def write_html(
    trace_path: str | os.PathLike,
    html_path: str | os.PathLike | None = None,
    *,
    title: str | None = None,
    open_browser: bool = False,
) -> Path:
    """Render the trace at *trace_path* into an HTML file.

    :param html_path: output file; defaults to *trace_path* with ``.html``
    :param title: page title; defaults to the trace file name
    :param open_browser: open the page in the default browser afterwards
    :return: path of the written file
    """
    trace_path = Path(trace_path)
    out = Path(html_path) if html_path is not None else trace_path.with_suffix(".html")
    page = render_html(read_trace(trace_path), title=title or trace_path.name)
    out.write_text(page, encoding="utf-8")
    if open_browser:
        webbrowser.open(out.resolve().as_uri())
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mango-trace",
        description="Render a mango trace (JSON lines) into an HTML viewer.",
    )
    parser.add_argument("trace", help="trace file written with configure_structlog")
    parser.add_argument(
        "-o", "--output", help="HTML file to write (default: TRACE with .html)"
    )
    parser.add_argument("--title", help="page title (default: trace file name)")
    parser.add_argument(
        "--open", action="store_true", help="open the page in the browser"
    )
    args = parser.parse_args(argv)
    out = write_html(args.trace, args.output, title=args.title, open_browser=args.open)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
