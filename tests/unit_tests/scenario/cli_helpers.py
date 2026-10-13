"""Helpers of the CLI tests: run ``mango-scenario`` in-process, isolated per test."""

import sys
import textwrap
from pathlib import Path

import pytest


@pytest.fixture(name="isolated")
def isolated_fixture(tmp_path, monkeypatch):
    """Run in *tmp_path*; undo sys.path changes, project imports and the project root."""
    from mango.scenario import _util

    monkeypatch.chdir(tmp_path)
    path = list(sys.path)
    modules = set(sys.modules)
    root = _util._PROJECT_ROOT
    yield tmp_path
    sys.path[:] = path
    for name in set(sys.modules) - modules:
        file = getattr(sys.modules[name], "__file__", None) or ""
        if file.startswith(str(tmp_path)):
            del sys.modules[name]
    _util.set_project_root(root)


def cli(capsys, *args):
    """Run the CLI with *args*; return (exit code, stdout, stderr)."""
    from mango.scenario.cli import main

    code = main([str(arg) for arg in args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def write(folder, name, text):
    path = Path(folder) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return path
