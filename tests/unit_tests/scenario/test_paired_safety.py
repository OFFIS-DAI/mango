"""paired() hardening (N03, N04), missing outcomes (N23), replication planning (N30),
log() with factors (N22), run order (N40), duplicate rows and shared seeds (N14, N38)."""

import math
import warnings
from typing import Literal

import pytest

pytest.importorskip("pandas")
from pydantic import BaseModel, Field  # noqa: E402

from mango.scenario import (  # noqa: E402
    Explicit,
    FromCsv,
    Scenario,
    ScenarioParams,
    Study,
    StudyError,
    load_study,
)
from mango.scenario.analysis import replications_for, t_quantile  # noqa: E402


class Greedy(BaseModel, extra="forbid"):
    kind: Literal["greedy"] = "greedy"
    threshold: float = 0.3


class Market(BaseModel, extra="forbid"):
    kind: Literal["market"] = "market"
    rounds: int = 5


class Params(ScenarioParams):
    algorithm: Greedy | Market = Field(default_factory=Greedy, discriminator="kind")
    beta: str = "none"
    x: float = 0.0
    censor: bool = False


def _score(ctx):
    algorithm = ctx.p.algorithm
    effect = (
        algorithm.threshold if algorithm.kind == "greedy" else algorithm.rounds / 10
    )
    return effect + ctx.p.x + ctx.seeds.rng("noise").normal()


scenario = Scenario("paired_toy", params=Params)
scenario.time(horizon=1, step=1)
scenario.record_world("score", _score, kind="flow")


def score(run):
    return float(run.ts("score").iloc[-1])


def censored(run):
    """NaN for some runs, as a time to an event that did not happen."""
    if run.params.censor and run.seed.get("spawn_key", [0])[-1] % 2 == 0:
        return math.nan
    return score(run)


def _run(out, **kwargs):
    defaults = dict(replications=3, seed=4, outcomes=[score], out=out, format="csv")
    study = Study(scenario, **{**defaults, **kwargs})
    assert study.run(progress=False).ok
    return load_study(out)


@pytest.fixture(scope="module")
def subexperiments(tmp_path_factory):
    return _run(
        tmp_path_factory.mktemp("paired") / "sub",
        subexperiments=[
            {"algorithm.kind": "greedy"},
            {"algorithm.kind": "market", "algorithm.rounds": [3, 10]},
        ],
    )


@pytest.fixture(scope="module")
def labeled(tmp_path_factory):
    return _run(
        tmp_path_factory.mktemp("paired") / "labeled",
        levers={
            "algorithm": {
                "levels": {
                    "greedy": {"kind": "greedy"},
                    "fast": {"kind": "market", "rounds": 3},
                    "slow": {"kind": "market", "rounds": 10},
                }
            }
        },
    )


# --------------------------------------------------------------------------- paired


def test_subexperiment_variants_pair_on_their_common_factors(subexperiments):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        table = subexperiments.paired("algorithm.kind", "score", baseline="greedy")
    assert table["algorithm.kind"].tolist() == ["market", "market"]
    assert table["algorithm.rounds"].tolist() == [3, 10]
    assert table["baseline"].tolist() == ["greedy", "greedy"]
    assert table["pairs"].tolist() == [3, 3]
    # common random numbers: the noise cancels, the effect remains
    assert table["mean"].tolist() == pytest.approx([0.0, 0.7])
    pairs = subexperiments.paired(
        "algorithm.kind", "score", baseline="greedy", pairs=True
    )
    assert (pairs["run_id"] != pairs["baseline_run_id"]).all()
    assert sorted(set(pairs["replication"])) == [0, 1, 2]


def test_runs_are_never_paired_with_themselves(subexperiments):
    with pytest.warns(
        UserWarning, match="3 done runs have no value of 'algorithm.rounds'"
    ):
        table = subexperiments.paired("algorithm.rounds", "score")
    assert table["baseline"].tolist() == ["3"]
    assert table["mean"].tolist() == pytest.approx([0.7])
    with pytest.warns(UserWarning, match="no value"):
        pairs = subexperiments.paired("algorithm.rounds", "score", pairs=True)
    assert (pairs["run_id"] != pairs["baseline_run_id"]).all()


def test_a_label_column_is_the_same_lever(labeled):
    by_label = labeled.paired("algorithm:label", "score", baseline="greedy")
    by_value = labeled.paired("algorithm", "score", baseline="greedy")
    assert by_label.equals(by_value)
    assert by_label["algorithm:label"].tolist() == ["fast", "slow"]
    assert "algorithm" not in by_label.columns  # its values are dict JSON
    assert by_label["mean"].tolist() == pytest.approx([0.0, 0.7])


def test_the_baseline_must_name_one_variant(tmp_path):
    frame = _run(
        tmp_path / "ambiguous",
        subexperiments=[
            {"algorithm.kind": "greedy", "algorithm.threshold": [0.2, 0.5]},
            {"algorithm.kind": "market"},
        ],
    )
    with pytest.raises(StudyError, match="covers 2 variants.*algorithm.threshold"):
        frame.paired("algorithm.kind", "score", baseline="greedy")
    table = frame.paired(
        "algorithm.kind",
        "score",
        baseline={"algorithm.kind": "greedy", "algorithm.threshold": 0.2},
    )
    assert table["baseline"].tolist()[0] == "greedy (algorithm.threshold=0.2)"
    assert len(table) == 2  # greedy 0.5 and market against greedy 0.2
    with pytest.raises(StudyError, match="not variant factors"):
        frame.paired(
            "algorithm.kind", "score", baseline={"algorithm.kind": "x", "y": 1}
        )


def test_a_nan_baseline_is_an_error(subexperiments):
    with pytest.raises(StudyError, match="baseline is NaN"):
        subexperiments.paired("algorithm.rounds", "score", baseline=math.nan)


def test_unmatched_runs_name_the_reason(subexperiments, labeled):
    # a filtered frame: the counterpart is missing, nothing failed
    with pytest.warns(
        UserWarning, match="2 have no counterpart in this frame"
    ) as caught:
        labeled.iloc[1:].paired("algorithm", "score", baseline="greedy")
    assert "failed or pending" not in str(caught[0].message)
    # a column only one side has: an error when nothing pairs
    with pytest.raises(StudyError, match="'algorithm.kind' never occurs"):
        subexperiments.paired("subexperiment", "score")


def test_missing_outcomes_are_counted_in_paired(labeled):
    frame = labeled.assign(score=labeled["score"].where(labeled["replication"] != 1))
    with pytest.warns(UserWarning, match="2 pairs lack a value of 'score'"):
        table = frame.paired("algorithm", "score", baseline="greedy")
    assert table["pairs"].tolist() == [2, 2]
    assert table["missing"].tolist() == [1, 1]


# --------------------------------------------------------------------------- summary


def test_summary_counts_and_warns_about_missing_values(labeled):
    frame = labeled.assign(score=labeled["score"].where(labeled["replication"] != 0))
    with pytest.warns(UserWarning, match="3 of 9 done runs have no value of 'score'"):
        table = frame.summary("score")
    assert table["runs"].tolist() == [2, 2, 2]
    assert table["missing"].tolist() == [1, 1, 1]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert labeled.summary("score")["missing"].tolist() == [0, 0, 0]


# --------------------------------------------------------------------------- planning


def test_replications_for_inverts_the_t_interval():
    n = replications_for(2.0, 0.5, 0.95)
    assert t_quantile(0.975, n - 1) * 2.0 / math.sqrt(n) <= 0.5
    assert t_quantile(0.975, n - 2) * 2.0 / math.sqrt(n - 1) > 0.5
    assert replications_for(0.0, 0.1, 0.95) == 2
    assert math.isnan(replications_for(math.nan, 0.1, 0.95))


def test_replications_needed_from_a_pilot(labeled):
    table = labeled.replications_needed("score", half_width=0.5)
    assert list(table.columns[-4:]) == ["half_width", "target", "needed", "additional"]
    for row in table.to_dict("records"):
        assert row["needed"] == replications_for(row["std"], 0.5, 0.95)
        assert row["additional"] == max(0, row["needed"] - row["n"])
    paired = labeled.replications_needed(
        "score", relative=0.5, lever="algorithm", baseline="greedy"
    )
    assert paired["algorithm:label"].tolist() == ["fast", "slow"]
    assert paired.loc[1, "target"] == pytest.approx(0.35)  # 0.5 x |0.7|
    with pytest.raises(ValueError, match="exactly one of half_width"):
        labeled.replications_needed("score")
    with pytest.raises(ValueError, match="positive number"):
        labeled.replications_needed("score", half_width=-1)


# --------------------------------------------------------------------------- log


def _logged(ctx):
    ctx.log("ticks", value=ctx.p.x)
    return ctx.p.x


logged = Scenario("logged_toy", params=Params)
logged.time(horizon=2, step=1)
logged.record_world("x", _logged, kind="flow")


def test_log_carries_the_run_columns(tmp_path):
    study = Study(logged, levers={"x": {"lo": 0.0, "hi": 1.0}}, replications=2, seed=1)
    assert study.run(out=tmp_path, progress=False).ok
    frame = load_study(tmp_path)
    ticks = frame.log("ticks")
    for column in ("replication", "point", "x", "x:label"):
        assert column in ticks.columns
    assert ticks.groupby("x:label", observed=True)["value"].mean().to_dict() == {
        "lo": 0.0,
        "hi": 1.0,
    }
    plain = frame.log("ticks", with_factors=False)
    assert "replication" not in plain.columns and "value" in plain.columns


# --------------------------------------------------------------------------- run order


def test_run_order_by_replication_interleaves_the_points(tmp_path):
    point = Study(scenario, levers={"x": [0.0, 1.0]}, replications=3, seed=1).plan()
    interleaved = Study(
        scenario,
        levers={"x": [0.0, 1.0]},
        replications=3,
        seed=1,
        run_order="replication",
    ).plan()
    assert [s["replication"] for s in point] == [0, 1, 2, 0, 1, 2]
    assert [s["replication"] for s in interleaved] == [0, 0, 1, 1, 2, 2]
    assert [s["index"] for s in interleaved] == list(range(6))
    assert set(point.run_ids) == set(interleaved.run_ids)
    assert "run order by replication" in str(interleaved)
    assert interleaved.study.definition(interleaved)["run_order"] == "replication"
    with pytest.raises(StudyError, match="run_order must be"):
        Study(scenario, run_order="random")


def test_study_files_take_run_order(tmp_path, monkeypatch):
    from mango.scenario import load_study_file

    monkeypatch.chdir(tmp_path)
    (tmp_path / "s.yaml").write_text(
        "scenario: tests.unit_tests.scenario.test_paired_safety:scenario\n"
        "levers: {x: [0.0, 1.0]}\nreplications: 2\nrun_order: replication\n"
    )
    plan = load_study_file(tmp_path / "s.yaml").plan()
    assert [s["replication"] for s in plan] == [0, 0, 1, 1]


# --------------------------------------------------------------------------- designs


def test_identical_design_rows_warn_and_count_as_one_point():
    rows = [{"x": 1.0}, {"x": 1.0}, {"x": 2.0}]
    with pytest.warns(UserWarning, match="rows 0 and 1 are identical"):
        plan = Study(scenario, design=Explicit(rows), replications=2, seed=1).plan()
    assert len(plan) == 4
    assert "3 design points (2 distinct)" in str(plan)
    assert "4 runs on 2 seeds" in str(plan)


def test_design_rows_set_their_own_replications(tmp_path):
    plan = Study(
        scenario,
        design=Explicit([{"x": 1.0}, {"x": 2.0, "replications": 5}]),
        replications=2,
        seed=1,
    ).plan()
    assert [(s["factors"]["x"], s["replication"]) for s in plan] == [
        (1.0, 0),
        (1.0, 1),
        *((2.0, r) for r in range(5)),
    ]
    assert plan.specs[2]["design"] == {"row": 1, "replications": 5}
    assert "2 design points x 2 to 5 replications (study default 2)" in str(plan)
    # replications stay nested: the equal-allocation runs are among them
    equal = Study(
        scenario, design=Explicit([{"x": 1.0}, {"x": 2.0}]), seed=1, replications=2
    ).plan()
    assert {s["run_id"] for s in equal} <= {s["run_id"] for s in plan}
    path = tmp_path / "design.csv"
    path.write_text("x,replications\n1.0,1\n2.0,3\n")
    plan = Study(scenario, design=FromCsv(path), replications=2, seed=1).plan()
    assert [s["replication"] for s in plan] == [0, 0, 1, 2]
    with pytest.raises(StudyError, match="row 0: replications must be an int >= 1"):
        Study(scenario, design=Explicit([{"x": 1.0, "replications": 0}])).plan()


def test_subexperiments_set_their_own_replications(tmp_path):
    from mango.scenario.yaml_scenario import load_study_file

    study = Study(
        scenario,
        levers={"x": [0.0]},
        subexperiments=[{}, {"x": [1.0], "replications": 3}],
        replications=1,
        seed=1,
    )
    plan = study.plan()
    assert [(s["factors"]["x"], s["replication"]) for s in plan] == [
        (0.0, 0),
        (1.0, 0),
        (1.0, 1),
        (1.0, 2),
    ]
    assert study.definition(plan)["subexperiments"][1]["replications"] == 3
    (tmp_path / "s.yaml").write_text(
        "scenario: tests.unit_tests.scenario.test_paired_safety:scenario\n"
        "levers: {x: [0.0]}\nsubexperiments: [{}, {x: [1.0], replications: 3}]\n"
    )
    assert len(load_study_file(tmp_path / "s.yaml").plan()) == 4


def test_a_large_csv_design_under_shared_seeds_warns(tmp_path):
    path = tmp_path / "design.csv"
    path.write_text("x\n" + "\n".join(str(i / 10) for i in range(10)) + "\n")
    with pytest.warns(UserWarning, match="block on replication"):
        Study(scenario, design=FromCsv(path), replications=2, seed=1).plan()
    small = tmp_path / "small.csv"
    small.write_text("x\n0.0\n1.0\n")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        Study(scenario, design=FromCsv(small), replications=2, seed=1).plan()
        Study(
            scenario,
            design=FromCsv(path),
            replications=2,
            seed=1,
            common_random_numbers="levers",
        ).plan()
