"""StudyFrame.sobol_indices on real studies run with the sequential executor."""

import sys
import types

import numpy as np
import pytest

from mango.scenario import (
    FullFactorial,
    Scenario,
    ScenarioParams,
    Sobol,
    Study,
    StudyError,
    load_study,
)

pytest.importorskip("scipy")
from scipy.stats import sobol_indices as scipy_sobol_indices  # noqa: E402


class AdditiveParams(ScenarioParams):
    x1: float = 0.0
    x2: float = 0.0
    x3: float = 0.0
    gain: float = 1.0
    noise: float = 0.0


def _make_scenario():
    built = Scenario("additive", params=AdditiveParams)
    built.time(horizon=1, step=1)
    built.record_world(
        "y",
        lambda ctx: (
            ctx.p.gain * (4 * ctx.p.x1 + 2 * ctx.p.x2 + 0 * ctx.p.x3)
            + ctx.p.noise * ctx.seeds.rng("noise").normal()
        ),
    )
    return built


scenario = _make_scenario()


def y(run):
    return float(run.ts("y").iloc[0])


def y_squared(run):
    return y(run) ** 2


def text(run):
    return "constant"


UNIT = {"range": [0.0, 1.0]}


@pytest.fixture(scope="module")
def additive(tmp_path_factory):
    """y = 4 x1 + 2 x2 + 0 x3 over uniform [0, 1]: S1 = ST = 0.8, 0.2, 0."""
    out = tmp_path_factory.mktemp("sobol") / "additive"
    study = Study(
        scenario,
        uncertainties={"x1": UNIT, "x2": UNIT, "x3": UNIT},
        design=Sobol(32),
        seed=11,
        outcomes=[y, y_squared, text],
        out=out,
        format="csv",
    )
    report = study.run(progress=False)
    assert report.ok and report.done == 32 * 5
    return load_study(out)


@pytest.fixture(scope="module")
def noisy(tmp_path_factory):
    """One sampled factor, a labeled lever and noisy replications."""
    out = tmp_path_factory.mktemp("sobol") / "noisy"
    study = Study(
        scenario,
        uncertainties={"x1": UNIT, "noise": 0.5},
        levers={"gain": {"flat": 0.0, "steep": 2.0}},
        design=Sobol(4),
        replications=2,
        common_random_numbers=False,  # noise differs per design point
        seed=5,
        outcomes=[y],
        out=out,
        format="csv",
    )
    assert study.run(progress=False).ok
    return load_study(out)


def test_recovers_known_indices_of_an_additive_model(additive):
    si = additive.sobol_indices("y")
    assert list(si.columns) == ["outcome", "factor", "S1", "S1_conf", "ST", "ST_conf"]
    assert si["factor"].tolist() == ["x1", "x2", "x3"]
    assert set(si["outcome"]) == {"y"}
    assert si["S1"].tolist() == pytest.approx([0.8, 0.2, 0.0], abs=0.1)
    assert si["ST"].tolist() == pytest.approx([0.8, 0.2, 0.0], abs=0.1)
    assert si.loc[2, "S1"] == 0.0 and si.loc[2, "ST"] == 0.0


def test_default_outcomes_are_the_numeric_ones(additive):
    si = additive.sobol_indices()
    assert si["outcome"].tolist() == ["y"] * 3 + ["y_squared"] * 3
    squared = si[si["outcome"] == "y_squared"]
    assert squared["ST"].iloc[0] > squared["ST"].iloc[1] > squared["ST"].iloc[2] == 0.0
    tidy = si.pivot(index="factor", columns="outcome", values="ST")
    assert list(tidy.index) == ["x1", "x2", "x3"]


def test_outcome_errors(additive):
    with pytest.raises(KeyError, match=r"No outcome\(s\) \['nope'\].*'y_squared'"):
        additive.sobol_indices(["y", "nope"])
    with pytest.raises(StudyError, match="'text' has no numeric values"):
        additive.sobol_indices("text")
    missing = additive.copy()
    missing.loc[3, "y"] = None
    with pytest.raises(
        StudyError, match=r"outcome 'y' has no finite value for run\(s\) \[3\]"
    ):
        missing.sobol_indices("y")


def test_incomplete_and_unfinished_designs(additive):
    with pytest.raises(StudyError, match=r"points of the Sobol design have no run"):
        additive.head(len(additive) - 1).sobol_indices("y")
    with pytest.raises(StudyError, match="lacks the AB runs"):
        additive[additive["index"] % 5 == 0].sobol_indices("y")
    failed = additive.copy()
    failed.loc[7, "status"] = "failed"
    with pytest.raises(
        StudyError,
        match=r"1 run\(s\) of the Sobol design are not done \(run indices \[7\]\)",
    ):
        failed.sobol_indices("y")
    with pytest.raises(StudyError, match=r"no second-order \(BA\) points"):
        additive.sobol_indices("y", second_order=True)


def _manual(frame, n, outcome="y"):
    """Average replications per Saltelli point, then call scipy directly."""
    import json

    store = {
        s["run_id"]: s
        for s in map(
            json.loads, (frame.study_path / "runs.jsonl").read_text().splitlines()
        )
    }
    slots = {}
    for run_id, value in zip(frame["run_id"], frame[outcome]):
        design = store[run_id]["design"]
        for meta in [design, *design.get("duplicates", [])]:
            slots.setdefault((meta["block"], meta["sample"]), []).append(value)
    mean = {key: np.mean(values) for key, values in slots.items()}
    f_a = np.array([[mean[("A", j)] for j in range(n)]] * 2)
    f_b = np.array([[mean[("B", j)] for j in range(n)]] * 2)
    f_ab = np.array([[[mean[("AB", j)] for j in range(n)]] * 2])
    result = scipy_sobol_indices(func={"f_A": f_a, "f_B": f_b, "f_AB": f_ab}, n=n)
    return float(np.ravel(result.first_order)[0]), float(
        np.ravel(result.total_order)[0]
    )


def test_per_level_combination_with_averaged_replications(noisy):
    assert len(noisy) == 2 * 2 * 4 * 2  # levels x replications x samples x (A, AB=B)
    si = noisy.sobol_indices()
    assert list(si.columns) == [
        "gain",
        "gain:label",
        "outcome",
        "factor",
        "S1",
        "S1_conf",
        "ST",
        "ST_conf",
    ]
    assert si["gain:label"].tolist() == ["flat", "steep"]
    assert si["gain"].tolist() == [0.0, 2.0]
    for (_, row), level in zip(si.iterrows(), (0.0, 2.0)):
        subset = noisy[noisy["gain"] == level]
        assert (row["S1"], row["ST"]) == pytest.approx(_manual(subset, 4))
        single = subset[subset["replication"] == 0]
        assert _manual(single, 4) != pytest.approx(_manual(subset, 4))
    first_replication = noisy[noisy["replication"] == 0].sobol_indices()
    assert first_replication["ST"].tolist() == pytest.approx(
        [
            _manual(noisy[(noisy["gain"] == g) & (noisy["replication"] == 0)], 4)[1]
            for g in (0.0, 2.0)
        ]
    )
    steep = noisy[noisy["gain"] == 2.0].sobol_indices()
    assert steep["gain:label"].tolist() == ["steep"]
    assert steep["ST"].iloc[0] > 0.5


def test_needs_a_sobol_study(tmp_path):
    study = Study(
        scenario,
        uncertainties={"x1": [0.0, 1.0]},
        design=FullFactorial(),
        seed=1,
        outcomes=[y],
        out=tmp_path / "ff",
        format="csv",
    )
    study.run(progress=False)
    with pytest.raises(
        StudyError, match=r"needs the runs of a Sobol design .*'full_factorial'"
    ):
        load_study(tmp_path / "ff").sobol_indices()


def test_needs_scipy(additive, monkeypatch):
    monkeypatch.setitem(sys.modules, "scipy.stats", None)
    with pytest.raises(ImportError, match=r"needs scipy >= 1.11"):
        additive.sobol_indices("y")


def _fake_salib(monkeypatch, calls):
    def analyze(
        problem,
        y,
        calc_second_order=True,
        print_to_console=False,
        conf_level=0.95,
        num_resamples=100,
        seed=None,
    ):
        calls.append(
            {
                "problem": problem,
                "y": np.asarray(y),
                "second": calc_second_order,
                "conf_level": conf_level,
                "num_resamples": num_resamples,
                "seed": seed,
            }
        )
        d = problem["num_vars"]
        s2 = np.full((d, d), np.nan)
        conf = np.full((d, d), np.nan)
        for i in range(d):
            for k in range(i + 1, d):
                s2[i, k] = 10 * i + k
                conf[i, k] = 0.01
        return {"S2": s2, "S2_conf": conf}

    sobol = types.ModuleType("SALib.analyze.sobol")
    sobol.analyze = analyze
    analyze_pkg = types.ModuleType("SALib.analyze")
    analyze_pkg.sobol = sobol
    salib = types.ModuleType("SALib")
    salib.analyze = analyze_pkg
    monkeypatch.setitem(sys.modules, "SALib", salib)
    monkeypatch.setitem(sys.modules, "SALib.analyze", analyze_pkg)
    monkeypatch.setitem(sys.modules, "SALib.analyze.sobol", sobol)


def test_second_order_indices_through_salib(tmp_path, monkeypatch):
    calls = []
    _fake_salib(monkeypatch, calls)
    study = Study(
        scenario,
        uncertainties={"x1": UNIT, "x2": UNIT, "x3": UNIT},
        design=Sobol(2, second_order=True),
        seed=3,
        outcomes=[y],
        out=tmp_path / "s2",
        format="csv",
    )
    assert len(study.plan()) == 2 * (2 * 3 + 2)
    study.run(progress=False)
    frame = load_study(tmp_path / "s2")
    si = frame.sobol_indices(second_order=True)
    assert list(si.columns) == ["outcome", "factor", "other", "S2", "S2_conf"]
    assert list(zip(si["factor"], si["other"], si["S2"])) == [
        ("x1", "x2", 1.0),
        ("x1", "x3", 2.0),
        ("x2", "x3", 12.0),
    ]
    (call,) = calls
    assert call["second"] and call["problem"]["names"] == ["x1", "x2", "x3"]
    # SALib's order per sample: A, AB_0..AB_2, BA_0..BA_2, B
    by_point = {
        (spec_block, column, sample): value
        for spec_block, column, sample, value in _points(frame)
    }
    expected = []
    for j in range(2):
        expected.append(by_point[("A", None, j)])
        expected += [by_point[("AB", i, j)] for i in range(3)]
        expected += [by_point[("BA", i, j)] for i in range(3)]
        expected.append(by_point[("B", None, j)])
    assert call["y"].tolist() == pytest.approx(expected)


def _points(frame):
    import json

    specs = {
        s["run_id"]: s
        for s in map(
            json.loads, (frame.study_path / "runs.jsonl").read_text().splitlines()
        )
    }
    for run_id, value in zip(frame["run_id"], frame["y"]):
        meta = specs[run_id]["design"]
        yield meta["block"], meta["column"], meta["sample"], value


# ----------------------------------------------------------------- confidence


def test_bootstrap_confidence_is_reproducible_and_brackets_the_truth(additive):
    with pytest.warns(UserWarning, match="n=32 base samples"):
        si = additive.sobol_indices("y")
    with pytest.warns(UserWarning):
        again = additive.sobol_indices("y")
    assert si.equals(again)  # drawn from the study seed
    assert (si["S1_conf"] >= 0).all() and (si["ST_conf"] >= 0).all()
    truth = [0.8, 0.2, 0.0]
    for index in ("S1", "ST"):
        low = si[index] - si[f"{index}_conf"]
        high = si[index] + si[f"{index}_conf"]
        assert all(lo - 1e-9 <= t <= hi + 1e-9 for lo, t, hi in zip(low, truth, high))
    narrow = additive.sobol_indices("y", confidence=0.5)
    assert (narrow["S1_conf"] <= si["S1_conf"] + 1e-12).all()


def test_confidence_none_skips_the_bootstrap(additive):
    with pytest.warns(UserWarning):
        si = additive.sobol_indices("y", confidence=None)
    assert list(si.columns) == ["outcome", "factor", "S1", "ST"]
    with pytest.raises(ValueError, match="between 0 and 1"):
        additive.sobol_indices("y", confidence=1.5)
    with pytest.raises(ValueError, match="n_resamples"):
        additive.sobol_indices("y", n_resamples=1)


def test_unreliable_estimates_are_named_in_one_warning(noisy):
    with pytest.warns(UserWarning) as record:
        noisy.sobol_indices()
    (warning,) = [w for w in record if "sobol_indices" in str(w.message)]
    text = str(warning.message)
    assert "n=4 base samples (fewer than 64" in text
    assert "outside [0, 1]" in text and "of y/x1 in the runs with gain=" in text


def test_second_order_confidence_is_passed_to_salib(tmp_path, monkeypatch):
    calls = []
    _fake_salib(monkeypatch, calls)
    study = Study(
        scenario,
        uncertainties={"x1": UNIT, "x2": UNIT},
        design=Sobol(2, second_order=True),
        seed=3,
        outcomes=[y],
        out=tmp_path / "s2c",
        format="csv",
    )
    study.run(progress=False)
    frame = load_study(tmp_path / "s2c")
    with pytest.warns(UserWarning):
        frame.sobol_indices(second_order=True, confidence=0.9, n_resamples=50)
        si = frame.sobol_indices(second_order=True, confidence=None)
    assert calls[0]["conf_level"] == 0.9 and calls[0]["num_resamples"] == 50
    assert isinstance(calls[0]["seed"], int)
    assert list(si.columns) == ["outcome", "factor", "other", "S2"]


def test_bootstrap_intervals_do_not_change_when_a_constant_is_added(additive):
    """N13: the first-order estimator is centred before the bootstrap, as in scipy."""
    shifted = additive.assign(y_shift=additive["y"] + 1000.0)
    table = shifted.sobol_indices(["y", "y_shift"])
    plain = table[table["outcome"] == "y"].reset_index(drop=True)
    moved = table[table["outcome"] == "y_shift"].reset_index(drop=True)
    for column in ("S1", "S1_conf", "ST", "ST_conf"):
        np.testing.assert_allclose(moved[column], plain[column], rtol=1e-9, atol=1e-9)
    assert (plain["S1_conf"] < 1.0).all()  # raw values + 1000 gave half-widths of ~100


def test_a_constant_outcome_has_nan_indices_and_a_warning(additive):
    flat = additive.assign(flat=0.0)
    with pytest.warns(UserWarning, match="'flat' is constant over the design"):
        table = flat.sobol_indices(["y", "flat"])
    rows = table[table["outcome"] == "flat"]
    assert rows[["S1", "S1_conf", "ST", "ST_conf"]].isna().all().all()
    assert table[table["outcome"] == "y"]["S1"].notna().all()
