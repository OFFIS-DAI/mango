import asyncio
from typing import Any, Dict

import pytest

import mango.container.factory as container_factory
from mango.agent.core import Agent


class PingPongAgent(Agent):
    """
    Simple PingPongAgent that can send ping and receive pong messages
    """

    def __init__(self, container):
        super().__init__(container)
        self.open_ping_requests = {}
        self.sending_tasks = []

    def handle_message(self, content, meta: Dict[str, Any]):
        # answer on ping
        if content == "ping":
            assert "sender_addr" in meta.keys() and "sender_id" in meta.keys()

            # get addr and id from sender
            receiver_host, receiver_port = meta["sender_addr"]
            receiver_id = meta["sender_id"]
            # send back pong, providing your own details
            t = self.schedule_instant_acl_message(
                content="pong",
                receiver_addr=(receiver_host, receiver_port),
                receiver_id=receiver_id,
                acl_metadata={"sender_addr": self.addr, "sender_id": self.aid},
            )
            self.sending_tasks.append(t)

        elif content == "pong":
            assert "sender_addr" in meta.keys() and "sender_id" in meta.keys()
            # get host, port and id from sender
            sender_host, sender_port = meta["sender_addr"]
            sender_id = meta["sender_id"]
            assert (
                (sender_host, sender_port),
                sender_id,
            ) in self.open_ping_requests.keys()

            self.open_ping_requests[((sender_host, sender_port), sender_id)].set_result(
                True
            )

    async def send_ping_to_other(self, other_addr, other_id):
        # create
        self.open_ping_requests[(other_addr, other_id)] = asyncio.Future()
        success = await self.send_acl_message(
            content="ping",
            receiver_addr=other_addr,
            receiver_id=other_id,
            acl_metadata={"sender_addr": self.addr, "sender_id": self.aid},
        )
        assert success

    async def wait_for_sending_messages(self, timeout=1):
        for t in self.sending_tasks:
            if not t.done():
                try:
                    await asyncio.wait_for(t, timeout=timeout)
                except asyncio.TimeoutError:
                    assert False, "Timeout occurred while waiting for sending a message"

            assert (
                t.exception() is None and t.result()
            ), "Sending of at least one message failed"

    async def wait_for_pong_replies(self, timeout=1):
        for addr_tuple, fut in self.open_ping_requests.items():
            try:
                await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError:
                print(
                    f"Timeout occurred while waiting for the ping response of {addr_tuple}, "
                    "going to check if all messages could be send"
                )
                await self.wait_for_sending_messages()
                assert False, "Not all pong replies have arrived on time"


@pytest.mark.asyncio
async def test_init_and_shutdown():
    c = await container_factory.create(addr=("127.0.0.1", 5555))
    a = PingPongAgent(c)
    assert a.aid is not None
    assert not a._check_inbox_task.done()
    assert not c._check_inbox_task.done()
    await a.shutdown()
    await c.shutdown()
    assert a._stopped
    assert not c.running
    assert len(asyncio.all_tasks()) == 1


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
        a = PingPongAgent(c)
        agents.append(a)
        addrs.append((c.addr, a.aid))

    # all agents send ping request to all agents (including themselves)
    for a in agents:
        for receiver_addr, receiver_id in addrs:
            await a.send_ping_to_other(other_addr=receiver_addr, other_id=receiver_id)

    for a in agents:
        if a._check_inbox_task.done():
            if a._check_inbox_task.exception() is not None:
                raise a._check_inbox_task.exception()
            else:
                assert False, f"check_inbox terminated unexpectedly."
    for a in agents:
        # await a.wait_for_sending_messages()
        await a.wait_for_pong_replies()

    # gracefully shutdown
    for a in agents:
        await a.shutdown()
    await asyncio.gather(*[c.shutdown() for c in containers])

    assert len(asyncio.all_tasks()) == 1
