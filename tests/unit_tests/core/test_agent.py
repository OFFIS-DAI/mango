import asyncio
from typing import Any

import pytest

from mango import activate, create_acl, create_tcp_container
from mango.agent.core import Agent


class MyAgent(Agent):
    test_counter: int = 0

    def handle_message(self, content, meta: dict[str, Any]):
        self.test_counter += 1


@pytest.mark.asyncio
async def test_periodic_facade():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.2", 5555))
    agent = c.include(MyAgent())
    l = []

    async def increase_counter():
        l.append(1)

    # WHEN
    t = agent.schedule_periodic_task(increase_counter, 0.2)
    try:
        await asyncio.wait_for(t, timeout=0.3)
    except asyncio.exceptions.TimeoutError:
        pass

    # THEN
    assert len(l) == 2
    await c.shutdown()


@pytest.mark.asyncio
async def test_send_message():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.2", 5555))
    agent = c.include(MyAgent())
    agent2 = c.include(MyAgent())

    async with activate(c) as c:
        await agent.send_message("", receiver_addr=agent2.addr)
        msg = await agent2.inbox.get()
        _, content, meta = msg
        agent2.handle_message(content=content, meta=meta)

    # THEN
    assert agent2.test_counter == 1


@pytest.mark.asyncio
async def test_send_acl_message():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.2", 5555))
    agent = c.include(MyAgent())
    agent2 = c.include(MyAgent())

    async with activate(c) as c:
        await agent.send_message(
            create_acl("", receiver_addr=agent2.addr, sender_addr=c.addr),
            receiver_addr=agent2.addr,
        )
        msg = await agent2.inbox.get()
        _, content, meta = msg
        agent2.handle_message(content=content, meta=meta)

    # THEN
    assert agent2.test_counter == 1


@pytest.mark.asyncio
async def test_schedule_message():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.2", 5555))
    agent = c.include(MyAgent())
    agent2 = c.include(MyAgent())

    async with activate(c) as c:
        await agent.schedule_instant_message("", receiver_addr=agent2.addr)

    # THEN
    assert agent2.test_counter == 1


@pytest.mark.asyncio
async def test_schedule_acl_message():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.2", 5555))
    agent = c.include(MyAgent())
    agent2 = c.include(MyAgent())

    async with activate(c) as c:
        await agent.schedule_instant_message(
            create_acl("", receiver_addr=agent2.addr, sender_addr=c.addr),
            receiver_addr=agent2.addr,
        )

    # THEN
    assert agent2.test_counter == 1
