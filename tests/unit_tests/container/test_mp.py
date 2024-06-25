import asyncio
import pytest
from mango import create_container, Agent


class MyAgent(Agent):
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


class P2PMainAgent(Agent):
    test_counter: int = 0
    current_task: object

    def handle_message(self, content, meta):
        self.test_counter += 1


class P2PTestAgent(Agent):
    def __init__(self, container, response_aid, suggested_aid: str = None):
        super().__init__(container, suggested_aid)
        self._response_aid = response_aid

    def handle_message(self, content, meta):
        receiver_host, receiver_port = meta["sender_addr"]
        # send back pong, providing your own details
        self.current_task = self.schedule_instant_acl_message(
            content="pong",
            receiver_addr=(receiver_host, receiver_port),
            receiver_id=self._response_aid,
            acl_metadata={
                "sender_addr": self.addr,
                "sender_id": self.aid,
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "num_sp_agents,num_sp",
    [
        (1, 1),
        (2, 1),
        (2, 2),
        (1, 2),
        (3, 2),
        (2, 3),
        (3, 3),
        (1, 10),
        (10, 1),
        (10, 2),
        (10, 10),
    ],
)
async def test_agent_processes_ping_pong(num_sp_agents, num_sp):
    # GIVEN
    c = await create_container(addr=("127.0.0.2", 15589), copy_internal_messages=False)
    for i in range(num_sp):
        await c.as_agent_process(
            agent_creator=lambda container: [
                MyAgent(container, suggested_aid=f"process_agent{i},{j}")
                for j in range(num_sp_agents)
            ]
        )
    agent = MyAgent(c)

    # WHEN
    for i in range(num_sp):
        for j in range(num_sp_agents):
            await agent.send_acl_message(
                "Message To Process Agent",
                receiver_addr=c.addr,
                receiver_id=f"process_agent{i},{j}",
                acl_metadata={"sender_id": agent.aid},
            )
    while agent.test_counter != num_sp_agents * num_sp:
        await asyncio.sleep(0.1)

    assert agent.test_counter == num_sp_agents * num_sp
    await c.shutdown()


@pytest.mark.asyncio
async def test_agent_processes_ping_pong_p_to_p():
    # GIVEN
    addr = ("127.0.0.2", 5826)
    aid_main_agent = "main_agent"
    c = await create_container(addr=addr, copy_internal_messages=False)
    await c.as_agent_process(
        agent_creator=lambda container: P2PTestAgent(
            container, aid_main_agent, suggested_aid=f"process_agent1"
        )
    )
    main_agent = P2PMainAgent(c, suggested_aid=aid_main_agent)

    # WHEN
    def agent_init(c):
        agent = MyAgent(c, suggested_aid=f"process_agent2")
        agent.schedule_instant_acl_message(
            "Message To Process Agent",
            receiver_addr=addr,
            receiver_id=f"process_agent1",
            acl_metadata={"sender_id": agent.aid},
        )
        return agent

    await c.as_agent_process(agent_creator=agent_init)

    while main_agent.test_counter != 1:
        await asyncio.sleep(0.1)

    assert main_agent.test_counter == 1

    await c.shutdown()

@pytest.mark.asyncio
async def test_async_agent_processes_ping_pong_p_to_p():
    # GIVEN
    addr = ("127.0.0.2", 5826)
    aid_main_agent = "main_agent"
    c = await create_container(addr=addr, copy_internal_messages=False)
    main_agent = P2PMainAgent(c, suggested_aid=aid_main_agent)

    async def agent_creator(container):
        p2pta = P2PTestAgent(
            container, aid_main_agent, suggested_aid=f"process_agent1"
        )
        await p2pta.send_message(
            content="pong",
            receiver_addr=addr,
            receiver_id=aid_main_agent,
            acl_metadata={
                "sender_addr": p2pta.addr,
                "sender_id": p2pta.aid,
            }
        )

    await c.as_agent_process(
        agent_creator=agent_creator
    )
    

    # WHEN
    def agent_init(c):
        agent = MyAgent(c, suggested_aid=f"process_agent2")
        agent.schedule_instant_acl_message(
            "Message To Process Agent",
            receiver_addr=addr,
            receiver_id=f"process_agent1",
            acl_metadata={"sender_id": agent.aid},
        )
        return agent

    await c.as_agent_process(agent_creator=agent_init)

    while main_agent.test_counter != 2:
        await asyncio.sleep(0.1)

    assert main_agent.test_counter == 2

    await c.shutdown()


if __name__ == "__main__":
    asyncio.run(test_agent_processes_ping_pong(5, 5))
