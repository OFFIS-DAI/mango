import datetime as dt

import pytest

import mango
from mango import (
    TIDY_COLUMNS,
    Agent,
    WorldRecording,
    collect_agent_data,
    collect_data,
    create_world,
    record_agent,
    record_world,
    recordings_to_frame,
    recordings_to_records,
)


class Counter(Agent):
    def __init__(self, category=None):
        super().__init__()
        self.count = 0
        if category:
            self.update_description(category=category)

    def on_step(self, env, clock, step_size_s):
        self.count += 1

    def handle_message(self, content, meta):
        pass


async def _world(**kwargs):
    world = create_world(**kwargs)
    world.register(Counter("ev"), suggested_aid="ev_0")
    world.register(Counter("ev"), suggested_aid="ev_1")
    world.register(Counter(), suggested_aid="hh_0")
    return world


def test_tidy_columns():
    assert TIDY_COLUMNS == ("time", "datetime", "group", "entity", "variable", "value")
    assert mango.simulation.TIDY_COLUMNS is TIDY_COLUMNS


def test_world_recording_entity_default():
    assert WorldRecording().entity is None


@pytest.mark.asyncio
async def test_records_order_and_content():
    world = await _world()
    record_world(world, "n_agents", lambda: len(world.agents))
    record_world(world, "load", lambda: 1.5, entity="grid")
    record_agent(world, "count", lambda a: a.count)
    record_agent(
        world, "double", lambda a: 2 * a.count, filter_fn=lambda a: a.aid != "hh_0"
    )
    async with world:
        await world.step(step_size_s=10)

    rows = recordings_to_records(world)
    assert all(
        list(r) == ["time", "group", "entity", "variable", "value"] for r in rows
    )
    assert rows[:4] == [
        {
            "time": 0.0,
            "group": "world",
            "entity": "world",
            "variable": "n_agents",
            "value": 3,
        },
        {
            "time": 10.0,
            "group": "world",
            "entity": "world",
            "variable": "n_agents",
            "value": 3,
        },
        {
            "time": 0.0,
            "group": "grid",
            "entity": "grid",
            "variable": "load",
            "value": 1.5,
        },
        {
            "time": 10.0,
            "group": "grid",
            "entity": "grid",
            "variable": "load",
            "value": 1.5,
        },
    ]
    agent_rows = [(r["variable"], r["entity"], r["time"], r["value"]) for r in rows[4:]]
    assert agent_rows == [
        ("count", "ev_0", 0.0, 0),
        ("count", "ev_0", 10.0, 1),
        ("count", "ev_1", 0.0, 0),
        ("count", "ev_1", 10.0, 1),
        ("count", "hh_0", 0.0, 0),
        ("count", "hh_0", 10.0, 1),
        ("double", "ev_0", 0.0, 0),
        ("double", "ev_0", 10.0, 2),
        ("double", "ev_1", 0.0, 0),
        ("double", "ev_1", 10.0, 2),
    ]
    groups = {r["entity"]: r["group"] for r in rows[4:]}
    assert groups == {"ev_0": "ev", "ev_1": "ev", "hh_0": "agent"}
    assert world.to_records() == rows


@pytest.mark.asyncio
async def test_groups_mapping_and_deregistered_agents():
    world = await _world()
    record_agent(world, "count", lambda a: a.count)
    async with world:
        await world.step(step_size_s=1)
        world.deregister("hh_0")
        rows = recordings_to_records(world, groups={"ev_0": "fleet"})
    groups = {r["entity"]: r["group"] for r in rows}
    assert groups == {"ev_0": "fleet", "ev_1": "ev", "hh_0": None}


@pytest.mark.asyncio
async def test_keys_and_include_filters():
    world = await _world()
    record_world(world, "w", lambda: 1)
    record_agent(world, "a", lambda a: 1)
    record_agent(world, "b", lambda a: 2)
    async with world:
        await world.step(step_size_s=1)
    assert {r["variable"] for r in recordings_to_records(world, keys=["b"])} == {"b"}
    assert {
        r["variable"] for r in recordings_to_records(world, include_world=False)
    } == {
        "a",
        "b",
    }
    assert {
        r["variable"] for r in recordings_to_records(world, include_agents=False)
    } == {"w"}
    with pytest.raises(KeyError, match="nope"):
        recordings_to_records(world, keys=["nope"])


@pytest.mark.asyncio
async def test_datetime_column_only_with_calendar():
    world = await _world(start_datetime="2024-06-01T00:00")
    record_world(world, "w", lambda: 1)
    record_agent(world, "a", lambda a: a.count)
    async with world:
        await world.step(step_size_s=3600)
    rows = world.to_records()
    assert all(list(r) == list(TIDY_COLUMNS) for r in rows)
    assert rows[1]["datetime"] == dt.datetime(2024, 6, 1, 1)
    assert rows[1]["time"] == 3600.0


@pytest.mark.asyncio
async def test_collect_data_entity_and_misaligned_world_recording():
    world = await _world()
    collect_data(
        world,
        "msgs",
        lambda w, rec: (rec.timeseries.append(0), rec.time.append(w.clock.time)),
        entity="network",
    )
    collect_data(world, "broken", lambda w, rec: rec.timeseries.append(1))
    async with world:
        await world.step(step_size_s=1)
    rows = recordings_to_records(world, keys=["msgs"])
    assert {(r["group"], r["entity"]) for r in rows} == {("network", "network")}
    with pytest.raises(ValueError, match="broken"):
        recordings_to_records(world, keys=["broken"])


@pytest.mark.asyncio
async def test_collect_agent_data_uses_shared_time_axis():
    world = await _world()
    collect_agent_data(
        world,
        "state",
        lambda w, a, rec: rec.timeseries.setdefault(a.aid, []).append(a.count),
    )
    async with world:
        await world.step(step_size_s=5)
        await world.step(step_size_s=5)
    rows = recordings_to_records(world, keys=["state"])
    assert [
        (r["entity"], r["time"], r["value"]) for r in rows if r["entity"] == "ev_0"
    ] == [
        ("ev_0", 0.0, 0),
        ("ev_0", 5.0, 1),
        ("ev_0", 10.0, 2),
    ]


@pytest.mark.asyncio
async def test_collect_agent_data_misaligned_raises():
    world = await _world()

    def collector(w, agent, rec):
        if agent.aid == "ev_0" and w.clock.time > 0:
            rec.timeseries.setdefault(agent.aid, []).append(agent.count)

    collect_agent_data(world, "sparse", collector)
    async with world:
        await world.step(step_size_s=1)
        await world.step(step_size_s=1)
    with pytest.raises(ValueError, match="ev_0"):
        recordings_to_records(world)


@pytest.mark.asyncio
async def test_agent_time_length_mismatch_raises():
    world = await _world()
    record_agent(world, "a", lambda a: 1)
    async with world:
        await world.step(step_size_s=1)
    world.data_agent_collections["a"].agent_time["ev_0"].pop()
    with pytest.raises(ValueError, match="ev_0"):
        recordings_to_records(world)


@pytest.mark.asyncio
async def test_frame_columns_and_values():
    pd = pytest.importorskip("pandas")
    world = await _world()
    record_world(world, "w", lambda: 2.0)
    record_agent(world, "count", lambda a: a.count)
    async with world:
        await world.step(step_size_s=1)
    df = recordings_to_frame(world)
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["time", "group", "entity", "variable", "value"]
    assert len(df) == 2 + 3 * 2
    assert df.equals(world.to_frame())
    assert list(df[df["entity"] == "ev_1"]["value"]) == [0, 1]


@pytest.mark.asyncio
async def test_frame_with_calendar_and_empty():
    pytest.importorskip("pandas")
    world = await _world(start_datetime="2024-06-01T00:00")
    df = recordings_to_frame(world)
    assert list(df.columns) == list(TIDY_COLUMNS)
    assert df.empty
    record_world(world, "w", lambda: 1)
    async with world:
        await world.step(step_size_s=60)
    df = world.to_frame()
    assert list(df["datetime"]) == [
        dt.datetime(2024, 6, 1, 0, 0),
        dt.datetime(2024, 6, 1, 0, 1),
    ]


def test_frame_import_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pandas":
            raise ImportError("no pandas")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError, match="pip install pandas"):
        recordings_to_frame(create_world())
