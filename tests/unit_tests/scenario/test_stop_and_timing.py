"""Within-step timing and sample semantics (C02, C12), early stops (C22) and
truncated smoke runs (C34)."""

import json
import textwrap

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from mango import Agent  # noqa: E402
from mango.scenario import (  # noqa: E402
    Scenario,
    ScenarioError,
    load_run,
    load_scenario,
)

H = 3600.0


class Power(Agent):
    """Draws ``1 + t/1h`` kW during the step that starts at t (computed in on_step)."""

    def __init__(self):
        super().__init__()
        self.power_kw = 0.0
        self.seen = -1.0

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        self.seen = clock.time
        self.power_kw = 1.0 + clock.time / H


def power_scenario(**time):
    scenario = Scenario("power")
    scenario.time(**{"horizon": "6h", "step": "1h", **time})
    group = scenario.group("meter", 2, lambda e: Power())
    scenario.record("power_kw", group, attr="power_kw", kind="flow")
    scenario.record("seen", group, attr="seen")
    return scenario


# --------------------------------------------------------------------------- C12


def test_a_row_is_the_state_after_the_step_that_ended_at_its_time():
    result = power_scenario().run(seed=1)
    seen = result.wide("seen")["meter_0"]
    assert seen.loc[0.0] == -1.0  # the initial state
    assert all(seen.loc[t] == t - H for t in seen.index if t > 0)


def test_warmup_rows_are_attributed_by_interval_and_integrate_is_exact():
    params = {"phases": [{"name": "warmup", "until": "2h", "record": False}]}
    result = power_scenario().run(params, seed=1)
    power = result.ts("power_kw", entity="meter_0")
    # 4 evaluated steps [2h,3h) .. [5h,6h): 4 rows, not 5
    assert power.index.tolist() == [3 * H, 4 * H, 5 * H, 6 * H]
    assert power.tolist() == [3.0, 4.0, 5.0, 6.0]
    # the energy of [2h, 6h) for one meter: 3 + 4 + 5 + 6 kWh
    assert result.integrate("power_kw", entity="meter_0") == 18.0
    assert result.ts("power_kw").sum() * result.step_h == 36.0
    assert result.integrate("power_kw", per_entity=True).to_dict() == {
        "meter_0": 18.0,
        "meter_1": 18.0,
    }
    assert result.integrate("power_kw", unit="1min", entity="meter_1") == 18.0 * 60


def test_integrate_without_phases_leaves_the_initial_row_out():
    result = power_scenario().run(seed=1)
    # steps [0,1h) .. [5h,6h) draw 1 .. 6 kW
    assert result.integrate("power_kw", entity="meter_0") == 21.0
    assert result.integrate("power_kw", phase="main", entity="meter_0") == 21.0
    with pytest.raises(KeyError, match="no phase 'eval'"):
        result.integrate("power_kw", phase="eval")
    with pytest.raises(KeyError, match="No variable 'pwr'"):
        result.integrate("pwr")


def test_integrate_with_every_and_in_event_mode():
    scenario = Scenario("power")
    scenario.time(horizon="6h", step="1h")
    group = scenario.group("meter", 1, lambda e: Power())
    scenario.record("power_kw", group, attr="power_kw", every="2h", kind="flow")
    result = scenario.run(seed=1)
    # rows at 2h, 4h, 6h hold the power of the steps [1h,2h), [3h,4h), [5h,6h)
    assert result.integrate("power_kw") == (2 + 4 + 6) * 2.0
    event_mode = power_scenario(step="event").run(seed=1)
    # one step to the horizon: the row at 6h covers [0, 6h) with 1 kW
    assert event_mode.integrate("power_kw", entity="meter_0") == 6.0


def test_integrate_rejects_text_values():
    scenario = Scenario("text")
    scenario.time(horizon="2h", step="1h")
    group = scenario.group("meter", 1, lambda e: Power())
    scenario.record("label", group, lambda a: "x", kind="flow")
    with pytest.raises(TypeError, match="non-numeric"):
        scenario.run(seed=1).integrate("label")


def test_integrate_works_on_loaded_runs(tmp_path):
    params = {"phases": [{"name": "warmup", "until": "2h", "record": False}]}
    result = power_scenario().run(params, seed=1)
    loaded = load_run(result.save(tmp_path / "run", format="csv"))
    assert loaded.integrate("power_kw") == result.integrate("power_kw") == 36.0


class Requester(Agent):
    def __init__(self):
        super().__init__()
        self.replies: list[tuple[float, float]] = []

    def handle_message(self, content, meta):
        self.replies.append((content, self.scheduler.clock.time))

    def on_step(self, env, clock, step_size_s):
        for neighbor in self.neighbors(tid="rr"):
            self.schedule_instant_message(clock.time, neighbor)


class Responder(Agent):
    def __init__(self):
        super().__init__()
        self.requests: list[tuple[float, float]] = []

    def handle_message(self, content, meta):
        self.requests.append((content, self.scheduler.clock.time))
        for neighbor in self.neighbors(tid="rr"):
            self.schedule_instant_message(content, neighbor)


@pytest.mark.parametrize(
    "delay, arrival",
    [(0.0, 1.0), (0.5, 2.0), (1.0, 2.0), (1.5, 3.0)],
)
def test_message_latency_from_on_step(delay, arrival):
    """The documented latency rule: a send from on_step at t is stamped t + dt."""
    scenario = Scenario("latency")
    scenario.time(horizon="4h", step="1h")
    a = scenario.group("a", 1, lambda e: Requester())
    b = scenario.group("b", 1, lambda e: Responder())
    scenario.link("rr", a, b, pattern="one_to_one")
    result = scenario.run({"comm": {"delay_s": delay * H}}, seed=1)
    sent_at_0 = [t for content, t in result.agent("b").requests if content == 0.0]
    assert sent_at_0 == [arrival * H]
    if delay == 0.0:  # replies with delay 0 complete within the same step
        assert (0.0, 1 * H) in result.agent("a").replies


# --------------------------------------------------------------------------- C22


class Halver(Agent):
    def __init__(self):
        super().__init__()
        self.value = 1.0

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        self.value /= 2


def halving(**stop):
    scenario = Scenario("halving")
    scenario.time(horizon="100s", step="1s")
    nodes = scenario.group("node", 2, lambda e: Halver())
    scenario.record("value", nodes, attr="value")
    return scenario


def small(ctx):
    return all(a.value < 1e-3 for a in ctx.agents("node")) and "converged"


def test_stop_when_ends_the_run_and_records_why():
    scenario = halving()
    scenario.stop_when(small)
    result = scenario.run(seed=1)
    assert result.stats["stopped_early"] is True
    assert result.stats["stop_time_s"] == 10.0  # 2**-10 < 1e-3
    assert result.stats["stop_reason"] == "converged"
    assert result.frame["time"].max() == 10.0  # no padding to the horizon
    assert "stop when: small" in str(scenario.plan())
    full = halving().run(seed=1)
    assert full.stats["stopped_early"] is False and full.stats["stop_reason"] is None


def test_stop_when_every_and_true_result():
    scenario = halving()

    @scenario.stop_when(every="4s")
    def below(ctx):
        return ctx.agents("node")[0].value < 1e-3

    result = scenario.run(seed=1)
    assert result.stats["stop_time_s"] == 12.0
    assert (
        result.stats["stop_reason"]
        == "stop_when test_stop_when_every_and_true_result.<locals>.below"
    )
    assert scenario.plan().stop_when == [
        "test_stop_when_every_and_true_result.<locals>.below every 4s"
    ]


def test_stop_when_is_part_of_the_fingerprint():
    scenario = halving()
    before = scenario.fingerprint()
    scenario.stop_when(small)
    assert scenario.fingerprint() != before


def test_stop_when_in_event_mode_checks_at_every_multiples():
    scenario = halving()
    scenario.time(step="event")
    nodes_seen = []

    def check(ctx):
        nodes_seen.append(ctx.world.clock.time)
        return ctx.world.clock.time >= 30

    scenario.stop_when(check, every="10s")
    result = scenario.run(seed=1)
    assert nodes_seen == [10.0, 20.0, 30.0]
    assert result.stats["stop_time_s"] == 30.0


def test_ctx_stop_from_an_event_and_run_stop():
    scenario = halving()

    @scenario.event("5s")
    def give_up(ctx):
        ctx.stop("given up")

    result = scenario.run(seed=1)
    # the event applies before the step that starts at 5 s: the run ends at 5 s
    assert result.stats["stop_time_s"] == 5.0
    assert result.stats["stop_reason"] == "given up"
    with pytest.raises(ScenarioError, match="needs a running run"):
        from mango.scenario.context import RunState

        RunState(scenario, result.params, scenario.build(seed=1).seeds).root.stop()


@pytest.mark.asyncio
async def test_manual_stop_in_a_step_loop():
    run = halving().build(seed=1)
    async with run:
        while await run.step() is not None:
            if run.elapsed_s >= 3:
                run.stop("enough")
                run.stop("ignored")  # the first reason stays
        assert run.done
        assert await run.step() is None
    stats = run.result().stats
    assert (stats["stop_time_s"], stats["stop_reason"]) == (3.0, "enough")
    with pytest.raises(ScenarioError, match="non-empty str"):
        run.stop("")


def test_stop_condition_errors_name_the_condition():
    scenario = halving()

    def broken(ctx):
        raise RuntimeError("bad")

    scenario.stop_when(broken)
    with pytest.raises(RuntimeError, match="bad") as info:
        scenario.run(seed=1)
    if hasattr(info.value, "__notes__"):
        assert "stop_when" in info.value.__notes__[0]


def test_stop_when_in_a_scenario_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "halving_kit.py").write_text(
        textwrap.dedent(
            """\
            from mango import Agent


            class Halver(Agent):
                value = 1.0

                def handle_message(self, content, meta):
                    pass

                def on_step(self, env, clock, step_size_s):
                    self.value /= 2


            def make(e):
                return Halver()


            def small(ctx):
                return all(a.value < 1e-3 for a in ctx.agents("node")) and "done"
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "scenario.yaml").write_text(
        textwrap.dedent(
            """\
            time: {horizon: 100s, step: 1s}
            groups:
              node: {factory: "halving_kit:make", count: 2}
            links:
              - {name: net, from: node, pattern: ring, delay: 2s, loss: 0.0}
            record:
              - {name: value, group: node, attr: value}
            stop_when: {fn: "halving_kit:small", every: 5s}
            """
        )
    )
    scenario = load_scenario("scenario.yaml")
    result = scenario.run(seed=1)
    assert result.stats["stop_time_s"] == 10.0 and result.stats["stop_reason"] == "done"
    assert scenario.plan().link("net").comm == {"delay": 2.0, "loss": 0.0}


def test_to_script_checks_stop_conditions(tmp_path):
    import runpy

    scenario = halving()
    scenario.stop_when(small)
    source = scenario.to_script(seed=1)
    assert "STOP_WHEN = [" in source and "stop_now(world.clock.time)" in source
    path = tmp_path / "generated.py"
    path.write_text(source, encoding="utf-8")
    rows = runpy.run_path(str(path), run_name="generated")["main"]()
    assert max(row["time"] for row in rows) == 10.0


# --------------------------------------------------------------------------- C34


OUT_OF_WINDOW = {
    "time": {"start": "2024-06-01T00:00", "horizon": "8h", "step": "1h"},
    "phases": [{"name": "warmup", "until": "2024-06-01T02:00", "record": False}],
    "events": [
        {
            "name": "outage",
            "at": "2024-06-01T03:00",
            "until": "2024-06-01T05:00",
            "comm": {"loss_percent": 1.0},
        }
    ],
}


def test_run_until_is_a_truncated_smoke_run_without_tripping_validation():
    scenario = power_scenario()
    with pytest.raises(Exception, match="end of the run|after the horizon"):
        scenario.run(OUT_OF_WINDOW, seed=1, overrides=["time.horizon=1h"])
    smoke = scenario.run(OUT_OF_WINDOW, seed=1, until="4h")
    assert smoke.stats["stopped_early"] and smoke.stats["stop_reason"] == "until"
    assert smoke.stats["stop_time_s"] == 4 * H
    assert sorted(set(smoke.frame["time"])) == [3 * H, 4 * H]  # after the warm-up
    full = scenario.run(OUT_OF_WINDOW, seed=1, until="8h")
    assert full.stats["stopped_early"] is False  # reaching the horizon is no stop
    with pytest.raises(ScenarioError, match="beyond the horizon"):
        scenario.run(OUT_OF_WINDOW, seed=1, until="9h")


def test_out_of_window_errors_point_to_until_and_print_iso_times():
    scenario = power_scenario()
    with pytest.raises(Exception) as short:
        scenario.run(OUT_OF_WINDOW, seed=1, overrides=["time.horizon=1h"])
    assert "pass until= (Scenario.run) or --until (CLI)" in str(short.value)
    # moving the start past absolute event/phase times: ISO text, no repr
    with pytest.raises(Exception) as moved:
        scenario.run(OUT_OF_WINDOW, seed=1, overrides=["time.start=2024-06-02T00:00"])
    text = str(moved.value)
    assert "datetime.datetime(" not in text
    assert "Invalid time point 2024-06-01T02:00:00: it lies before time.start " in text
    assert "(2024-06-02T00:00:00)" in text


@pytest.mark.asyncio
async def test_run_async_and_build_accept_until(tmp_path):
    scenario = power_scenario()
    result = await scenario.run_async(OUT_OF_WINDOW, seed=1, until="2024-06-01T03:00")
    assert result.stats["stop_time_s"] == 3 * H
    folder = result.save(tmp_path / "run", format="csv")
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["stats"]["stop_reason"] == "until"
    executed = await scenario.build(OUT_OF_WINDOW, seed=1).execute(until="2h")
    assert executed.stats["stop_time_s"] == 2 * H
    assert executed.frame.empty  # only warm-up steps ran
