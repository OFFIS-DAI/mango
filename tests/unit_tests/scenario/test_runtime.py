import asyncio
import datetime as dt
import sys

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from mango import Agent, TraceConfig  # noqa: E402
from mango.scenario import RunResult, Scenario, ScenarioError, ScenarioRun  # noqa: E402
from mango.scenario.runtime import run_sync  # noqa: E402
from mango.simulation.world import SimulationResult  # noqa: E402


class Plain(Agent):
    def __init__(self, **kwargs):
        super().__init__()
        self.__dict__.update(kwargs)

    def handle_message(self, content, meta):
        pass


class Ticker(Agent):
    """Wakes up every *period* seconds (an event source for event-driven runs)."""

    def __init__(self, period: float):
        super().__init__()
        self.period = period
        self.ticks = 0

    def handle_message(self, content, meta):
        pass

    def on_ready(self):
        async def tick():
            self.ticks += 1

        self.schedule_periodic_task(tick, self.period)


class Pinger(Agent):
    def __init__(self):
        super().__init__()
        self.received = 0

    def handle_message(self, content, meta):
        self.received += 1

    def on_step(self, env, clock, step_size_s):
        for neighbor in self.neighbors(tid="pair"):
            self.schedule_instant_message("ping", neighbor)


def fixed_scenario(**time):
    scenario = Scenario("fixed")
    scenario.time(**{"horizon": "6h", "step": "1h", **time})
    nodes = scenario.group("node", 2, lambda e: Pinger())
    scenario.link("pair", nodes, pattern="complete")
    scenario.record("received", nodes, attr="received")
    return scenario


@pytest.mark.asyncio
async def test_build_enter_step_run_until_and_run():
    run = fixed_scenario().build(seed=1)
    assert isinstance(run, ScenarioRun)
    assert run.world is None and run.ctx is None and run.entities == {}
    assert run.seed == {"entropy": 1, "spawn_key": []}
    async with run:
        assert run.elapsed_s == 0.0 and not run.done
        assert set(run.entities) == {"node_0", "node_1"}
        assert run.world.seed_tree is run.seeds
        result = await run.step()
        assert isinstance(result, SimulationResult) and result.step_size_s == 3600.0
        assert run.elapsed_s == 3600.0
        await run.run_until("3h")
        assert run.elapsed_s == 3 * 3600.0
        await run.run_until("3h")  # already there: no-op
        await run.run_until(4.5 * 3600)  # off-grid: stops at the last step before it
        assert run.elapsed_s == 4 * 3600.0
        with pytest.raises(ScenarioError, match="beyond the horizon"):
            await run.run_until("7h")
        with pytest.raises(ScenarioError, match="before the current time"):
            await run.run_until("1h")
        with pytest.raises(ScenarioError, match="run_until\\('soon'\\)"):
            await run.run_until("soon")
        await run.run()
        assert run.elapsed_s == 6 * 3600.0 and run.done
        assert await run.step() is None
        inside = run.result()
        assert isinstance(inside, RunResult)
    result = run.result()
    assert result is run.result()
    assert result.stats["steps"] == 6
    assert result.stats["messages_delivered"] == 12
    assert result.stats["agents_per_group"] == {"node": 2}
    assert result.stats["link_edges"] == {"pair": 1}
    assert result.stats["wall_s"] > 0 and result.stats["finished"] is not None
    assert result.ts("received", entity="node_0").tolist() == [0, 1, 2, 3, 4, 5, 6]


@pytest.mark.asyncio
async def test_run_lifecycle_errors():
    run = fixed_scenario().build(seed=1)
    with pytest.raises(ScenarioError, match="enter the run first"):
        await run.step()
    with pytest.raises(ScenarioError, match="needs a run that was entered"):
        run.result()
    async with run:
        pass
    with pytest.raises(ScenarioError, match="the run is finished"):
        await run.run()
    with pytest.raises(ScenarioError, match="runs once"):
        async with run:
            pass


@pytest.mark.asyncio
async def test_run_until_absolute_datetime_with_calendar():
    scenario = fixed_scenario(start="2024-06-01T00:00")
    run = scenario.build(seed=2)
    async with run:
        await run.run_until("2024-06-01T02:00")
        assert run.elapsed_s == 7200.0
        assert run.world.datetime == dt.datetime(2024, 6, 1, 2)
        await run.run()
    frame = run.result().frame
    assert list(frame.columns) == [
        "run_id",
        "time",
        "datetime",
        "phase",
        "group",
        "entity",
        "variable",
        "value",
    ]
    assert frame["datetime"].iloc[-1] == dt.datetime(2024, 6, 1, 6)


@pytest.mark.asyncio
async def test_event_driven_mode_stops_at_the_horizon():
    scenario = Scenario("events")
    scenario.time(horizon="1h", step="event")
    scenario.group("ticker", 1, lambda e: Ticker(period=600))
    scenario.record("ticks", "ticker", attr="ticks")
    run = scenario.build(seed=1)
    async with run:
        await run.step()
        assert run.elapsed_s == 600.0
        await run.run_until("30min")
        assert run.elapsed_s == 1800.0
        await run.run()
        assert run.elapsed_s == 3600.0 and run.done
        assert await run.step() is None
    result = run.result()
    assert result.step_s is None and result.step_h is None
    assert result.ts("ticks").index.tolist() == [
        0.0,
        600.0,
        1200.0,
        1800.0,
        2400.0,
        3000.0,
        3600.0,
    ]


@pytest.mark.asyncio
async def test_event_driven_mode_without_events_jumps_to_the_horizon():
    scenario = Scenario("quiet")
    scenario.time(horizon="2h", step="event")
    scenario.group("idle", 2, lambda e: Plain())
    scenario.record_world("t", lambda ctx: ctx.world.clock.time)
    run = scenario.build(seed=1)
    async with run:
        await run.run_until("30min")
        assert run.elapsed_s == 1800.0
        await run.run()
        assert run.elapsed_s == 7200.0
    result = run.result()
    assert result.stats["steps"] == 2
    assert result.ts("t").tolist() == [0.0, 1800.0, 7200.0]


@pytest.mark.asyncio
async def test_sync_run_inside_a_running_event_loop():
    asyncio.get_running_loop()
    result = fixed_scenario().run(seed=3)
    assert result.stats["steps"] == 6
    assert run_sync(lambda: asyncio.sleep(0, result="done")) == "done"


def test_sync_run_and_run_sync_outside_a_loop():
    assert run_sync(lambda: asyncio.sleep(0, result=5)) == 5
    result = fixed_scenario().run(seed=3)
    assert result.world is not None and not result.world.running


@pytest.mark.asyncio
async def test_run_async_matches_sync_run():
    scenario = fixed_scenario()
    assert (await scenario.run_async(seed=4)).checksum() == scenario.run(
        seed=4
    ).checksum()


def test_trace_passthrough(tmp_path):
    path = tmp_path / "run.jsonl"
    fixed_scenario(horizon="2h").run(seed=1, trace=TraceConfig(path=path, html=False))
    text = path.read_text()
    assert "world.started" in text and "node_0" in text


@pytest.mark.asyncio
async def test_failing_factory_marks_the_run_failed():
    scenario = Scenario("broken")

    def factory(e):
        raise ValueError("boom")

    scenario.group("ev", 2, factory)
    run = scenario.build(seed=1)
    with pytest.raises(ValueError, match="boom") as info:
        async with run:
            pass
    if sys.version_info >= (3, 11):
        assert "while building entity 'ev_0' of group 'ev'" in info.value.__notes__
    with pytest.raises(ScenarioError, match="failed to build"):
        await run.step()


def test_missing_super_init_names_the_entity():
    class NoSuper(Agent):
        def __init__(self):  # no super().__init__()
            self.level = 1

    scenario = Scenario("no_super")
    scenario.time(horizon=2, step=1)
    scenario.group("node", 1, lambda e: NoSuper())
    with pytest.raises(
        AttributeError, match="Call super\\(\\).__init__\\(\\) first"
    ) as info:
        scenario.run(seed=1)
    if sys.version_info >= (3, 11):
        assert "while building entity 'node' of group 'node'" in info.value.__notes__


def test_build_resolves_params_eagerly():
    scenario = fixed_scenario()
    with pytest.raises(Exception, match="time.horizon|horizon"):
        scenario.build(overrides=["time.horizon=90min"])
    run = scenario.build(overrides=["time.horizon=2h"], seed=1)
    assert run.time.n_steps == 2 and "fixed" in repr(run)


def test_run_id_and_scenario_ref_of_a_run():
    scenario = fixed_scenario()
    run = scenario.build(seed=1)
    assert run.scenario_ref == "fixed"
    assert run.run_id == scenario.build(seed=1).run_id
    assert run.run_id != scenario.build(seed=2).run_id
    assert run.run_id == scenario.run(seed=1).run_id


def test_phase_and_recording_defaults():
    run = fixed_scenario().build(seed=1)
    assert run.phase == "main" and run.recording_enabled
    assert run.stats["steps"] == 0 and run.stats["agents_per_group"] == {}


# ----------------------------------------------------------------- regressions


def test_sub_second_steps_land_exactly_on_the_step_grid():
    scenario = Scenario("grid")
    scenario.time(horizon="100s", step="100ms")
    scenario.record("n", scenario.group("a", 1, lambda e: Plain()), attr="aid")
    result = scenario.run(seed=1)
    assert result.stats["steps"] == result.time.n_steps == 1000
    assert result.stats["sim_time_s"] == 100.0
    times = result.ts("n").index.tolist()
    # grid points are exact decimals (0.3, not 0.30000000000000004)
    assert times == [round(k * 0.1, 9) for k in range(1001)]
    assert 0.3 in times


@pytest.mark.asyncio
async def test_a_failed_build_stops_the_agents_already_registered():
    scenario = Scenario("broken")
    scenario.group("ok", 2, lambda e: Plain())

    def factory(e):
        raise ValueError("boom")

    scenario.group("bad", 1, factory)
    before = asyncio.all_tasks()
    with pytest.raises(ValueError, match="boom"):
        async with scenario.build(seed=1):
            pass
    await asyncio.sleep(0)
    leaked = [task for task in asyncio.all_tasks() - before if not task.done()]
    assert leaked == []


def test_recorder_errors_name_the_recorder_and_entity():
    scenario = Scenario("rec")
    nodes = scenario.group("node", 2, lambda e: Plain())
    scenario.record("missing", nodes, attr="nonexistent")
    with pytest.raises(AttributeError) as info:
        scenario.run(seed=1)
    if sys.version_info >= (3, 11):
        assert info.value.__notes__ == [
            "while recording 'missing' of entity 'node_0' (group 'node') at t=0 s"
        ]
    scenario = Scenario("rec_world")

    def broken(ctx):
        raise KeyError("x")

    scenario.record_world("total", broken, entity="grid")
    with pytest.raises(KeyError) as info:
        scenario.run(seed=1)
    if sys.version_info >= (3, 11):
        assert info.value.__notes__ == [
            "while recording world variable 'total' (entity 'grid') at t=0 s"
        ]


def test_run_errors_are_not_chained_to_the_event_loop_probe():
    scenario = Scenario("chain")

    def factory(e):
        raise ValueError("boom")

    scenario.group("bad", 1, factory)
    with pytest.raises(ValueError) as info:
        scenario.run(seed=1)
    assert info.value.__context__ is None


class Clumsy(Agent):
    def handle_message(self, content):  # meta forgotten: mango passes two arguments
        pass

    def on_step(self, env, clock, step_size_s):
        for neighbor in self.neighbors(tid="pair"):
            self.schedule_instant_message("ping", neighbor)


def test_an_agent_whose_message_handler_fails_stops_the_run_instead_of_hanging():
    scenario = Scenario("clumsy")
    nodes = scenario.group("node", 2, lambda e: Clumsy())
    scenario.link("pair", nodes, pattern="complete")
    with pytest.raises(
        RuntimeError, match="Agent 'node_[01]' stopped processing"
    ) as info:
        scenario.run(seed=1)
    assert isinstance(info.value.__cause__, TypeError)


@pytest.mark.asyncio
async def test_event_driven_runs_stop_at_every_sample_time():
    scenario = Scenario("sampled")
    scenario.time(horizon="4h", step="event")
    scenario.record(
        "n", scenario.group("a", 1, lambda e: Plain()), attr="aid", every="1h"
    )
    run = scenario.build(seed=1)
    async with run:
        await run.run()
    assert run.result().ts("n").index.tolist() == [
        0.0,
        3600.0,
        7200.0,
        10800.0,
        14400.0,
    ]
