"""Early-stop and time-to-event tools: exact grid times, metric read-back,
per-run scratch state and padded study time series."""

import math
import runpy

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("pandas")

from mango import Agent  # noqa: E402
from mango.scenario import (  # noqa: E402
    Scenario,
    ScenarioError,
    ScenarioParams,
    Study,
    load_study,
)


class Clocked(Agent):
    def __init__(self):
        super().__init__()
        self.x = 0.0

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        self.x = clock.time


class Threshold(ScenarioParams):
    thr: float = 3.95


def threshold_scenario():
    scenario = Scenario("ttc", params=Threshold)
    scenario.time(horizon="10s", step="event")
    scenario.group("a", 1, lambda e: Clocked())
    scenario.record_world("x", lambda ctx: ctx.agents("a")[0].x, every="1s")

    @scenario.stop_when(every=0.1)
    def reached(ctx):
        return ctx.agents("a")[0].x >= ctx.p.thr

    return scenario


def test_every_grid_times_are_exact_decimals():
    result = threshold_scenario().run(seed=1)
    assert result.stats["stop_time_s"] == 4.1  # not 4.1000000000000005
    assert result.stats["sim_time_s"] == 4.1
    assert result.ts("x").index.tolist() == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_fixed_step_grid_times_are_exact_decimals():
    scenario = Scenario("tenths")
    scenario.time(horizon=1, step=0.1)
    scenario.group("a", 1, lambda e: Clocked())
    scenario.record("x", "a", attr="x")
    times = scenario.run(seed=1).ts("x").index.tolist()
    assert times == [round(k * 0.1, 9) for k in range(11)]
    assert 0.3 in times and 0.7 in times


def settled(ctx):
    t = ctx.world.clock.time
    if t >= 3 and ctx.get_metric("t_first") is None:
        ctx.set_metric("t_first", t)
    ctx.state["checks"] = ctx.state.get("checks", 0) + 1
    return ctx.state["checks"] >= 6


def clocked(e):
    return Clocked()


def first_and_stable():
    scenario = Scenario("conv")
    scenario.time(horizon=20, step=1)
    scenario.group("a", 1, clocked)
    scenario.record("x", "a", attr="x")
    scenario.stop_when(settled)
    return scenario


def test_ctx_get_metric_and_state_keep_values_within_one_run():
    first = first_and_stable().run(seed=1)
    assert first.metrics["t_first"] == 3.0
    assert first.stats["stop_time_s"] == 6.0
    again = first_and_stable().run(seed=1)  # a fresh state per run
    assert again.stats["stop_time_s"] == 6.0


def test_get_metric_and_state_need_a_running_run():
    from mango.scenario.context import BuildContext, RunState
    from mango.util.seeding import SeedTree

    scenario = Scenario("early")
    ctx = BuildContext(RunState(scenario, scenario.resolve_params(None), SeedTree(1)))
    with pytest.raises(ScenarioError, match="ctx.state needs a running run"):
        ctx.state  # noqa: B018
    with pytest.raises(ScenarioError, match=r"ctx.get_metric\(\) needs a running run"):
        ctx.get_metric("x")


def test_the_script_offers_get_metric_and_state(tmp_path):
    scenario = first_and_stable()
    source = scenario.to_script(seed=1)
    assert "def get_metric(self, name, default=None):" in source
    assert "def state(self):" in source or "self.state = {}" in source
    path = tmp_path / "generated.py"
    path.write_text(source, encoding="utf-8")
    rows = runpy.run_path(str(path), run_name="generated")["main"]()
    assert max(row["time"] for row in rows) == 6.0


def test_timeseries_pad_carries_stopped_runs_to_the_common_end(tmp_path):
    study = Study(
        threshold_scenario(),
        uncertainties={"thr": [2.5, 50.0]},
        seed=3,
        outcomes={
            "ttc": lambda run: (
                run.stats["stop_time_s"] if run.stats["stopped_early"] else math.nan
            )
        },
        out=tmp_path / "out",
        format="csv",
    )
    study.run(progress=False)
    frame = load_study(tmp_path / "out")
    plain = frame.timeseries(variables="x")
    assert "padded" not in plain.columns
    padded = frame.timeseries(variables="x", pad=True)
    stopped = frame.loc[frame["thr"] == 2.5, "run_id"].item()
    full = frame.loc[frame["thr"] == 50.0, "run_id"].item()
    rows = padded[padded["run_id"] == stopped]
    assert rows["time"].tolist() == [float(t) for t in range(11)]
    assert rows["padded"].tolist() == [False] * 3 + [True] * 8
    last = rows.loc[~rows["padded"], "value"].iloc[-1]
    assert (rows.loc[rows["padded"], "value"] == last).all()  # the last value held
    assert not padded.loc[padded["run_id"] == full, "padded"].any()
    means = padded.groupby("time")["value"].mean()
    end = padded.loc[(padded["run_id"] == full) & (padded["time"] == 10.0), "value"]
    assert len(means) == 11 and means.loc[10.0] == pytest.approx(
        (last + end.item()) / 2
    )
