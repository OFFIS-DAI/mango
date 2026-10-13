"""Link delay/loss and event times read from the run's parameters."""

import pytest

pytest.importorskip("pydantic")

from pydantic import BaseModel  # noqa: E402

from mango import Agent  # noqa: E402
from mango.scenario import (  # noqa: E402
    ParamsError,
    Scenario,
    ScenarioError,
    ScenarioParams,
)

from .cli_helpers import cli, isolated_fixture, write  # noqa: E402, F401


class Corridor(BaseModel):
    travel_s: float = 60.0
    loss: float = 0.0
    max_s: float = 600.0


class Market(BaseModel):
    period_s: float = 600.0
    open: str = "1h"


class Params(ScenarioParams):
    corridor: Corridor = Corridor()
    market: Market = Market()


class Lane(Agent):
    def __init__(self):
        super().__init__()
        self.events = []

    def on_global_event(self, event):
        self.events.append(event.time)


def corridor(**link):
    scenario = Scenario("corridor", Params)
    scenario.time(horizon="2h", step="1min")
    lanes = scenario.group("lane", 3, lambda e: Lane())
    scenario.link("platoon", lanes, pattern="line", directed=True, **link)
    return scenario


@pytest.mark.parametrize(
    "delay, expected",
    [
        ("$corridor.travel_s", 31.0),
        ("corridor.travel_s", 31.0),
        (lambda p: 2 * p.corridor.travel_s, 62.0),
        (
            {"uniform": ["$corridor.travel_s", "$corridor.max_s"]},
            {"kind": "uniform", "low": 31.0, "high": 600.0},
        ),
    ],
)
def test_link_delay_follows_the_parameters(delay, expected):
    scenario = corridor(delay=delay, loss="$corridor.loss")
    (link,) = scenario.plan(overrides=["corridor.travel_s=31"]).links
    assert link.comm == {"delay": expected, "loss": 0.0}


def test_a_parameter_driven_delay_delays_the_messages():
    class Sender(Agent):
        def __init__(self):
            super().__init__()
            self.received = []

        def on_step(self, env, clock, step_size_s):
            if self.aid == "lane_0" and clock.time == 0:
                for peer in self.neighbors(tid="platoon"):
                    self.schedule_instant_message("go", peer)

        def handle_message(self, content, meta):
            self.received.append(self.scheduler.clock.time)

    scenario = Scenario("corridor", Params)
    scenario.time(horizon="10min", step="1min")
    lanes = scenario.group("lane", 2, lambda e: Sender())
    scenario.link(
        "platoon", lanes, pattern="line", directed=True, delay="$corridor.travel_s"
    )
    for travel, arrival in ((60, 120.0), (180, 240.0)):
        result = scenario.run(overrides=[f"corridor.travel_s={travel}"], seed=1)
        assert result.agent("lane_1").received == [arrival]


def test_link_parameter_errors_name_the_source():
    with pytest.raises(ScenarioError, match="did you mean 'corridor.travel_s'"):
        corridor(delay="corridor.travl_s")
    with pytest.raises(
        ScenarioError,
        match="'corridor.travel_s' is a parameter path; write '\\$corridor",
    ):
        corridor(delay={"uniform": ["corridor.travel_s", 60]})
    scenario = corridor(delay="$corridor.travel_s")
    with pytest.raises(
        ScenarioError,
        match="delay '\\$corridor.travel_s' \\(= -3.0\\): link delay must be >= 0",
    ):
        scenario.plan(overrides=["corridor.travel_s=-3"])
    scenario = corridor(loss="$corridor.loss")
    with pytest.raises(ScenarioError, match="loss '\\$corridor.loss' \\(= 1.5\\)"):
        scenario.plan(overrides=["corridor.loss=1.5"])


def test_parameter_driven_links_change_the_fingerprint_by_spec_not_value():
    a = corridor(delay="$corridor.travel_s").fingerprint()
    b = corridor(delay="$corridor.travel_s").fingerprint()
    c = corridor(delay="$corridor.max_s").fingerprint()
    assert a == b != c


def market(**event):
    scenario = Scenario("market", Params)
    scenario.time(horizon="2h", step="1min")
    scenario.group("lane", 1, lambda e: Lane(), naming="{group}_{index}")
    scenario.event(name="tick", emit={"kind": "tick"}, **event)
    return scenario


@pytest.mark.parametrize(
    "event, expected",
    [
        (dict(at=0, every="market.period_s", count=3), [0.0, 1200.0, 2400.0]),
        (dict(at=0, every="$market.period_s", count=2), [0.0, 1200.0]),
        (dict(at="$market.open"), [3600.0]),
        (dict(at=lambda p: p.market.period_s, count=None), [1200.0]),
        (dict(at=0, until="$market.open"), [0.0, 3600.0]),
        (dict(at=0, every=lambda p: 2 * p.market.period_s), [0.0, 2400.0, 4800.0]),
    ],
)
def test_event_times_follow_the_parameters(event, expected):
    if event.get("count", 0) is None:
        event.pop("count")
    result = market(**event).run(overrides=["market.period_s=1200"], seed=1)
    assert result.agent("lane_0").events == expected


def test_event_parameter_errors():
    with pytest.raises(ScenarioError, match="did you mean 'market.period_s'"):
        market(at=0, every="market.perod_s")
    with pytest.raises(ScenarioError, match="Invalid time point 'soon'"):
        market(at="soon")
    scenario = market(at="$market.open")
    with pytest.raises(ScenarioError, match="at '\\$market.open' \\(= 'xyz'\\)"):
        scenario.run(overrides=["market.open=xyz"], seed=1)
    scenario = market(at=0, every="$market.period_s")
    with pytest.raises(ScenarioError, match="every '\\$market.period_s' \\(= -5.0\\)"):
        scenario.run(overrides=["market.period_s=-5"], seed=1)


def test_params_events_read_dollar_paths():
    scenario = Scenario("market", Params)
    scenario.time(horizon="2h", step="1min")
    scenario.group("lane", 1, lambda e: Lane(), naming="{group}_{index}")
    events = [
        {"name": "pe", "at": "$market.open", "every": "$market.period_s", "emit": {}}
    ]
    result = scenario.run(
        {"events": events}, overrides=["market.period_s=1800"], seed=1
    )
    assert result.agent("lane_0").events == [3600.0, 5400.0]
    assert result.config["events"][0]["at"] == "$market.open"
    with pytest.raises(
        ParamsError, match="events.0.at: '\\$market.opn' is not a parameter"
    ):
        scenario.plan({"events": [{"at": "$market.opn", "emit": {}}]})
    with pytest.raises(ParamsError, match="events.0.every: '\\$market.open'"):
        scenario.plan(
            {"events": [{"at": 0, "every": "$market.open", "emit": {}}]},
            overrides=["market.open=-1h"],
        )


def test_scenario_files_take_parameter_driven_link_delays(capsys, isolated):
    write(
        isolated,
        "lanes.py",
        """
        from mango import Agent


        def lane(e):
            return Agent()
        """,
    )
    write(
        isolated,
        "corridor.yaml",
        """
        name: corridor
        time: {horizon: 1h, step: 1min}
        parameters:
          corridor: {travel_s: 60.0, loss: 0.0}
        groups:
          lane: {count: 3, factory: "lanes:lane"}
        links:
          - {name: platoon, from: lane, pattern: line, directed: true,
             delay: $corridor.travel_s, loss: corridor.loss}
        """,
    )
    code, out, err = cli(capsys, "plan", "corridor.yaml", "-s", "corridor.travel_s=31")
    assert code == 0, err
    assert "2 edges, directed, delay 31.0, loss 0.0" in out
