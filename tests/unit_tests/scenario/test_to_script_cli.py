"""mango-scenario to-script: placeholder counts, study runs as targets, seeds, forms."""

import json
import runpy
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from mango.scenario import ScenarioError  # noqa: E402
from mango.scenario.result import frame_checksum, tidy_frame  # noqa: E402

from . import script_fixtures as sfx  # noqa: E402
from . import yaml_fixtures as fx  # noqa: E402
from .cli_helpers import cli, isolated_fixture, write  # noqa: E402, F401

REF = fx.REF
SCRIPT_REF = sfx.__name__

STUDY = f"""
scenario: {REF}:ev_scenario
seed: 7
uncertainties:
  ev.n: [1, 2]
replications: 2
out: results/small
"""


@pytest.fixture
def project(isolated):
    write(isolated, "studies/small.yaml", STUDY)
    return isolated


def replay(path: Path, calendar=False):
    namespace = runpy.run_path(str(path), run_name="replayed")
    rows = namespace["main"]()
    if "PHASES" in namespace:
        phases = [(start, name) for start, name, _ in namespace["PHASES"]]
    else:
        phases = [(0.0, rows[0]["phase"])]
    return frame_checksum(
        tidy_frame(rows, run_id="x", phases=phases, calendar=calendar)
    )


def test_reported_placeholder_count_matches_the_docstring(capsys, isolated):
    code, out, err = cli(
        capsys, "to-script", f"{SCRIPT_REF}:opaque_closure_scenario", "--seed", "1"
    )
    assert code == 0, err
    assert "note: 1 placeholder for code that could not be written out" in err
    assert "  - factory of group 'hub'" in err
    assert "\n1 PLACEHOLDER(s) stand for" in out
    assert out.count("= Placeholder(") == 1


def test_a_kit_resource_that_cannot_be_rebuilt_is_one_counted_placeholder():
    script = sfx.opaque_resource_scenario.to_script(seed=1)

    assert script.placeholders == ("resource 'kit.ids' (provided by 'kit')",)
    assert "\n1 PLACEHOLDER(s) stand for" in script
    assert "ctx.provide('kit.ids', resource_placeholder)" in script


def test_to_script_of_a_run_folder_replays_that_run(capsys, project):
    code, _, err = cli(capsys, "run", "studies/small.yaml", "-q")
    assert code == 0, err
    from mango.scenario import load_study

    frame = load_study("results/small")
    stored = json.loads(
        (Path("results/small/runs") / frame["run_id"][3] / "manifest.json").read_text()
    )["checksum"]

    code, _, err = cli(capsys, "to-script", "results/small/run_3", "-o", "run3.py")
    assert code == 0, err
    assert "replays run 3" in err
    assert replay(Path("run3.py")) == stored
    capsys.readouterr()

    # the study folder with --run, and the run folder as --run alone
    code, out, err = cli(capsys, "to-script", "results/small", "--run", "3")
    assert code == 0, err
    assert out == Path("run3.py").read_text()
    code, out2, err = cli(capsys, "to-script", "--run", "results/small/run_3")
    assert code == 0, err
    assert out2 == out
    # the study file with --run: run 3 of its plan (same seed: the study's)
    code, out3, err = cli(capsys, "to-script", "studies/small.yaml", "--run", "3")
    assert code == 0, err
    assert out3 == out

    code, _, err = cli(capsys, "to-script", "results/small/run_3", "--seed", "1")
    assert code == 2 and "the run fixes its parameters and seed" in err
    code, _, err = cli(capsys, "to-script", "studies/small.yaml", "--run", "9")
    assert code == 2 and "has no run 9 (runs 0..3)" in err
    code, _, err = cli(capsys, "to-script", f"{REF}:ev_scenario", "--run", "1")
    assert code == 2 and "--run picks a run of a study file or a study folder" in err


def test_a_study_file_without_run_says_it_writes_the_base_run(capsys, project):
    code, _, err = cli(capsys, "to-script", "studies/small.yaml", "-o", "base.py")
    assert code == 0, err
    assert "not a study run; --run N writes run N of the plan" in err


def test_seed_accepts_a_seed_dict_as_json(capsys, project):
    seed = {"entropy": 7, "spawn_key": [2]}
    code, out, err = cli(
        capsys,
        "to-script",
        f"{SCRIPT_REF}:closure_scenario",
        "--seed",
        json.dumps(seed),
    )
    assert code == 0, err
    assert "'spawn_key': [2]" in out
    assert out == sfx.closure_scenario.to_script(seed=seed)
    code, _, err = cli(capsys, "to-script", f"{SCRIPT_REF}:scenario", "--seed", "x")
    assert code == 2 and "expected an int or a seed dict as JSON" in err


def test_lean_and_full_flags(capsys, project):
    code, lean, err = cli(capsys, "to-script", f"{SCRIPT_REF}:closure_scenario")
    assert code == 0, err
    assert "Lean form" in lean and "PHASES" not in lean
    code, full, _ = cli(capsys, "to-script", f"{SCRIPT_REF}:closure_scenario", "--full")
    assert "Lean form" not in full and "PHASES = " in full
    code, _, err = cli(capsys, "to-script", f"{SCRIPT_REF}:scenario", "--lean")
    assert code == 1
    assert "cannot be written as a lean script: it uses phases, events" in err


def test_lean_true_raises_for_scenarios_that_need_the_full_form():
    with pytest.raises(ScenarioError, match="kits"):
        fx.ev_scenario.to_script(seed=1, lean=True)
    assert not fx.ev_scenario.to_script(seed=1).lean


def test_to_script_writes_aggregated_and_windowed_recorders(capsys, isolated):
    from . import test_script

    ref = f"{test_script.__name__}:reduced_scenario"
    for form in ("--lean", "--full"):
        code, _, err = cli(capsys, "to-script", ref, "--seed", "2", form, "-o", "r.py")
        assert code == 0, err
        assert "placeholder" not in err
        assert (
            replay(Path("r.py")) == test_script.reduced_scenario.run(seed=2).checksum()
        )


def test_scripts_with_runtime_features_run_from_a_subfolder(capsys, isolated):
    """The reported crashes (N06) and a script written into a subfolder (N29)."""
    from . import test_script_runtime_features as features

    for name in ("relinking", "population", "layered"):
        code, _, err = cli(
            capsys,
            "to-script",
            f"{features.__name__}:{name}",
            "--seed",
            "1",
            "-o",
            f"archive/{name}.py",
        )
        assert code == 0, err
        assert "placeholder" not in err
        script = Path(f"archive/{name}.py").read_text(encoding="utf-8")
        assert "PROJECT = Path(__file__).resolve().parents[1]" in script
        rows = runpy.run_path(f"archive/{name}.py", run_name="replayed")["main"]()
        run = getattr(features, name).run(seed=1)
        assert len(rows) == len(run.frame)
    capsys.readouterr()
