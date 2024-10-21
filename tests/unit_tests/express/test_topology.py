import asyncio
from typing import Any

import pytest

from mango import Agent, complete_topology, per_node, run_with_tcp


class TopAgent(Agent):
    counter: int = 0

    def handle_message(self, content, meta: dict[str, Any]):
        self.counter += 1


@pytest.mark.asyncio
async def test_run_api_style_agent():
    # GIVEN
    agents = []
    topology = complete_topology(3)
    for node in per_node(topology):
        agent = TopAgent()
        agents.append(agent)
        node.add(agent)

    # WHEN
    async with run_with_tcp(1, *agents):
        for neighbor in agents[0].neighbors():
            await agents[0].send_message("hello neighbors", neighbor)
        await asyncio.sleep(0.1)

    # THEN
    assert agents[1].counter == 1
    assert agents[2].counter == 1
