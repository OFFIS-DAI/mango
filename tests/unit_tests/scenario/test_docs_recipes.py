"""Run every recipe of ``docs/source/scenario-recipes.rst``.

Each recipe is one doctest group of the page (one test each).  As for the
user guide (``test_docs_scenario_guide.py``), the ``testsetup``,
``testcode`` and ``testcleanup`` blocks of a group run in one namespace in
page order and every ``testoutput`` must match what the preceding
``testcode`` printed.  Within a group's region:

* a ``code-block`` with a ``:caption:`` shows a file, which the hidden
  ``testsetup`` wrote: the file must have exactly the shown content;
* a ``code-block:: console`` is a terminal session: every ``$ mango-scenario
  ...`` or ``$ python <file>`` command runs in-process in the recipe's
  working directory and must print the lines below it (``...`` matches
  anything, whitespace is normalised).

So a recipe on the page cannot drift from what the framework does.
"""

import contextlib
import doctest
import io
import os
import runpy
import shlex
import sys
from pathlib import Path

import pytest

from .rst_blocks import console_commands, group_blocks, read_blocks

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

PAGE = "scenario-recipes.rst"

# sphinx.ext.doctest's defaults for testcode/testoutput pairs
FLAGS = (
    doctest.ELLIPSIS
    | doctest.IGNORE_EXCEPTION_DETAIL
    | doctest.DONT_ACCEPT_TRUE_FOR_1
    | doctest.DONT_ACCEPT_BLANKLINE
)
CONSOLE_FLAGS = FLAGS | doctest.NORMALIZE_WHITESPACE

#: the recipes of the page, one doctest group each
RECIPES = {
    "seir",
    "corridor",
    "beergame",
    "swarm",
    "auction",
    "gossip",
    "opinions",
    "schelling",
    "building",
}

#: recipes whose examples need optional packages
REQUIRES = {"seir": ("scipy",), "swarm": ("scipy",)}

GROUPS = group_blocks(read_blocks(PAGE))


def _flags(options: dict) -> int:
    flags = FLAGS
    for raw in options.get("options", "").split(","):
        item = raw.strip()
        if item:
            flag = doctest.OPTIONFLAGS_BY_NAME[item[1:]]
            flags = flags | flag if item[0] == "+" else flags & ~flag
    return flags


def _run(code: str, namespace: dict, where: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(code, where, "exec"), namespace)
    return out.getvalue()


def _shell(command: str, where: str) -> str:
    """Run one console command in-process; return what it printed (stdout + stderr)."""
    from mango.scenario.cli import main

    args = shlex.split(command)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        if args[0] == "mango-scenario":
            main(args[1:])
        elif args[0] == "python" and len(args) > 1:
            argv = sys.argv
            sys.argv = args[1:]
            try:
                runpy.run_path(args[1], run_name="__main__")
            finally:
                sys.argv = argv
        else:
            pytest.fail(
                f"{where}: console blocks may only run 'mango-scenario' or 'python "
                f"<file>' commands, got {command!r}; use a bash block for illustrations"
            )
    return out.getvalue()


def _check(want: str, got: str, flags: int, where: str, source: str) -> None:
    checker = doctest.OutputChecker()
    if not checker.check_output(want, got, flags):
        example = doctest.Example(source, want)
        pytest.fail(f"{where}:\n" + checker.output_difference(example, got, flags))


def _check_file(block, where: str) -> None:
    """A file shown with a caption is the file the hidden setup wrote."""
    path = Path(block.options["caption"])
    assert path.is_file(), f"{where}: the setup did not write {path}"
    written = path.read_text(encoding="utf-8")
    assert written == block.code, f"{where}: {path} differs from the page"


@contextlib.contextmanager
def _isolated():
    """Undo what a recipe changes in the process: cwd, imports, sys.path, registries.

    Yields a list; the test appends the recipe's working directories, and
    modules imported from them are forgotten afterwards.
    """
    from mango.scenario import _util, components

    cwd = os.getcwd()
    path = list(sys.path)
    modules = set(sys.modules)
    root = _util._PROJECT_ROOT
    registry = dict(components._REGISTRY)
    workdirs: list[str] = []
    try:
        yield workdirs
    finally:
        os.chdir(cwd)
        sys.path[:] = path
        for name in set(sys.modules) - modules:
            file = getattr(sys.modules[name], "__file__", None) or ""
            if any(file.startswith(workdir) for workdir in workdirs):
                del sys.modules[name]
        _util.set_project_root(root)
        components._REGISTRY.clear()
        components._REGISTRY.update(registry)


def test_the_page_has_one_group_per_recipe():
    assert set(GROUPS) == RECIPES
    for name, blocks in GROUPS.items():
        kinds = {block.kind for block in blocks}
        assert {"testsetup", "testcleanup"} <= kinds, name


def test_the_page_is_in_the_user_guide_toctree():
    index = (Path(__file__).resolve().parents[3] / "docs/source/index.rst").read_text()
    assert "\n   scenario-recipes\n" in index


@pytest.mark.parametrize("group", sorted(RECIPES))
def test_the_recipe_runs_and_prints_what_the_page_shows(group):
    for module in REQUIRES.get(group, ()):
        pytest.importorskip(module)
    blocks = GROUPS[group]
    namespace: dict = {}
    start = os.getcwd()
    checked = 0
    with _isolated() as workdirs:
        for block in blocks:
            if block.kind == "testsetup":
                _run(block.code, namespace, f"{PAGE}:{block.line}")
        if os.getcwd() != start:  # the setup's temporary project folder
            workdirs.extend({os.getcwd(), os.path.realpath(os.getcwd())})
        try:
            for block, following in zip(blocks, [*blocks[1:], None]):
                where = f"{PAGE}, {block.kind} at line {block.line}"
                if block.kind == "testcode":
                    printed = _run(block.code, namespace, f"{PAGE}:{block.line}")
                    shown = following is not None and following.kind == "testoutput"
                    want = following.code if shown else ""
                    flags = _flags(following.options) if shown else FLAGS
                    _check(want, printed, flags, where, block.code)
                    checked += 1
                elif block.kind == "code-block" and block.lang == "console":
                    for command, want in console_commands(block.code):
                        got = _shell(command, where)
                        _check(
                            want, got, CONSOLE_FLAGS, f"{where}: $ {command}", command
                        )
                        checked += 1
                elif block.kind == "code-block" and "caption" in block.options:
                    _check_file(block, where)
                    checked += 1
        finally:
            for block in blocks:
                if block.kind == "testcleanup":
                    _run(block.code, namespace, f"{PAGE}:{block.line}")
    assert checked, f"recipe {group!r} has no examples"
