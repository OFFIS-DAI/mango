"""The time axis of RunResult.ts() and RunResult.wide(): index='time'|'datetime'|'step'|unit."""

import pandas as pd
import pytest

from mango import Agent
from mango.scenario import Scenario, ScenarioParams, load_run


class Counter(Agent):
    def __init__(self):
        super().__init__()
        self.x = 0.0

    def on_step(self, env, clock, step_size_s):
        self.x += 1


def make(**time):
    scenario = Scenario("axis", params=ScenarioParams)
    scenario.time(**time)
    scenario.group("a", 2, lambda e: Counter())
    scenario.record("x", "a", attr="x")
    return scenario


@pytest.fixture(scope="module")
def daily():
    return make(horizon="4d", step="1d").run(seed=1)


@pytest.fixture(scope="module")
def calendar():
    return make(start="2024-01-01T00:00+01:00", horizon="3w", step="1w").run(seed=1)


def test_default_axis_is_seconds(daily):
    assert daily.wide("x").index.tolist() == [
        0.0,
        86400.0,
        172800.0,
        259200.0,
        345600.0,
    ]
    assert daily.wide("x").index.name == "time"
    assert daily.ts("x", entity="a_0").index.name == "time"


@pytest.mark.parametrize(
    ("index", "name", "values"),
    [
        ("step", "step", [0, 1, 2, 3, 4]),
        ("d", "time_d", [0.0, 1.0, 2.0, 3.0, 4.0]),
        ("h", "time_h", [0.0, 24.0, 48.0, 72.0, 96.0]),
        ("days", "time_days", [0.0, 1.0, 2.0, 3.0, 4.0]),
    ],
)
def test_steps_and_units(daily, index, name, values):
    wide = daily.wide("x", index=index)
    assert wide.index.name == name and wide.index.tolist() == values
    assert wide.columns.tolist() == ["a_0", "a_1"]
    ts = daily.ts("x", entity="a_1", index=index)
    assert ts.index.name == name and ts.index.tolist() == values
    assert ts.tolist() == daily.ts("x", entity="a_1").tolist()
    if index == "step":
        assert wide.index.dtype == "int64"


def test_datetime_axis_keeps_the_time_zone(calendar):
    wide = calendar.wide("x", index="datetime")
    assert isinstance(wide.index, pd.DatetimeIndex) and wide.index.name == "datetime"
    assert str(wide.index.tz) == "UTC+01:00"
    assert wide.index[1] == pd.Timestamp("2024-01-08T00:00+01:00")
    assert wide.resample("14D").mean().shape[1] == 2
    ts = calendar.ts("x", index="datetime")
    assert len(ts) == 8 and ts.index.name == "datetime"


def test_loaded_runs_have_the_same_axes(calendar, tmp_path):
    folder = calendar.save(tmp_path / "run", format="csv")
    loaded = load_run(folder)
    assert loaded.wide("x", index="datetime").index.equals(
        calendar.wide("x", index="datetime").index
    )
    assert loaded.wide("x", index="w").index.tolist() == [0.0, 1.0, 2.0, 3.0]


def test_errors_say_what_to_use(daily):
    with pytest.raises(ValueError, match="needs a calendar start"):
        daily.wide("x", index="datetime")
    with pytest.raises(ValueError, match="Unknown index 'weeks!'.*'step'"):
        daily.ts("x", index="weeks!")
    with pytest.raises(TypeError, match="index must be a str"):
        daily.ts("x", index=3600)
    event = make(horizon="1h", step="event").run(seed=1)
    with pytest.raises(ValueError, match="event-driven"):
        event.wide("x", index="step")
    assert event.wide("x", index="min").index.tolist() == [0.0, 60.0]
