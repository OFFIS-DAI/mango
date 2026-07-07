import sys
from typing import Any

import pytest

from mango import (
    Agent,
    AgentsRecording,
    MessageTransaction,
    create_world,
    record_agent,
    record_world,
    step_simulation,
)


class SimpleAgent(Agent):
    def __init__(self):
        super().__init__()
        self.messages: list[tuple[Any, dict]] = []

    def handle_message(self, content, meta):
        self.messages.append((content, meta))


@pytest.fixture(autouse=True)
def _use_agg_backend():
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")


@pytest.mark.asyncio
async def test_plot_world_returns_figure():
    from mango.simulation.visualization import plot_world

    world = create_world()
    world.register(SimpleAgent())
    record_world(world, "count", lambda: 1)
    async with world:
        await step_simulation(world, step_size_s=1.0)

    fig = plot_world(world, "count")
    assert fig is not None


@pytest.mark.asyncio
async def test_plot_world_missing_key_raises():
    from mango.simulation.visualization import plot_world

    world = create_world()
    world.register(SimpleAgent())
    async with world:
        await step_simulation(world, step_size_s=1.0)

    with pytest.raises(KeyError):
        plot_world(world, "nonexistent")


@pytest.mark.asyncio
async def test_plot_agents_returns_figure():
    from mango.simulation.visualization import plot_agents

    world = create_world()
    world.register(SimpleAgent())
    record_agent(world, "dummy", lambda a: 42)
    async with world:
        await step_simulation(world, step_size_s=1.0)

    fig = plot_agents(world, "dummy")
    assert fig is not None


@pytest.mark.asyncio
async def test_plot_recordings_returns_figure():
    from mango.simulation.visualization import plot_recordings

    world = create_world()
    world.register(SimpleAgent())
    record_world(world, "w", lambda: 1)
    record_agent(world, "a", lambda agent: 2)
    async with world:
        await step_simulation(world, step_size_s=1.0)

    fig = plot_recordings(world)
    assert fig is not None


@pytest.mark.asyncio
async def test_plot_recordings_no_recordings_raises():
    from mango.simulation.visualization import plot_recordings

    world = create_world()
    world.register(SimpleAgent())
    async with world:
        await step_simulation(world, step_size_s=1.0)

    with pytest.raises(ValueError):
        plot_recordings(world)


@pytest.mark.asyncio
async def test_show_communication_data_returns_figure():
    from mango.simulation.visualization import show_communication_data

    world = create_world()
    sender = world.register(SimpleAgent())
    receiver = world.register(SimpleAgent())
    async with world:
        await world.send_message(
            "ping", receiver_addr=receiver.addr, sender_id=sender.aid
        )
        await step_simulation(world, step_size_s=1.0)

    fig = show_communication_data(world)
    assert fig is not None


@pytest.mark.asyncio
async def test_show_communication_data_no_messages_raises():
    from mango.simulation.visualization import show_communication_data

    world = create_world()
    world.register(SimpleAgent())
    async with world:
        await step_simulation(world, step_size_s=1.0)

    with pytest.raises(ValueError):
        show_communication_data(world)


@pytest.mark.asyncio
async def test_show_communication_data_custom_names():
    from mango.simulation.visualization import show_communication_data

    world = create_world()
    sender = world.register(SimpleAgent())
    receiver = world.register(SimpleAgent())
    async with world:
        await world.send_message(
            "hi", receiver_addr=receiver.addr, sender_id=sender.aid
        )
        await step_simulation(world, step_size_s=1.0)

    fig = show_communication_data(
        world,
        aid_to_name={sender.aid: "Alice", receiver.aid: "Bob"},
        aid_to_color={sender.aid: "blue", receiver.aid: "red"},
    )
    assert fig is not None


def test_require_matplotlib_missing_raises(monkeypatch):
    from mango.simulation.visualization import _require_matplotlib

    monkeypatch.setitem(sys.modules, "matplotlib.pyplot", None)
    with pytest.raises(ImportError, match="matplotlib is required"):
        _require_matplotlib()


@pytest.mark.asyncio
async def test_plot_world_color_and_write_to(tmp_path):
    from mango.simulation.visualization import plot_world

    world = create_world()
    world.register(SimpleAgent())
    record_world(world, "count", lambda: 1)
    async with world:
        await step_simulation(world, step_size_s=1.0)

    out = tmp_path / "world.png"
    fig = plot_world(world, "count", title="Custom", color="red", write_to=str(out))
    assert fig is not None
    assert out.exists()


@pytest.mark.asyncio
async def test_plot_agents_missing_key_raises():
    from mango.simulation.visualization import plot_agents

    world = create_world()
    world.register(SimpleAgent())
    async with world:
        await step_simulation(world, step_size_s=1.0)

    with pytest.raises(KeyError):
        plot_agents(world, "nonexistent")


@pytest.mark.asyncio
async def test_plot_agents_color_list_and_write_to(tmp_path):
    from mango.simulation.visualization import plot_agents

    world = create_world()
    world.register(SimpleAgent())
    world.register(SimpleAgent())
    record_agent(world, "val", lambda a: 1)
    async with world:
        await step_simulation(world, step_size_s=1.0)

    out = tmp_path / "agents.png"
    fig = plot_agents(world, "val", color=["red", "blue"], write_to=str(out))
    assert fig is not None
    assert out.exists()


@pytest.mark.asyncio
async def test_plot_agents_single_color():
    from mango.simulation.visualization import plot_agents

    world = create_world()
    world.register(SimpleAgent())
    record_agent(world, "val", lambda a: 1)
    async with world:
        await step_simulation(world, step_size_s=1.0)

    fig = plot_agents(world, "val", color="green")
    assert fig is not None


def test_plot_agents_unregistered_aid_uses_raw_label():
    from mango.simulation.visualization import plot_agents

    world = create_world()
    world.data_agent_collections["ghost"] = AgentsRecording(
        timeseries={"ghost-aid": [1, 2]}, time=[0.0, 1.0]
    )

    fig = plot_agents(world, "ghost")
    assert fig is not None


@pytest.mark.asyncio
async def test_plot_recordings_colormap_hidden_axes_and_write_to(tmp_path):
    from mango.simulation.visualization import plot_recordings

    world = create_world()
    world.register(SimpleAgent())
    world.register(SimpleAgent())
    # Four recordings → 3x2 grid leaves two axes to hide.
    record_world(world, "w1", lambda: 1)
    record_world(world, "w2", lambda: 2)
    record_world(world, "w3", lambda: 3)
    record_agent(world, "a1", lambda a: 4)
    async with world:
        await step_simulation(world, step_size_s=1.0)

    out = tmp_path / "grid.png"
    fig = plot_recordings(
        world, figsize=(12, 8), colormap="viridis", write_to=str(out)
    )
    assert fig is not None
    assert out.exists()


@pytest.mark.asyncio
async def test_show_communication_data_write_to(tmp_path):
    from mango.simulation.visualization import show_communication_data

    world = create_world()
    sender = world.register(SimpleAgent())
    receiver = world.register(SimpleAgent())
    async with world:
        await world.send_message(
            "ping", receiver_addr=receiver.addr, sender_id=sender.aid
        )
        await step_simulation(world, step_size_s=1.0)

    out = tmp_path / "comms.png"
    fig = show_communication_data(world, write_to=str(out))
    assert fig is not None
    assert out.exists()


def test_show_communication_data_none_sender_and_self_message():
    from mango.simulation.visualization import show_communication_data

    world = create_world()
    world.recorded_messages = [
        MessageTransaction(
            sender_id=None,
            receiver_id="agent1",
            sent_time=0.0,
            arriving_time=1.0,
            content="broadcast",
        ),
        MessageTransaction(
            sender_id="agent1",
            receiver_id="agent1",
            sent_time=1.0,
            arriving_time=2.0,
            content="self ping",
        ),
        MessageTransaction(
            sender_id="agent1",
            receiver_id="agent2",
            sent_time=2.0,
            arriving_time=3.0,
            content="x" * 40,
        ),
    ]

    fig = show_communication_data(world)
    assert fig is not None
