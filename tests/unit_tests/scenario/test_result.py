import datetime as dt
import json
import math
import sys
import zoneinfo

import numpy as np
import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pd = pytest.importorskip("pandas")

from pydantic import BaseModel  # noqa: E402

from mango import Agent, Position2D  # noqa: E402
from mango.scenario import (  # noqa: E402
    Normal,
    RunResult,
    Scenario,
    ScenarioError,
    ScenarioParams,
    Uniform,
    load_run,
)
from mango.scenario.result import (  # noqa: E402
    compute_run_id,
    frame_checksum,
    tidy_frame,
    tidy_value,
)

# ----------------------------------------------------------------- scenario


class EVParams(BaseModel, extra="forbid"):
    n: int = 3
    soc: Uniform = Uniform(0.2, 0.8)
    arrival_h: Normal = Normal(16.0, 1.5)


class HHParams(BaseModel, extra="forbid"):
    n: int = 2
    load_kw: Uniform = Uniform(0.5, 2.0)


class Params(ScenarioParams):
    ev: EVParams = EVParams()
    hh: HHParams = HHParams()


class EV(Agent):
    def __init__(self, soc, arrival_h):
        super().__init__()
        self.soc = soc
        self.arrival_h = arrival_h

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        self.soc = min(1.0, self.soc + float(self.rng.uniform(0.0, 0.05)))


class Household(Agent):
    def __init__(self, load_kw):
        super().__init__()
        self.load_kw = load_kw

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        self.load_kw = float(self.rng.normal(self.load_kw, 0.1))


def make_scenario():
    scenario = Scenario("ev_toy", params=Params)
    scenario.time(horizon="4h", step="1h")
    scenario.group("hh", "hh.n", lambda e: Household(e.p.hh.load_kw.draw(e.rng)))
    scenario.group(
        "ev",
        "ev.n",
        lambda e: EV(e.p.ev.soc.draw(e.rng), e.p.ev.arrival_h.draw(e.rng)),
    )
    scenario.record("soc", "ev", attr="soc")
    scenario.record("arrival_h", "ev", attr="arrival_h")
    scenario.record("load_kw", "hh", attr="load_kw")
    scenario.record_world("n_agents", lambda ctx: len(ctx.world.agents))
    return scenario


SCENARIO = make_scenario()


@pytest.fixture(scope="module")
def result():
    return SCENARIO.run(seed=11)


# ----------------------------------------------------------------- queries


def test_result_attributes(result):
    assert isinstance(result, RunResult)
    assert result.scenario == f"{__name__}:SCENARIO"
    assert result.seed == {"entropy": 11, "spawn_key": []}
    assert result.config["ev"]["n"] == 3 and result.params.ev.n == 3
    assert (result.step_s, result.step_h, result.horizon_s, result.start) == (
        3600.0,
        1.0,
        14400.0,
        None,
    )
    assert result.entities == {
        "hh_0": "hh",
        "hh_1": "hh",
        "ev_0": "ev",
        "ev_1": "ev",
        "ev_2": "ev",
    }
    assert list(result.frame.columns) == [
        "run_id",
        "time",
        "phase",
        "group",
        "entity",
        "variable",
        "value",
    ]
    assert result.frame["value"].dtype == "float64"
    assert set(result.frame["run_id"]) == {result.run_id}
    assert set(result.frame["phase"]) == {"main"}
    assert result.variables() == ["n_agents", "soc", "arrival_h", "load_kw"]
    assert result.groups() == ["hh", "ev"]
    assert result.run_id in repr(result)


def test_ts_is_long_form(result):
    soc = result.ts("soc")
    assert soc.name == "soc" and soc.index.name == "time"
    assert len(soc) == 3 * 5
    frame = result.frame
    assert soc.sum() == pytest.approx(frame[frame.variable == "soc"].value.sum())
    ev0 = result.ts("soc", entity="ev_0")
    assert ev0.index.tolist() == [0.0, 3600.0, 7200.0, 10800.0, 14400.0]
    assert ev0.is_monotonic_increasing
    assert (
        len(result.ts("soc", entity=["ev_0", "ev_1"], group="ev", phase="main")) == 10
    )
    assert result.ts("n_agents").tolist() == [5.0] * 5


def test_ts_errors_list_known_values(result):
    with pytest.raises(KeyError, match="No variable 'sco'.*Did you mean 'soc'"):
        result.ts("sco")
    with pytest.raises(KeyError, match="has no entity 'ev_9'"):
        result.ts("soc", entity="ev_9")
    with pytest.raises(KeyError, match="has no group 'hh'"):
        result.ts("soc", group="hh")
    with pytest.raises(KeyError, match="has no phase 'warmup'"):
        result.ts("soc", phase="warmup")


def test_wide_keeps_entity_order():
    scenario = Scenario("wide")
    scenario.time(horizon="2h", step="1h")
    nodes = scenario.group("node", 12, lambda e: EV(float(e.index), 0.0))
    scenario.record("arrival", nodes, attr="soc")
    table = scenario.run(seed=1).wide("arrival")
    assert table.columns.tolist() == [f"node_{i}" for i in range(12)]
    assert table.index.tolist() == [0.0, 3600.0, 7200.0]
    assert table.loc[0.0, "node_11"] == 11.0 and table.dtypes.unique().tolist() == [
        np.dtype("float64")
    ]


def test_agent_access(result):
    assert result.agent("ev_0").aid == "ev_0"
    with pytest.raises(KeyError, match="Did you mean 'ev_0'"):
        result.agent("ev0")


# ----------------------------------------------------------------- identity


def test_replay_gives_identical_checksums(result):
    again = SCENARIO.run(seed=11)
    assert again.checksum() == result.checksum()
    assert again.run_id == result.run_id
    other = SCENARIO.run(seed=12)
    assert other.checksum() != result.checksum()
    assert other.run_id != result.run_id


def test_run_id_changes_with_any_parameter(result):
    assert SCENARIO.build(seed=11).run_id == result.run_id
    assert SCENARIO.build({"ev": {"n": 3}}, seed=11).run_id == result.run_id
    changed = [
        SCENARIO.build(overrides=["ev.n=4"], seed=11).run_id,
        SCENARIO.build(overrides=["hh.load_kw={uniform: [0.5, 2.5]}"], seed=11).run_id,
        SCENARIO.build(overrides=["comm.loss_percent=0.1"], seed=11).run_id,
        SCENARIO.build(overrides=["time.horizon=5h"], seed=11).run_id,
        SCENARIO.build(seed={"entropy": 11, "spawn_key": [0]}).run_id,
    ]
    assert len({result.run_id, *changed}) == 6
    assert compute_run_id("x", result.params, result.seed) == compute_run_id(
        "x", result.config, result.seed
    )
    assert compute_run_id("y", result.params, result.seed) != compute_run_id(
        "x", result.params, result.seed
    )


def test_adding_households_leaves_ev_draws_and_series_unchanged(result):
    more = SCENARIO.run(seed=11, overrides=["hh.n=7"])
    assert more.groups() == ["hh", "ev"] and len(more.entities) == 10
    for variable in ("soc", "arrival_h"):
        pd.testing.assert_series_equal(
            more.ts(variable, group="ev"), result.ts(variable, group="ev")
        )
    for name in ("ev_0", "ev_1", "ev_2"):
        assert more.agent(name).arrival_h == result.agent(name).arrival_h
    # the existing households are unchanged too
    pd.testing.assert_series_equal(
        more.ts("load_kw", entity=["hh_0", "hh_1"]), result.ts("load_kw")
    )


# ----------------------------------------------------------------- save / load


def assert_same_frames(loaded, original):
    assert loaded.checksum() == original.checksum()
    assert list(loaded.frame.columns) == list(original.frame.columns)
    assert loaded.frame["time"].tolist() == original.frame["time"].tolist()
    for a, b in zip(loaded.frame["value"].tolist(), original.frame["value"].tolist()):
        assert (a == b) or (isinstance(a, float) and math.isnan(a) and math.isnan(b))


def test_save_and_load_parquet(result, tmp_path):
    pytest.importorskip("pyarrow")
    folder = result.save(tmp_path / "run")
    assert folder == tmp_path / "run" and result.path == folder
    files = sorted(p.name for p in folder.iterdir())
    assert files == [
        "config.resolved.yaml",
        "manifest.json",
        "outcomes.json",
        "timeseries.parquet",
    ]
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["run_id"] == result.run_id
    assert manifest["checksum"] == result.checksum()
    assert manifest["format"] == "parquet" and manifest["status"] == "done"
    assert manifest["seed"] == result.seed and manifest["entities"] == result.entities
    assert manifest["provenance"]["versions"]["python"]
    loaded = load_run(folder)
    assert_same_frames(loaded, result)
    assert loaded.params is None and loaded.world is None
    assert loaded.config == result.config and loaded.time == result.time
    assert loaded.stats["agents_per_group"] == {"hh": 2, "ev": 3}
    assert loaded.groups() == ["hh", "ev"] and loaded.step_h == 1.0
    with pytest.raises(ScenarioError, match="results loaded from disk have no world"):
        loaded.agent("ev_0")
    typed = load_run(folder, scenario=SCENARIO)
    assert typed.params == result.params


def test_save_and_load_csv_without_pyarrow(result, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    folder = result.save(tmp_path / "run")
    assert (folder / "timeseries.csv").exists()
    assert json.loads((folder / "manifest.json").read_text())["format"] == "csv"
    assert_same_frames(load_run(folder), result)
    with pytest.raises(ImportError, match="pyarrow"):
        result.save(tmp_path / "other", format="parquet")
    with pytest.raises(ValueError, match="Unknown result format"):
        result.save(tmp_path / "other", format="xlsx")


def test_parquet_and_csv_give_identical_checksums(result, tmp_path):
    pytest.importorskip("pyarrow")
    parquet = load_run(result.save(tmp_path / "p", format="parquet"))
    csv = load_run(result.save(tmp_path / "c", format="csv"))
    assert parquet.checksum() == csv.checksum() == result.checksum()


def test_resaving_in_another_format_replaces_the_timeseries(result, tmp_path):
    pytest.importorskip("pyarrow")
    folder = tmp_path / "run"
    result.save(folder, format="parquet")
    result.save(folder, format="csv")
    assert not (folder / "timeseries.parquet").exists()
    assert load_run(folder).checksum() == result.checksum()


def test_manifest_extras_and_outcomes_are_kept(tmp_path):
    result = SCENARIO.run(seed=1)
    result.outcomes = {"peak": np.float64(2.5), "label": "x"}
    result.manifest["study"] = "fleet"
    folder = result.save(tmp_path / "run", format="csv")
    loaded = load_run(folder)
    assert loaded.outcomes == {"peak": 2.5, "label": "x"}
    assert loaded.manifest["study"] == "fleet"
    assert loaded.manifest["checksum"] == result.checksum()


@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_mixed_values_round_trip(tmp_path, fmt):
    if fmt == "parquet":
        pytest.importorskip("pyarrow")

    class Mixed(Agent):
        def handle_message(self, content, meta):
            pass

    values = iter(["on", "", "1.5", {"a": [1, 2]}, None, True, Position2D(1.0, 2.0), 3])
    scenario = Scenario("mixed")
    scenario.time(start="2024-06-01T00:00", horizon="7h", step="1h")
    scenario.group("m", 1, lambda e: Mixed())
    scenario.record_world("state", lambda: next(values))
    result = scenario.run(seed=1)
    assert result.ts("state").tolist()[:4] == ["on", "", "1.5", '{"a":[1,2]}']
    stored = result.ts("state").tolist()
    assert math.isnan(stored[4]) and stored[5:] == [1.0, '{"x":1.0,"y":2.0}', 3.0]
    loaded = load_run(result.save(tmp_path / fmt, format=fmt))
    assert loaded.checksum() == result.checksum()
    reloaded = loaded.ts("state").tolist()
    assert (
        reloaded[:4] == stored[:4]
        and math.isnan(reloaded[4])
        and reloaded[5:] == stored[5:]
    )
    assert loaded.frame["datetime"].tolist() == result.frame["datetime"].tolist()


def test_load_run_errors(tmp_path):
    with pytest.raises(FileNotFoundError, match="No run folder"):
        load_run(tmp_path / "missing")
    (tmp_path / "partial").mkdir()
    with pytest.raises(FileNotFoundError, match="did not finish"):
        load_run(tmp_path / "partial")


# ----------------------------------------------------------------- tidy helpers


def test_tidy_value_conversions():
    assert tidy_value(3) == 3.0 and isinstance(tidy_value(3), float)
    assert tidy_value(True) == 1.0 and tidy_value(np.bool_(False)) == 0.0
    assert tidy_value(np.int64(4)) == 4.0 and tidy_value(np.float32(0.5)) == 0.5
    assert math.isnan(tidy_value(None))
    assert tidy_value("x") == "x"
    assert tidy_value({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert tidy_value(np.array([1, 2])) == "[1,2]"
    assert tidy_value(Position2D(1.0, 2.0)) == '{"x":1.0,"y":2.0}'
    assert tidy_value(object()).startswith('"<object object')


def test_tidy_frame_phases_and_checksum():
    rows = [
        {"time": t, "group": "g", "entity": "e", "variable": "v", "value": t}
        for t in (0.0, 5.0, 10.0, 15.0)
    ]
    frame = tidy_frame(rows, run_id="r", phases=[(0.0, "warmup"), (10.0, "main")])
    # the row at 10 s closes the last warm-up step; t=0 is the initial state
    assert frame["phase"].tolist() == ["warmup", "warmup", "warmup", "main"]
    assert tidy_frame([], run_id="r")["value"].dtype == "float64"
    assert frame_checksum(frame) == frame_checksum(frame.copy())
    other = tidy_frame(rows, run_id="other", phases=[(0.0, "warmup"), (10.0, "main")])
    assert frame_checksum(other) == frame_checksum(frame)  # run_id is not content
    changed = rows[:3] + [{**rows[3], "value": "15"}]
    assert frame_checksum(
        tidy_frame(changed, run_id="r", phases=[(0.0, "warmup"), (10.0, "main")])
    ) != frame_checksum(frame)


# ----------------------------------------------------------------- calendars and identity


class Clockwatcher(Agent):
    def __init__(self):
        super().__init__()
        self.hour = None

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        self.hour = clock.to_datetime(clock.time).hour


def dst_scenario(start):
    scenario = Scenario("dst")
    scenario.time(start=start, horizon="5h", step="1h")
    scenario.record(
        "hour", scenario.group("a", 1, lambda e: Clockwatcher()), attr="hour"
    )
    return scenario


def test_replay_from_config_keeps_a_named_time_zone():
    berlin = zoneinfo.ZoneInfo("Europe/Berlin")
    scenario = dst_scenario(dt.datetime(2024, 3, 31, tzinfo=berlin))
    first = scenario.run(seed=1)
    assert first.config["time"]["start"] == "2024-03-31T00:00:00+01:00[Europe/Berlin]"
    replay = scenario.run(first.config, seed=first.seed)
    assert replay.run_id == first.run_id and replay.checksum() == first.checksum()
    assert replay.ts("hour").dropna().tolist() == [0.0, 1.0, 3.0, 4.0, 5.0]
    # a fixed offset is another calendar, so another run id
    fixed = dst_scenario("2024-03-31T00:00+01:00").run(seed=1)
    assert fixed.run_id != first.run_id
    assert fixed.ts("hour").dropna().tolist() == [0.0, 1.0, 2.0, 3.0, 4.0]


@pytest.mark.parametrize("fmt", ["parquet", "csv"])
def test_datetimes_across_a_dst_switch_load_in_both_formats(tmp_path, fmt):
    if fmt == "parquet":
        pytest.importorskip("pyarrow")
    berlin = zoneinfo.ZoneInfo("Europe/Berlin")
    result = dst_scenario(dt.datetime(2024, 3, 31, tzinfo=berlin)).run(seed=1)
    loaded = load_run(result.save(tmp_path / "run", format=fmt))
    stamps = loaded.frame["datetime"]
    assert str(stamps.dt.tz) == "Europe/Berlin"
    assert stamps.tolist() == result.frame["datetime"].tolist()
    assert [s.utcoffset().total_seconds() / 3600 for s in stamps.iloc[[0, -1]]] == [
        1.0,
        2.0,
    ]
    assert loaded.checksum() == result.checksum()


SET_PARAMS = """
import sys
import zoneinfo
from pydantic import BaseModel
from mango.scenario import Scenario, ScenarioParams

class Tags(BaseModel, extra="forbid"):
    tags: set[str] = {"alpha", "beta", "gamma", "delta", "eps"}
    frozen: frozenset[str] = frozenset({"x", "y", "z", "w"})

class P(ScenarioParams):
    ev: Tags = Tags()

run = Scenario("sets", params=P).build(seed=7)
print(run.run_id, run.params.model_dump(mode="json")["ev"]["tags"])
"""


def test_run_ids_of_set_fields_are_equal_in_every_process():
    import os
    import subprocess

    ids, raw_orders = set(), set()
    for hash_seed in ("1", "2"):
        env = dict(os.environ, PYTHONHASHSEED=hash_seed)
        output = subprocess.run(
            [sys.executable, "-c", SET_PARAMS],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        run_id, _, raw = output.strip().partition(" ")
        ids.add(run_id)
        raw_orders.add(raw)
    assert len(raw_orders) == 2  # pydantic's own dump differs between the processes
    assert len(ids) == 1
