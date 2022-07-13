
from typing import Any, Dict

import pytest, asyncio

from mango.core.agent import Agent
from mango.core.container import Container


class MyAgent(Agent): 
    def handle_msg(self, content, meta: Dict[str, Any]):
        pass


@pytest.mark.asyncio
async def test_periodic_facade():
    # GIVEN        
    c = await Container.factory(addr=('127.0.0.2', 5555))
    agent = MyAgent(c)
    l = []

    async def increase_counter():
        l.append(1)

    # WHEN
    t = agent.schedule_periodic_task(increase_counter, 2)
    try: 
        await asyncio.wait_for(t, timeout=3)
    except asyncio.exceptions.TimeoutError:
        pass

    # THEN
    assert len(l) == 2
