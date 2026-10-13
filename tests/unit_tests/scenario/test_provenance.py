"""Operations and provenance of studies: edits while a study runs, re-certification,
explained re-plans, run warnings, CPU time, environment lists, git diffs, run
selectors, Slurm interpreters and validate/run parity (removals, DST coverage,
FromColumn reuse)."""

import json
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

pytest.importorskip("pandas")

from mango.scenario import (  # noqa: E402
    Scenario,
    ScenarioParams,
    Study,
    StudyError,
    load_study,
)
from mango.scenario.designs import Factor  # noqa: E402
from mango.scenario.slurm import SlurmExecutor  # noqa: E402
from mango.scenario.store import StudyStore, select_run  # noqa: E402

from . import test_cli_operations as _ops  # noqa: E402
from . import test_freshness as _fresh  # noqa: E402
from .cli_helpers import cli, isolated_fixture, write  # noqa: E402, F401
from .test_freshness import _reimport, _run  # noqa: E402


@pytest.fixture(name="houses")
def houses_fixture(isolated):
    """The project of test_cli_operations (a study over FromColumn households)."""
    write(isolated, "housing/__init__.py", "")
    write(isolated, "housing/model.py", _ops.MODEL)
    write(isolated, "data/households.csv", "kwh\n3000\n4500\n")
    write(isolated, "presets/base.yaml", "time: {horizon: 2h, step: 1h}\n")
    write(
        isolated,
        "studies/houses.yaml",
        _ops.STUDY.replace("base: presets/base.yaml\n", ""),
    )
    return isolated


@pytest.fixture(name="toy")
def toy_fixture(isolated):
    """The project of test_freshness (a package with model and helpers, a data file)."""
    write(isolated, "toyproj/__init__.py", "")
    write(isolated, "toyproj/helpers.py", _fresh.HELPERS)
    write(isolated, "toyproj/model.py", _fresh.MODEL)
    write(isolated, "data/gain.csv", "gain\n1.0\n")
    sys.path.insert(0, str(isolated))
    return isolated


def _forget(package):
    """Drop a project package from sys.modules, as a new process would not have it."""
    for name in [name for name in sys.modules if name.split(".")[0] == package]:
        del sys.modules[name]


def _toy_study(outcomes=None, **kwargs):
    model = _reimport()
    return Study(
        model.scenario,
        levers={"n": [1, 2]},
        seed=5,
        outcomes=outcomes or [model.final_x],
        out="results/toy",
        format="csv",
        **kwargs,
    )


def _manifest(folder, run_id):
    return json.loads((Path(folder) / "runs" / run_id / "manifest.json").read_text())


# ----------------------------------------------------------------- edits while running


def test_code_edited_while_a_study_runs_makes_its_runs_stale(toy):
    model = _reimport()
    edited = []

    def edit_once(run):  # an outcome runs inside the run: an edit "during" it
        if not edited:
            Path("toyproj/helpers.py").write_text("FACTOR = 20.0\n")
            edited.append(run.run_id)
        return 1.0

    study = _toy_study(outcomes={"x": model.final_x, "edit": edit_once})
    report, _ = _run(study)
    assert report.done == 2
    # both runs executed the imported (old) code: both record it and are stale
    assert report.changed_during_run == 2
    assert report.changed_files == "toyproj/helpers.py"
    assert "2 runs used code or data that changed while the study ran" in str(report)
    manifest = _manifest("results/toy", edited[0])
    assert manifest["changed_during_run"] == ["toyproj/helpers.py"]
    assert load_study("results/toy")["stale"].tolist() == [True, True]
    # a new process (re-import) runs them again with the new code
    report, messages = _run(_toy_study())
    assert (report.done, report.stale) == (2, 2)
    assert any("may not match" in m or "running them again" in m for m in messages)
    assert report.changed_during_run == 0


def test_a_manifest_records_the_code_as_imported(toy):
    from mango.scenario.runner import _execute

    study = _toy_study()
    spec = study.plan(out="results/toy")[0]
    # the file changes after the import: a run in this process uses the old code
    Path("toyproj/helpers.py").write_text("FACTOR = 30.0\n")
    outcome = _execute(spec, study.scenario, study._outcomes)
    assert outcome["changed_during_run"] == ["toyproj/helpers.py"]
    manifest = _manifest("results/toy", spec["run_id"])
    from mango.scenario.freshness import stale_reasons

    assert stale_reasons(manifest, roots=[str(Path.cwd())])


# ----------------------------------------------------------------- certify


def test_certify_marks_stale_runs_current_and_records_why(toy):
    report, _ = _run(_toy_study())
    assert report.done == 2
    Path("toyproj/helpers.py").write_text("FACTOR = 1.0\n\n\ndef unused():\n    pass\n")
    assert load_study("results/toy")["stale"].tolist() == [True, True]
    study = _toy_study()
    with pytest.raises(StudyError, match="certify needs a reason"):
        study.certify(reason=" ")
    first = study.certify(runs=[0], reason="added an unused helper")
    assert len(first) == 1
    assert load_study("results/toy")["stale"].tolist() == [False, True]
    rest = study.certify(reason="added an unused helper")
    assert len(rest) == 1 and rest != first
    assert study.certify(reason="again") == []  # nothing stale any more
    manifest = _manifest("results/toy", first[0])
    (entry,) = manifest["certified"]
    assert entry["reason"] == "added an unused helper"
    assert entry["changes"] == "code changed in toyproj/helpers.py"
    assert (Path("results/toy/runs") / first[0] / "manifest.prev.json").is_file()
    report, messages = _run(_toy_study())
    assert (report.done, report.skipped, report.stale) == (0, 2, 0) and not messages


def test_cli_rerun_names_changes_and_certifies_identical_replays(capsys, houses):
    code, _, err = cli(capsys, "run", "studies/houses.yaml", "-q")
    assert code == 0, err
    model = Path("housing/model.py")
    model.write_text(model.read_text() + "\n\ndef helper():\n    return 1\n")
    code, out, err = cli(capsys, "status", "results/houses")
    assert "2 done runs are stale (code changed in housing/model.py)" in out
    assert "mango-scenario certify results/houses --reason ...' keeps them" in out
    code, out, err = cli(capsys, "rerun", "results/houses", "0")
    assert code == 0 and out.startswith("identical")
    assert "note: changed since the run: code changed in housing/model.py" in err
    assert "--certify' records that it is still valid" in err
    code, out, err = cli(capsys, "rerun", "results/houses", "run_0", "--certify")
    assert code == 0 and "certified: run" in out, err
    code, out, err = cli(
        capsys, "certify", "results/houses", "--reason", "unused helper"
    )
    assert code == 0 and "certified 1 run of results/houses (unused helper)" in out
    code, out, _ = cli(capsys, "status", "results/houses")
    assert "stale" not in out.splitlines()[0]
    assert "2 runs certified as current after changes" in out
    code, out, _ = cli(capsys, "certify", "results/houses", "--reason", "x")
    assert "no stale done runs to certify" in out
    code, _, err = cli(capsys, "certify", "results/houses", "9", "--reason", "x")
    assert code == 2 and "has no run 9" in err


def test_cli_prints_the_stale_summary_before_the_first_run(capsys, houses):
    code, _, err = cli(capsys, "run", "studies/houses.yaml")
    assert code == 0, err
    model = Path("housing/model.py")
    model.write_text(model.read_text().replace("float(run", "1.0 * float(run"))
    _forget("housing")  # a new mango-scenario process imports the edited module
    code, out, err = cli(capsys, "run", "studies/houses.yaml")
    assert code == 0, err
    stale = err.index("warning: Study 'houses': 2 finished runs are stale")
    assert stale < err.index("[1/2]")
    assert "(cpu " in err  # the progress lines show CPU time


# ----------------------------------------------------------------- re-plans


TOY_EXTRA_FIELD = "    gain: float = 1.0\n    kind: str = 'li-ion'\n"


def test_a_changed_parameter_model_is_explained(toy, capsys):
    report, _ = _run(_toy_study())
    assert report.done == 2
    model = Path("toyproj/model.py")
    model.write_text(
        model.read_text().replace("    gain: float = 1.0\n", TOY_EXTRA_FIELD)
    )
    _reimport()
    code, out, _ = cli(capsys, "status", "results/toy")
    assert "the study definition now plans 2 runs (0 of them on disk)" in out
    assert "new field kind" in out
    assert "runs them again" not in out  # the old ids will not run again
    report, messages = _run(_toy_study())
    assert report.done == 2
    (message,) = [m for m in messages if "resolved parameters" in m]
    assert "(new field kind)" in message
    assert "the study now plans 2 runs, 2 of them with new ids" in message
    assert "--keep-stale cannot keep them" in message
    code, out, _ = cli(capsys, "status", "results/toy")
    assert "earlier plan" in out and "--set variant" not in out


def test_plan_counts_the_planned_runs_on_disk(capsys, houses):
    cli(capsys, "run", "studies/houses.yaml", "-q")
    code, out, _ = cli(capsys, "plan", "studies/houses.yaml")
    assert code == 0 and "2 of the 2 planned runs are done in" in out


# ----------------------------------------------------------------- warnings, CPU time


BIG_STUDY = """
scenario: housing.model:scenario
seed: 1
uncertainties:
  n: [3, 5]
outcomes: [housing.model:total]
out: results/houses
"""


def test_run_warnings_are_stored_and_shown(capsys, houses):
    write(houses, "studies/houses.yaml", BIG_STUDY)
    code, out, err = cli(capsys, "run", "studies/houses.yaml", "-q")
    assert code == 0, err
    assert "with warnings (stored in manifest.json" in out
    frame = load_study("results/houses")
    texts = [
        _manifest("results/houses", run_id).get("warnings", [])
        for run_id in frame["run_id"]
    ]
    assert all(any("rows are reused" in text for text in run) for run in texts)
    code, out, _ = cli(capsys, "status", "results/houses")
    assert "with warnings" in out.splitlines()[0]
    assert "rows are reused" in out
    status = Path("results/houses/status.csv").read_text().splitlines()
    assert status[0] == "index,run_id,status,wall_s,cpu_s,warnings,error"


REUSE_STUDY = """
scenario: housing.model:scenario
seed: 1
replications: 3
uncertainties:
  n: [3, 5]
outcomes: [housing.model:total]
out: results/reuse
"""


@pytest.mark.parametrize(
    "executor",
    [["--executor", "sequential"], ["--workers", "2", "--runs-per-worker", "3"]],
)
def test_every_run_of_a_process_keeps_its_row_reuse_warning(capsys, houses, executor):
    # the warning is kept per run, not once per process: a sequential study
    # and pool workers running several runs store it with every run
    write(houses, "studies/reuse.yaml", REUSE_STUDY)
    code, out, err = cli(capsys, "run", "studies/reuse.yaml", "-q", *executor)
    assert code == 0, err
    frame = load_study("results/reuse")
    assert len(frame) == 6
    for run_id in frame["run_id"]:
        texts = _manifest("results/reuse", run_id).get("warnings", [])
        assert sum("rows are reused" in text for text in texts) == 1, run_id
    code, out, _ = cli(capsys, "status", "results/reuse")
    assert "6 with warnings" in out.splitlines()[0]


def test_in_process_runs_each_keep_the_row_reuse_warning(houses):
    model = _reimport_houses()
    for _ in range(2):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = model.scenario.run(overrides={"n": 5}, seed=1)
        (text,) = [t for t in result.warnings if "rows are reused" in t]
        assert "has 2 rows" in text
        issued = [w for w in caught if "rows are reused" in str(w.message)]
        assert len(issued) == 1  # once per run, not once per entity
        assert issued[0].filename.endswith("model.py")  # the user's factory


def _reimport_houses():
    sys.path.insert(0, str(Path.cwd()))
    _forget("housing")
    import housing.model

    return housing.model


def test_runs_record_cpu_time_and_the_environment(toy):
    report, _ = _run(_toy_study())
    assert report.done == 2
    frame = load_study("results/toy")
    assert (frame["cpu_s"] >= 0).all()
    manifest = _manifest("results/toy", frame["run_id"][0])
    assert manifest["cpu_s"] >= 0 and manifest["stats"]["cpu_s"] >= 0
    # the run's own thread: idle helper threads (BLAS) do not count
    assert manifest["cpu_s"] <= manifest["wall_s"] + 0.05
    assert manifest["stats"]["cpu_s"] <= manifest["stats"]["wall_s"] + 0.05
    assert manifest["load_s"] >= 0
    environment = manifest["provenance"]["environment"]
    listing = Path("results/toy/environments") / f"{environment}.txt"
    assert listing.is_file()
    assert any(
        line.startswith("pydantic==") for line in listing.read_text().splitlines()
    )


def test_removed_overrides_are_listed_on_the_frame(toy):
    model = _reimport()
    study = Study(
        model.scenario,
        overrides={"-table": None, "gain": 2.0},
        seed=1,
        outcomes=[model.final_x],
        out="results/rm",
        format="csv",
    )
    _run(study)
    frame = load_study("results/rm")
    assert frame.removed_overrides == ("table",)
    assert "gain" in frame.override_columns


# ----------------------------------------------------------------- git


def _git(cwd, *args):
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(cwd),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_dirty_trees_warn_store_the_diff_and_can_be_refused(toy):
    _git(toy, "init", "-q")
    _git(toy, "add", "toyproj", "data")
    _git(toy, "commit", "-q", "-m", "init")
    Path("toyproj/helpers.py").write_text("FACTOR = 4.0\n")
    with pytest.raises(StudyError, match=r"uncommitted changes \(toyproj/helpers.py\)"):
        _toy_study().run(progress=False, require_clean=True)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _toy_study().run(out="results/where", progress=False)
    (where,) = [w for w in caught if "uncommitted changes" in str(w.message)]
    assert Path(where.filename).name == "test_provenance.py"  # not '<sys>:0'
    report, messages = _run(_toy_study())
    assert report.done == 2
    (message,) = [m for m in messages if "uncommitted changes" in m]
    assert "runs code or data with uncommitted changes (toyproj/helpers.py)" in message
    manifest = _manifest("results/toy", load_study("results/toy")["run_id"][0])
    assert manifest["git"]["dirty"] is True
    diff = Path("results/toy") / manifest["git"]["diff"]
    assert diff.is_file() and "FACTOR = 4.0" in diff.read_text()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_the_git_state_is_taken_before_the_run(toy):
    # an edit during the run must not show in the run's git diff
    from mango.scenario import runner

    _git(toy, "init", "-q")
    _git(toy, "add", "toyproj", "data")
    _git(toy, "commit", "-q", "-m", "init")
    Path("toyproj/helpers.py").write_text("FACTOR = 4.0\n")
    model = _reimport()

    def edit(run):
        Path("toyproj/helpers.py").write_text("FACTOR = 99.0\n")
        return 1.0

    study = _toy_study(outcomes={"x": model.final_x, "edit": edit})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = study.plan(out="results/toy")[0]
    runner._EXECUTION_GIT.clear()
    runner._EXECUTION_DIFF.clear()
    outcome = runner._execute(spec, None, study._outcomes)
    assert outcome["status"] == "done", outcome.get("error")
    manifest = _manifest("results/toy", spec["run_id"])
    diff = (Path("results/toy") / manifest["git"]["diff"]).read_text()
    assert "FACTOR = 4.0" in diff and "FACTOR = 99.0" not in diff


# ----------------------------------------------------------------- run selectors


SPECS = [
    {"index": 0, "run_id": "3f2a9c1b0d4e"},
    {"index": 1, "run_id": "07f769579319"},
    {"index": 2, "run_id": "896732c313ab"},
    {"index": 3, "run_id": "0777efee6df2"},
    {"index": 4, "run_id": "01da3fdf3984"},
    {"index": 5, "run_id": "a1b2c3d4e5f6"},
    {"index": 6, "run_id": "b1b2c3d4e5f6"},
    {"index": 7, "run_id": "c1b2c3d4e5f6"},
]


@pytest.mark.parametrize(
    "selector, index",
    [
        (3, 3),
        ("3", 3),
        ("run_7", 7),
        ("run-1", 1),
        ("run0003", 3),
        ("07f", 1),
        ("0777", 3),  # out of range as an index
        ("01", 4),
        ("8967", 2),
        ("3f2a", 0),
        ("00", 0),  # no id starts with it: an index with padding
    ],
)
def test_run_selectors(selector, index):
    assert select_run(SPECS, selector)["index"] == index


def test_run_selectors_explain_failures():
    with pytest.raises(StudyError, match="has no run 99 .runs 0..7. and no run id"):
        select_run(SPECS, "99")
    with pytest.raises(StudyError, match="has no run 12 .runs 0..7.\\.$"):
        select_run(SPECS, "run_12")
    with pytest.raises(StudyError, match="'07' is ambiguous"):
        select_run(SPECS, "07")  # 07f7... and 0777...: not read as index 7
    with pytest.raises(StudyError, match="no run id starts with it"):
        select_run(SPECS, "zz")


def test_rerun_takes_an_all_digit_id_prefix(capsys, houses):
    cli(capsys, "run", "studies/houses.yaml", "-q")
    specs = StudyStore("results/houses").read_runs()
    run_id = specs[1]["run_id"]
    code, out, err = cli(capsys, "rerun", "results/houses", run_id[:6])
    assert code == 0 and run_id in out, err


# ----------------------------------------------------------------- slurm


def test_slurm_tasks_use_the_interpreter_a_setup_activates():
    plain = SlurmExecutor(submit=False)
    assert plain.python == sys.executable
    activated = SlurmExecutor(setup=["source .venv/bin/activate"], submit=False)
    assert activated.python == "python"
    pinned = SlurmExecutor(setup=["module load x"], python="/opt/py", submit=False)
    assert pinned.python == "/opt/py"
    text = activated.script(2, Path("/p/results/s"), "/p")
    assert text.splitlines()[-1] == (
        'python -m mango.scenario.cli run-one results/s/slurm/pending.jsonl --line "$TASK"'
    )
    from mango.scenario._util import SLURM_VARIABLES

    assert "MANGO_TASK_OFFSET" in SLURM_VARIABLES


def test_cli_python_flag(capsys, houses):
    code, out, err = cli(
        capsys,
        "run",
        "studies/houses.yaml",
        "--executor",
        "slurm",
        "--no-submit",
        "--setup",
        "module load python",
        "--python",
        "python3.12",
    )
    assert code == 0, err
    script = Path("results/houses/slurm/array.sbatch").read_text()
    assert "python3.12 -m mango.scenario.cli run-one" in script


# ----------------------------------------------------------------- validate parity


REMOVAL_MODEL = """
from mango import Agent
from mango.scenario import Scenario, ScenarioParams


class Params(ScenarioParams):
    n: int = 1
    resource: str = "ambiguous"


class Unit(Agent):
    def handle_message(self, content, meta):
        pass


def make(e):
    if e.p.resource == "ambiguous":
        raise ValueError("resource 'ambiguous' is provided by several kits")
    return Unit()


scenario = Scenario("removal", params=Params)
scenario.time(horizon="2h", step="1h")
scenario.defaults({"resource": "kit_a"})
scenario.group("unit", "n", make)
"""


def test_validate_build_applies_removal_overrides_like_run(capsys, isolated):
    write(isolated, "removal_model.py", REMOVAL_MODEL)
    code, out, err = cli(capsys, "validate", "removal_model:scenario", "--build")
    assert code == 0, err
    for flag in ("--set", "-s"):
        code, out, err = cli(
            capsys, "validate", "removal_model:scenario", "--build", flag, "-resource"
        )
        assert code == 1 and "provided by several kits" in err, (out, err)
    code, _, err = cli(capsys, "run", "removal_model:scenario", "--set", "-resource")
    assert code == 1 and "provided by several kits" in err


DST_MODEL = """
from mango import Agent
from mango.scenario import Scenario


class Reader(Agent):
    def __init__(self, profile):
        super().__init__()
        self.profile = profile

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        self.value = self.profile.at(clock)


scenario = Scenario("dst")


@scenario.group("reader", 1)
def make(e):
    return Reader(e.profile("load"))
"""


def _dst_data(folder):
    rows = ["time,load"]
    import datetime as dt

    moment = dt.datetime(2024, 3, 30)
    while moment < dt.datetime(2024, 4, 2):  # naive local time, 15 min
        if not (moment.date() == dt.date(2024, 3, 31) and moment.hour == 2):
            rows.append(f"{moment:%Y-%m-%dT%H:%M},1")  # 02:00-02:59 does not exist
        moment += dt.timedelta(minutes=15)
    write(folder, "data/dst.csv", "\n".join(rows) + "\n")


def test_dated_profiles_are_checked_on_elapsed_time_across_dst(capsys, isolated):
    write(isolated, "dst_model.py", DST_MODEL)
    _dst_data(isolated)
    write(
        isolated,
        "presets/spring.yaml",
        "time: {start: '2024-03-30T00:00[Europe/Berlin]', horizon: 3d, step: 15min}\n"
        "profiles: {load: {path: data/dst.csv, column: load, time_column: time}}\n",
    )
    # the data ends at 2024-04-02T00:00+02:00: 71 h after the start; the run
    # steps 72 h (3d is elapsed time) and ends at 01:00+02:00
    code, out, err = cli(
        capsys, "validate", "dst_model:scenario", "-p", "presets/spring.yaml"
    )
    assert code == 1, out
    assert "error: profile 'load' has data from 2024-03-30T00:00:00+01:00 to " in err
    assert "2024-04-02T00:00:00+02:00, but the run spans" in err
    assert "to 2024-04-02T01:00:00+02:00" in err
    assert "warning: profile 'load'" not in err  # reported once, as the error
    from dst_model import scenario

    with pytest.raises(Exception) as info:
        scenario.run("presets/spring.yaml")
    text = str(info.value)
    assert "covers 2024-03-30T00:00:00+01:00 to 2024-04-02T00:00:00+02:00" in text
    assert "reads it at 2024-04-02T00:15:00+02:00" in text


def test_validate_fails_for_a_profile_of_other_dates(capsys, isolated):
    write(isolated, "dst_model.py", DST_MODEL)
    write(
        isolated,
        "data/jan.csv",
        "time,load\n2023-01-01T00:00,1\n2023-01-01T01:00,2\n2023-01-01T02:00,3\n",
    )
    write(
        isolated,
        "presets/july.yaml",
        "time: {start: '2023-07-01T00:00[Europe/Berlin]', horizon: 2h, step: 1h}\n"
        "profiles: {load: {path: data/jan.csv, column: load, time_column: time}}\n",
    )
    code, out, err = cli(
        capsys, "validate", "dst_model:scenario", "-p", "presets/july.yaml"
    )
    assert code == 1 and "OK" not in out
    assert "reading it outside its dates will fail" in err
    from dst_model import scenario

    with pytest.raises(Exception, match=r"reads it at 2023-07-01T00:00:00\+02:00"):
        scenario.run("presets/july.yaml")


def test_validate_warns_when_a_table_is_shorter_than_its_group(capsys, houses):
    code, out, err = cli(capsys, "validate", "housing.model:scenario", "--set", "n=5")
    assert code == 0, err
    assert (
        "warning: kwh: FromColumn('data/households.csv', 'kwh') has 2 rows, but group "
        "'house' has 5 entities" in err
    )
    write(houses, "studies/houses.yaml", BIG_STUDY)
    code, out, err = cli(capsys, "validate", "studies/houses.yaml")
    assert code == 0 and "has 2 rows, but group 'house' has 5 entities" in err
    assert "has 3 entities" not in err  # one message per table, the largest group
    code, out, err = cli(capsys, "validate", "housing.model:scenario")
    assert "FromColumn" not in err


# ----------------------------------------------------------------- polish


def test_seed_tree_is_exported_and_warnings_point_at_the_caller():
    from mango.scenario import SeedTree
    from mango.util.seeding import SeedTree as Original

    assert SeedTree is Original
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        Factor.parse("x", {"low": 2.0, "high": 6.0}, "uncertainty")
    (item,) = [w for w in caught if "labeled levels" in str(w.message)]
    assert item.filename == __file__


def test_params_level_comm_factories_are_named():
    class Params(ScenarioParams):
        pass

    scenario = Scenario("radio", params=Params)
    params = scenario.resolve_params(
        {"comm": {"kind": "custom", "factory": "radio_module:make"}}
    )
    assert scenario.factory_refs(params)["communication"] == (
        "radio_module:make (comm.factory)"
    )
    assert scenario.factory_refs()["communication"] is None
