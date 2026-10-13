import json
import textwrap

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from mango.scenario import (  # noqa: E402
    Explicit,
    FromCsv,
    FullFactorial,
    ParamsError,
    Sobol,
    Study,
    StudyError,
    load_run,
    load_scenario,
    load_study,
    load_study_file,
    study_file_schema,
)
from mango.scenario.yaml_scenario import (  # noqa: E402
    STUDY_FILE_KEYS,
    read_study_file,
)

from . import yaml_fixtures as fx  # noqa: E402

REF = fx.REF


def write(folder, name, text):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return path


# the side-by-side files, with the stand-in scenario and outcomes of yaml_fixtures
BASE_PRESET = """
time: {start: 2024-06-01T00:00, horizon: 2d, step: 15min}
phases:
  - {name: warmup, until: 2024-06-02T00:00, record: false}
events:
  - {name: comm_outage, at: 2024-06-02T12:00, until: 2024-06-02T14:00, comm: {loss_percent: 1.0}}
profiles:
  h0: {source: csv, path: data/profiles.csv, column: h0, resolution: 15min}
  pv: {source: csv, path: data/profiles.csv, column: pv, resolution: 15min}
comm: {kind: simple, loss_percent: 0.0}
"""

FLEET_COMPARISON = f"""
# 3 fleet sizes x 2 comm models x 2 algorithms x 10 replications = 120 runs (concept C6)
scenario: {REF}:ev_scenario
base: presets/base.yaml
seed: 2026
uncertainties:
  ev.n: [5, 10, 20]
  comm.loss_percent: {{ideal: 0.0, lossy: 0.1}}
levers:
  algorithm: [greedy, proportional]
replications: 10
common_random_numbers: true
outcomes: [{REF}:self_consumption, {REF}:peak_import_kw, {REF}:min_voltage_pu]
out: results/fleet_comparison
"""

SOBOL_PV = """
# Follow-up: Sobol sensitivity to PV share and EV battery size (phase 3)
extends: studies/fleet_comparison.yaml
uncertainties:            # merged into the parent's; a single value fixes a factor
  ev.n: 10
  comm.loss_percent: 0.0
  grid.pv_share: {range: [0.2, 0.9]}
  ev.capacity_kwh: {range: [40, 100]}
levers: {algorithm: [proportional]}
design: {sobol: {n: 256, second_order: false}}
replications: 3
out: results/sobol_pv
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    write(tmp_path, "presets/base.yaml", BASE_PRESET)
    write(tmp_path, "data/profiles.csv", "h0,pv\n0.3,0.0\n0.4,0.5\n")
    write(tmp_path, "studies/fleet_comparison.yaml", FLEET_COMPARISON)
    write(tmp_path, "studies/sobol_pv.yaml", SOBOL_PV)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_the_side_by_side_fleet_comparison_study_file_loads(project):
    study = Study.from_yaml("studies/fleet_comparison.yaml")
    assert study.name == "fleet_comparison"
    assert study.scenario is fx.ev_scenario
    assert study.scenario_key == f"{REF}:ev_scenario"
    assert study.base == "presets/base.yaml"
    assert study.seed == 2026 and study.replications == 10
    assert study.common_random_numbers is True
    assert [(f.path, f.role) for f in study.factors] == [
        ("ev.n", "uncertainty"),
        ("comm.loss_percent", "uncertainty"),
        ("algorithm", "lever"),
    ]
    assert study.factors[1].labels == ("ideal", "lossy")
    assert isinstance(study.design, FullFactorial)
    assert list(study.outcomes) == [
        "self_consumption",
        "peak_import_kw",
        "min_voltage_pu",
    ]
    assert study.out.as_posix() == "results/fleet_comparison"
    plan = study.plan()
    assert len(plan) == 120
    assert plan[0]["params"]["phases"][0]["name"] == "warmup"
    assert str(plan).startswith("Study 'fleet_comparison': 120 runs")


def test_the_sobol_follow_up_extends_the_first_study(project):
    kwargs = read_study_file("studies/sobol_pv.yaml")
    assert kwargs["name"] == "sobol_pv"
    assert kwargs["scenario"] == f"{REF}:ev_scenario"  # inherited
    assert kwargs["base"] == "presets/base.yaml"
    assert kwargs["seed"] == 2026
    assert kwargs["uncertainties"] == {
        "ev.n": 10,
        "comm.loss_percent": 0.0,
        "grid.pv_share": {"range": [0.2, 0.9]},
        "ev.capacity_kwh": {"range": [40, 100]},
    }
    assert kwargs["levers"] == {"algorithm": ["proportional"]}
    assert kwargs["design"] == {"sobol": {"n": 256, "second_order": False}}
    assert kwargs["replications"] == 3
    assert kwargs["out"] == "results/sobol_pv"
    assert len(kwargs["outcomes"]) == 3

    pytest.importorskip("scipy")  # the Sobol design
    study = load_study_file("studies/sobol_pv.yaml")
    assert isinstance(study.design, Sobol) and study.design.n == 256
    kinds = {f.path: f.kind for f in study.factors}
    assert kinds == {
        "ev.n": "fixed",
        "comm.loss_percent": "fixed",
        "grid.pv_share": "range",
        "ev.capacity_kwh": "range",
        "algorithm": "levels",
    }
    small = load_study_file(
        "studies/sobol_pv.yaml", design={"sobol": {"n": 4}}, replications=1
    )
    plan = small.plan()
    assert len(plan) == 4 * (2 + 2)
    assert {spec["params"]["ev"]["n"] for spec in plan} == {10}
    assert {spec["params"]["algorithm"] for spec in plan} == {"proportional"}
    assert {spec["design"]["block"] for spec in plan} == {"A", "AB", "B"}


def test_a_study_with_a_scenario_file_runs_in_a_two_worker_process_pool(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    write(
        tmp_path,
        "scenarios/toy.yaml",
        f"""
        params: {REF}:Params
        time: {{horizon: 2h, step: 1h}}
        components:
          grid: {{kind: yaml_test_grid, n_households: 2}}
        groups:
          ev: {{factory: "{REF}:make_ev", count: ev.n}}
        record:
          - {{name: soc_kwh, group: ev, attr: soc}}
          - {{name: import_kw, resource: feeder.import_kw}}
        """,
    )
    write(
        tmp_path,
        "studies/toy.yaml",
        f"""
        scenario: ../scenarios/toy.yaml
        seed: 5
        levers: {{algorithm: [greedy, proportional]}}
        outcomes: {{soc: "{REF}:mean_soc", peak: "{REF}:peak_import"}}
        out: results/toy
        """,
    )
    study = load_study_file("studies/toy.yaml")
    assert study.scenario_key == "scenarios/toy.yaml"  # rewritten relative to cwd
    report = study.run(workers=2, progress=False)
    assert report.ok, report.failures
    assert (report.done, report.executor) == (2, "process pool (2 workers)")
    df = load_study("results/toy")
    assert df["status"].tolist() == ["done", "done"]
    assert df["soc"].notna().all() and df["peak"].notna().all()
    specs = [
        json.loads(line)
        for line in (tmp_path / "results/toy/runs.jsonl").read_text().splitlines()
    ]
    assert {spec["scenario"] for spec in specs} == {"scenarios/toy.yaml"}
    scenario = load_scenario("scenarios/toy.yaml")
    assert {spec["scenario_fingerprint"] for spec in specs} == {scenario.fingerprint()}
    stored = load_run(tmp_path / "results/toy/runs" / specs[1]["run_id"])
    replay = scenario.run(specs[1]["params"], seed=specs[1]["seed"])
    assert stored.checksum() == replay.checksum()
    # reloading the files gives the same run ids, so a rerun skips everything
    again = Study.from_yaml("studies/toy.yaml").run(progress=False)
    assert (again.skipped, again.done) == (2, 0)


def test_a_changed_scenario_file_fails_the_worker_run_clearly(tmp_path, monkeypatch):
    from mango.scenario import run_one

    monkeypatch.chdir(tmp_path)
    path = write(
        tmp_path,
        "toy.yaml",
        f"""
        params: {REF}:Params
        time: {{horizon: 1h, step: 1h}}
        groups:
          ev: {{factory: "{REF}:make_ev", count: 1}}
        """,
    )
    study = Study("toy.yaml", seed=1, out=tmp_path / "out")
    spec = study.plan()[0]
    path.write_text(path.read_text() + "gain: 0.5\n")
    outcome = run_one(spec)
    assert outcome["status"] == "failed"
    assert "scenario file 'toy.yaml' differs" in outcome["error"]


# ----------------------------------------------------------------- extends semantics


def test_extends_merges_factors_per_path_and_replaces_other_keys(tmp_path):
    write(
        tmp_path,
        "parent.yaml",
        f"""
        name: parent
        scenario: {REF}:ev_scenario
        overrides: ["gain=0.2", "ev.capacity_kwh=50"]
        uncertainties:
          ev.n: [1, 2]
          comm.loss_percent: {{ideal: 0.0, lossy: 0.1}}
          gain: [0.1, 0.2]
        levers:
          algorithm: [greedy, proportional]
        design: {{explicit: [{{ev.n: 1}}]}}
        outcomes: ["{REF}:mean_soc"]
        subexperiments: [{{}}]
        """,
    )
    write(
        tmp_path,
        "child.yaml",
        f"""
        extends: parent.yaml
        overrides: {{gain: 0.3}}
        uncertainties:
          comm.loss_percent: {{range: [0.0, 0.2]}}
          -gain: null
          algorithm: [greedy]
        design: {{lhs: {{n: 4}}}}
        +outcomes: ["{REF}:peak_import"]
        -subexperiments: null
        """,
    )
    kwargs = read_study_file(tmp_path / "child.yaml")
    assert kwargs["name"] == "child"  # the file name, not inherited
    assert kwargs["overrides"] == {"gain": 0.3, "ev.capacity_kwh": 50}
    assert kwargs["uncertainties"] == {
        "ev.n": [1, 2],
        "comm.loss_percent": {"range": [0.0, 0.2]},
        "algorithm": ["greedy"],
    }
    assert kwargs["levers"] == {}  # 'algorithm' moved to the uncertainties
    assert kwargs["design"] == {"lhs": {"n": 4}}
    assert kwargs["outcomes"] == [f"{REF}:mean_soc", f"{REF}:peak_import"]
    assert kwargs["subexperiments"] is None
    pytest.importorskip("scipy")  # the LHS design
    study = load_study_file(tmp_path / "child.yaml")
    assert len(study.plan()) == 4 * 2  # 4 samples x 2 levels of ev.n


def test_a_follow_up_study_does_not_take_over_the_parents_folder(project):
    write(project, "studies/child.yaml", "extends: fleet_comparison.yaml\n")
    write(project, "studies/plain.yaml", f"scenario: {REF}:ev_scenario\n")
    assert read_study_file("studies/child.yaml")["out"] == "results/child"
    assert read_study_file("studies/sobol_pv.yaml")["out"] == "results/sobol_pv"
    assert read_study_file("studies/plain.yaml")["out"] is None


def test_a_study_refuses_a_folder_that_holds_another_study(tmp_path):
    first = Study(fx.ev_scenario, name="first", out=tmp_path, replications=1)
    first.run(progress=False)
    second = Study(fx.ev_scenario, name="second", out=tmp_path, replications=1)
    with pytest.raises(StudyError, match="holds the study 'first'.*'second'"):
        second.run(progress=False)
    assert "name: first" in (tmp_path / "study.yaml").read_text()
    assert first.run(progress=False).skipped == 1


def test_extends_errors(tmp_path):
    write(tmp_path, "a.yaml", "extends: b.yaml\n")
    write(tmp_path, "b.yaml", "extends: a.yaml\n")
    with pytest.raises(StudyError, match="Cycle in 'extends'"):
        read_study_file(tmp_path / "a.yaml")
    write(tmp_path, "orphan.yaml", "extends: missing.yaml\n")
    with pytest.raises(StudyError, match="extends 'missing.yaml', which was not found"):
        read_study_file(tmp_path / "orphan.yaml")
    write(
        tmp_path,
        "plus.yaml",
        f"scenario: {REF}:ev_scenario\nuncertainties: {{+ev.n: [1]}}\n",
    )
    write(
        tmp_path, "plus_child.yaml", "extends: plus.yaml\nuncertainties: {+ev.n: [2]}\n"
    )
    with pytest.raises(StudyError, match="'\\+ev.n' is not supported"):
        read_study_file(tmp_path / "plus_child.yaml")
    write(tmp_path, "scalar.yaml", f"scenario: {REF}:ev_scenario\nseed: 1\n+seed: 2\n")
    with pytest.raises(StudyError, match="cannot append to 'seed'"):
        read_study_file(tmp_path / "scalar.yaml")


# ----------------------------------------------------------------- paths


def test_paths_resolve_against_cwd_then_the_file_that_sets_them(tmp_path, monkeypatch):
    write(tmp_path, "project/studies/presets/local.yaml", "gain: 0.4\n")
    write(tmp_path, "project/presets/base.yaml", "gain: 0.3\n")
    write(tmp_path, "project/studies/design.csv", "ev.n\n1\n2\n")
    write(
        tmp_path,
        "project/studies/parent.yaml",
        f"""
        scenario: {REF}:ev_scenario
        base: [presets/base.yaml, presets/local.yaml, {{ev: {{n: 3}}}}]
        design: {{csv: design.csv}}
        """,
    )
    write(tmp_path, "project/studies/sub/child.yaml", "extends: ../parent.yaml\n")
    monkeypatch.chdir(tmp_path / "project")
    kwargs = read_study_file("studies/sub/child.yaml")
    assert kwargs["base"] == [
        "presets/base.yaml",  # exists relative to cwd: kept
        "studies/presets/local.yaml",  # next to parent.yaml: rewritten
        {"ev": {"n": 3}},
    ]
    assert kwargs["design"] == {"csv": "studies/design.csv"}
    study = load_study_file("studies/sub/child.yaml")
    assert isinstance(study.design, FromCsv)
    plan = study.plan()
    assert [spec["params"]["ev"]["n"] for spec in plan] == [1, 2]
    assert {spec["params"]["gain"] for spec in plan} == {0.4}

    write(
        tmp_path,
        "project/studies/broken.yaml",
        f"scenario: {REF}:ev_scenario\nbase: nope.yaml\n",
    )
    with pytest.raises(StudyError, match="base preset 'nope.yaml' not found \\(tried"):
        read_study_file("studies/broken.yaml")
    write(
        tmp_path,
        "project/studies/csv.yaml",
        f"scenario: {REF}:ev_scenario\ndesign: {{csv: {{path: design.csv, delimiter: ','}}}}\n",
    )
    assert read_study_file("studies/csv.yaml")["design"] == {
        "csv": {"path": "studies/design.csv", "delimiter": ","}
    }
    write(tmp_path, "project/studies/missing_scenario.yaml", "scenario: none.yaml\n")
    with pytest.raises(StudyError, match="scenario file 'none.yaml' not found"):
        read_study_file("studies/missing_scenario.yaml")


# ----------------------------------------------------------------- validation


def test_unknown_keys_and_bad_values_fail_with_suggestions(tmp_path):
    write(tmp_path, "parent.yaml", f"scenario: {REF}:ev_scenario\nreplicatoins: 3\n")
    write(
        tmp_path,
        "child.yaml",
        """
        extends: parent.yaml
        uncertanties: {ev.n: [1, 2]}
        seed: -1
        common_random_numbers: maybe
        outcomes: [not a ref]
        levers: [algorithm]
        format: xlsx
        """,
    )
    with pytest.raises(StudyError) as info:
        read_study_file(tmp_path / "child.yaml")
    text = str(info.value)
    assert "Invalid study file" in text and "child.yaml" in text
    assert "replicatoins: unknown key; did you mean 'replications'?" in text
    assert "parent.yaml]" in text  # the typo is in the parent
    assert "uncertanties: unknown key; did you mean 'uncertainties'?" in text
    assert "seed: Input should be greater than or equal to 0" in text
    assert "common_random_numbers: expected true, false or levers" in text
    assert "outcomes: expected an import reference" in text
    assert "levers: expected a mapping {path: levels}" in text
    assert "format: Input should be 'auto', 'parquet' or 'csv'" in text

    write(tmp_path, "empty.yaml", "levers: {algorithm: [greedy]}\n")
    with pytest.raises(StudyError, match="scenario: required key missing"):
        read_study_file(tmp_path / "empty.yaml")
    write(tmp_path, "list.yaml", "- 1\n")
    with pytest.raises(StudyError, match="mapping at the top level"):
        read_study_file(tmp_path / "list.yaml")


def test_study_errors_name_the_file(tmp_path):
    write(
        tmp_path,
        "both.yaml",
        f"""
        scenario: {REF}:ev_scenario
        uncertainties: {{algorithm: [greedy]}}
        levers: {{algorithm: [proportional]}}
        """,
    )
    with pytest.raises(StudyError, match="both.yaml: Factor\\(s\\) \\['algorithm'\\]"):
        load_study_file(tmp_path / "both.yaml")
    write(
        tmp_path, "design.yaml", f"scenario: {REF}:ev_scenario\ndesign: {{sobel: 3}}\n"
    )
    with pytest.raises(StudyError, match="design.yaml: .*Did you mean 'sobol'"):
        load_study_file(tmp_path / "design.yaml")


def test_from_yaml_changes_and_explicit_designs(tmp_path):
    path = write(
        tmp_path,
        "s.yaml",
        f"""
        scenario: {REF}:ev_scenario
        design: {{explicit: [{{ev.n: 1, algorithm: greedy}}, {{ev.n: 2}}]}}
        levers: {{algorithm: [greedy, proportional]}}
        """,
    )
    study = Study.from_yaml(path, out=tmp_path / "out", seed=3)
    assert isinstance(study.design, Explicit)
    assert study.seed == 3 and study.out == tmp_path / "out"
    assert len(study.plan()) == 3
    with pytest.raises(TypeError, match="unknown arguments \\['workers'\\]"):
        Study.from_yaml(path, workers=2)
    with pytest.raises(TypeError, match="path must be a str"):
        read_study_file(3)


def test_definition_written_by_a_study_reads_back_as_a_study_file(tmp_path):
    study = load_study_file(
        write(
            tmp_path,
            "s.yaml",
            f"""
            scenario: {REF}:ev_scenario
            uncertainties: {{ev.n: [1, 2]}}
            outcomes: {{soc: "{REF}:mean_soc"}}
            """,
        )
    )
    definition = study.definition(study.plan())
    keys = {key: value for key, value in definition.items() if key in STUDY_FILE_KEYS}
    import yaml

    again = write(tmp_path, "again.yaml", yaml.safe_dump(keys, sort_keys=False))
    copy = load_study_file(again)
    assert copy.plan().run_ids == study.plan().run_ids


def test_study_file_schema_is_a_valid_json_schema():
    schema = study_file_schema()
    json.dumps(schema)
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(STUDY_FILE_KEYS)
    assert schema["anyOf"] == [{"required": ["scenario"]}, {"required": ["extends"]}]
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator.check_schema(schema)
    import yaml

    jsonschema.Draft202012Validator(schema).validate(yaml.safe_load(FLEET_COMPARISON))
    jsonschema.Draft202012Validator(schema).validate(yaml.safe_load(SOBOL_PV))


def test_loaders_name_the_kind_of_a_mixed_up_file(project):
    with pytest.raises(ParamsError, match="is a study file; load it with Study"):
        load_scenario("studies/fleet_comparison.yaml")
    with pytest.raises(StudyError, match="is a preset .*not a study file"):
        load_study_file("presets/base.yaml")
    with pytest.raises(FileNotFoundError, match="is a file, not a study folder"):
        load_study("studies/fleet_comparison.yaml")
    write(project, "studies/typo.yaml", "scenaro: x\n")
    with pytest.raises(StudyError, match="did you mean 'scenario'"):
        load_study_file("studies/typo.yaml")
