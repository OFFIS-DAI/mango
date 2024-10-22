import asyncio
from typing import Any

import pytest

from mango import Agent, complete_topology, create_topology, per_node, run_with_tcp


class TopAgent(Agent):
    counter: int = 0

    def handle_message(self, content, meta: dict[str, Any]):
        self.counter += 1


@pytest.mark.asyncio
async def test_run_api_style_agent():
    # GIVEN
    topology = complete_topology(3)
    for node in per_node(topology):
        agent = TopAgent()
        node.add(agent)

    # WHEN
    async with run_with_tcp(1, *topology.agents):
        for neighbor in topology.agents[0].neighbors():
            await topology.agents[0].send_message("hello neighbors", neighbor)
        await asyncio.sleep(0.1)

    # THEN
    assert topology.agents[1].counter == 1
    assert topology.agents[2].counter == 1


@pytest.mark.asyncio
async def test_run_api_style_custom_topology():
    # GIVEN
    agents = [TopAgent(), TopAgent(), TopAgent()]
    with create_topology() as topology:
        id_1 = topology.add_node(agents[0])
        id_2 = topology.add_node(agents[1])
        id_3 = topology.add_node(agents[2])
        topology.add_edge(id_1, id_2)
        topology.add_edge(id_1, id_3)

    # WHEN
    async with run_with_tcp(1, *agents):
        for neighbor in agents[0].neighbors():
            await agents[0].send_message("hello neighbors", neighbor)
        await asyncio.sleep(0.1)

    # THEN
    assert agents[1].counter == 1
    assert agents[2].counter == 1
