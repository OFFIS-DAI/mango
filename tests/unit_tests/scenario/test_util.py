import dataclasses
import datetime as dt
import hashlib
import importlib
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path, PurePosixPath

import numpy as np
import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from pydantic import BaseModel  # noqa: E402

from mango.scenario import _util  # noqa: E402
from mango.scenario._util import (  # noqa: E402
    call_flexible,
    canonical_json,
    content_hash,
    git_info,
    import_ref,
    object_ref,
    provenance,
)

MODULE_SOURCE = textwrap.dedent(
    """
    def plain():
        return "plain"

    class Thing:
        class Inner:
            def method(self):
                return 1

        @staticmethod
        def static():
            return 2

    instance = Thing()
    alias = plain
    named_lambda = lambda: 0  # noqa: E731

    def make_local():
        def local():
            return 3
        return local
    """
)


@pytest.fixture
def ref_module(tmp_path, monkeypatch):
    name = "scenario_util_refmod"
    (tmp_path / f"{name}.py").write_text(MODULE_SOURCE)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module(name)
    yield module
    sys.modules.pop(name, None)


# ----------------------------------------------------------------- import_ref


def test_import_ref_colon_and_dotted_forms():
    import collections
    import os.path

    assert import_ref("math:sqrt")(16) == 4
    assert import_ref("os.path:join") is os.path.join
    assert import_ref("os.path.join") is os.path.join
    assert import_ref("collections:OrderedDict.fromkeys") == (
        collections.OrderedDict.fromkeys
    )
    assert import_ref("  math:pi ") == pytest.approx(3.14159, rel=1e-5)


def test_import_ref_missing_module_names_the_ref():
    with pytest.raises(ImportError, match="no_such_pkg_xyz.mod:fn"):
        import_ref("no_such_pkg_xyz.mod:fn")
    with pytest.raises(ImportError, match="no module named 'no_such_pkg_xyz'"):
        import_ref("no_such_pkg_xyz.mod.fn")


def test_import_ref_missing_attribute_suggests():
    with pytest.raises(AttributeError, match=r"'math:sqr'.*did you mean 'sqrt'"):
        import_ref("math:sqr")
    with pytest.raises(AttributeError, match="has no attribute 'nope'"):
        import_ref("os.path.nope")


def test_import_ref_reports_broken_dependency(tmp_path, monkeypatch):
    (tmp_path / "scenario_util_broken.py").write_text("import not_installed_dep_xyz\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(ImportError, match="importing module 'scenario_util_broken'"):
        import_ref("scenario_util_broken:fn")
    with pytest.raises(ImportError, match="not_installed_dep_xyz"):
        import_ref("scenario_util_broken.fn")


@pytest.mark.parametrize("ref", ["math", ":sqrt", "math:", "a:b:c", "a..b"])
def test_import_ref_rejects_malformed(ref):
    with pytest.raises(ValueError, match="Invalid import reference"):
        import_ref(ref)


def test_import_ref_rejects_non_str():
    with pytest.raises(TypeError):
        import_ref(42)


# ----------------------------------------------------------------- object_ref


def test_object_ref_round_trips_functions_classes_and_instances(ref_module):
    for obj in (
        ref_module.plain,
        ref_module.Thing,
        ref_module.Thing.Inner,
        ref_module.Thing.Inner.method,
        ref_module.Thing.static,
        ref_module.instance,
        ref_module.named_lambda,
    ):
        ref = object_ref(obj)
        assert ref is not None and ref.startswith("scenario_util_refmod:")
        assert import_ref(ref) is obj
    assert object_ref(ref_module.plain) == "scenario_util_refmod:plain"
    assert object_ref(ref_module.instance) == "scenario_util_refmod:instance"


def test_object_ref_none_for_unreachable_objects(ref_module):
    assert object_ref(lambda: 0) is None
    assert object_ref(ref_module.make_local()) is None
    assert object_ref(ref_module.Thing()) is None
    assert object_ref(42) is None


def test_object_ref_uses_recorded_origin_module(ref_module):
    class Recorded:
        pass

    obj = Recorded()
    obj.__module__ = ref_module.__name__
    assert object_ref(obj) is None
    ref_module.registered = obj
    assert object_ref(obj) == "scenario_util_refmod:registered"


def test_object_ref_for_stdlib():
    import math

    assert object_ref(math.sqrt) == "math:sqrt"
    assert import_ref(object_ref(Path)) is Path


# ----------------------------------------------------------------- canonical_json


class _Model(BaseModel):
    when: dt.datetime
    step: float = 1.5


class _Color:
    pass


@dataclasses.dataclass
class _Point:
    x: int
    y: float


def test_canonical_json_is_independent_of_dict_order():
    a = {"b": 1, "a": {"y": [1, 2], "x": None}}
    b = {"a": {"x": None, "y": [1, 2]}, "b": 1}
    assert canonical_json(a) == canonical_json(b) == '{"a":{"x":null,"y":[1,2]},"b":1}'
    assert content_hash(a) == content_hash(b)


def test_canonical_json_normalizes_types():
    data = {
        "np_int": np.int64(3),
        "np_float": np.float32(0.5),
        "np_bool": np.bool_(True),
        "array": np.array([1, 2]),
        "tuple": (1, "a"),
        "when": dt.datetime(2024, 6, 1, 12, 30),
        "day": dt.date(2024, 6, 1),
        "path": PurePosixPath("a/b.csv"),
        "model": _Model(when=dt.datetime(2024, 1, 1)),
        "point": _Point(1, 2.0),
        "set": {3, 1, 2},
        1: "int key",
    }
    text = canonical_json(data)
    assert '"np_int":3' in text
    assert '"np_float":0.5' in text
    assert '"np_bool":true' in text
    assert '"array":[1,2]' in text
    assert '"tuple":[1,"a"]' in text
    assert '"when":"2024-06-01T12:30:00"' in text
    assert '"day":"2024-06-01"' in text
    assert '"path":"a/b.csv"' in text
    assert '"model":{"step":1.5,"when":"2024-01-01T00:00:00"}' in text
    assert '"point":{"x":1,"y":2.0}' in text
    assert '"set":[1,2,3]' in text
    assert '"1":"int key"' in text
    assert canonical_json({"v": np.float64(0.1)}) == canonical_json({"v": 0.1})
    assert canonical_json((1, 2)) == canonical_json([1, 2])


def test_canonical_json_floats_are_exact():
    assert canonical_json(0.1) == "0.1"
    assert canonical_json(1 / 3) == repr(1 / 3)
    assert canonical_json(1.0) != canonical_json(1)


def test_canonical_json_rejects_unknown_objects():
    with pytest.raises(TypeError, match="_Color"):
        canonical_json({"x": _Color()})


def test_content_hash_is_truncated_sha256():
    expected = hashlib.sha256(b'{"a":1}').hexdigest()
    assert content_hash({"a": 1}) == expected[:12]
    assert content_hash({"a": 1}, length=64) == expected
    assert content_hash({"a": 1}) != content_hash({"a": 2})
    with pytest.raises(ValueError):
        content_hash({}, length=0)


def test_content_hash_stable_for_datetimes_and_numpy():
    a = {"t": dt.datetime(2024, 6, 1), "n": np.int32(5)}
    b = {"n": 5, "t": "2024-06-01T00:00:00"}
    assert content_hash(a) == content_hash(b)


# ----------------------------------------------------------------- call_flexible


def test_call_flexible_passes_as_many_args_as_accepted():
    assert call_flexible(lambda: "none", 1, 2) == "none"
    assert call_flexible(lambda a: a, 1, 2) == 1
    assert call_flexible(lambda a, b: (a, b), 1, 2) == (1, 2)
    assert call_flexible(lambda a, b=5: (a, b), 1) == (1, 5)
    assert call_flexible(lambda *args: args, 1, 2) == (1, 2)
    assert call_flexible(lambda a, *, key=0: (a, key), 1, 2) == (1, 0)


def test_call_flexible_with_methods_and_builtins():
    class Recorder:
        def one(self, ctx):
            return ctx

        def zero(self):
            return "zero"

    recorder = Recorder()
    assert call_flexible(recorder.one, "ctx") == "ctx"
    assert call_flexible(recorder.zero, "ctx") == "zero"
    assert call_flexible(len, [1, 2]) == 2


def test_call_flexible_caches_signature(monkeypatch):
    calls = []
    original = _util.inspect.signature

    def counting(fn):
        calls.append(fn)
        return original(fn)

    monkeypatch.setattr(_util.inspect, "signature", counting)

    def fn(ctx):
        return ctx

    for value in range(5):
        assert call_flexible(fn, value) == value
    assert calls.count(fn) == 1


# ----------------------------------------------------------------- provenance / git


def test_provenance_lists_versions_platform_and_host():
    info = provenance()
    assert set(info) == {"versions", "platform", "hostname", "environment"}
    assert len(info["environment"]) == 16
    versions = info["versions"]
    assert set(versions) == {
        "mango-agents",
        "python",
        "numpy",
        "pandas",
        "pydantic",
        "pyarrow",
        "scipy",
        "networkx",
        "pyyaml",
    }
    assert versions["python"].count(".") == 2
    assert versions["numpy"] == np.__version__
    assert info["hostname"]


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_info_in_repository(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.txt").write_text("one")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "init")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    info = git_info(repo)
    assert info == {"commit": head, "dirty": False}
    (repo / "results").mkdir()
    (repo / "results" / "out.csv").write_text("x")
    assert git_info(repo)["dirty"] is False  # untracked files do not count
    (repo / "a.txt").write_text("two")
    assert git_info(repo / "a.txt") == {"commit": head, "dirty": True}


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_info_none_outside_repository(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert git_info(tmp_path) is None
    assert git_info(tmp_path / "missing") is None
    empty = tmp_path / "empty"
    empty.mkdir()
    _git(empty, "init", "-q")
    assert git_info(empty) is None  # no commit yet


def test_git_info_none_without_git(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(_util.subprocess, "run", missing)
    assert git_info(tmp_path) is None


def test_git_info_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.chdir(tmp_path)
    assert git_info() is None
    assert os.getcwd() == str(tmp_path)


# ----------------------------------------------------------------- plain data and files


def test_to_plain_keeps_key_order_and_converts_values():
    @dataclasses.dataclass
    class Point:
        x: float

    data = {"b": (1, np.float64(0.5)), "a": {"z": Point(1.0), "y": {3, 1, 2}}}
    plain = _util.to_plain(data)
    assert plain == {"b": [1, 0.5], "a": {"z": {"x": 1.0}, "y": [1, 2, 3]}}
    assert list(plain) == ["b", "a"] and list(plain["a"]) == ["z", "y"]
    with pytest.raises(TypeError, match="Cannot convert object"):
        _util.to_plain({"x": object()})
    assert _util.to_plain({"x": object()}, default=lambda o: "obj") == {"x": "obj"}


def test_atomic_writes_replace_the_whole_file(tmp_path):
    target = tmp_path / "deep" / "manifest.json"
    _util.write_json_atomic(target, {"b": 1, "a": np.int64(2), "c": object()})
    data = __import__("json").loads(target.read_text())
    assert list(data) == ["b", "a", "c"] and data["a"] == 2
    assert data["c"].startswith("<object")
    _util.write_text_atomic(target, "new")
    assert target.read_text() == "new"
    assert [p.name for p in target.parent.iterdir()] == ["manifest.json"]


def test_stable_ref_names_the_script_module(tmp_path, monkeypatch):
    import types

    script = tmp_path / "studies" / "run.py"
    script.parent.mkdir()
    script.write_text("")
    main = types.ModuleType("__main__")
    main.__file__ = str(script)
    monkeypatch.setitem(sys.modules, "__main__", main)
    monkeypatch.setattr(sys, "path", [str(script.parent), str(tmp_path)])
    assert _util.stable_ref("__main__:scenario") == "run:scenario"
    assert _util.stable_ref("__main__.scenario") == "run.scenario"
    assert _util.stable_ref("pkg.mod:scenario") == "pkg.mod:scenario"
    assert _util.stable_ref("ev_feeder") == "ev_feeder"
    main.__spec__ = types.SimpleNamespace(name="studies.run")  # python -m studies.run
    assert _util.stable_ref("__main__:scenario") == "studies.run:scenario"
    del main.__file__
    main.__spec__ = None  # an interactive session
    assert _util.stable_ref("__main__:scenario") == "__main__:scenario"


def test_add_note_falls_back_to_dunder_notes_without_add_note(monkeypatch):
    from mango.scenario import _util

    class Old(Exception):  # an exception as on Python 3.10: no add_note
        @property
        def add_note(self):
            raise AttributeError("add_note")

    exc = Old("boom")
    _util.add_note(exc, "in Kit.build() of kit 'grid'")
    _util.add_note(exc, "second")
    assert exc.__notes__ == ["in Kit.build() of kit 'grid'", "second"]
    monkeypatch.setattr(_util.sys, "version_info", (3, 10, 0))
    assert _util.notes_text(exc) == "in Kit.build() of kit 'grid'\nsecond\n"
    fresh = ValueError("x")
    _util.add_note(fresh, "note")
    assert fresh.__notes__ == ["note"]


def test_using_project_root_is_scoped_and_nests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project = tmp_path / "project"
    (project / "data").mkdir(parents=True)
    root = str(project)
    assert _util.project_root() is None
    with _util.using_project_root(root):
        assert root in sys.path and _util.project_root() == project
        assert _util.data_path_candidates("data/x.csv")[-1] == project / "data/x.csv"
        with _util.using_project_root(root):  # e.g. an overlapping thread
            pass
        assert root in sys.path and _util.project_root() == project
    assert root not in sys.path and _util.project_root() is None
    assert _util.data_path_candidates("data/x.csv") == [tmp_path / "data/x.csv"]
    with _util.using_project_root(None):
        assert _util.project_root() is None


def test_sequential_study_runs_leave_no_project_root_behind(tmp_path):
    from mango.scenario import Study
    from tests.unit_tests.scenario import study_fixtures as fx

    before = list(sys.path)
    Study(fx.scenario, out=tmp_path / "out", replications=1).run(progress=False)
    assert _util.project_root() is None
    assert sys.path == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_atomic_writes_honour_the_umask(tmp_path):
    target = _util.write_text_atomic(tmp_path / "manifest.json", "{}")
    plain = tmp_path / "plain.txt"
    plain.write_text("x")
    assert target.stat().st_mode & 0o777 == plain.stat().st_mode & 0o777
