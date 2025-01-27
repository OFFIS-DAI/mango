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
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    agent = c.register(MyAgent())
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


@pytest.mark.asyncio
async def test_send_message():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    agent = c.register(MyAgent())
    agent2 = c.register(MyAgent())

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
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    agent = c.register(MyAgent())
    agent2 = c.register(MyAgent())

    async with activate(c) as c:
        await agent.send_message(
            create_acl("", receiver_addr=agent2.addr, sender_addr=agent.addr),
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
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    agent = c.register(MyAgent())
    agent2 = c.register(MyAgent())

    async with activate(c) as c:
        await agent.schedule_instant_message("", receiver_addr=agent2.addr)

    # THEN
    assert agent2.test_counter == 1


@pytest.mark.asyncio
async def test_schedule_acl_message():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    agent = c.register(MyAgent())
    agent2 = c.register(MyAgent())

    async with activate(c) as c:
        await agent.schedule_instant_message(
            create_acl("", receiver_addr=agent2.addr, sender_addr=agent.addr),
            receiver_addr=agent2.addr,
        )

    # THEN
    assert agent2.test_counter == 1


@pytest.mark.asyncio
async def test_delayed_agent_creation():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    agent = c.register(MyAgent())

    async with activate(c) as c:
        agent2 = c.register(MyAgent())
        await agent.schedule_instant_message(
            create_acl("", receiver_addr=agent2.addr, sender_addr=agent.addr),
            receiver_addr=agent2.addr,
        )

    # THEN
    assert agent2.test_counter == 1


def test_lazy_setup_agent():
    # this test is not async and therefore does not provide a running event loop
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    # registration without async context should not raise "no running event loop" error
    agent = c.register(MyAgent())
    agent2 = c.register(MyAgent())

    async def run_this(c):
        async with activate(c) as c:
            await agent.schedule_instant_message(
                create_acl("", receiver_addr=agent2.addr, sender_addr=agent.addr),
                receiver_addr=agent2.addr,
            )  # THEN
        assert agent2.test_counter == 1

    asyncio.run(run_this(c))


async def do_weird_stuff():
    fut = asyncio.Future()
    await fut


@pytest.mark.asyncio
async def test_agent_with_deadlock_task():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    agent = c.register(MyAgent())

    async with activate(c) as c:
        t = agent.schedule_instant_task(do_weird_stuff())
        t = agent.schedule_instant_task(do_weird_stuff())
        t = agent.schedule_instant_task(do_weird_stuff())
        t = agent.schedule_instant_task(do_weird_stuff())

    # THEN
    assert len(agent.scheduler._scheduled_tasks) == 0
