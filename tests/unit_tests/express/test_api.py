import asyncio
from typing import Any

import pytest

from mango import (
    Agent,
    Role,
    activate,
    addr,
    agent_composed_of,
    create_tcp_container,
    run_with_mqtt,
    run_with_tcp,
    sender_addr,
)


class PingPongRole(Role):
    counter: int = 0

    def handle_message(self, content: Any, meta: dict):
        if self.counter >= 5:
            return

        self.counter += 1

        if content == "Ping":
            self.context.schedule_instant_message("Pong", sender_addr(meta))
        elif content == "Pong":
            self.context.schedule_instant_message("Ping", sender_addr(meta))


@pytest.mark.asyncio
async def test_activate_pingpong():
    container = create_tcp_container("127.0.0.1:5555")
    ping_pong_agent = agent_composed_of(PingPongRole(), register_in=container)
    ping_pong_agent_two = agent_composed_of(PingPongRole(), register_in=container)

    async with activate(container) as c:
        await c.send_message(
            "Ping", ping_pong_agent.addr, sender_id=ping_pong_agent_two.aid
        )
        while ping_pong_agent.roles[0].counter < 5:
            await asyncio.sleep(0.01)

    assert ping_pong_agent.roles[0].counter == 5


class MyAgent(Agent):
    test_counter: int = 0

    def handle_message(self, content, meta: dict[str, Any]):
        self.test_counter += 1


@pytest.mark.asyncio
async def test_activate_api_style_agent():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    agent = c.register(MyAgent())
    agent2 = c.register(MyAgent())

    # WHEN
    async with activate(c) as c:
        await agent.schedule_instant_message("", receiver_addr=agent2.addr)

    # THEN
    assert agent2.test_counter == 1


@pytest.mark.asyncio
async def test_run_api_style_agent():
    # GIVEN
    run_agent = MyAgent()
    run_agent2 = MyAgent()

    # WHEN
    async with run_with_tcp(1, run_agent, run_agent2) as c:
        await run_agent.schedule_instant_message("", receiver_addr=run_agent2.addr)

    # THEN
    assert run_agent2.test_counter == 1


@pytest.mark.asyncio
async def test_run_api_style_agent_auto_port():
    # GIVEN
    run_agent = MyAgent()
    run_agent2 = MyAgent()

    # WHEN
    async with run_with_tcp(
        2, run_agent, run_agent2, auto_port=True, addr=("127.0.0.1", None)
    ) as cl:
        await run_agent.schedule_instant_message("", receiver_addr=run_agent2.addr)
        await asyncio.sleep(0.1)
        assert cl[0].addr[1] is not None
        assert cl[1].addr[1] is not None

    # THEN
    assert run_agent2.test_counter == 1


@pytest.mark.asyncio
async def test_run_api_style_agent_with_aid():
    # GIVEN
    run_agent = MyAgent()
    run_agent2 = MyAgent()

    # WHEN
    async with run_with_tcp(1, run_agent, (run_agent2, dict(aid="my_custom_aid"))) as c:
        await run_agent.schedule_instant_message("", receiver_addr=run_agent2.addr)

    # THEN
    assert run_agent2.test_counter == 1
    assert run_agent2.aid == "my_custom_aid"
    assert run_agent.aid == "agent0"


@pytest.mark.asyncio
@pytest.mark.mqtt
async def test_run_api_style_agent_with_aid_mqtt():
    # GIVEN
    run_agent = MyAgent()
    run_agent2 = MyAgent()

    # WHEN
    async with run_with_mqtt(
        1,
        (run_agent, dict(topics=["my_top"])),
        (run_agent2, dict(topics=["your_top"], aid="my_custom_aid")),
    ) as c:
        await run_agent.schedule_instant_message("", addr("your_top", run_agent2.aid))
        while run_agent2.test_counter == 0:
            await asyncio.sleep(0.01)

    # THEN
    assert run_agent2.test_counter == 1
    assert run_agent2.aid == "my_custom_aid"
    assert run_agent.aid == "agent0"


@pytest.mark.asyncio
async def test_deactivate_suspendable():
    container = create_tcp_container("127.0.0.1:5555")
    ping_pong_agent = agent_composed_of(PingPongRole(), register_in=container)
    ping_pong_agent.suspendable_tasks = False

    async with activate(container) as c:
        assert ping_pong_agent.scheduler.suspendable is False
