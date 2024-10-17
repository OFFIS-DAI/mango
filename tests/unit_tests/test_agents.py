import asyncio
from typing import Any

import pytest

from mango import Agent, activate, create_tcp_container, sender_addr


class PingPongAgent(Agent):
    """
    Simple PingPongAgent that can send ping and receive pong messages
    """

    def __init__(self):
        super().__init__()
        self.open_ping_requests = {}
        self.sending_tasks = []

    def handle_message(self, content, meta: dict[str, Any]):
        # answer on ping
        if content == "ping":
            assert "sender_addr" in meta.keys() and "sender_id" in meta.keys()

            # send back pong, providing your own details
            t = self.schedule_instant_message(
                content="pong", receiver_addr=sender_addr(meta)
            )
            self.sending_tasks.append(t)

        elif content == "pong":
            assert "sender_addr" in meta.keys() and "sender_id" in meta.keys()
            # get host, port and id from sender
            assert sender_addr(meta) in self.open_ping_requests.keys()

            self.open_ping_requests[sender_addr(meta)].set_result(True)

    async def send_ping_to_other(self, other_addr):
        # create
        self.open_ping_requests[other_addr] = asyncio.Future()
        success = await self.send_message(content="ping", receiver_addr=other_addr)
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
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    a = c.register(PingPongAgent())

    async with activate(c) as c:
        assert a.aid is not None
        assert not a._check_inbox_task.done()
        assert not c._check_inbox_task.done()
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
        c = create_tcp_container(addr=("127.0.0.2", 5555 + i))
        containers.append(c)

    # create agents
    agents = []
    addrs = []
    for i in range(num_agents):
        c = containers[i % num_containers]
        a = c.register(PingPongAgent())
        agents.append(a)
        addrs.append(a.addr)

    async with activate(containers) as cl:
        # all agents send ping request to all agents (including themselves)
        for a in agents:
            for receiver_addr in addrs:
                await a.send_ping_to_other(receiver_addr)

        for a in agents:
            if a._check_inbox_task.done():
                if a._check_inbox_task.exception() is not None:
                    raise a._check_inbox_task.exception()
                else:
                    assert False, "check_inbox terminated unexpectedly."
        for a in agents:
            # await a.wait_for_sending_messages()
            await a.wait_for_pong_replies()

    assert len(asyncio.all_tasks()) == 1
