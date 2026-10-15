"""
Viewer for traces written with :mod:`mango.util.tracing`.

Turns a JSON-lines trace into one self-contained HTML page (no server, no
network needed) that lists all records and lets you

* filter by category, agent, free text or ``field=value`` on any field,
* select a record to highlight what started it (its ``cause`` chain) and
  everything it led to,
* jump to failures (``*.failed`` records),
* follow agents side by side in the Lanes view, aligned on the time line,
* see who messaged whom in the topology pane next to the records, and click a
  connection to show only its messages (the connection filter).

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
import posixpath
import re
import webbrowser
from collections.abc import Iterable
from functools import cache
from importlib import resources
from pathlib import Path

from .tracing import VIEWER_SCHEMA, read_trace

_NON_ASCII = re.compile("[^\x00-\x7f]")

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


_IMPORT = re.compile(r'^import\s*\{([^}]*)\}\s*from\s*"([^"]+)";?[ \t]*\n', re.M)
_EXPORT = re.compile(
    r"^export\s+(?=(?:async\s+)?function\b|const\b|let\b|class\b)", re.M
)
_DECLARED = re.compile(
    r"^(?:async\s+)?function\s*\*?\s*(\w+)|^(?:const|let|class)\s+(\w+)", re.M
)


def _modules(root, prefix: str = "") -> dict[str, str]:
    """All ``.js`` files below *root*, by their path relative to it."""
    found = {}
    for entry in root.iterdir():
        if entry.is_dir():
            found.update(_modules(entry, f"{prefix}{entry.name}/"))
        elif entry.name.endswith(".js"):
            found[prefix + entry.name] = entry.read_text(encoding="utf-8")
    return found


def _bundle(root, entry: str = "main.js") -> str:
    """The ES modules below *root* as one script, starting from *entry*.

    The modules import what they use from each other, so editors and linters
    can follow them. Here the imports and ``export`` keywords are dropped and
    the modules are joined in the order a browser evaluates them (the imports
    of a module before the module, in the order they are written). All modules
    then share one scope, so a name is declared in one module only. Imports
    must not go round in a circle: the modules form layers (see
    ``viewer/README.md``).
    """
    sources = _modules(root)
    order: list[str] = []
    seen: set[str] = set()
    path: list[str] = []

    def visit(name: str) -> None:
        if name in path:
            cycle = path[path.index(name) :] + [name]
            raise ValueError(f"import cycle: {' -> '.join(cycle)}")
        if name in seen:
            return
        seen.add(name)
        path.append(name)
        for names, spec in _IMPORT.findall(sources[name]):
            dep = posixpath.normpath(posixpath.join(posixpath.dirname(name), spec))
            if dep not in sources:
                raise ValueError(f"{name}: no module {spec}")
            if " as " in names:
                raise ValueError(f"{name}: imports are not renamed in the viewer")
            visit(dep)
        path.pop()
        order.append(name)

    visit(entry)
    if unused := sorted(set(sources) - seen):
        raise ValueError(f"modules not imported from {entry}: {', '.join(unused)}")
    owner: dict[str, str] = {}
    parts = []
    for name in order:
        body = _EXPORT.sub("", _IMPORT.sub("", sources[name]))
        if re.search(r"^(?:import|export)\b", body, re.M):
            raise ValueError(
                f"{name}: only `import {{ ... }} from` and `export` declarations"
            )
        for match in _DECLARED.finditer(body):
            declared = match.group(1) or match.group(2)
            if declared in owner:
                raise ValueError(
                    f"{declared} is declared in {owner[declared]} and {name}"
                )
            owner[declared] = name
        parts.append(f"// {name}\n{body.strip()}")
    return '"use strict";\n\n' + "\n\n".join(parts)


@cache
def _page_template() -> str:
    """The page from the files in ``viewer/``, with ``__TITLE__`` and
    ``__DATA__`` still to fill in; the record names the viewer knows come from
    :data:`~mango.util.tracing.VIEWER_SCHEMA`."""
    viewer = resources.files(__package__) / "viewer"
    js = _bundle(viewer / "js")
    # an ASCII page reads back correctly whatever encoding a tool assumes
    js = _NON_ASCII.sub(lambda m: f"\\u{ord(m.group()):04x}", js)
    css = (viewer / "viewer.css").read_text(encoding="utf-8").rstrip("\n")
    page = (viewer / "page.html").read_text(encoding="utf-8")
    return (
        page.replace("__STYLE__", css)
        .replace("__SCRIPT__", js)
        .replace("__SCHEMA__", json.dumps(VIEWER_SCHEMA))
    )


def _render_page(records: Iterable[dict], title: str) -> str:
    # escape "<" so no record can close the embedding <script> element
    data = json.dumps(list(records), default=str).replace("<", "\\u003c")
    return (
        _page_template()
        .replace("__TITLE__", html.escape(title))
        .replace("__DATA__", data)
    )


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
