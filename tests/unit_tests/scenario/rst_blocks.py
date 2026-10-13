"""Read code blocks from reStructuredText pages, to run documentation examples in tests."""

import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

DOCS = Path(__file__).resolve().parents[3] / "docs" / "source"

_DIRECTIVE = re.compile(r"^(?P<indent> *)\.\. (?P<name>[\w-]+)::\s*(?P<arg>.*)$")
_BLOCKS = {"code-block", "testsetup", "testcode", "testcleanup", "testoutput"}
_PROMPT = "$ "


@dataclass
class Block:
    """One directive with its body: ``kind`` is the directive, ``lang`` its argument."""

    kind: str
    lang: str
    code: str
    line: int
    options: dict


def read_blocks(page: str | Path) -> list[Block]:
    """Return the code-like directives of *page* (a path or a name under docs/source)."""
    path = Path(page) if Path(page).is_absolute() else DOCS / page
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        match = _DIRECTIVE.match(lines[i])
        if not match or match["name"] not in _BLOCKS:
            i += 1
            continue
        indent = len(match["indent"])
        start = i + 1
        i += 1
        options = {}
        while i < len(lines) and lines[i].strip().startswith(":"):
            key, _, value = lines[i].strip()[1:].partition(":")
            options[key] = value.strip()
            i += 1
        body = []
        while i < len(lines) and (
            not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent
        ):
            body.append(lines[i])
            i += 1
        code = textwrap.dedent("\n".join(body)).strip("\n") + "\n"
        blocks.append(Block(match["name"], match["arg"].strip(), code, start, options))
    return blocks


def group_blocks(blocks: list[Block]) -> dict[str, list[Block]]:
    """Return the blocks of every doctest group, in page order.

    The ``test*`` directives name their group (``default`` without one).  A
    ``code-block`` belongs to the group whose ``testsetup`` is the last one
    before it on the page, until that group's ``testcleanup``; code blocks
    outside such a region belong to no group.
    """
    groups: dict[str, list[Block]] = {}
    region = None
    for block in blocks:
        if block.kind == "code-block":
            if region is not None:
                groups[region].append(block)
            continue
        group = block.lang or "default"
        groups.setdefault(group, []).append(block)
        if block.kind == "testsetup":
            region = group
        elif block.kind == "testcleanup" and region == group:
            region = None
    return groups


def console_commands(code: str) -> list[tuple[str, str]]:
    """Split a console block into ``(command, expected output)`` pairs.

    Commands start with ``$ ``; the lines up to the next command are the
    output the command prints.
    """
    commands: list[tuple[str, list[str]]] = []
    for line in code.splitlines():
        if line.startswith(_PROMPT):
            commands.append((line[len(_PROMPT) :].strip(), []))
        elif commands:
            commands[-1][1].append(line)
        elif line.strip():
            raise ValueError(f"console output before the first command: {line!r}")
    return [(command, "\n".join(out).strip("\n")) for command, out in commands]


def normalize_output(text: str) -> str:
    """Strip trailing whitespace per line and surrounding blank lines."""
    return "\n".join(line.rstrip() for line in text.strip("\n").splitlines())
