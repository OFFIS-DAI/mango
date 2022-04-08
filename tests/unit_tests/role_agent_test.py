import datetime
import pytest
import asyncio
from typing import Dict, Any
from mango.core.container import Container
from mango.role.api import Role, RoleContext, SimpleReactiveRole
from mango.role.core import RoleAgent, RoleAgentContext
from mango.util.scheduling import DateTimeScheduledTask

class PongRole(SimpleReactiveRole):
    def __init__(self):
        self.sending_tasks = []

    def handle_msg(self, content, meta: Dict[str, Any]):
        assert 'sender_addr' in meta.keys() and 'sender_id' in meta.keys()

        # get addr and id from sender
        receiver_host, receiver_port = meta['sender_addr']
        receiver_id = meta['sender_id']
        # send back pong, providing your own details
        t = asyncio.create_task(self.context.send_message(
            content='pong', receiver_addr=(receiver_host, receiver_port), receiver_id=receiver_id,
            acl_metadata={'sender_addr': self.context.addr,
                            'sender_id': self.context.aid},
            create_acl=True)
        )        
        print("PING RECEIVE")

        self.sending_tasks.append(t)

    def is_applicable(self, content, meta):
        return content == 'ping'

class PingRole(SimpleReactiveRole):
    def __init__(self, addr, expect_no_answer=False):
        self.open_ping_requests = {}
        self._addr = addr
        self._expect_no_answer = expect_no_answer

    def handle_msg(self, content, meta: Dict[str, Any]):
        assert 'sender_addr' in meta.keys() and 'sender_id' in meta.keys()
        # get host, port and id from sender
        sender_host, sender_port = meta['sender_addr']
        sender_id = meta['sender_id']
        assert ((sender_host, sender_port), sender_id) in self.open_ping_requests.keys()

        print("PONG RECEIVE")

        self.open_ping_requests[((sender_host, sender_port), sender_id)].set_result(True)

    def is_applicable(self, content, meta):
        return content == 'pong'

    def setup(self):
        super().setup()
        for task in list(map(lambda a: DateTimeScheduledTask(self.send_ping_to_other(a[0], a[1], self.context), datetime.datetime.now()), self._addr)):
            self.context.schedule_task(task)

    async def send_ping_to_other(self, other_addr, other_id, agent_context : RoleContext):
        # create
        self.open_ping_requests[(other_addr, other_id)] = asyncio.Future()
        success = await agent_context.send_message(
            content='ping', receiver_addr=other_addr, receiver_id=other_id,
            acl_metadata={'sender_addr': agent_context.addr, 'sender_id': agent_context.aid},
            create_acl=True)
        assert success

    async def on_stop(self):
        print("STOP")
        await self.wait_for_pong_replies()

    async def wait_for_pong_replies(self, timeout=1):
        for addr_tuple, fut in self.open_ping_requests.items():
            try:
                await asyncio.wait_for(fut, timeout=timeout)
                assert not self._expect_no_answer
            except asyncio.TimeoutError:
                print('Timeout occurred while waiting for the ping response of %s, '
                      'going to check if all messages could be send' % str(addr_tuple))
                assert self._expect_no_answer, 'Not all pong replies have arrived on time'
    
class DeactivateAllRoles(Role):
    def __init__(self, roles):
        self.roles = roles

    def setup(self):
        super().setup()
        for r in self.roles:
            self.context.deactivate(r)


@pytest.mark.asyncio
@pytest.mark.parametrize("num_agents,num_containers",
                         [(1, 1), (2, 1), (2, 2), (10, 2), (10, 10)])
async def test_send_ping_pong(num_agents, num_containers):
    # create containers
    containers = []
    for i in range(num_containers):
        c = await Container.factory(addr=('127.0.0.2', 5555 + i))
        containers.append(c)

    # create agents
    agents = []
    addrs = []
    for i in range(num_agents):
        c = containers[i % num_containers]
        a = RoleAgent(c)
        a.add_role(PongRole())
        agents.append(a)
        addrs.append((c.addr, a._aid))

    # all agents send ping request to all agents (including themselves)
    for a in agents:
        a.add_role(PingRole(addrs))

    for a in agents:
        if a._check_inbox_task.done():
            if a._check_inbox_task.exception() is not None:
                raise a._check_inbox_task.exception()
            else:
                assert False, f'check_inbox terminated unexpectedly.'
    
    for a in agents:
        await a.tasks_complete()

    # gracefully shutdown
    for a in agents:
        await a.shutdown()
    for c in containers:
        await c.shutdown()

    assert len(asyncio.all_tasks()) == 1

@pytest.mark.asyncio
@pytest.mark.parametrize("num_agents,num_containers",
                         [(2, 1)])
async def test_send_ping_pong_deactivated_pong(num_agents, num_containers):
    # create containers
    containers = []
    for i in range(num_containers):
        c = await Container.factory(addr=('127.0.0.2', 5555 + i))
        containers.append(c)

    # create agents
    agents = []
    addrs = []
    for i in range(num_agents):
        c = containers[i % num_containers]
        a = RoleAgent(c)
        a.add_role(PongRole())
        agents.append(a)
        addrs.append((c.addr, a._aid))

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
                assert False, f'check_inbox terminated unexpectedly.'
    
    for a in agents:
        await a.tasks_complete()

    # gracefully shutdown
    for a in agents:
        await a.shutdown()
    for c in containers:
        await c.shutdown()

    assert len(asyncio.all_tasks()) == 1