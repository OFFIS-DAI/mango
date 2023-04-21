def test_dummy():
    assert True
    pass


import pytest
from mango import create_container, Agent


class MyAgent(Agent):
    test_counter: int = 0

    def handle_message(self, content, meta):
        self.test_counter += 1


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
