"""Sampling designs: Lhs, RandomSample and the Saltelli design of Sobol."""

import math
import re
import sys
import warnings

import pytest

from mango.scenario import Lhs, RandomSample, Sobol, Study, StudyError
from mango.scenario.designs import (
    SOBOL_BLOCKS,
    Factor,
    FullFactorial,
    design_kinds,
    resolve_design,
)
from mango.util.seeding import SeedTree

from .study_fixtures import scenario

pytest.importorskip("scipy")

SEEDS = SeedTree(2026).child("design")


def _factors():
    return [
        Factor.parse("x", {"range": [10.0, 20.0]}),
        Factor.parse("k", {"int_range": [1, 4]}),
        Factor.parse("s", {"log_range": [0.01, 100.0]}),
        Factor.parse("gain", 0.5),
        Factor.parse("algorithm", ["a", "b"], "lever"),
    ]


def _check_bounds(points):
    for p in points:
        assert list(p.values) == ["x", "k", "s", "gain", "algorithm"]
        assert 10.0 <= p.values["x"] <= 20.0 and isinstance(p.values["x"], float)
        assert p.values["k"] in (1, 2, 3, 4) and isinstance(p.values["k"], int)
        assert 0.01 <= p.values["s"] <= 100.0
        assert p.values["gain"] == 0.5


# ----------------------------------------------------------------- Lhs and RandomSample


@pytest.mark.parametrize("design", [Lhs(8), Lhs(8, scramble=False), RandomSample(8)])
def test_sizes_bounds_crossing_and_fixed_factors(design):
    points = design.generate(_factors(), SEEDS)
    assert len(points) == 8 * 2
    _check_bounds(points)
    # samples vary slowest; every sample is crossed with both algorithms
    assert [p.meta for p in points[:4]] == [{"sample": 0}] * 2 + [{"sample": 1}] * 2
    assert [p.values["algorithm"] for p in points[:4]] == ["a", "b", "a", "b"]
    for first, second in zip(points[::2], points[1::2]):
        assert {k: v for k, v in first.values.items() if k != "algorithm"} == {
            k: v for k, v in second.values.items() if k != "algorithm"
        }


def test_lhs_stratifies_every_factor():
    factors = [
        Factor.parse("x", {"range": [0.0, 1.0]}),
        Factor.parse("k", {"int_range": [0, 9]}),
    ]
    points = Lhs(10).generate(factors, SEEDS)
    assert sorted(math.floor(p.values["x"] * 10) for p in points) == list(range(10))
    assert sorted(p.values["k"] for p in points) == list(range(10))
    centred = Lhs(4, scramble=False).generate(factors[:1], SEEDS)
    assert sorted(p.values["x"] for p in centred) == [0.125, 0.375, 0.625, 0.875]


def test_log_range_is_log_uniform():
    points = RandomSample(400).generate(
        [Factor.parse("s", {"log_range": [0.01, 100.0]})], SEEDS
    )
    below_one = sum(p.values["s"] < 1.0 for p in points)
    assert 160 < below_one < 240  # half of the log range lies below 1


@pytest.mark.parametrize("design", [Lhs(6), RandomSample(6), Sobol(4)])
def test_designs_are_deterministic_per_seed(design):
    factors = _factors()
    first = design.generate(factors, SeedTree(1).child("design"))
    again = design.generate(factors, SeedTree(1).child("design"))
    other = design.generate(factors, SeedTree(2).child("design"))
    assert [p.values for p in first] == [p.values for p in again]
    assert [p.values for p in first] != [p.values for p in other]


def test_random_sample_needs_no_scipy(monkeypatch):
    monkeypatch.setitem(sys.modules, "scipy.stats", None)
    assert len(RandomSample(3).generate(_factors(), SEEDS)) == 6
    with pytest.raises(ImportError, match=r"mango-agents\[scenario-all\]"):
        Lhs(3).generate(_factors(), SEEDS)
    with pytest.raises(ImportError, match="need scipy"):
        Sobol(4).generate(_factors(), SEEDS)


def test_sampling_designs_need_a_continuous_factor():
    with pytest.raises(StudyError, match="samples continuous factors"):
        Lhs(4).generate([Factor.parse("algorithm", ["a", "b"])], SEEDS)


def test_registry_specs_and_reprs():
    assert {"lhs", "sobol", "random"} <= set(design_kinds())
    assert repr(resolve_design({"lhs": {"n": 64}})) == "Lhs(n=64)"
    assert repr(resolve_design({"lhs": 5})) == "Lhs(n=5)"
    sobol = resolve_design({"sobol": {"n": 256, "second_order": False}})
    assert isinstance(sobol, Sobol) and sobol.n == 256 and not sobol.second_order
    for design in (Lhs(8, scramble=False), RandomSample(3), Sobol(8, scramble=False)):
        again = resolve_design(design.to_spec())
        assert type(again) is type(design) and again.to_spec() == design.to_spec()
    assert Sobol(8).to_spec() == {
        "sobol": {"n": 8, "second_order": False, "scramble": True}
    }
    assert repr(Sobol(8, scramble=False)) == "Sobol(n=8, scramble=False)"
    assert repr(Lhs(2, scramble=False)) == "Lhs(n=2, scramble=False)"
    assert repr(RandomSample(2)) == "RandomSample(n=2)"


@pytest.mark.parametrize("cls", [Lhs, RandomSample, Sobol])
@pytest.mark.parametrize("n", [0, -3, 2.5, True, "8", None])
def test_invalid_n(cls, n):
    with pytest.raises(
        StudyError, match=r"needs n, the number of samples, as an int >= 1"
    ):
        cls(n)


def test_invalid_flags():
    with pytest.raises(StudyError, match="scramble must be True or False"):
        Lhs(4, scramble="yes")
    with pytest.raises(StudyError, match="second_order must be True or False"):
        Sobol(4, second_order=1)
    with pytest.raises(StudyError, match="Invalid arguments for design 'lhs'"):
        resolve_design({"lhs": {"m": 4}})


# ----------------------------------------------------------------- Sobol / Saltelli


def test_sobol_rounds_n_up_to_a_power_of_two():
    with pytest.warns(UserWarning, match=r"n=100 is not a power of two; using n=128"):
        assert Sobol(100).n == 128
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert Sobol(1).n == 1 and Sobol(64).n == 64


def test_sobol_size_meta_and_crossing():
    points = Sobol(4).generate(_factors(), SEEDS)
    d = 3
    assert len(points) == 4 * (d + 2) * 2
    _check_bounds(points)
    metas = [p.meta for p in points[::2]]
    assert metas[: d + 2] == [
        {"block": "A", "column": None, "sample": 0},
        {"block": "AB", "column": 0, "sample": 0, "factor": "x"},
        {"block": "AB", "column": 1, "sample": 0, "factor": "k"},
        {"block": "AB", "column": 2, "sample": 0, "factor": "s"},
        {"block": "B", "column": None, "sample": 0},
    ]
    assert {m["block"] for m in metas} <= set(SOBOL_BLOCKS)
    assert [m["sample"] for m in metas] == [j for j in range(4) for _ in range(d + 2)]
    assert [p.values["algorithm"] for p in points[:2]] == ["a", "b"]


def _blocks(points, combo="a"):
    table = {}
    for p in points:
        if p.values.get("algorithm", "a") != combo:
            continue
        table[(p.meta["block"], p.meta["column"], p.meta["sample"])] = p.values
    return table


@pytest.mark.parametrize("second_order", [False, True])
def test_saltelli_structure(second_order, monkeypatch):
    if second_order:
        monkeypatch.setitem(sys.modules, "SALib", type(sys)("SALib"))
    design = Sobol(8, second_order=second_order)
    factors = _factors()
    points = design.generate(factors, SEEDS)
    sampled = ["x", "k", "s"]
    per_sample = 2 * len(sampled) + 2 if second_order else len(sampled) + 2
    assert len(points) == 8 * per_sample * 2
    for combo in ("a", "b"):
        table = _blocks(points, combo)
        for j in range(8):
            a, b = table[("A", None, j)], table[("B", None, j)]
            assert all(a[path] != b[path] for path in ("x", "s"))
            for i, path in enumerate(sampled):
                ab = table[("AB", i, j)]
                assert ab == {**a, path: b[path]}
                if second_order:
                    assert table[("BA", i, j)] == {**b, path: a[path]}
            if not second_order:
                assert not any(key[0] == "BA" for key in table)


def test_sobol_matrices_are_independent_and_balanced():
    factors = [
        Factor.parse("x", {"range": [0.0, 1.0]}),
        Factor.parse("y", {"range": [0.0, 1.0]}),
    ]
    table = _blocks(Sobol(16).generate(factors, SEEDS))
    for block in ("A", "B"):
        for path in ("x", "y"):
            column = [table[(block, None, j)][path] for j in range(16)]
            # a (scrambled) Sobol' sequence of 2^m points hits every 1/16 stratum once
            assert sorted(math.floor(v * 16) for v in column) == list(range(16))
    unscrambled = _blocks(Sobol(2, scramble=False).generate(factors, SEEDS))
    assert unscrambled[("A", None, 0)] == {"x": 0.0, "y": 0.0}


def test_second_order_needs_salib(monkeypatch):
    monkeypatch.setitem(sys.modules, "SALib", None)
    with pytest.raises(ImportError, match="pip install SALib"):
        Sobol(8, second_order=True)
    with pytest.raises(ImportError, match="second_order=False"):
        resolve_design({"sobol": {"n": 8, "second_order": True}})


def test_full_factorial_still_rejects_ranges():
    with pytest.raises(StudyError, match="use a sampling design"):
        FullFactorial().generate(_factors(), SEEDS)


# ----------------------------------------------------------------- through a study


def _study(design, **kwargs):
    kwargs.setdefault(
        "uncertainties", {"gain": {"range": [0.0, 0.2]}, "ev.n": {"int_range": [1, 3]}}
    )
    kwargs.setdefault("levers", {"algorithm": ["greedy", "proportional"]})
    return Study(scenario, design=design, seed=kwargs.pop("seed", 7), **kwargs)


def test_study_plan_with_sobol():
    plan = _study(Sobol(4), replications=2).plan()
    assert plan.points == 4 * (2 + 2) * 2
    assert len(plan) + plan.dropped == plan.points * 2
    for spec in plan:
        assert spec["design"]["block"] in SOBOL_BLOCKS
        assert 0.0 <= spec["factors"]["gain"] <= 0.2
        assert spec["params"]["gain"] == spec["factors"]["gain"]
        assert spec["params"]["ev"]["n"] == spec["factors"]["ev.n"]
    assert re.search(
        r"Sobol\(n=4\), 32 design points \(\d+ distinct\) x 2 replications", str(plan)
    )
    definition = _study(Sobol(4)).definition(plan)
    assert definition["design"] == {
        "sobol": {"n": 4, "second_order": False, "scramble": True}
    }
    assert definition["uncertainties"] == {
        "gain": {"range": [0.0, 0.2]},
        "ev.n": {"int_range": [1, 3]},
    }


def test_study_design_is_deterministic_per_study_seed():
    first = _study(Lhs(5)).plan()
    again = _study(Lhs(5)).plan()
    other = _study(Lhs(5), seed=8).plan()
    assert [s["factors"] for s in first] == [s["factors"] for s in again]
    assert first.run_ids == again.run_ids
    assert [s["factors"] for s in first] != [s["factors"] for s in other]
    # the design seed is the study's design stream
    direct = Lhs(5).generate(_study(Lhs(5)).factors, SeedTree(7).child("design"))
    assert [s["factors"] for s in first] == [p.values for p in direct]


def test_study_records_dropped_saltelli_points_on_the_kept_run():
    # one sampled factor: AB_0 is B, so every B point duplicates its AB_0 point
    plan = _study(
        Sobol(4), uncertainties={"gain": {"range": [0.0, 0.2]}}, levers=None
    ).plan()
    assert plan.points == 4 * 3 and plan.dropped == 4 and len(plan) == 8
    ab = [spec for spec in plan if spec["design"]["block"] == "AB"]
    assert len(ab) == 4
    for spec in ab:
        assert spec["design"]["duplicates"] == [
            {"block": "B", "column": None, "sample": spec["design"]["sample"]}
        ]
    assert all(
        "duplicates" not in spec["design"]
        for spec in plan
        if spec["design"]["block"] == "A"
    )


def test_study_rejects_ranges_with_full_factorial():
    with pytest.raises(
        StudyError, match="FullFactorial cannot cross the continuous factor 'gain'"
    ):
        _study(None).plan()
