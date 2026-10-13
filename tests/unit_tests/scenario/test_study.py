import json
import os

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pd = pytest.importorskip("pandas")

import yaml  # noqa: E402

from mango.scenario import (  # noqa: E402
    Explicit,
    FullFactorial,
    ParamsError,
    Scenario,
    Study,
    StudyError,
    StudyPlan,
    StudyReport,
)
from mango.scenario.result import compute_run_id  # noqa: E402
from mango.scenario.study import SPEC_VERSION  # noqa: E402
from mango.util.seeding import SeedTree  # noqa: E402

from . import study_fixtures as fx  # noqa: E402

UNCERTAINTIES = {
    "ev.n": [1, 2, 3],
    "comm.loss_percent": {"ideal": 0.0, "lossy": 0.1},
}
LEVERS = {"algorithm": ["greedy", "proportional"]}

SPEC_KEYS = [
    "spec_version",
    "study",
    "index",
    "run_id",
    "scenario",
    "scenario_fingerprint",
    "code_fingerprint",
    "data_fingerprint",
    "params",
    "seed",
    "factors",
    "labels",
    "replication",
    "design",
    "outcomes",
    "out",
    "project_root",
    "git",
    "format",
]


def make_study(**kwargs):
    options = {
        "uncertainties": UNCERTAINTIES,
        "levers": LEVERS,
        "replications": 10,
        "seed": 2026,
        "outcomes": [fx.mean_soc],
    }
    options.update(kwargs)
    return Study(fx.scenario, **options)


@pytest.fixture(scope="module")
def plan():
    return make_study().plan()


# ----------------------------------------------------------------- run table


def test_plan_size_and_order(plan):
    assert isinstance(plan, StudyPlan)
    assert len(plan) == 3 * 2 * 2 * 10 == 120
    assert plan.points == 12 and plan.dropped == 0 and plan.seed == 2026
    assert [spec["index"] for spec in plan] == list(range(120))
    first = plan[0]
    assert first["factors"] == {
        "ev.n": 1,
        "comm.loss_percent": 0.0,
        "algorithm": "greedy",
    }
    assert first["labels"] == {"comm.loss_percent": "ideal"}
    assert [spec["replication"] for spec in plan.specs[:11]] == [*range(10), 0]
    assert plan[10]["factors"]["algorithm"] == "proportional"
    assert plan[20]["factors"]["comm.loss_percent"] == 0.1
    assert plan[40]["factors"]["ev.n"] == 2


def test_specs_are_plain_json_data(plan):
    spec = plan[13]
    assert list(spec) == SPEC_KEYS
    assert json.loads(json.dumps(spec)) == spec
    assert spec["spec_version"] == SPEC_VERSION
    assert spec["study"] == "study_toy"
    assert spec["scenario"] == f"{fx.REF}:scenario"
    assert spec["params"]["ev"]["n"] == 1
    assert spec["params"]["comm"]["loss_percent"] == 0.0
    assert spec["params"]["algorithm"] == "proportional"
    assert spec["outcomes"] == {"mean_soc": f"{fx.REF}:mean_soc"}
    assert spec["out"] is None and spec["format"] == "auto"
    assert spec["design"] == {}
    assert spec["run_id"] == compute_run_id(
        spec["scenario"], spec["params"], spec["seed"]
    )


def test_run_ids_are_stable_and_unique(plan, tmp_path):
    again = make_study(out=tmp_path).plan()
    assert again.run_ids == plan.run_ids
    assert len(set(plan.run_ids)) == len(plan)
    assert again[0]["out"] == str(tmp_path / "runs" / again[0]["run_id"])
    other_seed = make_study(seed=1).plan()
    assert not set(other_seed.run_ids) & set(plan.run_ids)


def test_base_and_overrides_reach_params():
    study = make_study(
        base={"gain": 0.2, "ev": {"n": 7}},
        overrides=["tags=[x]"],
        uncertainties=None,
        levers={"algorithm": ["market"]},
        replications=1,
    )
    (spec,) = study.plan()
    assert spec["params"]["gain"] == 0.2
    assert spec["params"]["ev"]["n"] == 7
    assert spec["params"]["tags"] == ["x"]
    assert spec["params"]["algorithm"] == "market"


def test_scalar_factor_is_fixed_and_listed():
    plan = make_study(uncertainties={"ev.n": 3}, replications=1).plan()
    assert len(plan) == 2
    assert all(spec["factors"]["ev.n"] == 3 for spec in plan)


def test_plan_to_frame_and_str(plan):
    frame = plan.to_frame()
    assert list(frame.columns) == [
        "index",
        "run_id",
        "replication",
        "point",
        "ev.n",
        "comm.loss_percent",
        "comm.loss_percent:label",
        "algorithm",
    ]
    assert len(frame) == 120
    assert frame["point"].nunique() == 12  # replications share their point
    assert frame.groupby("algorithm").size().to_dict() == {
        "greedy": 60,
        "proportional": 60,
    }
    text = str(plan)
    assert "Study 'study_toy': 120 runs" in text
    assert "12 design points x 10 replications" in text
    assert "seed 2026, common random numbers" in text
    assert "comm.loss_percent: ideal=0.0 | lossy=0.1" in text
    assert "algorithm: greedy | proportional" in text
    assert "(not set)" in text
    assert repr(plan) == "<StudyPlan 'study_toy': 120 runs>"


# ----------------------------------------------------------------- validation


def test_factor_path_typos_fail_at_plan_with_suggestions():
    study = make_study(
        uncertainties={"ev.nn": [1, 2]},
        levers={"algoritm": ["greedy"]},
    )
    with pytest.raises(StudyError) as info:
        study.plan()
    message = str(info.value)
    assert "unknown factor paths for Params" in message
    assert "uncertainties: Unknown parameter 'ev.nn'" in message
    assert "did you mean 'ev.n'" in message
    assert "levers: Unknown parameter 'algoritm'" in message
    assert "did you mean 'algorithm'" in message


def test_design_columns_and_subexperiment_paths_are_checked():
    with pytest.raises(StudyError, match="design: Unknown parameter 'ev.cap'"):
        make_study(design=Explicit([{"ev.cap": 3}])).plan()
    with pytest.raises(StudyError, match="subexperiment 0: Unknown parameter 'gian'"):
        make_study(subexperiments=[{"gian": 0.3}]).plan()


def test_invalid_factor_values_name_the_design_point():
    study = make_study(levers={"algorithm": ["greedy", "auction"]}, replications=1)
    with pytest.raises(StudyError) as info:
        study.plan()
    message = str(info.value)
    assert (
        "the design point ev.n=1, comm.loss_percent=0.0, algorithm=auction" in message
    )
    assert (
        "  algorithm: Input should be 'greedy', 'proportional' or 'market'" in message
    )
    assert "[from overrides]" not in message  # the value came from the design
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_invalid_base_raises_params_error():
    with pytest.raises(ParamsError, match="ev.capacity"):
        make_study(base={"ev": {"capacity": 3}}).plan()


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"levers": {"ev.n": [1]}}, r"declared as uncertainty and as lever"),
        ({"uncertainties": {"ev": [{}], "ev.n": [1]}}, "'ev' and 'ev.n' overlap"),
        ({"uncertainties": [("ev.n", [1])]}, "must be a mapping"),
        ({"replications": 0}, "replications must be an int >= 1"),
        ({"seed": -1}, "seed must be an int >= 0"),
        ({"seed": True}, "seed must be an int >= 0"),
        ({"format": "xlsx"}, "format must be one of"),
        ({"outcomes": {"run_id": fx.mean_soc}}, "clashes with a column"),
        ({"outcomes": {"ev.n": fx.mean_soc}}, "clashes with a column"),
        ({"outcomes": [lambda run: 1]}, "Cannot name the outcome"),
        ({"outcomes": [fx.mean_soc, fx.mean_soc]}, "Duplicate outcome name 'mean_soc'"),
        ({"outcomes": {"x": 3}}, "must be a function"),
        ({"outcomes": ["math:pi"]}, "not a function"),
        ({"outcomes": [f"{fx.REF}:mean_sco"]}, "did you mean 'mean_soc'"),
        ({"subexperiments": {"ev.n": 1}}, "list of factor dicts"),
        ({"subexperiments": []}, "subexperiments is empty"),
        ({"subexperiments": [3]}, "Subexperiment 0 must be a mapping"),
        ({"design": "latin"}, "Unknown design 'latin'"),
        ({"name": ""}, "non-empty str"),
    ],
)
def test_invalid_study_arguments(kwargs, message):
    with pytest.raises(StudyError, match=message):
        make_study(**kwargs)


def test_scenario_argument_forms():
    by_ref = Study(f"{fx.REF}:scenario")
    assert by_ref.scenario is fx.scenario
    assert by_ref.scenario_ref == f"{fx.REF}:scenario"
    dotted = Study(f"{fx.REF}.scenario")
    assert dotted.scenario_ref == f"{fx.REF}:scenario"
    factory = Study(f"{fx.REF}:make_scenario")
    assert factory.scenario is not fx.scenario
    assert factory.scenario_ref == f"{fx.REF}:make_scenario"
    with pytest.raises(StudyError, match="Cannot load the scenario"):
        Study(f"{fx.REF}:nope")
    with pytest.raises(StudyError, match="not a Scenario or a zero-argument"):
        Study(f"{fx.REF}:REF")
    with pytest.raises(StudyError, match="not a zero-argument scenario factory"):
        Study(f"{fx.REF}:make_ev")
    with pytest.raises(StudyError, match="Cannot load the scenario file .*not found"):
        Study("studies/scenario.yaml")
    with pytest.raises(StudyError, match="needs a Scenario"):
        Study(3)


def test_unreferenceable_scenario_uses_its_name():
    local = Scenario("local_toy", params=fx.Params)
    local.group("ev", "ev.n", fx.make_ev)
    study = Study(local, levers={"algorithm": ["greedy"]})
    assert study.scenario_ref is None and study.scenario_key == "local_toy"
    assert study.plan()[0]["scenario"] == "local_toy"


# ----------------------------------------------------------------- seeds


def _seeds_by(plan, key):
    table = {}
    for spec in plan:
        table.setdefault(key(spec), set()).add(json.dumps(spec["seed"]))
    return table


def test_common_random_numbers_pair_variants(plan):
    per_replication = _seeds_by(plan, lambda spec: spec["replication"])
    assert all(len(seeds) == 1 for seeds in per_replication.values())
    assert len({next(iter(s)) for s in per_replication.values()}) == 10
    assert plan[0]["seed"] == SeedTree(2026).child("replication", 0).to_dict()


def test_independent_seeds_without_crn():
    plan = make_study(common_random_numbers=False).plan()
    per_replication = _seeds_by(plan, lambda spec: spec["replication"])
    assert all(len(seeds) == 12 for seeds in per_replication.values())
    all_seeds = [json.dumps(spec["seed"]) for spec in plan]
    assert len(set(all_seeds)) == 120
    assert make_study(common_random_numbers=False).plan()[5]["seed"] == plan[5]["seed"]
    assert "independent seeds" in str(plan)


def test_drawn_seed_is_stable_per_study_instance():
    study = make_study(seed=None, replications=1)
    first = study.plan()
    assert first.run_ids == study.plan().run_ids
    assert 0 <= first.seed < 2**63


# ----------------------------------------------------------------- subexperiments


def test_subexperiments_union_with_dedup():
    study = make_study(
        uncertainties={"ev.n": [1, 2]},
        replications=2,
        subexperiments=[
            {},
            {"ev.n": 2, "algorithm": "greedy"},
            {"ev.n": [3], "gain": [0.1, 0.2]},
        ],
    )
    plan = study.plan()
    # block 0: 2 x 2 points, block 1: 1 point (a duplicate), block 2: 1 x 2 x 2 points
    assert plan.points == 4 + 1 + 4
    assert plan.dropped == 2
    assert len(plan) == (4 + 4) * 2
    assert len(set(plan.run_ids)) == len(plan)
    assert [spec["design"]["subexperiment"] for spec in plan][::2] == [0] * 4 + [2] * 4
    last = plan[-1]
    assert last["factors"] == {"ev.n": 3, "algorithm": "proportional", "gain": 0.2}
    assert "in 3 subexperiments, 2 duplicate runs dropped" in str(plan)
    assert study.subexperiments[2]["gain"].role == "lever"
    assert study.subexperiments[2]["ev.n"].role == "uncertainty"


def test_explicit_design_through_the_study():
    study = make_study(
        uncertainties={"comm.loss_percent": {"ideal": 0.0, "lossy": 0.1}},
        levers={"algorithm": ["greedy", "proportional"]},
        design=Explicit([{"comm.loss_percent": "lossy", "algorithm": "greedy"}]),
        replications=2,
    )
    plan = study.plan()
    assert len(plan) == 2
    assert plan[0]["factors"] == {"comm.loss_percent": 0.1, "algorithm": "greedy"}
    assert plan[0]["labels"] == {"comm.loss_percent": "lossy"}
    assert plan[0]["design"] == {"row": 0}


# ----------------------------------------------------------------- definition and report


def test_definition(tmp_path):
    study = make_study(
        base=[{"gain": 0.2}, None],
        overrides={"tags": ["x"]},
        design=FullFactorial(),
        out=tmp_path,
    )
    definition = study.definition(study.plan())
    assert list(definition)[:4] == ["name", "scenario", "base", "overrides"]
    assert definition["base"] == [{"gain": 0.2}, None]
    assert definition["overrides"] == {"tags": ["x"]}
    assert definition["uncertainties"] == {
        "ev.n": [1, 2, 3],
        "comm.loss_percent": {"levels": {"ideal": 0.0, "lossy": 0.1}},
    }
    assert definition["levers"] == {"algorithm": ["greedy", "proportional"]}
    assert definition["design"] == "full_factorial"
    assert definition["seed"] == 2026 and definition["runs"] == 120
    assert definition["common_random_numbers"] is True
    assert definition["outcomes"] == {"mean_soc": f"{fx.REF}:mean_soc"}
    assert yaml.safe_load(yaml.safe_dump(definition)) == definition
    assert "Study('study_toy'" in repr(study)


def test_report_str():
    report = StudyReport(
        total=3,
        done=1,
        skipped=1,
        failed=1,
        failures=[{"run_id": "abc", "index": 2, "error": "ValueError: boom"}],
        out="results/x",
        wall_s=1.25,
    )
    text = str(report)
    assert text.startswith("3 runs: 1 done, 1 skipped, 1 failed in 1.2 s (sequential)")
    assert "failed run 2 (abc): ValueError: boom" in text
    assert not report.ok


def test_run_needs_an_output_folder():
    with pytest.raises(StudyError, match="needs an output folder"):
        make_study().run(progress=False)


# ----------------------------------------------------------------- seeds inside studies


def test_seed_is_not_a_factor():
    with pytest.raises(StudyError, match="'seed' cannot be a factor") as info:
        make_study(uncertainties={"seed": [1, 2, 3]}).plan()
    assert "replications=N" in str(info.value)
    with pytest.raises(StudyError, match="'seed' cannot be a factor"):
        make_study(
            levers=None, uncertainties=None, design=Explicit([{"seed": 1}])
        ).plan()


def test_a_base_seed_seeds_the_study_and_runs_record_their_own_seed():
    plan = make_study(seed=None, base={"seed": 42}).plan()
    assert plan.seed == 42
    assert plan.specs == make_study(seed=42).plan().specs
    assert {spec["params"]["seed"] for spec in plan} == {None}
    # an explicit study seed wins
    assert make_study(seed=7, base={"seed": 42}).plan().seed == 7


def test_non_finite_factor_values_are_rejected():
    with pytest.raises(StudyError, match=r"not finite for \['gain'\]"):
        make_study(levers={"gain": [0.1, float("inf")]}).plan()


def test_specs_carry_the_scenario_fingerprint(plan):
    assert {spec["scenario_fingerprint"] for spec in plan} == {
        fx.scenario.fingerprint()
    }


# ----------------------------------------------------------------- running


def test_outcomes_added_later_are_computed_for_finished_runs(tmp_path):
    first = make_study(uncertainties=None, replications=1, out=tmp_path).run(
        progress=False
    )
    assert first.done == 2
    again = make_study(
        uncertainties=None,
        replications=1,
        out=tmp_path,
        outcomes=[fx.mean_soc, fx.first_soc],
    ).run(progress=False)
    assert (again.done, again.skipped) == (0, 2)
    from mango.scenario import load_study

    frame = load_study(tmp_path)
    assert frame["first_soc"].notna().all()


def test_a_second_run_on_the_same_folder_is_refused_while_the_first_holds_it(tmp_path):
    from mango.scenario.store import StudyStore

    study = make_study(uncertainties=None, replications=1, out=tmp_path)
    with StudyStore(tmp_path).lock():
        with pytest.raises(StudyError, match="is in use by another study.run") as info:
            study.run(progress=False)
    assert "delete" in str(info.value)
    assert study.run(progress=False).done == 2
    assert not (tmp_path / ".lock").exists()


@pytest.mark.skipif(os.name == "nt", reason="no process liveness check on Windows")
def test_a_stale_lock_of_a_finished_process_is_taken_over(tmp_path):
    import socket

    (tmp_path / ".lock").write_text(
        json.dumps({"pid": 2**22 + 12345, "host": socket.gethostname(), "since": "x"})
    )
    report = make_study(uncertainties=None, replications=1, out=tmp_path).run(
        progress=False
    )
    assert report.done == 2
