"""StudyFrame analysis: design columns, timeseries with factors, summary, paired, expand."""

import math
import statistics
import warnings

import pandas as pd
import pytest

from mango.scenario import (
    Lhs,
    Scenario,
    ScenarioParams,
    Study,
    StudyError,
    load_study,
)
from mango.scenario.analysis import mean_interval, t_quantile


class Params(ScenarioParams):
    x: float = 0.0
    gain: float = 1.0
    noise: float = 1.0
    tags: list[int] = []
    shape: dict = {}


def _value(ctx):
    return ctx.p.gain * ctx.p.x + ctx.p.noise * ctx.seeds.rng("noise").normal()


scenario = Scenario("analysis_toy", params=Params)
scenario.time(horizon=2, step=1)
scenario.record_world("v", _value)


def final(run):
    return float(run.ts("v").iloc[-1])


def parts(run):
    value = final(run)
    return {"a": value, "b": 2 * value}


def run_study(out, **kwargs):
    defaults = dict(
        uncertainties={"x": [0.0, 1.0, 2.0]},
        levers={"gain": {"low": 1.0, "high": 3.0}},
        replications=4,
        seed=11,
        outcomes=[final, parts],
        out=out,
        format="csv",
    )
    study = Study(scenario, **{**defaults, **kwargs})
    assert study.run(progress=False).ok
    return load_study(out)


@pytest.fixture(scope="module")
def crn(tmp_path_factory):
    return run_study(tmp_path_factory.mktemp("analysis") / "crn")


@pytest.fixture(scope="module")
def independent(tmp_path_factory):
    out = tmp_path_factory.mktemp("analysis") / "independent"
    return run_study(out, common_random_numbers=False)


# ----------------------------------------------------------------- design columns


def test_point_column_numbers_design_points(crn):
    assert list(crn.columns[:4]) == ["index", "run_id", "replication", "point"]
    assert crn["point"].nunique() == 6
    per_point = crn.groupby("point")[["x", "gain"]].nunique()
    assert (per_point == 1).all().all()  # one factor combination per point
    assert crn.groupby("point").size().tolist() == [4] * 6
    assert "subexperiment" not in crn.columns and "sample" not in crn.columns


def test_subexperiment_and_sample_columns(tmp_path):
    pytest.importorskip("scipy")
    study = Study(
        scenario,
        uncertainties={"x": {"range": [0.0, 1.0]}},
        design=Lhs(3),
        subexperiments=[{"gain": 1.0}, {"gain": 2.0}],
        seed=1,
        outcomes=[final],
        out=tmp_path / "subs",
        format="csv",
    )
    plan = study.plan()
    table = plan.to_frame()
    assert list(table.columns[:6]) == [
        "index",
        "run_id",
        "replication",
        "point",
        "subexperiment",
        "sample",
    ]
    assert table["subexperiment"].tolist() == [0, 0, 0, 1, 1, 1]
    assert table["sample"].tolist() == [0, 1, 2, 0, 1, 2]
    assert table["point"].tolist() == [0, 1, 2, 3, 4, 5]
    assert "subexperiment 0: gain: fixed 1.0" in str(plan)
    assert "subexperiment 1: gain: fixed 2.0" in str(plan)
    study.run(progress=False)
    frame = load_study(tmp_path / "subs")
    assert frame["subexperiment"].tolist() == [0, 0, 0, 1, 1, 1]
    assert frame["sample"].tolist() == [0, 1, 2, 0, 1, 2]


def test_list_and_dict_factor_values_are_groupable_json(tmp_path):
    frame = run_study(
        tmp_path / "json",
        uncertainties={"tags": {"one": [1], "two": [1, 2]}, "shape": [{"k": 1}]},
        levers=None,
        replications=2,
        outcomes=[final],
    )
    assert frame["tags"].tolist() == ["[1]", "[1]", "[1,2]", "[1,2]"]
    assert frame["shape"].tolist() == ['{"k":1}'] * 4
    assert frame.groupby("tags")["final"].size().to_dict() == {"[1]": 2, "[1,2]": 2}
    assert len(frame.drop_duplicates(["tags", "shape"])) == 2
    store_spec = frame.study_spec  # the run specs keep the values themselves
    assert store_spec["uncertainties"]["tags"] == {
        "levels": {"one": [1], "two": [1, 2]}
    }


# ----------------------------------------------------------------- timeseries


def test_timeseries_carries_the_run_columns(crn):
    ts = crn.timeseries(variables="v")
    assert list(ts.columns[:7]) == [
        "run_id",
        "time",
        "phase",
        "group",
        "entity",
        "variable",
        "value",
    ]
    assert list(ts.columns[7:]) == ["replication", "point", "x", "gain", "gain:label"]
    assert isinstance(ts["gain:label"].dtype, pd.CategoricalDtype)
    row = crn.set_index("run_id")
    for run_id, part in ts.groupby("run_id", observed=True):
        assert (part["x"] == row.loc[run_id, "x"]).all()
        assert (part["point"] == row.loc[run_id, "point"]).all()
    means = ts.groupby(["gain:label", "time"], observed=True)["value"].mean()
    assert means.index.nlevels == 2
    plain = crn.timeseries(variables="v", with_factors=False)
    assert list(plain.columns) == list(ts.columns[:7])
    sliced = crn[crn["gain:label"] == "high"].timeseries(variables="v")
    assert set(sliced["gain:label"]) == {"high"}


def test_timeseries_without_done_runs_has_the_columns(crn):
    empty = crn.iloc[0:0].timeseries()
    assert len(empty) == 0 and "x" in empty.columns


# ----------------------------------------------------------------- summary


def test_summary_per_design_point_is_a_t_interval_over_replications(crn):
    summary = crn.summary("final")
    assert list(summary.columns) == [
        "point",
        "x",
        "gain",
        "gain:label",
        "outcome",
        "runs",
        "missing",
        "n",
        "mean",
        "std",
        "ci_low",
        "ci_high",
    ]
    assert len(summary) == 6 and set(summary["n"]) == {4}
    first = crn[crn["point"] == 0]["final"].tolist()
    mean = statistics.fmean(first)
    half = t_quantile(0.975, 3) * statistics.stdev(first) / math.sqrt(4)
    row = summary.iloc[0]
    assert row["mean"] == pytest.approx(mean)
    assert (row["ci_low"], row["ci_high"]) == pytest.approx((mean - half, mean + half))


def test_coarse_summaries_count_shared_seeds_once(crn, independent):
    # common random numbers: the 12 runs of a gain level share 4 seeds
    by_gain = crn.summary("final", by="gain:label")
    assert by_gain["runs"].tolist() == [12, 12] and by_gain["n"].tolist() == [4, 4]
    # independent seeds: 12 units
    assert independent.summary("final", by="gain:label")["n"].tolist() == [12, 12]


def test_summary_defaults_and_options(crn):
    expanded = crn.expand("parts")
    outcomes = set(expanded.summary(confidence=None)["outcome"])
    assert outcomes == {"final", "parts.a", "parts.b"}
    assert "ci_low" not in expanded.summary("final", confidence=None).columns
    with pytest.raises(KeyError, match="Did you mean 'final'"):
        crn.summary("finale")
    with pytest.raises(KeyError, match="no column 'gian'"):
        crn.summary("final", by="gian")
    with pytest.raises(ValueError, match="between 0 and 1"):
        crn.summary("final", confidence=95)


# ----------------------------------------------------------------- paired


def test_paired_differences_cancel_common_noise(crn):
    by_x = crn.paired("gain", "final", baseline="low", by="x")
    assert by_x["x"].tolist() == [0.0, 1.0, 2.0]
    assert by_x["gain:label"].tolist() == ["high"] * 3
    assert set(by_x["baseline"]) == {"low"}
    # same seed in both runs: the difference is exactly (3 - 1) * x
    assert by_x["mean"].tolist() == pytest.approx([0.0, 2.0, 4.0])
    assert by_x["std"].tolist() == pytest.approx([0.0, 0.0, 0.0])
    assert by_x["pairs"].tolist() == [4, 4, 4] and by_x["n"].tolist() == [4, 4, 4]
    pooled = crn.paired("gain", "final")
    assert pooled["pairs"].tolist() == [12] and pooled["n"].tolist() == [4]
    assert pooled["mean"].tolist() == pytest.approx([2.0])


def test_paired_pairs_and_baselines(crn):
    pairs = crn.paired("gain", "final", baseline=3.0, pairs=True)
    assert list(pairs.columns) == [
        "x",
        "replication",
        "gain",
        "gain:label",
        "outcome",
        "baseline",
        "baseline_value",
        "value",
        "difference",
        "run_id",
        "baseline_run_id",
    ]
    assert len(pairs) == 12 and set(pairs["gain:label"]) == {"low"}
    assert (pairs["difference"] == pairs["value"] - pairs["baseline_value"]).all()
    with pytest.raises(KeyError, match="not a level"):
        crn.paired("gain", "final", baseline="medium")
    with pytest.raises(StudyError, match="cannot also be a by= column"):
        crn.paired("gain", "final", by="gain:label")
    with pytest.raises(StudyError, match="at least two"):
        crn[crn["gain:label"] == "low"].paired("gain", "final")


def test_paired_warns_about_unpaired_runs_and_unshared_seeds(crn, independent):
    with pytest.warns(
        UserWarning,
        match="1 done run of 'gain' has no counterpart.*1 has no "
        "counterpart in this frame",
    ):
        result = crn.iloc[1:].paired("gain", "final")
    assert result["pairs"].tolist() == [11]
    with pytest.warns(UserWarning, match="do not share seeds"):
        result = independent.paired("gain", "final")
    assert result["n"].tolist() == [12]


# ----------------------------------------------------------------- expand


def test_expand_turns_dict_outcomes_into_columns(crn):
    expanded = crn.expand()
    position = list(crn.columns).index("parts")
    assert list(expanded.columns[position : position + 2]) == ["parts.a", "parts.b"]
    assert "parts" not in expanded.columns
    assert (expanded["parts.b"] == 2 * expanded["parts.a"]).all()
    assert type(expanded).__name__ == "StudyFrame"
    assert expanded.study_path == crn.study_path
    assert list(crn.expand("parts", sep="/").columns).count("parts/a") == 1
    with pytest.raises(StudyError, match="not dicts"):
        crn.expand("final")


# ----------------------------------------------------------------- statistics


def test_t_quantile_without_scipy_is_close(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_scipy(name, *args, **kwargs):
        if name.startswith("scipy"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    expected = {
        (0.975, 1): 12.706,
        (0.975, 2): 4.303,
        (0.975, 3): 3.182,
        (0.975, 9): 2.262,
        (0.95, 4): 2.132,
        (0.995, 30): 2.750,
    }
    monkeypatch.setattr(builtins, "__import__", no_scipy)
    for (p, df), value in expected.items():
        assert t_quantile(p, df) == pytest.approx(value, abs=0.006)
    assert math.isnan(t_quantile(0.975, 0))


def test_mean_interval_edge_cases():
    single = mean_interval([2.0], 0.95)
    assert single["n"] == 1 and single["mean"] == 2.0 and math.isnan(single["ci_low"])
    assert "ci_low" not in mean_interval([1.0, 2.0], None)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert mean_interval([1.0, 1.0], 0.9)["ci_high"] == 1.0
