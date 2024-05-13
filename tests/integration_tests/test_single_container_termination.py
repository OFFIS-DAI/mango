import asyncio

import pytest

from mango import Agent, create_container
from mango.messages.codecs import JSON
from mango.util.clock import ExternalClock
from mango.util.distributed_clock import DistributedClockAgent, DistributedClockManager
from mango.util.termination_detection import tasks_complete_or_sleeping


class Caller(Agent):
    def __init__(self, container, receiver_addr, receiver_id):
        super().__init__(container)
        self.schedule_timestamp_task(
            coroutine=self.send_hello_world(receiver_addr, receiver_id),
            timestamp=self.current_timestamp + 5,
        )
        self.i = 0

    async def send_hello_world(self, receiver_addr, receiver_id):
        await self.send_acl_message(
            receiver_addr=receiver_addr, receiver_id=receiver_id, content="Hello World"
        )

    def handle_message(self, content, meta):
        print(f"{self.aid} Received a message with the following content {content}.")
        self.i += 1
        if self.i < 100:
            self.schedule_instant_acl_message(
                receiver_addr=self.addr, receiver_id="agent0", content=self.i
            )


class Receiver(Agent):
    def __init__(self, container):
        super().__init__(container)

    def handle_message(self, content, meta):
        print(f"{self.aid} Received a message with the following content {content}.")
        self.schedule_instant_acl_message(
            receiver_addr=self.addr, receiver_id="agent1", content=content
        )


@pytest.mark.asyncio
async def test_termination_single_container():
    clock = ExternalClock(start_time=1000)
    addr = ("127.0.0.1", 5555)

    c = await create_container(addr=addr, clock=clock)
    receiver = Receiver(c)
    caller = Caller(c, addr, receiver.aid)
    if isinstance(clock, ExternalClock):
        await asyncio.sleep(1)
        clock.set_time(clock.time + 5)

    # wait until each agent is done with all tasks at some point
    await receiver._scheduler.tasks_complete_or_sleeping()
    await caller._scheduler.tasks_complete_or_sleeping()
    # this does not end far too early
    assert caller.i < 30

    # checking tasks completed for each agent does not help here, as they are sleeping alternating
    # the following container-wide function catches this behavior in a single container
    # to solve this situation for multiple containers a distributed termination detection
    # is needed
    await tasks_complete_or_sleeping(c)
    assert caller.i == 100
    await c.shutdown()
