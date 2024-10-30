import asyncio

import pytest

from mango import Agent, AgentAddress, activate, addr, create_tcp_container, sender_addr


class MyAgent(Agent):
    test_counter: int = 0
    current_task: object

    def handle_message(self, content, meta):
        self.test_counter += 1

        # get addr and id from sender
        if self.test_counter == 1:
            # send back pong, providing your own details
            self.current_task = self.schedule_instant_message(
                content="pong", receiver_addr=sender_addr(meta)
            )


class P2PMainAgent(Agent):
    test_counter: int = 0
    current_task: object

    def handle_message(self, content, meta):
        self.test_counter += 1


class P2PTestAgent(Agent):
    receiver_id: str

    def __init__(self, receiver_id):
        super().__init__()
        self.receiver_id = receiver_id

    def handle_message(self, content, meta):
        # send back pong, providing your own details
        self.current_task = self.schedule_instant_message(
            content="pong", receiver_addr=addr(meta["sender_addr"], self.receiver_id)
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
    c = create_tcp_container(addr=("127.0.0.1", 15589), copy_internal_messages=False)
    for i in range(num_sp):
        c.as_agent_process(
            agent_creator=lambda container: [
                container.register(MyAgent(), suggested_aid=f"process_agent{i},{j}")
                for j in range(num_sp_agents)
            ]
        )
    agent = c.register(MyAgent())

    # WHEN
    async with activate(c) as c:
        for i in range(num_sp):
            for j in range(num_sp_agents):
                await agent.send_message(
                    "Message To Process Agent",
                    receiver_addr=addr(c.addr, f"process_agent{i},{j}"),
                )
        while agent.test_counter != num_sp_agents * num_sp:
            await asyncio.sleep(0.1)

    assert agent.test_counter == num_sp_agents * num_sp


@pytest.mark.asyncio
async def test_agent_processes_ping_pong_p_to_p():
    # GIVEN
    addr = ("127.0.0.1", 5829)
    aid_main_agent = "main_agent"
    c = create_tcp_container(addr=addr, copy_internal_messages=False)
    await c.as_agent_process(
        agent_creator=lambda container: container.register(
            P2PTestAgent(aid_main_agent), suggested_aid="process_agent1"
        )
    )
    main_agent = c.register(P2PMainAgent(), suggested_aid=aid_main_agent)

    # WHEN
    def agent_init(c):
        agent = c.register(MyAgent(), suggested_aid="process_agent2")
        agent.schedule_instant_message(
            "Message To Process Agent",
            receiver_addr=AgentAddress(addr, "process_agent1"),
        )
        return agent

    async with activate(c) as c:
        await c.as_agent_process(agent_creator=agent_init)

        while main_agent.test_counter != 1:
            await asyncio.sleep(0.1)

    assert main_agent.test_counter == 1


@pytest.mark.asyncio
async def test_async_agent_processes_ping_pong_p_to_p():
    # GIVEN
    addr = ("127.0.0.1", 5811)
    aid_main_agent = "main_agent"
    c = create_tcp_container(addr=addr, copy_internal_messages=False)
    main_agent = c.register(P2PMainAgent(), suggested_aid=aid_main_agent)

    target_addr = main_agent.addr

    async def agent_creator(container):
        p2pta = container.register(
            P2PTestAgent(aid_main_agent), suggested_aid="process_agent1"
        )
        await p2pta.send_message(content="pong", receiver_addr=target_addr)

    async with activate(c) as c:
        await c.as_agent_process(agent_creator=agent_creator)

        # WHEN
        def agent_init(c):
            agent = c.register(MyAgent(), suggested_aid="process_agent2")
            agent.schedule_instant_message(
                "Message To Process Agent", AgentAddress(addr, "process_agent1")
            )
            return agent

        await c.as_agent_process(agent_creator=agent_init)

        while main_agent.test_counter != 2:
            await asyncio.sleep(0.1)

    assert main_agent.test_counter == 2


def test_sync_setup_agent_processes():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 15589), copy_internal_messages=False)
    c.as_agent_process(
        agent_creator=lambda container: [
            container.register(MyAgent(), suggested_aid="process_agent0")
        ]
    )
    agent = c.register(MyAgent())

if __name__ == "__main__":
    asyncio.run(test_agent_processes_ping_pong(5, 5))


