def test_dummy():
    assert True
    pass


import asyncio
import pytest
from mango import create_container, Agent


class MyAgent(Agent):
    test_counter: int = 0
    current_task: object

    def handle_message(self, content, meta):
        self.test_counter += 1

        # get addr and id from sender
        receiver_host, receiver_port = meta["sender_addr"]
        receiver_id = meta["sender_id"]
        # send back pong, providing your own details
        self.current_task = self.context.schedule_instant_acl_message(
            content="pong",
            receiver_addr=(receiver_host, receiver_port),
            receiver_id=receiver_id,
            acl_metadata={
                "sender_addr": self.context.addr,
                "sender_id": self.context.aid,
            },
        )


@pytest.mark.asyncio
async def test_agent_processes():
    # GIVEN
    c = await create_container(addr=("127.0.0.2", 5555), copy_internal_messages=False)
    c.as_agent_process(
        agent_creator=lambda container: MyAgent(
            container, suggested_aid="process_agent"
        )
    )
    agent = MyAgent(c)

    # WHEN
    await agent.send_message(
        "Message To Process Agent", receiver_addr=c.addr, receiver_id="process_agent"
    )
    timeout = 10
    while agent.test_counter == 0 and timeout > 0:
        await asyncio.sleep(0.1)
        timeout = timeout - 1

    assert agent.test_counter == 1

    c.shutdown()
