from typing import Any

import pytest

from mango import Agent, create_world, record_agent, record_world, step_simulation


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
