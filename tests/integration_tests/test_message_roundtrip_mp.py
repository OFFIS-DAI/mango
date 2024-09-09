import asyncio

import pytest

import mango.container.factory as container_factory
from mango.agent.core import Agent


class PingPongAgent(Agent):
    test_counter: int = 0
    current_task: object

    def handle_message(self, content, meta):
        self.test_counter += 1

        # get addr and id from sender
        if self.test_counter == 1:
            receiver_host, receiver_port = meta["sender_addr"]
            receiver_id = meta["sender_id"]
            # send back pong, providing your own details
            self.current_task = self.schedule_instant_acl_message(
                content="pong",
                receiver_addr=(receiver_host, receiver_port),
                receiver_id=receiver_id,
                acl_metadata={
                    "sender_addr": self.addr,
                    "sender_id": self.aid,
                },
            )


@pytest.mark.asyncio
async def test_mp_simple_ping_pong_multi_container_tcp():
    init_addr = ("localhost", 1555)
    repl_addr = ("localhost", 1556)
    aid1 = "c1_p1_agent"
    aid2 = "c2_p1_agent"

    container_1 = await container_factory.create(
        addr=init_addr,
    )
    container_2 = await container_factory.create(
        addr=repl_addr,
    )
    await container_1.as_agent_process(
        agent_creator=lambda c: PingPongAgent(c, suggested_aid=aid1)
    )
    await container_2.as_agent_process(
        agent_creator=lambda c: PingPongAgent(c, suggested_aid=aid2)
    )
    agent = PingPongAgent(container_1)

    await agent.send_acl_message(
        "Message To Process Agent1",
        receiver_addr=container_1.addr,
        receiver_id=aid1,
        acl_metadata={"sender_id": agent.aid},
    )

    await agent.send_acl_message(
        "Message To Process Agent2",
        receiver_addr=container_2.addr,
        receiver_id=aid2,
        acl_metadata={"sender_id": agent.aid},
    )

    while agent.test_counter != 2:
        await asyncio.sleep(0.1)

    assert agent.test_counter == 2

    await asyncio.gather(
        container_1.shutdown(),
        container_2.shutdown(),
    )
