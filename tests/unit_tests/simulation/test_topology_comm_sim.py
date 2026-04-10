from typing import Any

import pytest

from mango import Agent, create_world, step_simulation
from mango.simulation.communication import create_distribution_based_com_sim


class SimpleAgent(Agent):
    def __init__(self):
        super().__init__()
        self.messages: list[tuple[Any, dict]] = []

    def handle_message(self, content, meta):
        self.messages.append((content, meta))


def test_path_graph_delays():
    nx = pytest.importorskip("networkx")
    g = nx.path_graph(["a0", "a1", "a2"])
    sim = create_distribution_based_com_sim(g, default_delay_per_edge_ms=100.0)
    assert ("a0", "a1") in sim.delay_s_directed_edge_dict
    assert sim.delay_s_directed_edge_dict[("a0", "a1")]() == pytest.approx(0.1)
    assert sim.delay_s_directed_edge_dict[("a0", "a2")]() == pytest.approx(0.2)


def test_base_delay_added():
    nx = pytest.importorskip("networkx")
    g = nx.path_graph(["x", "y"])
    sim = create_distribution_based_com_sim(
        g,
        default_delay_per_edge_ms=50.0,
        base_delay_per_message_ms=10.0,
    )
    assert sim.delay_s_directed_edge_dict[("x", "y")]() == pytest.approx(0.06)


def test_max_cap_applied():
    nx = pytest.importorskip("networkx")
    g = nx.path_graph(["a", "b", "c", "d"])
    sim = create_distribution_based_com_sim(
        g,
        default_delay_per_edge_ms=100.0,
        max_edge_delay_ms=150.0,
    )
    assert sim.delay_s_directed_edge_dict[("a", "d")]() == pytest.approx(0.15)


def test_custom_distribution_provider():
    nx = pytest.importorskip("networkx")
    g = nx.path_graph(["p", "q"])
    sampled = []

    def provider(mean_ms):
        def _sample():
            sampled.append(mean_ms)
            return mean_ms / 1000.0

        return _sample

    sim = create_distribution_based_com_sim(
        g, default_delay_per_edge_ms=200.0, distribution_provider=provider
    )
    sim.delay_s_directed_edge_dict[("p", "q")]()
    assert sampled == [200.0]


def test_no_self_loops():
    nx = pytest.importorskip("networkx")
    g = nx.complete_graph(["a", "b", "c"])
    sim = create_distribution_based_com_sim(g)
    for key in sim.delay_s_directed_edge_dict:
        assert key[0] != key[1]


def test_directed_graph_one_way():
    nx = pytest.importorskip("networkx")
    g = nx.DiGraph()
    g.add_edge("src", "dst")
    sim = create_distribution_based_com_sim(g, default_delay_per_edge_ms=10.0)
    assert ("src", "dst") in sim.delay_s_directed_edge_dict
    assert ("dst", "src") not in sim.delay_s_directed_edge_dict


@pytest.mark.asyncio
async def test_delays_messages_in_simulation():
    nx = pytest.importorskip("networkx")
    g = nx.path_graph(["agent0", "agent1"])
    sim = create_distribution_based_com_sim(g, default_delay_per_edge_ms=1000.0)

    world = create_world(communication_sim=sim)
    sender = world.register(SimpleAgent(), suggested_aid="agent0")
    receiver = world.register(SimpleAgent(), suggested_aid="agent1")

    async with world:
        await world.send_message(
            "hi", receiver_addr=receiver.addr, sender_id=sender.aid
        )
        r1 = await step_simulation(world, step_size_s=0.5)
        assert r1.messages_delivered == 0
        r2 = await step_simulation(world, step_size_s=1.0)
        assert r2.messages_delivered == 1
