"""Common random numbers: the True / "levers" / False modes and the pseudo-replication warning."""

import warnings

import pytest

from mango.scenario import (
    Explicit,
    FullFactorial,
    Lhs,
    RandomSample,
    Scenario,
    ScenarioParams,
    Sobol,
    Study,
    StudyError,
    load_study_file,
)
from mango.scenario._util import canonical_json
from mango.util.seeding import SeedTree


class Params(ScenarioParams):
    x: float = 0.0
    y: float = 0.0
    policy: str = "a"


scenario = Scenario("crn_toy", params=Params)
scenario.time(horizon=1, step=1)
scenario.record_world("v", lambda ctx: ctx.p.x + ctx.seeds.rng("noise").normal())

RANGE = {"range": [0.0, 1.0]}


def make(**kwargs):
    defaults = dict(
        uncertainties={"x": RANGE},
        levers={"policy": ["a", "b", "c"]},
        design=Lhs(4),
        replications=3,
        seed=5,
    )
    return Study(scenario, **{**defaults, **kwargs})


def seeds_by(plan, key):
    groups = {}
    for spec in plan:
        groups.setdefault(key(spec), set()).add(canonical_json(spec["seed"]))
    return groups


def plan_quietly(study):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return study.plan()


@pytest.mark.parametrize("design", [Lhs(4), RandomSample(4)])
def test_sampling_designs_default_to_levers(design):
    pytest.importorskip("scipy")
    study = make(design=design)
    assert study.common_random_numbers == "levers"
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no pseudo-replication warning
        plan = study.plan()
    assert len(plan) == 4 * 3 * 3
    # paired across the lever: one seed per (sample, replication) ...
    pairs = seeds_by(plan, lambda s: (s["factors"]["x"], s["replication"]))
    assert all(len(seeds) == 1 for seeds in pairs.values())
    # ... and every sample its own seed: 4 samples x 3 replications
    assert len({canonical_json(s["seed"]) for s in plan}) == 12
    assert "seed 5, common random numbers across levers" in str(plan)


def test_true_with_a_sampling_design_warns_about_pseudo_replication():
    pytest.importorskip("scipy")
    study = make(common_random_numbers=True)
    with pytest.warns(UserWarning, match="pseudo-replication") as record:
        plan = study.plan()
    assert "36 runs use only 3 distinct seeds" in str(record[0].message)
    assert "common_random_numbers='levers'" in str(record[0].message)
    assert {canonical_json(s["seed"]) for s in plan} == {
        canonical_json(SeedTree(5).child("replication", r).to_dict()) for r in range(3)
    }


def test_false_gives_every_run_its_own_seed():
    pytest.importorskip("scipy")
    plan = make(common_random_numbers=False).plan()
    assert len({canonical_json(s["seed"]) for s in plan}) == len(plan)
    assert "independent seeds" in str(plan)


def test_full_factorial_and_sobol_keep_true_and_its_seeds():
    pytest.importorskip("scipy")
    factorial = make(uncertainties={"x": [0.0, 1.0]}, design=FullFactorial())
    sobol = make(uncertainties={"x": RANGE, "y": RANGE}, levers=None, design=Sobol(2))
    for study in (factorial, sobol):
        assert study.common_random_numbers is True
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            plan = study.plan()
        # run ids of the default mode are unchanged: replication r -> one seed
        for spec in plan:
            expected = SeedTree(5).child("replication", spec["replication"]).to_dict()
            assert spec["seed"] == expected


def test_levers_shares_seeds_only_across_levers_in_a_factorial():
    study = make(
        uncertainties={"x": [0.0, 1.0]},
        design=FullFactorial(),
        common_random_numbers="levers",
    )
    plan = study.plan()
    by_point = seeds_by(plan, lambda s: (s["factors"]["x"], s["replication"]))
    assert all(len(seeds) == 1 for seeds in by_point.values())
    x0 = {canonical_json(s["seed"]) for s in plan if s["factors"]["x"] == 0.0}
    x1 = {canonical_json(s["seed"]) for s in plan if s["factors"]["x"] == 1.0}
    assert not x0 & x1


def test_levers_without_uncertainties_equals_true():
    levers = make(uncertainties=None, design=None, common_random_numbers="levers")
    true = make(uncertainties=None, design=None, common_random_numbers=True)
    assert levers.plan().run_ids == true.plan().run_ids


def test_undeclared_design_paths_count_as_uncertainties():
    rows = [{"x": 0.0, "policy": "a"}, {"x": 0.0, "policy": "b"}, {"x": 1.0}]
    study = Study(
        scenario,
        levers={"policy": ["a", "b"]},
        design=Explicit(rows),
        seed=1,
        common_random_numbers="levers",
    )
    plan = study.plan()
    seeds = [canonical_json(spec["seed"]) for spec in plan]
    # x=0.0 with a and b share a seed; x=1.0 (crossed with a, b) has another
    assert seeds[0] == seeds[1] and seeds[2] == seeds[3] and seeds[0] != seeds[2]


def test_invalid_mode_names_the_choices():
    with pytest.raises(StudyError, match="'levers'") as info:
        make(common_random_numbers="lever")
    assert "None (choose by the design)" in str(info.value)


def test_mode_is_recorded_and_read_from_study_files(tmp_path):
    pytest.importorskip("scipy")
    study = make(out=tmp_path / "out")
    definition = study.definition(plan_quietly(study))
    assert definition["common_random_numbers"] == "levers"
    path = tmp_path / "crn.yaml"
    path.write_text(
        "scenario: tests.unit_tests.scenario.study_fixtures:scenario\n"
        "levers: {algorithm: [greedy, proportional]}\n"
        "common_random_numbers: levers\n"
    )
    assert load_study_file(path).common_random_numbers == "levers"
    path.write_text(
        "scenario: tests.unit_tests.scenario.study_fixtures:scenario\n"
        "levers: {algorithm: [greedy, proportional]}\n"
    )
    assert load_study_file(path).common_random_numbers is True  # by the design
