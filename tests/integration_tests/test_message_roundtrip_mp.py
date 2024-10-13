import asyncio

import pytest

from mango.agent.core import Agent
from mango import AgentAddress, sender_addr, create_tcp_container, activate


class PingPongAgent(Agent):
    test_counter: int = 0
    current_task: object

    def handle_message(self, content, meta):
        self.test_counter += 1

        # get addr and id from sender
        if self.test_counter == 1:
            # send back pong, providing your own details
            self.current_task = self.schedule_instant_message(
                content="pong",
                receiver_addr=sender_addr(meta),
            )


@pytest.mark.asyncio
async def test_mp_simple_ping_pong_multi_container_tcp():
    init_addr = ("localhost", 1555)
    repl_addr = ("localhost", 1556)
    aid1 = "c1_p1_agent"
    aid2 = "c2_p1_agent"

    container_1 = create_tcp_container(
        addr=init_addr,
    )
    container_2 = create_tcp_container(
        addr=repl_addr,
    )
    await container_1.as_agent_process(
        agent_creator=lambda c: c.include(PingPongAgent(), suggested_aid=aid1)
    )
    await container_2.as_agent_process(
        agent_creator=lambda c: c.include(PingPongAgent(), suggested_aid=aid2)
    )
    agent = container_1.include(PingPongAgent())

    async with activate(container_1, container_2) as cl:
        await agent.send_message(
            "Message To Process Agent1",
            receiver_addr=AgentAddress(container_1.addr,aid1)
        )

        await agent.send_message(
            "Message To Process Agent2",
            receiver_addr=AgentAddress(container_2.addr,aid2)
        )

        while agent.test_counter != 2:
            await asyncio.sleep(0.1)

    assert agent.test_counter == 2
