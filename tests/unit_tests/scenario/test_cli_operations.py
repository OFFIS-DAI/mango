"""mango-scenario: validate pre-flights (references, data files, --build), study run
options (--runs-per-worker, --keep-stale, Slurm flags) and rerun's project discovery."""

import json
import os
import shutil
from pathlib import Path

import pytest

pytest.importorskip("pandas")

from .cli_helpers import cli, isolated_fixture, write  # noqa: E402, F401

MODEL = """
from mango import Agent
from mango.scenario import Dist, FromColumn, Scenario, ScenarioParams


class Params(ScenarioParams):
    n: int = 2
    kwh: Dist = FromColumn("data/households.csv", "kwh")


class House(Agent):
    def __init__(self, kwh):
        super().__init__()
        self.kwh = kwh

    def handle_message(self, content, meta):
        pass


def make(e):
    return House(e.draw(e.p.kwh))


scenario = Scenario("houses", params=Params)
scenario.time(horizon="2h", step="1h")
scenario.group("house", "n", make)
scenario.record("kwh", "house", attr="kwh")


def total(run):
    return float(run.ts("kwh").sum())
"""

PRESET = """
time: {start: "2024-06-01T00:00", horizon: 2h, step: 1h}
profiles:
  load: {source: csv, path: data/load.csv, column: load, time_column: time}
"""

STUDY = """
scenario: housing.model:scenario
base: presets/base.yaml
seed: 1
uncertainties:
  n: [1, 2]
outcomes: [housing.model:total]
out: results/houses
"""


@pytest.fixture
def houses(isolated):
    write(isolated, "housing/__init__.py", "")
    write(isolated, "housing/model.py", MODEL)
    write(isolated, "data/households.csv", "kwh\n3000\n4500\n")
    write(
        isolated,
        "data/load.csv",
        "time,load\n2024-06-01T00:00,1.0\n2024-06-01T01:00,2.0\n2024-06-01T02:00,1.5\n",
    )
    write(isolated, "presets/base.yaml", PRESET)
    write(isolated, "studies/houses.yaml", STUDY)
    return isolated


# ----------------------------------------------------------------- validate


def test_validate_accepts_scenario_references(capsys, houses):
    code, out, err = cli(capsys, "validate", "housing.model:scenario")
    assert code == 0, err
    assert out.startswith("OK: housing.model:scenario is a valid scenario 'houses'")
    code, out, _ = cli(capsys, "validate", "housing.model:scenario", "--set", "n=3")
    assert code == 0 and "3 agents" in out
    code, _, err = cli(capsys, "validate", "housing.nope:scenario")
    assert code == 1 and "Cannot load the scenario" in err
    code, _, err = cli(capsys, "validate", "nonsense")
    assert code == 2 and "neither a scenario file" in err


def test_validate_loads_every_data_file(capsys, houses):
    code, _, err = cli(
        capsys, "validate", "housing.model:scenario", "--set", "kwh.column=kwhh"
    )
    assert code == 1
    assert "error: data file: kwh:" in err
    assert "has no column 'kwhh'. Did you mean 'kwh'?" in err
    code, _, err = cli(
        capsys,
        "validate",
        "housing.model:scenario",
        "-p",
        "presets/base.yaml",
        "--set",
        "profiles.load.path=data/missing.csv",
        "--set",
        "kwh.path=data/nope.csv",
    )
    assert code == 1
    assert err.count("error: data file:") == 2  # every problem at once
    assert "profiles.load:" in err and "kwh:" in err
    write(houses, "data/bad_time.csv", "time,load\nmonday,1.0\n")
    code, _, err = cli(
        capsys,
        "validate",
        "housing.model:scenario",
        "-p",
        "presets/base.yaml",
        "--set",
        "profiles.load.path=data/bad_time.csv",
    )
    assert code == 1 and "time_column 'time'" in err


def test_validate_a_study_checks_the_data_of_every_run(capsys, houses):
    code, out, err = cli(capsys, "validate", "studies/houses.yaml")
    assert code == 0, err
    assert "data files of 2 distinct parameter sets checked" in out
    assert "--build builds run 0" in out
    code, _, err = cli(
        capsys, "validate", "studies/houses.yaml", "--set", "kwh.column=kw"
    )
    assert code == 1
    assert "kwh (every run):" in err
    assert "1 data file problem would fail runs" in err


def test_validate_build_runs_one_step(capsys, houses):
    code, out, err = cli(capsys, "validate", "studies/houses.yaml", "--build")
    assert code == 0, err
    assert "run built (1 agent), one step taken and" in out
    import sys

    for name in [name for name in sys.modules if name.startswith("housing")]:
        del sys.modules[name]
    write(houses, "housing/model.py", MODEL.replace('attr="kwh"', 'attr="kwhh"'))
    for cache in (houses / "housing").glob("__pycache__"):
        shutil.rmtree(cache)
    code, _, err = cli(capsys, "validate", "housing.model:scenario", "--build")
    assert code == 1 and "kwhh" in err


def test_validate_build_checks_recorders_in_a_warm_up_that_records_nothing(
    capsys, houses
):
    write(
        houses,
        "presets/warm.yaml",
        "phases: [{name: warmup, until: 1h, record: false}]\n",
    )
    code, out, err = cli(
        capsys,
        "validate",
        "housing.model:scenario",
        "-p",
        "presets/warm.yaml",
        "--build",
    )
    assert code == 0, err
    assert "0 values recorded; 1 recorder evaluated (2 values)" in out
    import sys

    for name in [name for name in sys.modules if name.startswith("housing")]:
        del sys.modules[name]
    write(houses, "housing/model.py", MODEL.replace('attr="kwh"', 'attr="kwhh"'))
    for cache in (houses / "housing").glob("__pycache__"):
        shutil.rmtree(cache)
    code, _, err = cli(
        capsys,
        "validate",
        "housing.model:scenario",
        "-p",
        "presets/warm.yaml",
        "--build",
    )
    assert code == 1
    assert "'House' object has no attribute 'kwhh'" in err
    if sys.version_info >= (3, 12):  # Python's own hint survives
        assert "Did you mean: 'kwh'?" in err
    assert "while recording 'kwh' of entity 'house_0'" in err


# ----------------------------------------------------------------- run options


def test_run_options_of_studies(capsys, houses):
    code, out, err = cli(capsys, "run", "studies/houses.yaml", "-q")
    assert code == 0, err
    write(houses, "data/households.csv", "kwh\n1000\n1500\n")
    code, out, err = cli(capsys, "run", "studies/houses.yaml", "-q", "--keep-stale")
    assert code == 0
    assert "warning: Study 'houses': 2 finished runs are stale (data changed in " in err
    assert "2 stale runs (data changed in data/households.csv) kept" in out
    code, out, err = cli(capsys, "run", "studies/houses.yaml", "-q")
    assert code == 0 and "running them again" in err and "2 done" in out
    code, _, err = cli(capsys, "run", "studies/houses.yaml", "--runs-per-worker", "5")
    assert code == 1 and "runs_per_worker=5 applies to the managed process pool" in err
    code, _, err = cli(capsys, "run", "housing.model:scenario", "--keep-stale")
    assert code == 2 and "--keep-stale applies to study files" in err
    code, _, err = cli(
        capsys, "run", "studies/houses.yaml", "--executor", "slurm", "--workers", "2"
    )
    assert code == 2 and "--runs-per-task" in err


@pytest.mark.skipif(os.name == "nt", reason="the Slurm script is bash")
def test_slurm_flags_and_the_study_files_executor_block(capsys, houses):
    write(
        houses,
        "studies/cluster.yaml",
        STUDY.replace("out: results/houses", "out: results/cluster")
        + "executor:\n  kind: slurm\n  slurm: {partition: short, setup: [module load py]}\n",
    )
    code, out, err = cli(
        capsys,
        "run",
        "studies/cluster.yaml",
        "--no-submit",
        "--account",
        "grid",
        "--runs-per-task",
        "2",
        "--extra-sbatch=--constraint=ib",
        "--setup",
        "source .venv/bin/activate",
    )
    assert code == 0, err
    script = (houses / "results/cluster/slurm/array.sbatch").read_text()
    assert "#SBATCH --partition=short" in script  # from the study file
    assert "#SBATCH --account=grid" in script and "#SBATCH --constraint=ib" in script
    assert "source .venv/bin/activate" in script  # --setup replaces the file's
    assert "module load py" not in script
    assert '--lines "$(( TASK * 2 )):$(( TASK * 2 + 2 ))"' in script


# ----------------------------------------------------------------- rerun


def test_rerun_finds_a_moved_project_and_says_the_definition_changed(capsys, houses):
    code, _, err = cli(capsys, "run", "studies/houses.yaml", "-q")
    assert code == 0, err
    moved = houses / "moved"
    moved.mkdir()
    for name in ("housing", "data", "presets", "studies", "results"):
        shutil.move(str(houses / name), str(moved / name))
    import sys

    for name in [name for name in sys.modules if name.startswith("housing")]:
        del sys.modules[name]
    sys.path[:] = [entry for entry in sys.path if entry != str(houses)]
    other = houses / "elsewhere"
    other.mkdir()
    os.chdir(other)
    code, out, err = cli(capsys, "rerun", moved / "results/houses", "0")
    assert code == 0, err
    assert f"note: the recorded project root {houses} does not hold" in err
    assert f"using {moved}, which does" in err
    assert out.startswith("identical: run ")

    for name in [name for name in sys.modules if name.startswith("housing")]:
        del sys.modules[name]
    write(
        moved,
        "housing/model.py",
        MODEL + '\nscenario.record("kwh2", "house", attr="kwh")\n',
    )
    for cache in (moved / "housing").glob("__pycache__"):
        shutil.rmtree(cache)
    code, out, err = cli(capsys, "rerun", moved / "results/houses", "0")
    assert code == 1 and out.startswith("differs: ")
    assert "differs from the definition run" in err
    assert "the replay will probably differ" in err
    assert "workers=1" not in err


def test_new_recommends_sequential_runs(capsys, tmp_path):
    code, out, _ = cli(capsys, "new", tmp_path / "proj")
    assert code == 0
    assert "mango-scenario run study.yaml\n" in out
    assert "--runs-per-worker" in out


def test_run_one_reports_outcome_errors(capsys, houses):
    write(houses, "housing/extra.py", "def broken(run):\n    raise KeyError('x')\n")
    write(
        houses,
        "studies/broken.yaml",
        STUDY.replace("out: results/houses", "out: results/broken").replace(
            "outcomes: [housing.model:total]", "outcomes: [housing.extra:broken]"
        ),
    )
    code, out, err = cli(capsys, "run", "studies/broken.yaml", "-q")
    assert code == 0 and "(2 with outcome errors)" in out
    table = Path("results/broken/runs.jsonl")
    spec = json.loads(table.read_text().splitlines()[0])
    shutil.rmtree(Path(spec["out"]))
    code, out, err = cli(capsys, "run-one", table, "--line", "0")
    assert code == 0 and "outcome 'broken' failed: KeyError" in err
