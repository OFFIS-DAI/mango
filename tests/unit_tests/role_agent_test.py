import asyncio
import datetime
from typing import Any, Dict

import pytest

from mango import activate, create_tcp_container, sender_addr
from mango.agent.role import Role, RoleAgent, RoleContext
from mango.util.scheduling import TimestampScheduledTask


class SimpleReactiveRole(Role):
    def setup(self):
        self.context.subscribe_message(
            self, self.react_handle_message, self.is_applicable
        )

    def react_handle_message(self, content, meta: Dict[str, Any]) -> None:
        pass

    def is_applicable(self, content, meta: Dict[str, Any]) -> bool:
        return True


class PongRole(SimpleReactiveRole):
    def __init__(self):
        super().__init__()
        self.sending_tasks = []

    def react_handle_message(self, content, meta: Dict[str, Any]):
        assert "sender_addr" in meta.keys() and "sender_id" in meta.keys()

        # send back pong, providing your own details
        t = self.context.schedule_instant_message(
            content="pong", receiver_addr=sender_addr(meta)
        )

        self.sending_tasks.append(t)

    def is_applicable(self, content, meta):
        return content == "ping"


class PingRole(SimpleReactiveRole):
    def __init__(self, target, expect_no_answer=False):
        self.open_ping_requests = {}
        self.target = target
        self._expect_no_answer = expect_no_answer

    def react_handle_message(self, content, meta: Dict[str, Any]):
        assert "sender_addr" in meta.keys() and "sender_id" in meta.keys()
        sender = sender_addr(meta)
        assert sender in self.open_ping_requests.keys()

        self.open_ping_requests[sender].set_result(True)

    def is_applicable(self, content, meta):
        return content == "pong"

    def on_ready(self):
        for task in list(
            map(
                lambda a: TimestampScheduledTask(
                    self.send_ping_to_other(a, self.context),
                    datetime.datetime.now().timestamp(),
                ),
                self.target,
            )
        ):
            self.context.schedule_task(task)

    async def send_ping_to_other(self, other_addr, agent_context: RoleContext):
        # create
        self.open_ping_requests[other_addr] = asyncio.Future()
        success = await agent_context.send_message(
            content="ping",
            receiver_addr=other_addr,
        )
        assert success

    async def on_stop(self):
        await self.wait_for_pong_replies()

    async def wait_for_pong_replies(self, timeout=1):
        for addr, fut in self.open_ping_requests.items():
            try:
                await asyncio.wait_for(fut, timeout=timeout)
                assert not self._expect_no_answer
            except asyncio.TimeoutError:
                print(
                    f"Timeout occurred while waiting for the ping response of {addr}, "
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
        c = create_tcp_container(addr=("127.0.0.2", 5555 + i))
        containers.append(c)

    # create agents
    agents = []
    addrs = []
    for i in range(num_agents):
        c = containers[i % num_containers]
        a = c.register(RoleAgent())
        a.add_role(PongRole())
        agents.append(a)
        addrs.append(a.addr)

    # all agents send ping request to all agents (including themselves)
    for a in agents:
        a.add_role(PingRole(addrs))

    async with activate(containers) as cl:
        for a in agents:
            if a._check_inbox_task.done():
                if a._check_inbox_task.exception() is not None:
                    raise a._check_inbox_task.exception()
                else:
                    assert False, "check_inbox terminated unexpectedly."

        for a in agents:
            await a.tasks_complete()

    assert len(asyncio.all_tasks()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("num_agents,num_containers", [(2, 1)])
async def test_send_ping_pong_deactivated_pong(num_agents, num_containers):
    # create containers
    containers = []
    for i in range(num_containers):
        c = create_tcp_container(addr=("127.0.0.2", 5555 + i))
        containers.append(c)

    # create agents
    agents = []
    addrs = []
    for i in range(num_agents):
        c = containers[i % num_containers]
        a = c.register(RoleAgent())
        a.add_role(PongRole())
        agents.append(a)
        addrs.append(a.addr)

    # add Ping Role and deactivate it immediately
    for a in agents:
        ping_role = PingRole(addrs, expect_no_answer=True)
        a.add_role(ping_role)
        a.add_role(DeactivateAllRoles([ping_role]))

    async with activate(containers) as cl:
        for a in agents:
            if a._check_inbox_task.done():
                if a._check_inbox_task.exception() is not None:
                    raise a._check_inbox_task.exception()
                else:
                    assert False, "check_inbox terminated unexpectedly."

        for a in agents:
            await a.tasks_complete()

    assert len(asyncio.all_tasks()) == 1


@pytest.mark.asyncio
async def test_role_add_remove():
    c = create_tcp_container(addr=("127.0.0.2", 5555))
    agent = c.register(RoleAgent())
    role = SampleRole()
    agent.add_role(role)

    assert agent.roles[0] == role

    agent.remove_role(role)

    assert len(agent.roles) == 0


@pytest.mark.asyncio
async def test_role_add_remove_context():
    c = create_tcp_container(addr=("127.0.0.2", 5555))
    agent = c.register(RoleAgent())
    role = SampleRole()
    agent.add_role(role)

    assert role.setup_called
    assert agent.roles[0] == role

    agent.remove_role(role)

    assert len(agent.roles) == 0
