import asyncio

import pytest

from mango import Agent, activate, addr, create_ec_container, sender_addr
from mango.util.clock import ExternalClock
from mango.util.distributed_clock import DistributedClockAgent, DistributedClockManager
from mango.util.termination_detection import tasks_complete_or_sleeping

from . import create_test_container


class Caller(Agent):
    def __init__(
        self,
        receiver_addr,
        send_response_messages=False,
        max_count=100,
        schedule_timestamp=False,
    ):
        super().__init__()
        self.i = 0
        self.send_response_messages = send_response_messages
        self.max_count = max_count
        self.schedule_timestamp = schedule_timestamp
        self.done = asyncio.Future()
        self.target = receiver_addr

    def on_ready(self):
        self.schedule_timestamp_task(
            coroutine=self.send_hello_world(self.target),
            timestamp=self.current_timestamp + 5,
        )

    async def send_hello_world(self, receiver_addr):
        await self.send_message(receiver_addr=receiver_addr, content="Hello World")

    async def send_ordered(self, meta):
        await self.send_message(
            content=self.i,
            receiver_addr=sender_addr(meta),
        )

    def handle_message(self, content, meta):
        self.i += 1
        if self.i < self.max_count and self.send_response_messages:
            if self.schedule_timestamp:
                self.schedule_timestamp_task(
                    self.send_ordered(meta), self.current_timestamp + 5
                )
            else:
                self.schedule_instant_task(self.send_ordered(meta))
        elif not self.done.done():
            self.done.set_result(True)


class Receiver(Agent):
    def handle_message(self, content, meta):
        self.schedule_instant_message(
            receiver_addr=sender_addr(meta),
            content=content,
        )


@pytest.mark.asyncio
async def test_termination_single_container():
    clock = ExternalClock(start_time=1000)

    c = create_ec_container(clock=clock)
    receiver = c.include(Receiver())
    caller = c.include(Caller(receiver.addr, send_response_messages=True))

    async with activate(c) as c:
        await asyncio.sleep(0.1)
        clock.set_time(clock.time + 5)
        # wait until each agent is done with all tasks at some point
        await receiver.scheduler.tasks_complete_or_sleeping()
        await caller.scheduler.tasks_complete_or_sleeping()

        # this does not end far too early
        assert caller.i < 30

        # checking tasks completed for each agent does not help here, as they are sleeping alternating
        # the following container-wide function catches this behavior in a single container
        # to solve this situation for multiple containers a distributed termination detection
        # is needed
        await tasks_complete_or_sleeping(c)

    assert caller.i == caller.max_count


async def distribute_ping_pong_test(connection_type, codec=None, max_count=100):
    init_addr = ("localhost", 1555) if connection_type == "tcp" else "c1"
    repl_addr = ("localhost", 1556) if connection_type == "tcp" else "c2"

    container_man, container_ag = create_test_container(
        connection_type, init_addr, repl_addr, codec
    )

    clock_agent = container_ag.include(DistributedClockAgent())
    clock_manager = container_man.include(
        DistributedClockManager(
            receiver_clock_addresses=[addr(repl_addr, clock_agent.aid)]
        )
    )
    receiver = container_ag.include(Receiver())
    caller = container_man.include(
        Caller(
            addr(repl_addr, receiver.aid),
            send_response_messages=True,
            max_count=max_count,
        )
    )

    async with activate(container_man, container_ag) as c:
        container_man.clock.set_time(container_man.clock.time + 5)

        # we do not have distributed termination detection yet in core
        assert caller.i < caller.max_count
        await caller.done

    assert caller.i == caller.max_count


async def distribute_ping_pong_test_timestamp(
    connection_type, codec=None, max_count=10
):
    init_addr = ("localhost", 1555) if connection_type == "tcp" else "c1"
    repl_addr = ("localhost", 1556) if connection_type == "tcp" else "c2"

    container_man, container_ag = create_test_container(
        connection_type, init_addr, repl_addr, codec
    )

    clock_agent = container_ag.include(DistributedClockAgent())
    clock_manager = container_man.include(
        DistributedClockManager(
            receiver_clock_addresses=[addr(repl_addr, clock_agent.aid)]
        )
    )
    receiver = container_ag.include(Receiver())
    caller = container_man.include(
        Caller(
            addr(repl_addr, receiver.aid),
            send_response_messages=True,
            max_count=max_count,
            schedule_timestamp=True,
        )
    )

    # we do not have distributed termination detection yet in core
    async with activate(container_man, container_ag) as cl:
        assert caller.i < caller.max_count

        import time

        tt = 0
        if isinstance(container_man.clock, ExternalClock):
            for i in range(caller.max_count):
                await tasks_complete_or_sleeping(container_man)
                t = time.time()
                await clock_manager.send_current_time()
                next_event = await clock_manager.get_next_event()
                tt += time.time() - t

                container_man.clock.set_time(next_event)

        await caller.done

    assert caller.i == caller.max_count


@pytest.mark.asyncio
async def test_distribute_ping_pong_tcp():
    await distribute_ping_pong_test("tcp")


@pytest.mark.asyncio
async def test_distribute_ping_pong_mqtt():
    await distribute_ping_pong_test("mqtt")


@pytest.mark.asyncio
async def test_distribute_ping_pong_ts_tcp():
    await distribute_ping_pong_test_timestamp("tcp")


@pytest.mark.asyncio
async def test_distribute_ping_pong_ts_mqtt():
    await distribute_ping_pong_test_timestamp("mqtt")


async def distribute_time_test_case(connection_type, codec=None):
    init_addr = ("localhost", 1555) if connection_type == "tcp" else "c1"
    repl_addr = ("localhost", 1556) if connection_type == "tcp" else "c2"

    container_man, container_ag = create_test_container(
        connection_type, init_addr, repl_addr, codec
    )

    clock_agent = container_ag.include(DistributedClockAgent())
    clock_manager = container_man.include(
        DistributedClockManager(
            receiver_clock_addresses=[addr(repl_addr, clock_agent.aid)]
        )
    )
    receiver = container_ag.include(Receiver())
    caller = container_ag.include(Caller(addr(repl_addr, receiver.aid)))

    async with activate(container_man, container_ag) as cl:
        assert receiver.scheduler.clock.time == 0
        # first synchronize the clock to the receiver
        next_event = await clock_manager.distribute_time(container_man.clock.time)
        await tasks_complete_or_sleeping(container_man)
        # this is to early, as we did not wait a whole roundtrip
        assert receiver.scheduler.clock.time == 0
        # increase the time, triggering an action in the caller
        container_man.clock.set_time(10)
        # distribute the new time to the clock_manager
        next_event = await clock_manager.distribute_time()
        # wait until everything is done
        await tasks_complete_or_sleeping(container_man)
        # also wait for the result in the agent container
        next_event = await clock_manager.distribute_time()
        assert receiver.scheduler.clock.time == 10
        # now the response should be received
        await tasks_complete_or_sleeping(container_man)
        assert caller.i == 1, "received one message"
        container_man.clock.set_time(15)
        next_event = await clock_manager.distribute_time()
        await tasks_complete_or_sleeping(container_man)
        next_event = await clock_manager.distribute_time()
        # the clock_manager distributed the time to the other container
        assert container_ag.clock.time == 15
        container_man.clock.set_time(1000)
        next_event = await clock_manager.distribute_time()
        next_event = await clock_manager.distribute_time()
        # did work the second time too
        assert container_ag.clock.time == 1000


async def send_current_time_test_case(connection_type, codec=None):
    init_addr = ("localhost", 1555) if connection_type == "tcp" else "c1"
    repl_addr = ("localhost", 1556) if connection_type == "tcp" else "c2"

    container_man, container_ag = create_test_container(
        connection_type, init_addr, repl_addr, codec
    )

    clock_agent = container_ag.include(DistributedClockAgent())
    clock_manager = container_man.include(
        DistributedClockManager(
            receiver_clock_addresses=[addr(repl_addr, clock_agent.aid)]
        )
    )
    receiver = container_ag.include(Receiver())
    caller = container_ag.include(Caller(addr(repl_addr, receiver.aid)))

    async with activate(container_man, container_ag) as cl:
        await tasks_complete_or_sleeping(container_man)

        assert receiver.scheduler.clock.time == 0
        # first synchronize the clock to the receiver
        await clock_manager.send_current_time()
        # just waiting until it is done is not enough
        await tasks_complete_or_sleeping(container_man)
        # as we return to soon and did not yet have set the time
        assert receiver.scheduler.clock.time == 0
        # increase the time, triggering an action in the caller
        container_man.clock.set_time(10)
        # distribute the new time to the clock_manager
        await clock_manager.send_current_time()
        # and wait until everything is done
        await tasks_complete_or_sleeping(container_man)
        # also wait for the result in the agent container
        next_event = await clock_manager.get_next_event()
        assert receiver.scheduler.clock.time == 10
        # now the response should be received
        assert caller.i == 1, "received one message"
        container_man.clock.set_time(15)
        await clock_manager.send_current_time()
        next_event = await clock_manager.get_next_event()
        # the clock_manager distributed the time to the other container
        assert container_ag.clock.time == 15
        container_man.clock.set_time(1000)
        await clock_manager.send_current_time()
        next_event = await clock_manager.get_next_event()
        # did work the second time too
        assert container_ag.clock.time == 1000


@pytest.mark.asyncio
async def test_distribute_time_tcp():
    await distribute_time_test_case("tcp")


@pytest.mark.asyncio
async def test_distribute_time_mqtt():
    await distribute_time_test_case("mqtt")


@pytest.mark.asyncio
async def test_send_current_time_tcp():
    await send_current_time_test_case("tcp")


@pytest.mark.asyncio
async def test_send_current_time_mqtt():
    await send_current_time_test_case("mqtt")
