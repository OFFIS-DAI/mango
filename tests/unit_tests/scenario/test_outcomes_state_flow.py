"""State vs. flow recorders: integrate, time_average, windows, calendar bins (N01, N12, N39)."""

import math
import statistics

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pd = pytest.importorskip("pandas")

from mango import Agent  # noqa: E402
from mango.scenario import Scenario, ScenarioError, load_run  # noqa: E402
from mango.scenario.analysis import t_quantile  # noqa: E402
from mango.scenario.script import generate_script  # noqa: E402

WARMUP = {"phases": [{"name": "warmup", "until": 3, "record": False}]}


class Toggle(Agent):
    """A level that a task sets to 1 at t=1 and back to 0 at t=10 (the true area is 9)."""

    def __init__(self):
        super().__init__()
        self.n = 0

    def on_ready(self):
        self.schedule_timestamp_task(self.set_to(1), 1.0)
        self.schedule_timestamp_task(self.set_to(0), 10.0)

    async def set_to(self, value):
        self.n = value


def toggle_scenario(step):
    scenario = Scenario("toggle")
    scenario.time(horizon=20, step=step)
    group = scenario.group("t", 1, lambda e: Toggle())
    scenario.record("n", group, attr="n", kind="state")
    scenario.record("n_mean", group, attr="n", every=5, window="mean", kind="state")
    scenario.record("n_max", group, attr="n", every=5, window="max", kind="state")
    scenario.record_world(
        "n_world",
        lambda ctx: ctx.agents("t")[0].n,
        every=5,
        window="mean",
        kind="state",
    )
    scenario.record("n_flow", group, attr="n", kind="flow")
    return scenario


@pytest.mark.parametrize("step", ["event", 1.0])
def test_a_state_is_integrated_forward_and_exactly(step):
    result = toggle_scenario(step).run(seed=1)
    assert result.integrate("n", unit="1s") == 9.0
    assert result.time_average("n") == pytest.approx(9 / 20)
    assert result.recorded_s == 20.0
    # window means are time-weighted means of the held levels: [0,5) .. [15,20)
    assert result.ts("n_mean").tolist() == pytest.approx([0.0, 0.8, 1.0, 0.0, 0.0])
    assert result.ts("n_world").tolist() == pytest.approx([0.0, 0.8, 1.0, 0.0, 0.0])
    assert result.integrate("n_mean", unit="1s") == pytest.approx(9.0)
    assert result.ts("n_max").tolist() == [0.0, 1.0, 1.0, 0.0, 0.0]
    assert result.record_kinds == {
        "n": "state",
        "n_mean": "flow",  # window statistics describe the window before the row
        "n_max": "flow",
        "n_world": "flow",
        "n_flow": "flow",
    }


def test_integrating_a_state_as_a_flow_is_biased_in_event_mode():
    result = toggle_scenario("event").run(seed=1)
    # the documented bias of the right-endpoint rule: each level is charged to
    # the interval before it was set
    assert result.integrate("n_flow", unit="1s") == 5.0
    assert result.integrate("n_flow", kind="state", unit="1s") == 9.0
    assert result.integrate("n", kind="flow", unit="1s") == 5.0


@pytest.mark.parametrize("step", ["event", 1.0])
def test_a_state_recorder_writes_a_row_at_the_start_of_a_recorded_phase(step):
    result = toggle_scenario(step).run(WARMUP, seed=1)
    n = result.ts("n")
    assert n.index[0] == 3.0 and n.iloc[0] == 1.0  # the level at the warm-up end
    rows = result.frame[result.frame["variable"] == "n"]
    assert set(rows["phase"]) == {"main"}  # a state row belongs to the phase it starts
    assert result.integrate("n", unit="1s") == 7.0  # [3, 10)
    assert result.recorded_s == 17.0
    assert result.time_average("n") == pytest.approx(7 / 17)
    assert result.integrate("n_mean", unit="1s") == pytest.approx(7.0)
    assert result.ts("n_mean").index.tolist() == [5.0, 10.0, 15.0, 20.0]
    # the flow recorder starts with the step after the warm-up, as before
    flow_times = result.ts("n_flow").index.tolist()
    assert flow_times[0] == (4.0 if step == 1.0 else 5.0)


def test_a_state_holds_until_a_record_false_phase_and_the_stop():
    phases = {
        "phases": [
            {"name": "eval", "until": 8},
            {"name": "cooldown", "record": False},
        ]
    }
    result = toggle_scenario("event").run(phases, seed=1)
    assert result.ts("n").index.max() < 8.0
    assert result.integrate("n", unit="1s") == 7.0  # [1, 8)
    assert result.integrate("n", phase="eval", unit="1s") == 7.0
    assert result.integrate("n", phase="cooldown", unit="1s") == 0.0
    assert result.recorded_s == 8.0
    stopped = toggle_scenario("event").run(until=6, seed=1)
    assert stopped.integrate("n", unit="1s") == 5.0  # [1, 6)
    assert stopped.recorded_s == 6.0


def test_integrate_asks_for_the_kind_instead_of_guessing():
    scenario = Scenario("toggle")
    scenario.time(horizon=20, step="event")
    group = scenario.group("t", 1, lambda e: Toggle())
    scenario.record("n", group, attr="n")
    result = scenario.run(seed=1)
    assert result.record_kinds == {}
    for method in (result.integrate, result.time_average):
        with pytest.raises(ValueError, match="kind='state' for a level"):
            method("n")
    with pytest.raises(ValueError, match="needs to know"):
        result.bins("n", 5)
    assert result.integrate("n", kind="state", unit="1s") == 9.0
    with pytest.raises(ValueError, match="use 'flow' or 'state'"):
        result.integrate("n", kind="level")
    with pytest.raises(KeyError, match="No variable 'm'"):
        result.integrate("m")  # an unknown variable fails first


def test_kinds_are_stored_with_the_run(tmp_path):
    result = toggle_scenario("event").run(WARMUP, seed=1)
    loaded = load_run(result.save(tmp_path / "run", format="csv"))
    assert loaded.manifest["record_kinds"]["n"] == "state"
    assert loaded.record_kinds == result.record_kinds
    assert loaded.integrate("n", unit="1s") == 7.0
    assert loaded.recorded_s == 17.0
    pd.testing.assert_frame_equal(loaded.frame, result.frame)


def test_kind_declarations_are_checked():
    scenario = Scenario("toggle")
    scenario.time(horizon=20, step="event")
    group = scenario.group("t", 1, lambda e: Toggle())
    with pytest.raises(ScenarioError, match="unknown kind 'stat'.*'state'"):
        scenario.record("n", group, attr="n", kind="stat")
    with pytest.raises(
        ScenarioError, match="window='sum' adds up the samples of a state"
    ):
        scenario.record("n", group, attr="n", every=5, window="sum", kind="state")
    with pytest.raises(ScenarioError, match="unknown kind"):
        scenario.record_world("w", lambda: 1, kind="levels")
    other = scenario.group("u", 1, lambda e: Toggle())
    scenario.record("n", group, attr="n", kind="state")
    scenario.record("n", other, attr="n", kind="flow")
    with pytest.raises(ScenarioError, match="different kind="):
        scenario.run(seed=1)


def test_the_kind_shows_in_the_plan_and_the_fingerprint():
    first = toggle_scenario("event")
    assert "(state)" in str(first.plan())
    plain = Scenario("toggle")
    plain.time(horizon=20, step="event")
    group = plain.group("t", 1, lambda e: Toggle())
    plain.record("n", group, attr="n")
    declared = Scenario("toggle")
    declared.time(horizon=20, step="event")
    group = declared.group("t", 1, lambda e: Toggle())
    declared.record("n", group, attr="n", kind="state")
    assert plain.fingerprint() != declared.fingerprint()


def test_yaml_recorders_take_kind(tmp_path, monkeypatch):
    from mango.scenario import load_scenario

    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "toggle_mod.py").write_text(
        "from tests.unit_tests.scenario.test_outcomes_state_flow import Toggle\n"
        "def make(e):\n    return Toggle()\n"
        "def level(ctx):\n    return ctx.agents('t')[0].n\n"
    )
    (tmp_path / "toggle.yaml").write_text(
        "time: {horizon: 20, step: event}\n"
        "groups:\n  t: {count: 1, factory: 'toggle_mod:make'}\n"
        "record:\n"
        "  - {name: n, group: t, attr: n, kind: state}\n"
        "  - {name: level, fn: 'toggle_mod:level', kind: state}\n"
    )
    result = load_scenario(tmp_path / "toggle.yaml").run(seed=1)
    assert result.integrate("n", unit="1s") == 9.0
    assert result.integrate("level", unit="1s") == 9.0
    (tmp_path / "bad.yaml").write_text(
        "time: {horizon: 20, step: event}\n"
        "groups:\n  t: {count: 1, factory: 'toggle_mod:make'}\n"
        "record:\n  - {name: n, group: t, attr: n, kind: level}\n"
    )
    with pytest.raises(ScenarioError, match="unknown kind 'level'"):
        load_scenario(tmp_path / "bad.yaml")


@pytest.mark.parametrize("step", ["event", 1.0])
@pytest.mark.parametrize("params", [None, WARMUP])
def test_to_script_records_states_like_the_run(tmp_path, step, params):
    from . import script_fixtures as fx

    scenario = toggle_scenario(step)
    script = generate_script(scenario, params, seed=1)
    assert "state=True" in script  # hold windows (and phase gates) of states
    rows, _ = fx.run_generated(script, tmp_path)
    run = scenario.run(params, seed=1)
    assert len(rows) == len(run.frame)
    # hold windows, a row at the start of the recorded phase, phase labels
    assert fx.rows_checksum(rows) == run.checksum()


# --------------------------------------------------------------------------- analytic queue


class MMcK(Agent):
    """An M/M/c/K queue (c servers, room for k customers); ``n`` changes in tasks: a state."""

    def __init__(self, lam, mu, c, k):
        super().__init__()
        self.lam, self.mu, self.c, self.k = lam, mu, c, k
        self.n = 0
        self.area = 0.0
        self.since = 0.0
        self.measure_from = 0.0

    def on_ready(self):
        self._schedule(self._arrive(), self.lam)

    def _schedule(self, coro, rate):
        delay = float(self.rng.exponential(1 / rate))
        self.schedule_timestamp_task(coro, self.current_timestamp + delay)

    def _change(self, delta):
        """Exact bookkeeping of the area under n, independent of the recorders."""
        now = self.current_timestamp
        start = max(self.since, self.measure_from)
        if now > start:
            self.area += self.n * (now - start)
        self.since = now
        self.n += delta

    async def _arrive(self):
        if self.n < self.k:
            self._change(+1)
            if self.n <= self.c:  # a free server
                self._schedule(self._depart(), self.mu)
        self._schedule(self._arrive(), self.lam)

    async def _depart(self):
        self._change(-1)
        if self.n >= self.c:  # the next waiting customer starts service
            self._schedule(self._depart(), self.mu)


def analytic_mean(lam, mu, c, k):
    """Mean number in an M/M/c/K system (a birth-death process)."""
    weights = [1.0]
    for n in range(k):
        weights.append(weights[-1] * lam / (min(n + 1, c) * mu))
    return sum(n * w for n, w in enumerate(weights)) / sum(weights)


QUEUE = {"lam": 1.0, "mu": 0.625, "c": 2, "k": 6}


def queue_scenario(horizon):
    scenario = Scenario("mmck")
    scenario.time(horizon=horizon, step="event")
    group = scenario.group("queue", 1, lambda e: MMcK(**QUEUE))
    scenario.record("n", group, attr="n", kind="state")
    scenario.record("n_100", group, attr="n", every=100, window="mean", kind="state")

    @scenario.on_build
    def start_measuring(ctx):
        ctx.agents("queue")[0].measure_from = 200.0

    @scenario.metric("area")
    def area(ctx):
        queue = ctx.agents("queue")[0]
        queue._change(0)  # account up to the end
        return queue.area

    return scenario


QUEUE_WARMUP = {"phases": [{"name": "warmup", "until": 200, "record": False}]}


def test_time_average_equals_an_exact_accumulator():
    result = queue_scenario(1500).run(QUEUE_WARMUP, seed=3)
    exact = result.metrics["area"] / 1300.0
    assert result.recorded_s == 1300.0
    assert result.time_average("n") == pytest.approx(exact, rel=1e-12)
    assert result.integrate("n_100", unit="1s") / 1300.0 == pytest.approx(
        exact, rel=1e-12
    )
    # the right-endpoint rule is biased upward for a queue (it leaves 0 upward only)
    assert result.integrate("n", kind="flow", unit="1s") / 1300.0 > exact


def test_time_average_covers_the_analytic_mean_queue_length():
    analytic = analytic_mean(**QUEUE)
    scenario = queue_scenario(1500)
    values = [
        scenario.run(QUEUE_WARMUP, seed=seed).time_average("n") for seed in range(6)
    ]
    mean, sd = statistics.mean(values), statistics.stdev(values)
    half = t_quantile(0.975, len(values) - 1) * sd / math.sqrt(len(values))
    assert mean - half <= analytic <= mean + half


# --------------------------------------------------------------------------- calendar bins


class LateLoad(Agent):
    """Draws 1 kW during 23:00-24:00 (computed in on_step for the coming step: a flow)."""

    def __init__(self):
        super().__init__()
        self.p_kw = 0.0

    def on_step(self, env, clock, step_size_s):
        self.p_kw = 1.0 if clock.to_datetime(clock.time).hour == 23 else 0.0


def late_load(start="2024-01-16", horizon="3d"):
    scenario = Scenario("late")
    scenario.time(start=start, horizon=horizon, step="15min")
    group = scenario.group("load", 1, lambda e: LateLoad())
    scenario.record("p_kw", group, attr="p_kw", kind="flow")
    return scenario


def test_calendar_bins_hold_each_day_exactly():
    result = late_load().run(seed=1)
    daily = result.bins("p_kw", "1D", how="integrate")
    assert daily.index.tolist() == list(
        pd.date_range("2024-01-16", periods=3, freq="D")
    )
    assert daily["load"].tolist() == [1.0, 1.0, 1.0]
    assert result.integrate("p_kw") == 3.0
    # the documented pitfall: end-labelled rows binned by their datetime
    naive = result.wide("p_kw", index="datetime").resample("1D").sum() * 0.25
    assert naive["load"].tolist() == [0.75, 1.0, 1.0, 0.25]
    hourly = result.bins("p_kw", "1h")
    assert len(hourly) == 72
    assert hourly["load"].iloc[23] == 1.0 and hourly["load"].iloc[24] == 0.0
    assert result.bins("p_kw", "1D", how="last")["load"].tolist() == [1.0] * 3
    with pytest.raises(ValueError, match="how='median'"):
        result.bins("p_kw", "1D", how="median")


def test_calendar_bins_follow_local_days_across_dst():
    berlin = "2024-03-30T00:00+01:00[Europe/Berlin]"
    result = late_load(start=berlin, horizon="47h").run(seed=1)
    daily = result.bins("p_kw", "1D", how="integrate")
    assert daily["load"].tolist() == [1.0, 1.0]
    covered = result.bins("p_kw", "1D", how="mean") * 24
    assert covered["load"].iloc[1] == pytest.approx(24 / 23)  # a 23 h day


def test_bins_without_a_calendar_use_durations():
    result = toggle_scenario("event").run(WARMUP, seed=1)
    table = result.bins("n", 5, how="integrate", unit="1s")
    assert table.index.name == "time"
    assert table.index.tolist() == [0.0, 5.0, 10.0, 15.0]
    assert table["t"].tolist() == [2.0, 5.0, 0.0, 0.0]
    means = result.bins("n", "5s")["t"].tolist()
    assert means == [1.0, 1.0, 0.0, 0.0]  # [3, 5) is the recorded part of the first bin


def test_time_average_after_a_warmup_matches_the_rows():
    """The template's mean_spread (N39): a warm-up must not dilute the mean."""
    scenario = Scenario("ramp")
    scenario.time(horizon=10, step=1)

    class Ramp(Agent):
        def __init__(self):
            super().__init__()
            self.x = 0.0

        def on_step(self, env, clock, step_size_s):
            self.x = clock.time

    group = scenario.group("r", 1, lambda e: Ramp())
    scenario.record("x", group, attr="x", kind="flow")
    result = scenario.run(
        {"phases": [{"name": "warmup", "until": 5, "record": False}]}, seed=1
    )
    assert result.time_average("x") == pytest.approx(result.ts("x").mean())
    assert result.time_average("x") != pytest.approx(
        result.integrate("x", unit="1s") / result.horizon_s
    )
