"""Events and phases (C10): params, scenario.event, runtime, plan and results."""

import datetime as dt
import textwrap

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from mango import Agent  # noqa: E402
from mango.scenario import (  # noqa: E402
    Event,
    ParamsError,
    Phase,
    Scenario,
    ScenarioError,
    ScenarioEvent,
    ScenarioParams,
    Study,
    load_params,
    load_run,
)
from mango.simulation import SimpleCommunicationSimulation  # noqa: E402

H = 3600.0
DAY = 24 * H

SIDE_BY_SIDE = textwrap.dedent(
    """\
    time: {start: 2024-06-01T00:00, horizon: 2d, step: 15min}
    phases:
      - {name: warmup, until: 2024-06-02T00:00, record: false}
    events:
      - {name: comm_outage, at: 2024-06-02T12:00, until: 2024-06-02T14:00, comm: {loss_percent: 1.0}}
    comm: {kind: simple, loss_percent: 0.0}
    """
)


class Node(Agent):
    """Pings its neighbours every *period* seconds and logs what it sees."""

    def __init__(self, period: float = 900.0):
        super().__init__()
        self.period = period
        self.sent: list[float] = []
        #: send times of the messages this node received
        self.delivered: list[float] = []
        self.received = 0
        self.global_events: list = []
        self.agent_events: list = []

    def handle_message(self, content, meta):
        self.received += 1
        self.delivered.append(meta["sent_at"])

    def on_ready(self):
        async def ping():
            for neighbor in self.neighbors(tid="pair"):
                now = self.scheduler.clock.time
                self.sent.append(now)
                await self.send_message("ping", neighbor, sent_at=now)

        self.schedule_periodic_task(ping, self.period)

    def on_global_event(self, event):
        self.global_events.append(event)

    def on_agent_event(self, event):
        self.agent_events.append(event)


def node_scenario(name="events", count=2, period=900.0):
    scenario = Scenario(name)
    nodes = scenario.group("node", count, lambda e: Node(period))
    scenario.link("pair", nodes, pattern="complete")
    scenario.record("received", nodes, attr="received")
    return scenario


def message_times(result):
    """(send times, send times of delivered messages) of a finished in-process run.

    Scenario runs do not keep delivered messages (``record_messages=False``),
    so the nodes note the send time of what they receive.
    """
    world = result.world
    sent = sorted(t for agent in world.agents.values() for t in agent.sent)
    delivered = sorted(t for agent in world.agents.values() for t in agent.delivered)
    return sent, delivered


def lost_times(result) -> list[float]:
    sent, delivered = message_times(result)
    remaining = list(delivered)
    lost = []
    for t in sent:
        if t in remaining:
            remaining.remove(t)
        else:
            lost.append(t)
    return lost


ACTION_LOG: list = []


def record_action(ctx):
    ACTION_LOG.append((ctx.world.clock.time, len(ctx.agents("node"))))


# --------------------------------------------------------------------------- side by side


def test_side_by_side_preset_warmup_and_outage(tmp_path):
    preset = tmp_path / "base.yaml"
    preset.write_text(SIDE_BY_SIDE)
    scenario = node_scenario()
    result = scenario.run(str(preset), seed=3)

    frame = result.frame
    # the row at DAY is the state after the last warm-up step: not recorded
    assert frame["time"].min() == DAY + 900
    assert frame["datetime"].min() == dt.datetime(2024, 6, 2, 0, 15)
    assert set(frame["phase"]) == {"main"}
    assert len(result.ts("received", phase="main")) == len(frame)
    # one row per evaluated 15 min interval: 96 per entity
    assert set(frame.groupby("entity", observed=True).size()) == {96}

    sent, delivered = message_times(result)
    outage = (DAY + 12 * H, DAY + 14 * H)
    lost = lost_times(result)
    inside = [t for t in sent if outage[0] <= t < outage[1]]
    assert inside and sorted(lost) == inside  # every message in the window is lost
    assert all(not (outage[0] <= t < outage[1]) for t in delivered)
    assert DAY + 14 * H in delivered and DAY + 12 * H - 900 in delivered

    with pytest.raises(KeyError, match="declared but has no samples"):
        result.ts("received", phase="warmup")


def test_side_by_side_params_dump_and_plan():
    scenario = node_scenario()
    params = load_params(scenario, __import__("yaml").safe_load(SIDE_BY_SIDE))
    assert params.phases == [
        Phase(name="warmup", until="2024-06-02T00:00", record=False)
    ]
    assert params.events[0].comm == {"loss_percent": 1.0}
    plan = scenario.plan(params)
    text = str(plan)
    assert (
        "warmup           2024-06-01T00:00 .. 2024-06-02T00:00  (not recorded)" in text
    )
    assert "main             2024-06-02T00:00 .. 2024-06-03T00:00" in text
    assert (
        "comm_outage      2024-06-02T12:00 .. 2024-06-02T14:00  (comm loss_percent=1)"
        in text
    )
    assert [p.name for p in plan.phases] == ["warmup", "main"]
    assert plan.phases[0].record is False and plan.phases[1].start == DAY
    assert plan.events[0].at == DAY + 12 * H and plan.events[0].until == DAY + 14 * H
    assert plan.events[0].at_datetime == dt.datetime(2024, 6, 2, 12)
    data = plan.to_dict()
    assert data["phases"][0]["name"] == "warmup"
    assert data["events"][0]["comm"] == {"loss_percent": 1.0}
    assert data["warnings"] == []


def test_schedule_phases_carry_their_datetimes():
    from mango.scenario.events import Schedule

    params = load_params(node_scenario(), __import__("yaml").safe_load(SIDE_BY_SIDE))
    schedule = Schedule(params)
    warmup, main = schedule.phases
    assert schedule.phase_at(0.0) is warmup and schedule.phase_at(DAY) is main
    assert warmup.start_datetime == dt.datetime(2024, 6, 1)
    assert warmup.end_datetime == main.start_datetime == dt.datetime(2024, 6, 2)
    assert main.end_datetime == dt.datetime(2024, 6, 3)
    plans = schedule.phase_plans()
    assert plans == schedule.phases
    assert plans[0].comm is not warmup.comm  # the plan gets its own copy


# --------------------------------------------------------------------------- phases


def test_phase_column_follows_phase_spans():
    scenario = node_scenario()
    result = scenario.run(
        {
            "time": {"horizon": "1d", "step": "1h"},
            "phases": [{"name": "a", "until": "6h"}, {"name": "b", "until": "12h"}],
        },
        seed=1,
    )
    frame = result.frame[result.frame["entity"] == "node_0"]
    phases = dict(zip(frame["time"], frame["phase"]))
    # a row is the state after the step that ended at its time: the row at 6h
    # closes the last step of phase a
    assert phases[0.0] == "a" and phases[6 * H] == "a"
    assert phases[7 * H] == "b" and phases[12 * H] == "b"
    assert phases[13 * H] == "main" and phases[DAY] == "main"
    assert result.ts("received", phase=["a", "b"]).index.max() == 12 * H


def test_phase_record_is_per_phase_and_comm_cascades():
    scenario = node_scenario()
    params = {
        "time": {"horizon": "8h", "step": "1h"},
        "phases": [
            {
                "name": "p1",
                "until": "2h",
                "record": False,
                "comm": {"loss_percent": 1.0},
            },
            {"name": "p2", "until": "4h"},
            {"name": "p3", "until": "6h", "record": True, "comm": {"delay_s": 0.0}},
        ],
    }
    plan = scenario.plan(params)
    # record does not cascade: p2 records although p1 says record: false
    assert [(p.name, p.record, p.comm) for p in plan.phases] == [
        ("p1", False, {"loss_percent": 1.0}),
        ("p2", True, {"loss_percent": 1.0}),
        ("p3", True, {"loss_percent": 1.0, "delay_s": 0.0}),
        ("main", True, {}),
    ]
    result = scenario.run(params, seed=1)
    assert sorted(set(result.frame["time"])) == [h * H for h in range(3, 9)]
    assert list(dict.fromkeys(result.frame["phase"])) == ["p2", "p3", "main"]
    lost = lost_times(result)
    assert set(lost) == {h * H for h in range(6)}  # p1 .. p3; main restores comm


def test_last_phase_without_until_runs_to_horizon():
    scenario = node_scenario()
    params = {
        "time": {"horizon": "4h", "step": "1h"},
        "phases": [{"name": "warm", "until": "1h"}, {"name": "eval"}],
    }
    assert [p.name for p in scenario.plan(params).phases] == ["warm", "eval"]
    result = scenario.run(params, seed=1)
    assert set(result.frame["phase"]) == {"warm", "eval"}
    ending = scenario.plan(
        {
            "time": {"horizon": "4h", "step": "1h"},
            "phases": [{"name": "x", "until": "4h"}],
        }
    )
    assert [p.name for p in ending.phases] == ["x"]


@pytest.mark.asyncio
async def test_run_phase_and_recording_enabled_follow_the_clock():
    scenario = node_scenario()
    run = scenario.build(
        {
            "time": {"horizon": "4h", "step": "1h"},
            "phases": [{"name": "warm", "until": "2h", "record": False}],
        },
        seed=1,
    )
    assert run.phase == "warm"
    async with run:
        assert run.phase == "warm" and not run.recording_enabled
        await run.run_until("2h")
        # the sample at 2h is the state after the last warm-up step
        assert run.phase == "main" and not run.recording_enabled
        await run.step()
        assert run.recording_enabled
        await run.run()
    assert run.result().frame["time"].min() == 3 * H


# --------------------------------------------------------------------------- comm


class CountingComm:
    def __init__(self):
        self.calls: list[tuple[float, float]] = []

    def __call__(self, ctx):
        clock_time = 0.0 if ctx.world is None else ctx.world.clock.time
        self.calls.append((clock_time, ctx.comm.loss_percent))
        return SimpleCommunicationSimulation(loss_percent=ctx.comm.loss_percent)


def test_overlapping_events_merge_in_at_order_and_rebuild_only_on_change():
    scenario = node_scenario()
    factory = CountingComm()
    scenario.communication(factory)
    params = {
        "time": {"horizon": "8h", "step": "1h"},
        "phases": [{"name": "warm", "until": "1h"}],  # no comm: no rebuild at 1h
        "events": [
            {"name": "late", "at": "3h", "until": "4h", "comm": {"loss_percent": 0.0}},
            {
                "name": "outage",
                "at": "2h",
                "until": "6h",
                "comm": {"loss_percent": 1.0},
            },
            {"at": "5h", "until": "7h", "comm": {"options": {"note": "x"}}},
        ],
    }
    result = scenario.run(params, seed=1)
    # one simulation per distinct setting: returning settings reuse theirs
    assert factory.calls == [
        (0.0, 0.0),
        (2 * H, 1.0),
        (3 * H, 0.0),  # "late" starts after "outage", so it wins while both are active
        (5 * H, 1.0),  # options changed (4h: the 2h settings again)
        (6 * H, 0.0),  # 7h: the settings of the start again
    ]
    assert lost_times(result) == sorted([2 * H] * 2 + [4 * H] * 2 + [5 * H] * 2)


@pytest.mark.asyncio
async def test_ctx_comm_and_world_sim_switch_at_boundaries():
    scenario = node_scenario()
    run = scenario.build(
        {
            "time": {"horizon": "4h", "step": "1h"},
            "comm": {"delay_s": 0.5},
            "events": [{"at": "1h", "until": "2h", "comm": {"loss_percent": 0.25}}],
        },
        seed=1,
    )
    async with run:
        first = run.world.communication_sim
        assert run.ctx.comm.loss_percent == 0.0 and first.default_delay_s == 0.5
        await run.run_until("1h")
        switched = run.world.communication_sim
        assert switched is not first and switched.loss_percent == 0.25
        assert switched.default_delay_s == 0.5 and run.ctx.comm.loss_percent == 0.25
        assert switched.rng is run.seeds.rng("comm")  # the comm stream continues
        await run.run_until("2h")
        assert run.world.communication_sim.loss_percent == 0.0
        assert run.params.comm.loss_percent == 0.0  # params are not changed


def test_lossy_event_replays_identically():
    scenario = node_scenario()
    params = {
        "time": {"horizon": "6h", "step": "15min"},
        "events": [{"at": "1h", "until": "5h", "comm": {"loss_percent": 0.5}}],
    }
    first = scenario.run(params, seed=11)
    again = scenario.run(params, seed=11)
    replay = scenario.run(first.config, seed=first.seed)
    lost = lost_times(first)
    assert 0 < len(lost) < 32
    assert lost == lost_times(again) == lost_times(replay)
    assert first.checksum() == again.checksum() == replay.checksum()
    assert replay.run_id == first.run_id


# --------------------------------------------------------------------------- emit and action


def test_emit_reaches_all_agents_as_scenario_event():
    scenario = node_scenario(count=3)
    payload = {"kind": "grid_fault", "bus": 7}
    result = scenario.run(
        {
            "time": {"start": "2024-06-01T00:00", "horizon": "1d", "step": "1h"},
            "events": [{"name": "fault", "at": "2024-06-01T18:00", "emit": payload}],
        },
        seed=1,
    )
    agents = list(result.world.agents.values())
    for agent in agents:
        assert len(agent.global_events) == 1 and agent.agent_events == []
        event = agent.global_events[0]
        assert isinstance(event, ScenarioEvent)
        assert (event.name, event.time) == ("fault", 18 * H)
        assert event.datetime == dt.datetime(2024, 6, 1, 18)
        assert event.kind == "grid_fault" and event["bus"] == 7
        assert event.get("missing", 0) == 0
    agents[0].global_events[0].payload["bus"] = 99  # a copy per emission
    assert result.params.events[0].emit == payload


def test_emit_to_reaches_one_agent():
    scenario = node_scenario(count=3)
    result = scenario.run(
        {
            "time": {"horizon": "4h", "step": "1h"},
            "events": [{"at": "1h", "emit": "trip", "emit_to": "node_1"}],
        },
        seed=1,
    )
    received = {
        a.aid: [e.payload for e in a.agent_events] for a in result.world.agents.values()
    }
    assert received == {"node_0": [], "node_1": ["trip"], "node_2": []}
    event = result.world.agents["node_1"].agent_events[0]
    assert event.time == H and event.datetime is None and event.kind is None
    with pytest.raises(TypeError, match="needs a mapping payload"):
        event["x"]


def test_action_ref_from_params_gets_ctx():
    ACTION_LOG.clear()
    scenario = node_scenario()
    params = {
        "time": {"horizon": "4h", "step": "1h"},
        "events": [{"at": "2h", "action": f"{__name__}:record_action"}],
    }
    assert scenario.plan(params).events[0].action == f"{__name__}:record_action"
    scenario.run(params, seed=1)
    assert ACTION_LOG == [(2 * H, 2)]
    # an importable function is stored as its reference
    converted = load_params(
        scenario, {"events": [{"at": "1h", "action": record_action}]}
    )
    assert converted.events[0].action == f"{__name__}:record_action"


def test_structure_events_with_lambdas_and_decorator():
    calls = []
    scenario = node_scenario()
    scenario.time(horizon="4h", step="1h")
    scenario.event(
        "1h",
        name="poke",
        action=lambda ctx: calls.append(("lambda", ctx.world.clock.time)),
    )
    scenario.event("2h", emit=object(), emit_to="node_0")

    @scenario.event("3h")
    def late(ctx):
        calls.append(("decorated", ctx.world.clock.time))

    scenario.event(0, action=lambda: calls.append(("start", 0.0)))
    result = scenario.run(seed=1)
    assert calls == [("start", 0.0), ("lambda", H), ("decorated", 3 * H)]
    node = result.world.agents["node_0"]
    assert len(node.agent_events) == 1 and node.agent_events[0].time == 2 * H
    plan = scenario.plan()
    assert [e.source for e in plan.events] == ["scenario"] * 4
    assert plan.events[1].action.endswith("<lambda>")


def test_structure_and_params_events_share_one_order():
    calls = []
    scenario = node_scenario()
    scenario.time(horizon="4h", step="1h")
    scenario.event("2h", action=lambda: calls.append("scenario"))
    scenario.run({"events": [{"at": "1h", "emit": 1}, {"at": "2h", "emit": 2}]}, seed=1)
    plan = scenario.plan({"events": [{"at": "1h", "emit": 1}, {"at": "2h", "emit": 2}]})
    assert [(e.at, e.source) for e in plan.events] == [
        (H, "params"),
        (2 * H, "scenario"),
        (2 * H, "params"),
    ]
    assert calls == ["scenario"]


# --------------------------------------------------------------------------- timing


@pytest.mark.asyncio
async def test_event_mode_steps_exactly_to_boundaries():
    scenario = node_scenario(period=420.0)
    run = scenario.build(
        {
            "time": {"horizon": "3h", "step": "event"},
            "phases": [{"name": "warm", "until": "1h30min", "record": False}],
            "events": [
                {
                    "name": "cut",
                    "at": 3900,
                    "until": 5100,
                    "comm": {"loss_percent": 1.0},
                },
                {"name": "ping", "at": 4000, "emit": "now"},
            ],
        },
        seed=1,
    )
    times = []
    async with run:
        while await run.step() is not None:
            times.append(run.elapsed_s)
    for boundary in (3900.0, 4000.0, 5100.0, 5400.0):
        assert boundary in times
    result = run.result()
    node = result.world.agents["node_0"]
    assert [(e.payload, e.time) for e in node.global_events] == [("now", 4000.0)]
    lost = lost_times(result)
    assert set(lost) == {4200.0, 4620.0, 5040.0}  # 3780 and 5460 are delivered
    assert result.frame["time"].min() == 5460.0  # the first step after the warm-up


def test_fixed_step_snaps_misaligned_times_and_plan_warns():
    scenario = node_scenario()
    params = {
        "time": {"start": "2024-06-01T00:00", "horizon": "1d", "step": "15min"},
        "phases": [{"name": "warm", "until": "2024-06-01T06:10", "record": False}],
        "events": [
            {"name": "blip", "at": "2024-06-01T12:05", "emit": "x"},
            {"name": "late", "at": "23h55min", "emit": "never"},
        ],
    }
    with pytest.warns(UserWarning) as caught:
        plan = scenario.plan(params)
    messages = [str(w.message) for w in caught]
    assert any(
        "event 'blip' at 2024-06-01T12:05 is not on the 15min step grid; it applies "
        "at 2024-06-01T12:15" in m
        for m in messages
    )
    assert any("phase 'warm' until 2024-06-01T06:10 is not" in m for m in messages)
    assert any("never applies" in m for m in messages)
    assert plan.warnings == messages and "  warnings:" in str(plan)
    assert plan.events[0].at == 12 * H + 900 and plan.phases[1].start == 6 * H + 900
    result = scenario.run(params, seed=1)
    events = result.world.agents["node_0"].global_events
    assert [(e.payload, e.time) for e in events] == [("x", 12 * H + 900)]
    assert result.frame["time"].min() == 6 * H + 1800  # after the first main step


def test_aligned_times_give_no_warnings(recwarn):
    scenario = node_scenario()
    scenario.plan(
        {
            "time": {"horizon": "1d", "step": "15min"},
            "events": [
                {"at": "12h15min", "until": "13h", "comm": {"loss_percent": 1.0}}
            ],
        }
    )
    assert [w for w in recwarn if issubclass(w.category, UserWarning)] == []


# --------------------------------------------------------------------------- identity


def test_run_id_changes_when_an_event_moves(tmp_path):
    preset = tmp_path / "base.yaml"
    preset.write_text(SIDE_BY_SIDE)
    scenario = node_scenario()
    base = scenario.build(str(preset), seed=1).run_id
    moved = scenario.build(
        str(preset), seed=1, overrides=["events.0.at=2024-06-02T13:00"]
    ).run_id
    second = scenario.build(
        str(preset),
        seed=1,
        overrides={"+events": [{"at": "2024-06-02T18:00", "emit": "x"}]},
    ).run_id
    assert len({base, moved, second}) == 3
    assert scenario.build(str(preset), seed=1).run_id == base


def test_events_are_sweepable_in_studies(tmp_path):
    preset = tmp_path / "base.yaml"
    preset.write_text(SIDE_BY_SIDE)
    study = Study(
        node_scenario(),
        base=str(preset),
        uncertainties={"events.0.at": ["2024-06-02T12:00", "2024-06-02T13:00"]},
        seed=1,
    )
    plan = study.plan()
    assert len(set(plan.run_ids)) == 2


def test_structure_event_is_part_of_the_fingerprint():
    scenario = node_scenario()
    before = scenario.fingerprint()
    scenario.event("1h", emit={"kind": "x"})
    after = scenario.fingerprint()
    assert after != before
    other = node_scenario()
    other.event("2h", emit={"kind": "x"})
    assert other.fingerprint() != after


def test_saved_run_keeps_phase_column(tmp_path):
    scenario = node_scenario()
    result = scenario.run(
        {
            "time": {"horizon": "4h", "step": "1h"},
            "phases": [{"name": "warm", "until": "2h"}],
        },
        seed=1,
    )
    loaded = load_run(result.save(tmp_path / "run", format="csv"))
    assert list(loaded.frame["phase"]) == list(result.frame["phase"])
    assert len(loaded.ts("received", phase="warm")) == 6  # 0h, 1h, 2h x 2 nodes
    assert loaded.checksum() == result.checksum()


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "data, path, text",
    [
        (
            {"events": [{"at": "1h", "comm": {"loss_porcent": 1.0}}]},
            "events.0.comm.loss_porcent",
            "Did you mean 'loss_percent'?",
        ),
        (
            {"events": [{"at": "1h", "comm": {"loss_percent": 2}}]},
            "events.0.comm.loss_percent",
            "less than or equal to 1",
        ),
        (
            {"events": [{"at": "2h", "until": "1h", "comm": {}}]},
            "events.0.until",
            "after at",
        ),
        ({"events": [{"at": "1d", "emit": 1}]}, "events.0.at", "end of the run"),
        ({"events": [{"at": "1h"}]}, "events.0", "needs comm, emit or action"),
        (
            {"events": [{"at": "1h", "emit_to": "a", "comm": {}}]},
            "events.0",
            "emit_to needs emit",
        ),
        (
            {"events": [{"at": "1h", "until": "2h", "action": "m:f"}]},
            "events.0",
            "neither comm nor emit",
        ),
        (
            {"events": [{"at": "1h", "emit": 1, "count": 2}]},
            "events.0",
            "count needs every",
        ),
        (
            {"events": [{"at": "1h", "emit": 1, "every": "0s"}]},
            "events.0.every",
            "positive duration",
        ),
        (
            {"events": [{"at": "1h", "emit": 1, "emit_to": []}]},
            "events.0.emit_to",
            "at least one",
        ),
        (
            {"events": [{"at": "2024-06-01T01:00", "emit": 1}]},
            "events.0.at",
            "time.start",
        ),
        ({"events": [{"at": "1h", "emit": {1, 2}}]}, "events.0.emit", "plain data"),
        (
            {"events": [{"at": "1h", "action": lambda ctx: 0}]},
            "events.0.action",
            "lambda",
        ),
        (
            {"events": [{"at": "1h", "emit": 1, "colour": "red"}]},
            "events.0.colour",
            "unknown",
        ),
        (
            {"phases": [{"name": "a", "until": "2h"}, {"name": "b", "until": "2h"}]},
            "phases.1.until",
            "must lie after the until of phase 'a'",
        ),
        (
            {"phases": [{"name": "a", "until": "2d"}]},
            "phases.0.until",
            "after the horizon",
        ),
        (
            {"phases": [{"name": "a"}, {"name": "b", "until": "2h"}]},
            "phases.0.until",
            "needs until",
        ),
        ({"phases": [{"name": "main", "until": "2h"}]}, "phases.0.name", "reserved"),
        (
            {"phases": [{"name": "a", "until": "1h"}, {"name": "a", "until": "2h"}]},
            "phases.1.name",
            "used twice",
        ),
        ({"phases": [{"name": "a", "until": 0}]}, "phases.0.until", "after the start"),
        ({"phases": [{"name": " ", "until": "1h"}]}, "phases.0.name", "blank"),
        (
            {
                "comm": {"kind": "ideal"},
                "events": [{"at": "1h", "comm": {"loss_percent": 1.0}}],
            },
            "events.0.comm",
            "use kind: simple",
        ),
        (
            {"phases": [{"name": "a", "until": "1h", "comm": {"kind": "custom"}}]},
            "phases.0.comm",
            "comm.factory",
        ),
    ],
)
def test_invalid_phases_and_events(data, path, text):
    with pytest.raises(ParamsError) as info:
        load_params(ScenarioParams, data)
    assert info.value.path == path
    assert text in str(info.value)


def test_event_and_phase_models_validate_directly():
    event = Event(at="1h", comm={"delay_s": {"normal": [0.04, 0.008]}})
    assert event.at == H and event.comm["delay_s"]["kind"] == "normal"
    assert Phase(name="x", until="2024-06-02").until == dt.datetime(2024, 6, 2)
    with pytest.raises(ValueError, match="mapping"):
        Event(at=0, comm=[1])
    with pytest.raises(ValueError, match="import reference"):
        Event(at=0, action=5)


def test_emit_to_must_name_an_entity():
    scenario = node_scenario()
    with pytest.raises(
        ScenarioError,
        match=r"emit_to='node_9' is neither an entity nor a group.*Did you mean "
        r"'node_1'.*Groups: node; entities: node_0, node_1",
    ):
        scenario.plan({"events": [{"at": "1h", "emit": 1, "emit_to": "node_9"}]})
    with pytest.raises(ScenarioError, match="emit_to='nod'"):
        scenario.plan(
            {"events": [{"at": "1h", "emit": 1, "emit_to": ["node_0", "nod"]}]}
        )


def test_unimportable_action_fails_at_plan():
    scenario = node_scenario()
    with pytest.raises(ScenarioError, match="action of events.0 cannot be imported"):
        scenario.plan({"events": [{"at": "1h", "action": f"{__name__}:missing_fn"}]})
    with pytest.raises(ScenarioError, match="not a function"):
        scenario.plan({"events": [{"at": "1h", "action": f"{__name__}:ACTION_LOG"}]})


def test_scenario_event_argument_errors():
    scenario = node_scenario()
    with pytest.raises(ScenarioError, match="comm.loss_porcent: unknown communication"):
        scenario.event("1h", comm={"loss_porcent": 1.0})
    with pytest.raises(ScenarioError, match="neither comm nor emit"):
        scenario.event("1h", until="2h", action=lambda ctx: None)
    with pytest.raises(ScenarioError, match="Invalid time point"):
        scenario.event("soon", emit=1)
    with pytest.raises(TypeError, match="must be callable"):
        scenario.event("1h", action=5)
    with pytest.raises(ScenarioError, match="Invalid scenario.event"):
        scenario.event("1h", action="no_such_module_xyz:fn")
    scenario.event("2d", emit=1)  # time points are checked against the horizon per run
    with pytest.raises(ScenarioError, match="scenario.event.*end of the run"):
        scenario.plan()


def test_combined_comm_overrides_are_checked_at_plan():
    scenario = node_scenario()
    params = {
        "time": {"horizon": "4h", "step": "1h"},
        "events": [
            {"name": "a", "at": "1h", "until": "3h", "comm": {"kind": "ideal"}},
            {"name": "b", "at": "2h", "until": "3h", "comm": {"loss_percent": 0.5}},
        ],
    }
    load_params(scenario, params)  # each event alone is valid
    with pytest.raises(
        ScenarioError,
        match=r"settings at 2h \(phase 'main', active: event 'a', event 'b'\) are "
        r"invalid: comm.kind is 'ideal'",
    ):
        scenario.plan(params)


def test_event_action_errors_name_the_event():
    scenario = node_scenario()
    scenario.time(horizon="2h", step="1h")

    def explode(ctx):
        raise RuntimeError("boom")

    scenario.event("1h", name="kaboom", action=explode)
    with pytest.raises(RuntimeError, match="boom") as info:
        scenario.run(seed=1)
    if hasattr(info.value, "__notes__"):
        assert "while applying event 'kaboom' at t=3600 s" in info.value.__notes__


def test_scenario_event_helpers():
    event = ScenarioEvent("x", {"kind": "k", "v": 1}, 5.0)
    assert event.kind == "k" and event["v"] == 1 and event.datetime is None
    assert ScenarioEvent(None, [1], 0.0).get("v", 3) == 3


@pytest.mark.asyncio
async def test_outage_over_a_communication_preset_keeps_its_delay():
    from mango.scenario import CommunicationPreset
    from mango.simulation import DelayProviderCommunicationSimulation

    scenario = node_scenario()
    scenario.use(CommunicationPreset("lte"))
    run = scenario.build(
        {
            "time": {"horizon": "4h", "step": "1h"},
            "events": [{"at": "1h", "until": "5h", "comm": {"loss_percent": 1.0}}],
        },
        seed=1,
    )
    async with run:
        await run.run_until("1h")
        sim = run.world.communication_sim
        assert isinstance(sim, DelayProviderCommunicationSimulation)
        assert sim.loss_percent == 1.0 and run.ctx.comm.delay_s.mean == 0.04
        await run.run()
    assert set(lost_times(run.result())) == {H, 2 * H, 3 * H, 4 * H}
    with pytest.raises(ParamsError, match="comm.kind is 'ideal'"):
        ideal = node_scenario()
        ideal.use(CommunicationPreset("ideal"))
        ideal.plan({"events": [{"at": "1h", "comm": {"loss_percent": 1.0}}]})


# --------------------------------------------------------------------------- C13


NAMED = {
    "time": {"horizon": "12h", "step": "1h"},
    "phases": [{"name": "warm", "until": "1h"}],
    "events": [
        {
            "name": "closure",
            "at": "4h",
            "until": "6h",
            "emit": {"kind": "close", "strength": 0.3},
            "emit_to": "node_1",
        },
        {"name": "news", "at": "2h", "emit": {"kind": "news"}},
    ],
}


def test_events_and_phases_are_addressable_by_name():
    params = load_params(
        ScenarioParams,
        NAMED,
        overrides=["events.closure.at=3h", "phases.warm.until=2h"],
    )
    assert params.events[0].at == 3 * H and params.phases[0].until == 2 * H
    with pytest.raises(
        ParamsError, match="no item named 'clsure'.*Did you mean 'closure'"
    ):
        load_params(ScenarioParams, NAMED, overrides=["events.clsure.at=3h"])
    duplicate = {**NAMED, "events": [NAMED["events"][1], NAMED["events"][1]]}
    with pytest.raises(ParamsError, match="both named 'news'; use an index"):
        load_params(ScenarioParams, duplicate, overrides=["events.news.at=3h"])


def test_name_paths_are_study_factors():
    study = Study(
        node_scenario(),
        base=NAMED,
        uncertainties={"events.closure.at": ["3h", "5h"]},
        seed=1,
    )
    plan = study.plan()
    assert [spec["params"]["events"][0]["at"] for spec in plan] == ["3h", "5h"]
    assert [spec["factors"] for spec in plan] == [
        {"events.closure.at": "3h"},
        {"events.closure.at": "5h"},
    ]


def test_payload_typos_are_rejected():
    with pytest.raises(
        ParamsError, match="unknown payload key 'strenght'.*'events.0.emit.strength'"
    ):
        load_params(ScenarioParams, NAMED, overrides=["events.0.emit.strenght=0.9"])
    with pytest.raises(ParamsError, match="set the payload as a whole"):
        load_params(ScenarioParams, NAMED, overrides=["events.closure.emit.extra=1"])
    params = load_params(
        ScenarioParams, NAMED, overrides=["events.closure.emit.strength=0.9"]
    )
    assert params.events[0].emit == {"kind": "close", "strength": 0.9}
    whole = load_params(  # setting the payload as a whole may add keys
        ScenarioParams,
        NAMED,
        overrides={"events.closure.emit": {"kind": "close", "extra": 1}},
    )
    assert whole.events[0].emit == {"kind": "close", "strength": 0.3, "extra": 1}
    with pytest.raises(Exception, match="strenght"):
        Study(
            node_scenario(),
            base=NAMED,
            uncertainties={"events.closure.emit.strenght": [0.1, 0.9]},
            seed=1,
        ).plan()


def test_emit_with_until_is_delivered_at_start_and_end():
    scenario = node_scenario()
    result = scenario.run(NAMED, seed=1)
    events = result.agent("node_1").agent_events
    assert [(e.time, e.stage, e.until) for e in events] == [
        (4 * H, "start", 6 * H),
        (6 * H, "end", 6 * H),
    ]
    assert result.agent("node_0").agent_events == []
    news = result.agent("node_0").global_events
    assert [(e.time, e.stage, e.until) for e in news] == [(2 * H, None, None)]
    plan = scenario.plan(NAMED)
    assert "to node_1 (on_agent_event and at until)" in str(plan)


def test_recurring_events_expand_in_plan_and_run():
    scenario = node_scenario()
    params = {
        "time": {"horizon": "1d", "step": "1h"},
        "events": [
            {
                "name": "news",
                "at": "1h",
                "every": "6h",
                "emit": {"kind": "news"},
                "emit_to": "node",
            },
            {
                "name": "outage",
                "at": "2h",
                "until": "3h",
                "every": "10h",
                "count": 2,
                "comm": {"loss_percent": 1.0},
            },
        ],
    }
    plan = scenario.plan(params)
    assert [(e.name, e.at, e.until) for e in plan.events] == [
        ("news", H, None),
        ("outage", 2 * H, 3 * H),
        ("news", 7 * H, None),
        ("outage", 12 * H, 13 * H),
        ("news", 13 * H, None),
        ("news", 19 * H, None),
    ]
    result = scenario.run(params, seed=1)
    for name in ("node_0", "node_1"):  # a group name targets all its entities
        times = [e.time for e in result.agent(name).agent_events]
        assert times == [H, 7 * H, 13 * H, 19 * H]
    with pytest.warns(UserWarning, match="count 9, but only 3 occurrences"):
        scenario.plan(
            {
                "time": {"horizon": "1d", "step": "1h"},
                "events": [{"at": "0s", "every": "10h", "count": 9, "emit": 1}],
            }
        )
    dumped = load_params(ScenarioParams, params).events[0].model_dump(mode="json")
    assert dumped["every"] == "6h" and "count" not in dumped


def test_unset_recurrence_fields_keep_run_ids():
    dumped = Event(at="1h", emit=1).model_dump(mode="json")
    assert "every" not in dumped and "count" not in dumped


def test_scenario_event_accepts_recurrence_and_target_lists():
    scenario = node_scenario()
    scenario.time(horizon="6h", step="1h")
    scenario.event("1h", every="2h", emit="x", emit_to=["node_0"], name="tick")
    result = scenario.run(seed=1)
    assert [e.time for e in result.agent("node_0").agent_events] == [H, 3 * H, 5 * H]
    assert result.agent("node_1").agent_events == []


def test_script_reproduces_emit_windows_and_targets(tmp_path):
    import runpy

    from mango.scenario.result import frame_checksum, tidy_frame

    scenario = Scenario("scripted")
    nodes = scenario.group("node", 2, make_node)
    scenario.link("pair", nodes, pattern="complete")
    scenario.record("events_seen", nodes, attr="n_agent_events")
    source = scenario.to_script(NAMED, seed=4)
    path = tmp_path / "generated.py"
    path.write_text(source, encoding="utf-8")
    namespace = runpy.run_path(str(path), run_name="generated")
    rows = namespace["main"]()
    phases = [(start, name) for start, name, _ in namespace["PHASES"]]
    result = scenario.run(NAMED, seed=4)
    assert frame_checksum(tidy_frame(rows, run_id="x", phases=phases)) == (
        result.checksum()
    )
    assert result.ts("events_seen", entity="node_1").max() == 2


class CountingNode(Node):
    @property
    def n_agent_events(self):
        return len(self.agent_events)


def make_node(e):
    return CountingNode()
