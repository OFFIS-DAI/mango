"""The project templates of `mango-scenario new`: created, validated, planned and run."""

import fnmatch
import json
import runpy
import sys
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from mango.scenario import load_study  # noqa: E402
from mango.scenario.templates import create_project  # noqa: E402

from .cli_helpers import cli, isolated_fixture  # noqa: E402, F401

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = ROOT / "mango" / "scenario" / "templates"

EXPECTED = [
    ".gitignore",
    "README.md",
    "agents/__init__.py",
    "agents/sensors.py",
    "data/temperature.csv",
    "kits/__init__.py",
    "kits/field.py",
    "outcomes.py",
    "presets/base.yaml",
    "scenario.yaml",
    "studies/demo.yaml",
]

MINIMAL = [".gitignore", "README.md", "model.py", "presets/base.yaml", "study.yaml"]


def test_create_project_copies_the_template(tmp_path):
    created = create_project(tmp_path / "demo", template="full")

    names = sorted(path.relative_to(tmp_path / "demo").as_posix() for path in created)
    assert names == sorted(EXPECTED)  # the study file is named after the project
    readme = (tmp_path / "demo" / "README.md").read_text()
    assert readme.startswith("# demo\n")
    assert "name: demo" in (tmp_path / "demo" / "scenario.yaml").read_text()
    assert not list((tmp_path / "demo").rglob("*.tmpl"))
    for path in created:
        assert "__PROJECT__" not in path.read_text()


def test_create_project_refuses_non_empty_folders(tmp_path):
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "notes.txt").write_text("keep")
    with pytest.raises(FileExistsError, match="is not empty"):
        create_project(tmp_path / "busy")
    created = create_project(
        tmp_path / "busy", name="busy_project", force=True, template="full"
    )
    assert (tmp_path / "busy" / "notes.txt").read_text() == "keep"
    assert len(created) == len(EXPECTED)
    assert (tmp_path / "busy" / "studies" / "busy_project.yaml").is_file()
    assert "# busy_project" in (tmp_path / "busy" / "README.md").read_text()
    (tmp_path / "file").write_text("x")
    with pytest.raises(FileExistsError, match="not a folder"):
        create_project(tmp_path / "file")


def test_new_then_validate_plan_run_status_rerun(capsys, isolated):
    code, out, err = cli(capsys, "new", "sensors", "--template", "full")
    assert code == 0, err
    assert "Created 11 files in sensors/ (template 'full')" in out
    assert "mango-scenario validate studies/sensors.yaml" in out
    code, _, err = cli(capsys, "new", "sensors")
    assert code == 2 and "is not empty" in err

    project = isolated / "sensors"
    import os

    os.chdir(project)
    code, out, err = cli(capsys, "validate", "studies/sensors.yaml")
    assert code == 0, err
    assert out.startswith(
        "OK: studies/sensors.yaml is a valid study: 8 runs (4 to 9 agents each, 1 link)"
    )

    code, out, err = cli(capsys, "plan", "studies/sensors.yaml")
    assert code == 0, err
    assert out.splitlines()[0] == "8 runs, 4 to 9 agents each, 1 link"
    assert "field.n: 3 | 8" in out

    code, out, err = cli(capsys, "run", "studies/sensors.yaml", "-q")
    assert code == 0, err
    assert out.startswith("8 runs: 8 done, 0 skipped, 0 failed")
    frame = load_study("results/sensors")
    assert frame["mean_abs_error"].notna().all()
    noisy = frame.groupby("field.noise_std")["mean_abs_error"].mean()
    assert noisy[1.0] > noisy[0.2]  # noisier sensors estimate worse

    code, out, _ = cli(capsys, "status", "results/sensors")
    assert out.startswith("sensors: 8 runs: 8 done, 0 failed, 0 pending")
    code, out, err = cli(capsys, "rerun", "results/sensors/run_5")
    assert code == 0, err
    assert out.startswith("identical: ")


def test_minimal_template_is_the_default_and_runs(capsys, isolated):
    import os

    code, out, err = cli(capsys, "new", "my-ring")
    assert code == 0, err
    assert "Created 5 files in my-ring/ (template 'minimal')" in out
    assert "python model.py" in out
    assert "mango-scenario run study.yaml" in out
    names = sorted(
        path.relative_to(isolated / "my-ring").as_posix()
        for path in (isolated / "my-ring").rglob("*")
        if path.is_file()
    )
    assert names == sorted(MINIMAL)
    os.chdir(isolated / "my-ring")
    model = Path("model.py").read_text()
    assert 'Scenario("my-ring", params=Params)' in model
    # domain-neutral, and outcomes follow the sample semantics (integrate())
    for word in ("sensor", "temperature", "energy", "ev_", "kwh"):
        assert word not in model.lower()
    assert 'run.time_average("spread")' in model

    code, out, err = cli(capsys, "validate", "study.yaml")
    assert code == 0, err
    assert out.startswith("OK: study.yaml is a valid study: 12 runs")
    code, out, err = cli(capsys, "run", "study.yaml", "-q")
    assert code == 0, err
    assert out.startswith("12 runs: 12 done, 0 skipped, 0 failed")
    frame = load_study("results/study")
    stubborn = frame.groupby("peers.stubbornness")["mean_spread"].mean()
    assert stubborn[0.8] > stubborn[0.2]  # stubborn agents agree later
    # mean_spread integrates the samples after t=0: one row per step
    run = frame.run(0)
    spread = run.ts("spread")
    assert len(spread) == 51
    assert run.integrate("spread", unit="1s") == pytest.approx(spread.iloc[1:].sum())
    assert run.time_average("spread") == pytest.approx(spread.iloc[1:].mean())

    runpy.run_path("model.py", run_name="__main__")  # python model.py
    assert "final " in capsys.readouterr().out
    code, _, err = cli(
        capsys,
        "to-script",
        "model:scenario",
        "-p",
        "presets/base.yaml",
        "--seed",
        "1",
        "-o",
        "plain.py",
    )
    assert code == 0 and "placeholder" not in err
    plain = Path("plain.py").read_text()
    assert "Lean form" in plain and "PHASES" not in plain
    rows = runpy.run_path("plain.py", run_name="plain")["main"]()
    capsys.readouterr()
    assert len(rows) == 51 * 11  # 10 agents and the spread, at t=0 and 50 steps


def test_unknown_template_is_a_usage_error(capsys, isolated):
    code, _, err = cli(capsys, "new", "x", "--template", "huge")
    assert code == 2 and "invalid choice: 'huge'" in err
    with pytest.raises(ValueError, match="Unknown project template 'huge'"):
        create_project(isolated / "y", template="huge")


def test_template_scenario_runs_alone_and_as_a_script(capsys, isolated):
    create_project(isolated / "demo", template="full")
    import os

    os.chdir(isolated / "demo")
    code, out, err = cli(
        capsys,
        "run",
        "scenario.yaml",
        "-p",
        "presets/base.yaml",
        "--seed",
        "1",
        "--set",
        "field.n=2",
        "-o",
        "results/one",
    )
    assert code == 0, err
    manifest = json.loads(Path("results/one/manifest.json").read_text())
    assert manifest["stats"]["agents"] == 3

    code, _, err = cli(
        capsys,
        "to-script",
        "scenario.yaml",
        "-p",
        "presets/base.yaml",
        "--seed",
        "1",
        "--set",
        "field.n=2",
        "-o",
        "script.py",
    )
    assert code == 0, err
    assert "placeholder" not in err  # everything in the template is importable
    namespace = runpy.run_path("script.py", run_name="script")
    rows = namespace["main"]()
    capsys.readouterr()
    assert len(rows) == manifest["rows"]

    code, _, err = cli(capsys, "form", "scenario.yaml")
    assert code == 0 and Path("demo_form.html").is_file()
    code, out, _ = cli(capsys, "schema", "scenario.yaml")
    assert "field" in json.loads(out)["properties"]


def _matches(relative: str, pattern: str) -> bool:
    parts, globs = relative.split("/"), pattern.split("/")
    return len(parts) == len(globs) and all(
        fnmatch.fnmatchcase(part, glob) for part, glob in zip(parts, globs)
    )


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib needs Python 3.11")
def test_package_data_ships_every_template_file():
    import tomllib

    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    patterns = config["tool"]["setuptools"]["package-data"]["mango.scenario.templates"]
    files = [
        path.relative_to(TEMPLATES).as_posix()
        for path in TEMPLATES.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".py"
    ]
    assert "form.html" in files and "project_full/scenario.yaml" in files
    assert "project_minimal/study.yaml" in files
    missing = [f for f in files if not any(_matches(f, p) for p in patterns)]
    assert missing == []


def test_template_preset_satisfies_the_params_schema(isolated):
    import os

    import yaml

    from mango.scenario import load_scenario, params_schema

    create_project(isolated / "demo", template="full")
    os.chdir(isolated / "demo")
    sys.path.insert(0, str(isolated / "demo"))  # as the CLI does
    schema = params_schema(load_scenario("scenario.yaml"))
    preset = yaml.safe_load(Path("presets/base.yaml").read_text())
    profile = schema["$defs"]["ProfileSpec"]
    assert "source" not in profile.get("required", [])  # inferred from path/values
    for spec in preset.get("profiles", {}).values():
        assert set(spec) <= set(profile["properties"])
    jsonschema = pytest.importorskip("jsonschema")
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(preset))
    assert errors == []


def test_to_script_of_a_study_file_uses_its_base_presets(capsys, isolated):
    import os

    create_project(isolated / "demo", template="full")
    os.chdir(isolated / "demo")
    code, _, err = cli(
        capsys,
        "run",
        "scenario.yaml",
        "-p",
        "presets/base.yaml",
        "--seed",
        "1",
        "--set",
        "field.n=2",
        "-o",
        "results/one",
    )
    assert code == 0, err
    rows = json.loads(Path("results/one/manifest.json").read_text())["rows"]
    code, _, err = cli(
        capsys,
        "to-script",
        "studies/demo.yaml",
        "--seed",
        "1",
        "--set",
        "field.n=2",
        "-o",
        "study_script.py",
    )
    assert code == 0, err
    assert "the study's base presets and overrides" in err
    assert "(e.g. --set field.n=3)" in err  # the study's own first factor
    namespace = runpy.run_path("study_script.py", run_name="script")
    assert len(namespace["main"]()) == rows  # the base preset's horizon and profile
    capsys.readouterr()


def test_template_outcomes_are_time_weighted_with_declared_kinds(isolated):
    """The minimal template's mean_spread averages over the recorded time (N39)."""
    import importlib
    import os

    create_project(isolated / "ring", template="minimal")
    create_project(isolated / "field", template="full")
    os.chdir(isolated / "ring")
    sys.path.insert(0, str(isolated / "ring"))
    try:
        model = importlib.import_module("model")
        warm = {"phases": [{"name": "warmup", "until": 25, "record": False}]}
        run = model.scenario.run(warm, seed=1)
        assert run.record_kinds == {"value": "flow", "spread": "flow"}
        spread = run.ts("spread")
        assert len(spread) == 25 and run.recorded_s == 25.0
        # the warm-up is left out of both the integral and the duration
        assert model.mean_spread(run) == pytest.approx(spread.mean())
        assert model.mean_spread(run) == pytest.approx(
            run.integrate("spread", unit="1s") / 25.0
        )
    finally:
        sys.path.remove(str(isolated / "ring"))
        sys.modules.pop("model", None)
    os.chdir(isolated / "field")
    from mango.scenario import load_scenario

    sys.path.insert(0, str(isolated / "field"))
    try:
        run = load_scenario("scenario.yaml").run("presets/base.yaml", seed=1)
        assert run.record_kinds == {
            "measured": "flow",
            "temperature": "flow",
            "estimate": "flow",
        }
        # one row per recorded step: the row mean is the time average
        outcomes = runpy.run_path("outcomes.py")
        error = (run.ts("estimate") - run.ts("temperature")).abs()
        assert outcomes["mean_abs_error"](run) == pytest.approx(error.mean())
    finally:
        sys.path.remove(str(isolated / "field"))
        for name in ("agents", "agents.sensors", "kits", "kits.field"):
            sys.modules.pop(name, None)


def test_a_form_of_the_template_preset_shows_no_change(capsys, isolated):
    """The preset repeats the scenario's time(): the form must not list it (N35)."""
    import os
    import re

    create_project(isolated / "ring", template="minimal")
    os.chdir(isolated / "ring")
    code, _, err = cli(capsys, "form", "model:scenario", "-p", "presets/base.yaml")
    assert code == 0, err
    page = Path("ring_form.html").read_text(encoding="utf-8")
    data = json.loads(
        re.search(r'id="mango-form-data">(.*?)</script>', page, re.S).group(1)
    )
    assert data["start"]["time"] == data["defaults"]["time"]
    assert data["note"] == ""
    # a study's form says on the page that the study file keeps its factors
    code, _, err = cli(capsys, "form", "study.yaml", "-o", "study_form.html")
    assert code == 0, err
    page = Path("study_form.html").read_text(encoding="utf-8")
    note = json.loads(
        re.search(r'id="mango-form-data">(.*?)</script>', page, re.S).group(1)
    )["note"]
    assert "base preset of the study study.yaml" in note and "replications" in note
    sys.modules.pop("model", None)
