"""Code and data fingerprints, stale runs on resume, execution-time provenance and run-one."""

import importlib
import json
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

pytest.importorskip("pandas")

from mango.scenario import Study, StudyError, load_study  # noqa: E402
from mango.scenario.freshness import (  # noqa: E402
    StaleReason,
    changed_since_import,
    code_digest,
    code_files,
    data_files,
    describe_stale,
    stale_reasons,
)
from mango.scenario.runner import (  # noqa: E402
    PREVIOUS_MANIFEST_FILE,
    run_one,
    run_one_from_file,
)

from .cli_helpers import cli, isolated_fixture, write  # noqa: E402, F401

HELPERS = """
FACTOR = 1.0
"""

MODEL = """
from mango import Agent
from mango.scenario import Scenario, ScenarioParams

from toyproj.helpers import FACTOR


class Params(ScenarioParams):
    n: int = 2
    gain: float = 1.0
    table: str = "data/gain.csv"


class Unit(Agent):
    def __init__(self, gain):
        super().__init__()
        self.gain = gain
        self.x = 0.0

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        self.x += self.gain


def make(e):
    return Unit(e.p.gain * FACTOR)


scenario = Scenario("toy", params=Params)
scenario.time(horizon="3h", step="1h")
scenario.group("unit", "n", make)
scenario.record("x", "unit", attr="x")


def final_x(run):
    return float(run.ts("x").iloc[-1])
"""


@pytest.fixture
def toy(isolated):
    """A project with a package (model + helpers) and a data file, imported fresh."""
    write(isolated, "toyproj/__init__.py", "")
    write(isolated, "toyproj/helpers.py", HELPERS)
    write(isolated, "toyproj/model.py", MODEL)
    write(isolated, "data/gain.csv", "gain\n1.0\n")
    sys.path.insert(0, str(isolated))
    return isolated


def _reimport():
    """Import the toy project anew, as a new process would."""
    for name in [name for name in sys.modules if name.startswith("toyproj")]:
        del sys.modules[name]
    for cache in Path("toyproj").glob("__pycache__"):  # same size, same second
        shutil.rmtree(cache)
    importlib.invalidate_caches()
    return importlib.import_module("toyproj.model")


def _study(**kwargs):
    model = _reimport()
    return Study(
        model.scenario,
        levers={"n": [1, 2]},
        seed=5,
        outcomes=[model.final_x],
        out="results/toy",
        format="csv",
        **kwargs,
    )


def _run(study, **kwargs):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        report = study.run(progress=False, **kwargs)
    return report, [str(item.message) for item in caught]


def _manifests(root):
    return [
        json.loads(path.read_text())
        for path in sorted((root / "results/toy/runs").glob("*/manifest.json"))
    ]


# ----------------------------------------------------------------- fingerprints


def test_code_files_follow_imports_of_the_project_only(toy):
    model = _reimport()
    files = code_files(toy, objects=[model.scenario])
    assert set(files) == {
        "toyproj/__init__.py",
        "toyproj/helpers.py",
        "toyproj/model.py",
    }
    assert all(len(digest) == 16 for digest in files.values())
    # mango itself and installed packages are not project code
    assert not any(key.startswith("mango/") for key in files)
    assert code_files(None, objects=[model.scenario]) == {}
    extra = code_files(toy, objects=[], files=[toy / "data/gain.csv"])
    assert extra == {"data/gain.csv": files_digest(toy / "data/gain.csv")}


def files_digest(path):
    from mango.scenario.freshness import file_digest

    return file_digest(path)


def test_imports_inside_functions_count(toy):
    write(
        toy,
        "toyproj/late.py",
        "def gain():\n    from toyproj.helpers import FACTOR\n    return FACTOR\n",
    )
    module = importlib.import_module("toyproj.late")
    importlib.import_module("toyproj.helpers")
    assert set(code_files(toy, objects=[module.gain])) == {
        "toyproj/__init__.py",
        "toyproj/helpers.py",
        "toyproj/late.py",
    }


def test_data_files_name_existing_files_in_the_params(toy):
    params = {
        "table": "data/gain.csv",
        "profiles": {"h0": {"path": "data/missing.csv"}},
        "algorithm": "greedy",
        "ref": "toyproj.model:scenario",
    }
    assert list(data_files(params, toy)) == ["data/gain.csv"]
    assert data_files({}, toy) == {}


def test_stale_reasons_compare_the_recorded_files_with_the_disk(toy):
    model = _reimport()
    manifest = {
        "code_files": code_files(toy, objects=[model.scenario]),
        "data_files": data_files({"table": "data/gain.csv"}, toy),
        "scenario_fingerprint": "abc",
    }
    assert stale_reasons(manifest, roots=[toy]) == []
    (toy / "toyproj/helpers.py").write_text("FACTOR = 2.0\n")
    (toy / "data/gain.csv").write_text("gain\n2.0\n")
    reasons = stale_reasons(manifest, roots=[toy], scenario_fingerprint="xyz")
    assert reasons == [
        StaleReason("code", "toyproj/helpers.py"),
        StaleReason("data", "data/gain.csv"),
        StaleReason("scenario"),
    ]
    assert describe_stale([reasons]) == (
        "code changed in toyproj/helpers.py; data changed in data/gain.csv; the "
        "scenario definition changed"
    )
    # a project root that no longer exists says nothing (moved project)
    assert stale_reasons(manifest, roots=[toy / "gone"]) == []
    # removed files are stale; manifests without fingerprints never are
    (toy / "data/gain.csv").unlink()
    assert StaleReason("data", "data/gain.csv") in stale_reasons(manifest, roots=[toy])
    assert stale_reasons({"status": "done"}, roots=[toy]) == []


CODE = """def gain(x, factor=2):
    \"\"\"The gain.\"\"\"
    label = f"{x!r:>4} x {factor}"
    return x * factor, label
"""


@pytest.mark.parametrize(
    "edited",
    [
        CODE + "# a trailing comment\n",
        "# header comment\n\n\n" + CODE.replace("factor=2):", "factor=2):  # why"),
        CODE.replace("    ", "  "),  # indentation width
        CODE.replace("(x, factor=2)", "(\n    x,\n    factor=2\n)"),  # line breaks
        CODE.replace("\n", "\r\n"),
    ],
)
def test_code_digest_ignores_comments_and_layout(tmp_path, edited):
    original = write(tmp_path, "a.py", CODE)
    changed = write(tmp_path, "b.py", edited)
    assert len(code_digest(original)) == 16
    assert code_digest(changed) == code_digest(original)


@pytest.mark.parametrize(
    "edited",
    [
        CODE.replace("factor=2", "factor=3"),
        CODE.replace("The gain.", "The gain factor."),  # docstrings count
        CODE.replace("{x!r:>4}", "{x!r:>5}"),  # f-string contents count
        CODE.replace("    return", "    if x:\n        return") + "    return 0\n",
    ],
)
def test_code_digest_changes_with_the_code(tmp_path, edited):
    original = write(tmp_path, "a.py", CODE)
    changed = write(tmp_path, "b.py", edited)
    assert code_digest(changed) != code_digest(original)


def test_code_digest_of_scenario_files_and_other_files(tmp_path):
    from mango.scenario.freshness import file_digest

    base = write(tmp_path, "s.yaml", "name: toy\ntime: {horizon: 1d}\n")
    commented = write(
        tmp_path,
        "t.yaml",
        "# my scenario\nname: toy   # the name\ntime:\n  horizon: 1d\n",
    )
    assert code_digest(commented) == code_digest(base)
    assert code_digest(write(tmp_path, "u.yaml", "name: toy2\n")) != code_digest(base)
    # other files, and files that do not tokenize or load, count by their bytes
    other = write(tmp_path, "data.csv", "a\n1\n")
    assert code_digest(other) == file_digest(other)
    broken = write(tmp_path, "broken.py", 'x = "unterminated\n')
    assert code_digest(broken) == file_digest(broken)
    assert code_digest(tmp_path / "missing.py") is None


def test_comments_do_not_make_runs_stale(toy):
    _run(_study())
    helpers = toy / "toyproj/helpers.py"
    helpers.write_text("# Shared constants.\n\n" + HELPERS + "# FACTOR = 2.0\n")
    assert load_study(toy / "results/toy")["stale"].tolist() == [False, False]
    report, messages = _run(_study())
    assert (report.done, report.skipped, report.stale) == (0, 2, 0) and not messages


def test_byte_digests_of_older_manifests_still_match(toy):
    from mango.scenario.freshness import file_digest

    model = _reimport()
    files = code_files(toy, objects=[model.scenario])
    old = {key: file_digest(toy / key) for key in files}  # before code_digest
    assert stale_reasons({"code_files": old}, roots=[toy]) == []
    (toy / "toyproj/helpers.py").write_text("FACTOR = 2.0\n")
    assert stale_reasons({"code_files": old}, roots=[toy]) == [
        StaleReason("code", "toyproj/helpers.py")
    ]


# ----------------------------------------------------------------- resume


def test_resume_reruns_runs_whose_code_changed(toy):
    report, messages = _run(_study())
    assert report.done == 2 and not messages
    manifests = _manifests(toy)
    assert set(manifests[0]["code_files"]) == {
        "toyproj/__init__.py",
        "toyproj/helpers.py",
        "toyproj/model.py",
    }
    assert manifests[0]["data_files"] == {
        "data/gain.csv": files_digest(toy / "data/gain.csv")
    }
    assert manifests[0]["code_fingerprint"] and manifests[0]["data_fingerprint"]
    plan_spec = json.loads((toy / "results/toy/runs.jsonl").read_text().splitlines()[0])
    assert plan_spec["code_fingerprint"] and plan_spec["data_fingerprint"]

    report, messages = _run(_study())  # nothing changed
    assert (report.done, report.skipped, report.stale) == (0, 2, 0) and not messages

    (toy / "toyproj/helpers.py").write_text("FACTOR = 3.0\n")
    frame = load_study(toy / "results/toy")
    assert frame["stale"].tolist() == [True, True]
    report, messages = _run(_study())
    assert (report.done, report.skipped, report.stale) == (2, 0, 2)
    assert messages == [
        "Study 'toy': 2 finished runs are stale (code changed in toyproj/helpers.py); "
        "running them again (stale='keep' / --keep-stale keeps them; "
        "'mango-scenario certify' marks runs as still valid)."
    ]
    assert "2 stale runs (code changed in toyproj/helpers.py) run again" in str(report)
    frame = load_study(toy / "results/toy")
    assert frame["stale"].tolist() == [False, False]
    assert frame["final_x"].tolist() == [9.0, 9.0]  # 3 steps x gain 3
    folder = toy / "results/toy/runs" / frame["run_id"][0]
    assert (folder / PREVIOUS_MANIFEST_FILE).is_file()


def test_resume_reruns_runs_whose_data_changed_and_keep_stale_keeps_them(toy):
    _run(_study())
    (toy / "data/gain.csv").write_text("gain\n5.0\n")
    report, messages = _run(_study(), stale="keep")
    assert (report.done, report.skipped, report.stale, report.stale_kept) == (
        0,
        2,
        2,
        True,
    )
    assert "data changed in data/gain.csv" in messages[0]
    assert "kept (stale='keep')" in messages[0]
    assert "2 stale runs (data changed in data/gain.csv) kept" in str(report)
    assert load_study(toy / "results/toy")["stale"].tolist() == [True, True]
    report, messages = _run(_study())
    assert (report.done, report.stale) == (2, 2)
    with pytest.raises(StudyError, match="stale must be 'rerun' or 'keep'"):
        _study().run(stale="ignore")


def test_scenario_definition_changes_make_runs_stale(toy):
    _run(_study())
    study = _study()
    study.scenario.record("x2", "unit", attr="x")
    report, messages = _run(study)
    assert report.stale == 2 and "the scenario definition changed" in messages[0]


def test_editing_code_after_the_import_refuses_in_process_runs(toy):
    study = _study()
    _run(study)
    (toy / "toyproj/model.py").write_text(MODEL.replace("self.x += self.gain", "pass"))
    with pytest.raises(
        StudyError, match=r"toyproj/model.py changed after this process"
    ):
        study.run(progress=False)
    report, _ = _run(_study())  # a fresh import runs the new code
    assert report.stale == 2
    assert load_study(toy / "results/toy")["final_x"].tolist() == [0.0, 0.0]
    assert changed_since_import(toy, objects=[_reimport().scenario]) == []


# ----------------------------------------------------------------- provenance


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_manifest_records_the_git_state_when_the_run_executes(toy, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(toy.parent))
    from mango.scenario import runner

    monkeypatch.setattr(runner, "_EXECUTION_GIT", {})
    _git(toy, "init", "-q")
    _git(toy, "add", "toyproj", "data")
    _git(toy, "commit", "-q", "-m", "first")
    plan = _study().plan()
    (toy / "toyproj/helpers.py").write_text("FACTOR = 2.0\n")
    _git(toy, "commit", "-q", "-am", "second")
    from mango.scenario.store import StudyStore

    StudyStore(toy / "results/toy").write_runs(plan.specs)
    spec = plan[0]
    run_one(spec)  # a fresh import, like a Slurm task hours later
    manifest = json.loads((Path(spec["out"]) / "manifest.json").read_text())
    assert manifest["planned_git"] == spec["git"]
    assert manifest["git"]["commit"] != spec["git"]["commit"]
    assert manifest["git"]["dirty"] is False
    frame = load_study(toy / "results/toy")
    assert frame["git_commit"][0] == manifest["git"]["commit"][:12]
    assert not frame["git_dirty"][0]


def test_manifest_provenance_has_slurm_ids_and_the_environment(toy, monkeypatch):
    monkeypatch.setenv("SLURM_JOB_ID", "4242")
    monkeypatch.setenv("SLURM_ARRAY_TASK_ID", "7")
    monkeypatch.setenv("SLURMD_NODENAME", "node7")
    _run(_study())
    provenance = _manifests(toy)[0]["provenance"]
    assert provenance["slurm"] == {
        "SLURM_JOB_ID": "4242",
        "SLURM_ARRAY_TASK_ID": "7",
        "SLURMD_NODENAME": "node7",
    }
    assert len(provenance["environment"]) == 16
    assert {"pyarrow", "scipy"} <= set(provenance["versions"])


# ----------------------------------------------------------------- run-one


def test_run_one_skips_done_runs_unless_forced_or_stale(toy, capsys):
    _run(_study())
    table = toy / "results/toy/runs.jsonl"
    folder = Path(json.loads(table.read_text().splitlines()[0])["out"])
    before = (folder / "manifest.json").read_text()
    outcome = run_one_from_file(table, 0)
    assert outcome["status"] == "done" and outcome["skipped"] is True
    assert (folder / "manifest.json").read_text() == before
    assert not (folder / PREVIOUS_MANIFEST_FILE).exists()

    code, out, _ = cli(capsys, "run-one", table, "--line", 0)
    assert code == 0 and "already done; skipped (--force runs it again)" in out

    code, out, _ = cli(capsys, "run-one", table, "--line", 0, "--force")
    assert code == 0 and " done in " in out
    assert json.loads((folder / PREVIOUS_MANIFEST_FILE).read_text()) == json.loads(
        before
    )

    (toy / "data/gain.csv").write_text("gain\n4.0\n")
    outcome = run_one_from_file(table, 1)
    assert outcome["status"] == "done" and outcome["stale"] is True


def test_run_one_runs_a_range_of_lines(toy, capsys):
    plan = _study().plan()
    from mango.scenario.store import StudyStore

    StudyStore(toy / "results/toy").write_runs(plan.specs)
    table = toy / "results/toy/runs.jsonl"
    code, out, _ = cli(capsys, "run-one", table, "--lines", "0:10")
    assert code == 0 and out.count(" done in ") == 2
    code, out, _ = cli(capsys, "run-one", table, "--lines", "1:2")
    assert code == 0 and "already done" in out
    code, _, err = cli(capsys, "run-one", table, "--lines", "2:1")
    assert code == 2 and "STOP must be greater than START" in err
    code, _, err = cli(capsys, "run-one", table, "--lines", "x")
    assert code == 2 and "--lines takes START:STOP" in err
    code, _, err = cli(capsys, "run-one", table, "--lines", "5:9")
    assert code == 1 and "line 5 is out of range" in err


def test_status_shows_stale_runs_and_the_git_mix(toy, capsys):
    _run(_study())
    (toy / "toyproj/helpers.py").write_text("FACTOR = 2.0\n")
    code, out, _ = cli(capsys, "status", "results/toy")
    assert code == 0
    assert "2 done, 0 failed, 0 pending, 2 stale" in out
    assert "2 done runs are stale (code changed in toyproj/helpers.py)" in out


def test_submodules_of_a_package_are_not_its_imports(toy):
    write(toy, "toyproj/unrelated.py", "X = 1\n")
    importlib.import_module("toyproj.unrelated")
    model = _reimport()
    importlib.import_module("toyproj.unrelated")  # an attribute of the package now
    assert "toyproj/unrelated.py" not in code_files(toy, objects=[model.scenario])
