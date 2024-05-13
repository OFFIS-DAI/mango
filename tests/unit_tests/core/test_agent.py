import asyncio
from typing import Any, Dict

import pytest

from mango import create_container
from mango.agent.core import Agent


class MyAgent(Agent):

    test_counter: int = 0

    def handle_message(self, content, meta: Dict[str, Any]):
        self.test_counter += 1


@pytest.mark.asyncio
async def test_periodic_facade():
    # GIVEN
    c = await create_container(addr=("127.0.0.2", 5555))
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
    await c.shutdown()


@pytest.mark.asyncio
async def test_send_message():
    # GIVEN
    c = await create_container(addr=("127.0.0.2", 5555))
    agent = MyAgent(c)
    agent2 = MyAgent(c)

    await agent.send_message("", receiver_addr=agent.addr, receiver_id=agent2.aid)
    msg = await agent2.inbox.get()
    _, content, meta = msg
    agent2.handle_message(content=content, meta=meta)

    # THEN
    assert agent2.test_counter == 1
    await c.shutdown()


@pytest.mark.asyncio
async def test_send_acl_message():
    # GIVEN
    c = await create_container(addr=("127.0.0.2", 5555))
    agent = MyAgent(c)
    agent2 = MyAgent(c)

    await agent.send_acl_message("", receiver_addr=agent.addr, receiver_id=agent2.aid)
    msg = await agent2.inbox.get()
    _, content, meta = msg
    agent2.handle_message(content=content, meta=meta)

    # THEN
    assert agent2.test_counter == 1
    await c.shutdown()


@pytest.mark.asyncio
async def test_schedule_message():
    # GIVEN
    c = await create_container(addr=("127.0.0.2", 5555))
    agent = MyAgent(c)
    agent2 = MyAgent(c)

    agent.schedule_instant_message("", receiver_addr=agent.addr, receiver_id=agent2.aid)
    msg = await agent2.inbox.get()
    _, content, meta = msg
    agent2.handle_message(content=content, meta=meta)

    # THEN
    assert agent2.test_counter == 1
    await c.shutdown()


@pytest.mark.asyncio
async def test_schedule_acl_message():
    # GIVEN
    c = await create_container(addr=("127.0.0.2", 5555))
    agent = MyAgent(c)
    agent2 = MyAgent(c)

    agent.schedule_instant_acl_message(
        "", receiver_addr=agent.addr, receiver_id=agent2.aid
    )
    msg = await agent2.inbox.get()
    _, content, meta = msg
    agent2.handle_message(content=content, meta=meta)

    # THEN
    assert agent2.test_counter == 1
    await c.shutdown()
