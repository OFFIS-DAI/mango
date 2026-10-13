"""The mango-scenario command line tool (run in-process through cli.main)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from mango.scenario import cli as cli_module  # noqa: E402
from mango.scenario import load_study, params_schema  # noqa: E402

from . import script_fixtures as sfx  # noqa: E402
from . import yaml_fixtures as fx  # noqa: E402
from .cli_helpers import cli, isolated_fixture, write  # noqa: E402, F401

REF = fx.REF
SCRIPT_REF = sfx.__name__
ROOT = Path(__file__).resolve().parents[3]

BASE_PRESET = """
time: {start: "2024-06-01T00:00", horizon: 2h, step: 30min}
comm: {kind: simple, loss_percent: 0.0}
"""

# the side-by-side study (with the stand-in scenario of yaml_fixtures)
FLEET_COMPARISON = f"""
scenario: {REF}:ev_scenario
base: presets/base.yaml
seed: 2026
uncertainties:
  ev.n: [5, 10, 20]
  comm.loss_percent: {{ideal: 0.0, lossy: 0.1}}
levers:
  algorithm: [greedy, proportional]
replications: 10
outcomes: [{REF}:mean_soc]
out: results/fleet_comparison
"""

SMALL_STUDY = f"""
scenario: {REF}:ev_scenario
base: presets/base.yaml
seed: 7
uncertainties:
  ev.n: [1, 2]
replications: 2
outcomes: [{REF}:mean_soc]
out: results/small
"""


@pytest.fixture
def project(isolated):
    write(isolated, "presets/base.yaml", BASE_PRESET)
    write(isolated, "studies/fleet_comparison.yaml", FLEET_COMPARISON)
    write(isolated, "studies/small.yaml", SMALL_STUDY)
    return isolated


# --------------------------------------------------------------------------- usage


def test_help_lists_commands_with_examples(capsys):
    code, out, _ = cli(capsys, "--help")
    assert code == 0
    for command in (
        "validate",
        "plan",
        "run",
        "run-one",
        "rerun",
        "status",
        "schema",
        "new",
        "to-script",
        "form",
    ):
        assert command in out
    assert "exit codes: 0 success, 1 failure, 2 wrong usage" in out

    code, out, _ = cli(capsys, "run", "--help")
    assert code == 0
    assert "--executor slurm --partition short" in out
    assert "--workers" in out and "--seed" in out


def test_usage_errors_exit_with_2(capsys, project):
    assert cli(capsys)[0] == 2  # no command
    code, _, err = cli(capsys, "bogus")
    assert code == 2 and "invalid choice" in err
    code, _, err = cli(capsys, "run", "studies/small.yaml", "--partition", "short")
    assert code == 2
    assert "--partition only applies to --executor slurm" in err
    code, _, err = cli(capsys, "run", "studies/small.yaml", "--seed", "3")
    assert code == 2 and "--seed applies to single scenario runs" in err
    code, _, err = cli(capsys, "validate", "presets/base.yaml")
    assert code == 2 and "pass --scenario REF" in err
    code, _, err = cli(capsys, "plan", "missing.yaml")
    assert code == 2 and "No such file: missing.yaml" in err
    code, _, err = cli(capsys, "plan", "nonsense")
    assert code == 2 and "neither a scenario file" in err


def test_module_entry_points_run(tmp_path):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), *sys.path])}
    for module in ("mango.scenario", "mango.scenario.cli"):
        completed = subprocess.run(
            [sys.executable, "-m", module, "--help"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.startswith("usage: mango-scenario")


def test_console_script_is_declared():
    text = (ROOT / "pyproject.toml").read_text()
    assert 'mango-scenario = "mango.scenario.cli:main"' in text


# --------------------------------------------------------------------------- validate / plan


def test_validate_and_plan_the_side_by_side_study(capsys, project):
    code, out, err = cli(capsys, "validate", "studies/fleet_comparison.yaml")
    assert code == 0, err
    assert out.startswith(
        "OK: studies/fleet_comparison.yaml is a valid study: 120 runs (9 to 24 agents "
        f"each, 2 links), scenario {REF}:ev_scenario"
    )

    code, out, err = cli(capsys, "plan", "studies/fleet_comparison.yaml")
    assert code == 0, err
    assert out.splitlines()[0] == "120 runs, 9 to 24 agents each, 2 links"
    assert "Study 'fleet_comparison': 120 runs" in out
    assert "ev.n: 5 | 10 | 20" in out

    code, out, err = cli(
        capsys, "plan", "studies/fleet_comparison.yaml", "--set", "ev.n=3"
    )
    assert code == 0
    assert "120 runs" in out  # ev.n is a factor: its levels replace the override
    assert "warning: --set ev.n has no effect where the study varies ev.n" in err

    code, out, _ = cli(capsys, "plan", "studies/small.yaml", "--json")
    plan = json.loads(out)
    assert plan["runs"] == 4 and plan["summary"] == "5 to 6 agents each, 2 links"
    assert [row["ev.n"] for row in plan["run_table"]] == [1, 1, 2, 2]


def test_validate_reports_typos_readably(capsys, project):
    write(project, "studies/typo.yaml", SMALL_STUDY.replace("ev.n:", "ev.nn:"))
    code, out, err = cli(capsys, "validate", "studies/typo.yaml")
    assert code == 1
    assert out == ""
    assert "unknown factor paths" in err and "did you mean 'ev.n'" in err
    assert "Traceback" not in err

    code, _, err = cli(capsys, "--debug", "validate", "studies/typo.yaml")
    assert code == 1
    assert "Traceback" in err


def test_validate_a_preset_against_a_scenario(capsys, project):
    code, out, err = cli(
        capsys, "validate", "presets/base.yaml", "--scenario", f"{REF}:ev_scenario"
    )
    assert code == 0, err
    assert out.startswith("OK: presets/base.yaml is a valid preset for ")
    write(project, "presets/bad.yaml", "time: {horizon: 2h, stepp: 30min}\n")
    code, _, err = cli(
        capsys, "validate", "presets/bad.yaml", "--scenario", f"{REF}:ev_scenario"
    )
    assert code == 1
    assert "time.stepp" in err and "did you mean 'time.step'" in err


def test_validate_and_plan_a_scenario_file(capsys, project):
    write(project, "scenarios/c5.yaml", fx.C5_SCENARIO)
    code, out, err = cli(capsys, "validate", "scenarios/c5.yaml")
    assert code == 0, err
    assert out.startswith("OK: scenarios/c5.yaml is a valid scenario 'c5': 8 agents")

    code, out, _ = cli(capsys, "plan", "scenarios/c5.yaml", "--set", "ev.n=5")
    assert code == 0
    assert "Scenario 'c5': 10 agents in 3 groups" in out


def test_plan_prints_warnings_once(capsys, project):
    code, out, err = cli(
        capsys,
        "plan",
        f"{SCRIPT_REF}:scenario",
        "--set",
        "events.1.at=3h10min",
    )
    assert code == 0
    assert err.count("warning: event 'ping' at") == 1
    assert "is not on the 30min step grid" in err


def test_cwd_is_put_on_sys_path(capsys, isolated):
    write(
        isolated,
        "localmod.py",
        """
        from mango.scenario import Scenario
        scenario = Scenario("local")
        """,
    )
    code, out, err = cli(capsys, "plan", "localmod:scenario")
    assert code == 0, err
    assert sys.path[0] == str(isolated)
    assert "Scenario 'local': 0 agents" in out


# --------------------------------------------------------------------------- run / rerun / status


def test_run_a_single_scenario_and_replay_it(capsys, project):
    code, out, err = cli(
        capsys, "run", f"{SCRIPT_REF}:scenario", "--seed", "4", "--out", "runs/one"
    )
    assert code == 0, err
    assert "of scenario 'script_demo' done" in out
    folder = project / "runs" / "one"
    spec = json.loads((folder / "spec.json").read_text())
    assert spec["seed"] == {"entropy": 4, "spawn_key": []}
    assert spec["scenario"] == f"{SCRIPT_REF}:scenario"
    assert json.loads((folder / "manifest.json").read_text())["status"] == "done"

    code, out, err = cli(capsys, "rerun", "runs/one")
    assert code == 0, err
    assert out.startswith("identical: run ")

    # a replay that records other values: exit code 1, the differing variables
    spec["seed"] = {"entropy": 5, "spawn_key": []}
    (folder / "spec.json").write_text(json.dumps(spec))
    code, out, _ = cli(capsys, "rerun", "runs/one")
    assert code == 1
    assert out.startswith("differs: run ")
    assert "stored checksum" in out and "value: " in out


def test_single_run_default_folder_and_params(capsys, project):
    code, out, err = cli(
        capsys,
        "run",
        f"{REF}:ev_scenario",
        "-p",
        "presets/base.yaml",
        "--set",
        "ev.n=1",
        "--seed",
        "1",
        "--format",
        "csv",
    )
    assert code == 0, err
    folders = list((project / "results" / "ev_feeder").iterdir())
    assert len(folders) == 1
    assert (folders[0] / "timeseries.csv").is_file()
    assert folders[0].name in out


def test_a_failing_single_run_exits_1_and_points_to_the_traceback(capsys, project):
    code, out, err = cli(
        capsys, "run", f"{SCRIPT_REF}:failing_scenario", "--seed", "1", "-o", "runs/bad"
    )
    assert code == 1 and out == ""
    assert "failed: ValueError: boom" in err
    assert "traceback: runs/bad/error.txt" in err
    assert "make_broken" in (project / "runs" / "bad" / "error.txt").read_text()
    code, _, err = cli(capsys, "rerun", "runs/bad")
    assert code == 2 and "only done runs can be compared" in err

    code, _, err = cli(capsys, "run", f"{SCRIPT_REF}:scenario", "--set", "foo=1")
    assert code == 1
    assert "foo: unknown parameter" in err and "--debug" not in err


def test_run_status_rerun_and_run_one_of_a_study(capsys, project):
    code, out, err = cli(capsys, "run", "studies/small.yaml", "-q")
    assert code == 0, err
    assert out.startswith("4 runs: 4 done, 0 skipped, 0 failed")

    code, out, _ = cli(capsys, "run", "studies/small.yaml", "-q")
    assert out.startswith("4 runs: 0 done, 4 skipped")

    code, out, _ = cli(capsys, "status", "results/small")
    assert code == 0
    assert out.splitlines()[0] == "small: 4 runs: 4 done, 0 failed, 0 pending"

    # the side-by-side spelling: <study>/run_<index>; also <study> <index> and run ids
    code, out, err = cli(capsys, "rerun", "results/small/run_0003")
    assert code == 0, err
    assert out.startswith("identical: ")
    frame = load_study("results/small")
    run_id = frame.run_id[1]
    assert cli(capsys, "rerun", "results/small", "1")[1].startswith(
        f"identical: run {run_id}"
    )
    assert cli(capsys, "rerun", "results/small", run_id[:6])[0] == 0
    assert cli(capsys, "rerun", f"results/small/runs/{run_id}")[0] == 0
    code, _, err = cli(capsys, "rerun", "results/small/run_9")
    assert code == 2 and "has no run 9 (runs 0..3)" in err

    # run-one runs one line of the run table (the Slurm array entry point)
    import shutil

    shutil.rmtree(project / "results" / "small" / "runs" / run_id)
    code, out, _ = cli(capsys, "status", "results/small")
    assert "3 done, 0 failed, 1 pending" in out
    code, out, err = cli(capsys, "run-one", "results/small/runs.jsonl", "--line", "1")
    assert code == 0, err
    assert out.startswith(f"run 1 ({run_id}) done")
    code, out, _ = cli(capsys, "status", "results/small", "--write")
    assert "4 done" in out
    assert (project / "results" / "small" / "status.csv").is_file()

    code, _, err = cli(capsys, "run-one", "results/small/runs.jsonl", "--line", "9")
    assert code == 1 and "out of range" in err


def test_status_lists_failed_runs_and_exits_1(capsys, project):
    write(
        project,
        "studies/failing.yaml",
        SMALL_STUDY.replace("out: results/small", "out: results/failing"),
    )
    cli(capsys, "run", "studies/failing.yaml", "-q")
    manifest_file = next(
        (project / "results" / "failing" / "runs").glob("*/manifest.json")
    )
    manifest = json.loads(manifest_file.read_text())
    manifest.update(status="failed", error="ValueError: boom")
    manifest_file.write_text(json.dumps(manifest))

    code, out, _ = cli(capsys, "status", "results/failing")
    assert code == 1
    assert "3 done, 1 failed, 0 pending" in out
    assert "ValueError: boom" in out

    code, _, err = cli(capsys, "status", "presets")
    assert code == 2 and "is not a study folder" in err


def test_run_with_slurm_writes_the_array_job(capsys, project):
    code, out, err = cli(
        capsys,
        "run",
        "studies/small.yaml",
        "--executor",
        "slurm",
        "--partition",
        "short",
        "--time",
        "00:10:00",
        "--no-submit",
    )
    assert code == 0, err
    assert "4 pending in" in out and "not submitted" in out
    script = (project / "results" / "small" / "slurm" / "array.sbatch").read_text()
    assert "#SBATCH --partition=short" in script
    assert "#SBATCH --time=00:10:00" in script
    assert "-m mango.scenario.cli run-one" in script


def test_interrupt_exits_130(capsys, project, monkeypatch):
    def interrupted(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "cmd_plan", interrupted)
    code, _, err = cli(capsys, "plan", "studies/small.yaml")
    assert code == 130 and "interrupted" in err


def test_unexpected_errors_suggest_debug(capsys, project, monkeypatch):
    def broken(args):
        raise RuntimeError("factory crashed")

    monkeypatch.setattr(cli_module, "cmd_plan", broken)
    code, _, err = cli(capsys, "plan", "studies/small.yaml")
    assert code == 1
    assert "error: RuntimeError: factory crashed" in err
    assert "--debug" in err


# --------------------------------------------------------------------------- schema / to-script / form


def test_schema_command(capsys, project):
    code, out, _ = cli(capsys, "schema", f"{REF}:ev_scenario")
    assert code == 0
    assert json.loads(out) == json.loads(json.dumps(params_schema(fx.ev_scenario)))

    code, _, err = cli(capsys, "schema", "studies/small.yaml", "-o", "out/schema.json")
    assert code == 0 and "wrote the params schema to out/schema.json" in err
    assert (
        "grid"
        in json.loads((project / "out" / "schema.json").read_text())["properties"]
    )

    code, out, _ = cli(capsys, "schema", "--kind", "study-file")
    assert json.loads(out)["title"] == "mango study file"
    code, out, _ = cli(capsys, "schema", "--kind", "scenario-file")
    assert json.loads(out)["additionalProperties"] is True
    assert cli(capsys, "schema")[0] == 2
    assert cli(capsys, "schema", "x:y", "--kind", "study-file")[0] == 2


def test_to_script_command(capsys, project):
    code, out, err = cli(capsys, "to-script", f"{SCRIPT_REF}:scenario", "--seed", "3")
    assert code == 0, err
    assert out.startswith('"""\nPlain mango script of scenario \'script_demo\'')
    assert "SEED = 3" in out and err == ""

    code, _, err = cli(
        capsys,
        "to-script",
        f"{REF}:ev_scenario",
        "-p",
        "presets/base.yaml",
        "--seed",
        "1",
        "-o",
        "ev_script.py",
    )
    assert code == 0
    assert "wrote the script to ev_script.py" in err
    assert "placeholder" not in err  # the kit's closures and behavior are written out
    compile((project / "ev_script.py").read_text(), "ev_script.py", "exec")


def test_form_command(capsys, project):
    code, _, err = cli(capsys, "form", f"{REF}:ev_scenario")
    assert code == 0
    assert "wrote the form to ev_feeder_form.html" in err
    page = (project / "ev_feeder_form.html").read_text()
    assert page.startswith("<!doctype html>") and "ev_feeder parameters" in page

    code, _, _ = cli(
        capsys, "form", f"{REF}:ev_scenario", "-o", "f.html", "--title", "EV"
    )
    assert code == 0
    assert "<title>EV</title>" in (project / "f.html").read_text()


def _form_data(path):
    import re

    text = Path(path).read_text()
    return json.loads(
        re.search(r'id="mango-form-data">(.*?)</script>', text, re.S).group(1)
    )


def test_form_starts_from_presets_and_study_bases(capsys, project):
    code, _, err = cli(
        capsys,
        "form",
        f"{REF}:ev_scenario",
        "-p",
        "presets/base.yaml",
        "--set",
        "ev.n=7",
        "-o",
        "preset.html",
    )
    assert code == 0, err
    data = _form_data(project / "preset.html")
    assert data["start"]["time"]["horizon"] == "2h"  # from presets/base.yaml
    assert data["start"]["ev"]["n"] == 7
    assert data["framework"][:2] == ["seed", "time"]

    code, _, err = cli(capsys, "form", "studies/small.yaml", "-o", "study.html")
    assert code == 0, err
    assert "starts from the study's base presets and overrides" in err
    study = _form_data(project / "study.html")
    assert study["start"]["time"]["horizon"] == "2h"
    cli(capsys, "form", f"{REF}:ev_scenario", "-o", "scenario.html")
    assert "start" not in _form_data(project / "scenario.html")


def test_file_kind_classifies_files(project):
    write(project, "scenarios/c5.yaml", fx.C5_SCENARIO)
    write(
        project, "studies/child.yaml", "extends: studies/small.yaml\nreplications: 1\n"
    )
    assert cli_module.file_kind("studies/small.yaml") == "study"
    assert cli_module.file_kind("studies/child.yaml") == "study"
    assert cli_module.file_kind("scenarios/c5.yaml") == "scenario"
    assert cli_module.file_kind("presets/base.yaml") == "preset"
    with pytest.raises(cli_module.UsageError, match="No such file"):
        cli_module.file_kind("nope.yaml")


def test_debug_is_accepted_after_the_command(capsys, isolated):
    code, _, err = cli(capsys, "validate", "missing.yaml", "--debug")
    assert code == 2 and "Traceback" in err and "No such file" in err
    code, _, err = cli(capsys, "--debug", "validate", "missing.yaml")
    assert code == 2 and "Traceback" in err
    code, _, err = cli(capsys, "validate", "missing.yaml")
    assert code == 2 and "Traceback" not in err


def test_a_closed_stdout_ends_the_command_quietly(capsys, isolated, monkeypatch):
    class ClosedPipe:
        def write(self, text):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            raise BrokenPipeError(32, "Broken pipe")

        def fileno(self):
            raise ValueError("no descriptor")

    write(isolated, "s.yaml", f"params: {fx.REF}:Params\n")
    monkeypatch.setattr(sys, "stdout", ClosedPipe())
    code = cli_module.main(["plan", "s.yaml"])
    monkeypatch.undo()
    assert code == 0
    assert "BrokenPipeError" not in capsys.readouterr().err


def test_new_force_help_says_files_are_overwritten(capsys):
    code = cli_module.main(["new", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert code == 0 and "overwriting files of the same name" in out


def test_design_warnings_are_printed_as_cli_warnings(capsys, project):
    pytest.importorskip("scipy")
    write(
        project,
        "studies/sobol3.yaml",
        "extends: small.yaml\nuncertainties: {gain: {range: [0.1, 0.2]}}\n"
        "design: {sobol: {n: 3}}\nreplications: 1\n",
    )
    code, out, err = cli(capsys, "validate", "studies/sobol3.yaml")
    assert code == 0, err
    assert "warning: Sobol: n=3 is not a power of two; using n=4" in err
    assert "designs.py" not in err and "UserWarning" not in err


def test_every_slurm_setting_reaches_the_executor():
    import argparse

    values = {
        "partition": "short",
        "time": "00:10:00",
        "mem": "2G",
        "account": "grid",
        "qos": "low",
        "cpus_per_task": 2,
        "runs_per_task": 4,
        "max_array_size": 500,
    }
    args = argparse.Namespace(
        no_submit=True,
        array_parallelism=3,
        extra_sbatch=["--constraint=ib"],
        setup=["module load python"],
        python=None,
        **values,
    )
    study = argparse.Namespace(executor={"slurm": {"partition": "long", "mem": "8G"}})
    executor = cli_module._slurm_executor(args, study)
    assert executor.submit is False and executor.array_parallelism == 3
    assert all(getattr(executor, key) == value for key, value in values.items())
    assert executor.extra_sbatch == ("--constraint=ib",)
    assert executor.setup == ("module load python",)
    # with setup lines the tasks run the 'python' the setup puts on PATH
    assert executor.python == "python"
    # the study file's settings fill in what the flags leave out
    empty = argparse.Namespace(
        no_submit=False, **dict.fromkeys(cli_module._SLURM_SETTINGS)
    )
    from_file = cli_module._slurm_executor(empty, study)
    assert (from_file.partition, from_file.mem) == ("long", "8G")
    assert set(cli_module._SLURM_OPTIONS) == {*cli_module._SLURM_SETTINGS, "no_submit"}
