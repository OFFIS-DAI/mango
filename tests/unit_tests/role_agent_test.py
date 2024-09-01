import asyncio
import datetime
from abc import abstractmethod
from typing import Any, Dict

import pytest

import mango.container.factory as container_factory
from mango.agent.role import Role, RoleAgent, RoleContext
from mango.util.scheduling import TimestampScheduledTask


class SimpleReactiveRole(Role):
    def setup(self):
        self.context.subscribe_message(self, self.handle_message, self.is_applicable)

    @abstractmethod
    def handle_message(self, content, meta: Dict[str, Any]) -> None:
        pass

    def is_applicable(self, content, meta: Dict[str, Any]) -> bool:
        return True


class PongRole(SimpleReactiveRole):
    def __init__(self):
        super().__init__()
        self.sending_tasks = []

    def handle_message(self, content, meta: Dict[str, Any]):
        assert "sender_addr" in meta.keys() and "sender_id" in meta.keys()

        # get addr and id from sender
        receiver_host, receiver_port = meta["sender_addr"]
        receiver_id = meta["sender_id"]
        # send back pong, providing your own details
        t = self.context.schedule_instant_acl_message(
            content="pong",
            receiver_addr=(receiver_host, receiver_port),
            receiver_id=receiver_id,
            acl_metadata={
                "sender_addr": self.context.addr,
                "sender_id": self.context.aid,
            },
        )

        self.sending_tasks.append(t)

    def is_applicable(self, content, meta):
        return content == "ping"


class PingRole(SimpleReactiveRole):
    def __init__(self, addr, expect_no_answer=False):
        self.open_ping_requests = {}
        self._addr = addr
        self._expect_no_answer = expect_no_answer

    def handle_message(self, content, meta: Dict[str, Any]):
        assert "sender_addr" in meta.keys() and "sender_id" in meta.keys()
        # get host, port and id from sender
        sender_host, sender_port = meta["sender_addr"]
        sender_id = meta["sender_id"]
        assert ((sender_host, sender_port), sender_id) in self.open_ping_requests.keys()

        self.open_ping_requests[((sender_host, sender_port), sender_id)].set_result(
            True
        )

    def is_applicable(self, content, meta):
        return content == "pong"

    def setup(self):
        super().setup()
        for task in list(
            map(
                lambda a: TimestampScheduledTask(
                    self.send_ping_to_other(a[0], a[1], self.context),
                    datetime.datetime.now().timestamp(),
                ),
                self._addr,
            )
        ):
            self.context.schedule_task(task)

    async def send_ping_to_other(
        self, other_addr, other_id, agent_context: RoleContext
    ):
        # create
        self.open_ping_requests[(other_addr, other_id)] = asyncio.Future()
        success = await agent_context.send_acl_message(
            content="ping",
            receiver_addr=other_addr,
            receiver_id=other_id,
            acl_metadata={
                "sender_addr": agent_context.addr,
                "sender_id": agent_context.aid,
            },
        )
        assert success

    async def on_stop(self):
        await self.wait_for_pong_replies()

    async def wait_for_pong_replies(self, timeout=1):
        for addr_tuple, fut in self.open_ping_requests.items():
            try:
                await asyncio.wait_for(fut, timeout=timeout)
                assert not self._expect_no_answer
            except asyncio.TimeoutError:
                print(
                    f"Timeout occurred while waiting for the ping response of {addr_tuple}, "
                    "going to check if all messages could be send"
                )
                assert (
                    self._expect_no_answer
                ), "Not all pong replies have arrived on time"


class DeactivateAllRoles(Role):
    def __init__(self, roles):
        self.roles = roles

    def setup(self):
        super().setup()
        for r in self.roles:
            self.context.deactivate(r)


class SampleRole(Role):
    def __init__(self):
        self.setup_called = False

    def setup(self):
        assert self.context is not None
        self.setup_called = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "num_agents,num_containers", [(1, 1), (2, 1), (2, 2), (10, 2), (10, 10)]
)
async def test_send_ping_pong(num_agents, num_containers):
    # create containers
    containers = []
    for i in range(num_containers):
        c = await container_factory.create(addr=("127.0.0.2", 5555 + i))
        containers.append(c)

    # create agents
    agents = []
    addrs = []
    for i in range(num_agents):
        c = containers[i % num_containers]
        a = RoleAgent(c)
        a.add_role(PongRole())
        agents.append(a)
        addrs.append((c.addr, a.aid))

    # all agents send ping request to all agents (including themselves)
    for a in agents:
        a.add_role(PingRole(addrs))

    for a in agents:
        if a._check_inbox_task.done():
            if a._check_inbox_task.exception() is not None:
                raise a._check_inbox_task.exception()
            else:
                assert False, f"check_inbox terminated unexpectedly."

    for a in agents:
        await a.tasks_complete()

    # gracefully shutdown
    for a in agents:
        await a.shutdown()
    await asyncio.gather(*[c.shutdown() for c in containers])

    assert len(asyncio.all_tasks()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("num_agents,num_containers", [(2, 1)])
async def test_send_ping_pong_deactivated_pong(num_agents, num_containers):
    # create containers
    containers = []
    for i in range(num_containers):
        c = await container_factory.create(addr=("127.0.0.2", 5555 + i))
        containers.append(c)

    # create agents
    agents = []
    addrs = []
    for i in range(num_agents):
        c = containers[i % num_containers]
        a = RoleAgent(c)
        a.add_role(PongRole())
        agents.append(a)
        addrs.append((c.addr, a.aid))

    # add Ping Role and deactivate it immediately
    for a in agents:
        ping_role = PingRole(addrs, expect_no_answer=True)
        a.add_role(ping_role)
        a.add_role(DeactivateAllRoles([ping_role]))

    for a in agents:
        if a._check_inbox_task.done():
            if a._check_inbox_task.exception() is not None:
                raise a._check_inbox_task.exception()
            else:
                assert False, f"check_inbox terminated unexpectedly."

    for a in agents:
        await a.tasks_complete()

    # gracefully shutdown
    for a in agents:
        await a.shutdown()
    await asyncio.gather(*[c.shutdown() for c in containers])

    assert len(asyncio.all_tasks()) == 1


@pytest.mark.asyncio
async def test_role_add_remove():
    c = await container_factory.create(addr=("127.0.0.2", 5555))
    agent = RoleAgent(c)
    role = SampleRole()
    agent.add_role(role)

    assert agent._role_handler.roles[0] == role

    agent.remove_role(role)

    assert len(agent._role_handler.roles) == 0
    await c.shutdown()


@pytest.mark.asyncio
async def test_role_add_remove_context():
    c = await container_factory.create(addr=("127.0.0.2", 5555))
    agent = RoleAgent(c)
    role = SampleRole()
    agent._role_context.add_role(role)

    assert role.setup_called
    assert agent._role_handler.roles[0] == role

    agent._role_context.remove_role(role)

    assert len(agent._role_handler.roles) == 0
    await c.shutdown()
