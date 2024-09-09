import asyncio

import pytest

from mango import Agent, create_container
from mango.util.clock import ExternalClock
from mango.util.distributed_clock import DistributedClockAgent, DistributedClockManager
from mango.util.termination_detection import tasks_complete_or_sleeping


class Caller(Agent):
    def __init__(
        self,
        container,
        receiver_addr,
        receiver_id,
        send_response_messages=False,
        max_count=100,
        schedule_timestamp=False,
    ):
        super().__init__(container)
        self.schedule_timestamp_task(
            coroutine=self.send_hello_world(receiver_addr, receiver_id),
            timestamp=self.current_timestamp + 5,
        )
        self.i = 0
        self.send_response_messages = send_response_messages
        self.max_count = max_count
        self.schedule_timestamp = schedule_timestamp
        self.done = asyncio.Future()

    async def send_hello_world(self, receiver_addr, receiver_id):
        await self.send_acl_message(
            receiver_addr=receiver_addr, receiver_id=receiver_id, content="Hello World"
        )

    async def send_ordered(self, meta):
        await self.send_acl_message(
            receiver_addr=meta["sender_addr"],
            receiver_id=meta["sender_id"],
            content=self.i,
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
    def __init__(self, container, receiver_addr=None, receiver_id=None):
        super().__init__(container)
        self.receiver_addr = receiver_addr
        self.receiver_id = receiver_id

    def handle_message(self, content, meta):
        self.schedule_instant_acl_message(
            receiver_addr=self.receiver_addr or self.addr,
            receiver_id=self.receiver_id,
            content=content,
            acl_metadata={"sender_id": self.aid},
        )


@pytest.mark.asyncio
async def test_termination_single_container():
    clock = ExternalClock(start_time=1000)

    c = await create_container(connection_type="external_connection", clock=clock)
    receiver = Receiver(c, receiver_id="agent1")
    caller = Caller(c, c.addr, receiver.aid, send_response_messages=True)
    if isinstance(clock, ExternalClock):
        await asyncio.sleep(0.1)
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
    assert caller.i == caller.max_count
    await c.shutdown()


async def distribute_ping_pong_test(connection_type, codec=None, max_count=100):
    init_addr = ("localhost", 1555) if connection_type == "tcp" else "c1"
    repl_addr = ("localhost", 1556) if connection_type == "tcp" else "c2"

    broker = ("localhost", 1883, 60)
    mqtt_kwargs_1 = {
        "client_id": "container_1",
        "broker_addr": broker,
        "transport": "tcp",
    }

    mqtt_kwargs_2 = {
        "client_id": "container_2",
        "broker_addr": broker,
        "transport": "tcp",
    }

    clock_man = ExternalClock(5)
    container_man = await create_container(
        connection_type=connection_type,
        codec=codec,
        addr=init_addr,
        mqtt_kwargs=mqtt_kwargs_1,
        clock=clock_man,
    )
    clock_ag = ExternalClock()
    container_ag = await create_container(
        connection_type=connection_type,
        codec=codec,
        addr=repl_addr,
        mqtt_kwargs=mqtt_kwargs_2,
        clock=clock_ag,
    )

    clock_agent = DistributedClockAgent(container_ag)
    clock_manager = DistributedClockManager(
        container_man, receiver_clock_addresses=[(repl_addr, "clock_agent")]
    )
    receiver = Receiver(container_ag, init_addr, "agent0")
    caller = Caller(
        container_man,
        repl_addr,
        receiver.aid,
        send_response_messages=True,
        max_count=max_count,
    )

    clock_man.set_time(clock_man.time + 5)

    # we do not have distributed termination detection yet in core
    assert caller.i < caller.max_count

    await caller.done
    assert caller.i == caller.max_count

    # finally shut down
    await asyncio.gather(
        container_man.shutdown(),
        container_ag.shutdown(),
    )


async def distribute_ping_pong_test_timestamp(
    connection_type, codec=None, max_count=10
):
    init_addr = ("localhost", 1555) if connection_type == "tcp" else "c1"
    repl_addr = ("localhost", 1556) if connection_type == "tcp" else "c2"

    broker = ("localhost", 1883, 60)
    mqtt_kwargs_1 = {
        "client_id": "container_1",
        "broker_addr": broker,
        "transport": "tcp",
    }

    mqtt_kwargs_2 = {
        "client_id": "container_2",
        "broker_addr": broker,
        "transport": "tcp",
    }

    clock_man = ExternalClock(5)
    container_man = await create_container(
        connection_type=connection_type,
        codec=codec,
        addr=init_addr,
        mqtt_kwargs=mqtt_kwargs_1,
        clock=clock_man,
    )
    clock_ag = ExternalClock()
    container_ag = await create_container(
        connection_type=connection_type,
        codec=codec,
        addr=repl_addr,
        mqtt_kwargs=mqtt_kwargs_2,
        clock=clock_ag,
    )

    clock_agent = DistributedClockAgent(container_ag)
    clock_manager = DistributedClockManager(
        container_man, receiver_clock_addresses=[(repl_addr, "clock_agent")]
    )
    receiver = Receiver(container_ag, init_addr, "agent0")
    caller = Caller(
        container_man,
        repl_addr,
        receiver.aid,
        send_response_messages=True,
        max_count=max_count,
        schedule_timestamp=True,
    )

    # we do not have distributed termination detection yet in core
    assert caller.i < caller.max_count

    import time

    tt = 0
    if isinstance(clock_man, ExternalClock):
        for i in range(caller.max_count):
            await tasks_complete_or_sleeping(container_man)
            t = time.time()
            await clock_manager.send_current_time()
            next_event = await clock_manager.get_next_event()
            tt += time.time() - t

            clock_man.set_time(next_event)

    await caller.done

    assert caller.i == caller.max_count

    # finally shut down
    await asyncio.gather(
        container_man.shutdown(),
        container_ag.shutdown(),
    )


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

    broker = ("localhost", 1883, 60)
    mqtt_kwargs_1 = {
        "client_id": "container_1",
        "broker_addr": broker,
        "transport": "tcp",
    }

    mqtt_kwargs_2 = {
        "client_id": "container_2",
        "broker_addr": broker,
        "transport": "tcp",
    }

    clock_man = ExternalClock(5)
    container_man = await create_container(
        connection_type=connection_type,
        codec=codec,
        addr=init_addr,
        mqtt_kwargs=mqtt_kwargs_1,
        clock=clock_man,
    )
    clock_ag = ExternalClock()
    container_ag = await create_container(
        connection_type=connection_type,
        codec=codec,
        addr=repl_addr,
        mqtt_kwargs=mqtt_kwargs_2,
        clock=clock_ag,
    )

    clock_agent = DistributedClockAgent(container_ag)
    clock_manager = DistributedClockManager(
        container_man, receiver_clock_addresses=[(repl_addr, "clock_agent")]
    )
    receiver = Receiver(container_ag, init_addr, "agent0")
    caller = Caller(container_man, repl_addr, receiver.aid)

    assert receiver._scheduler.clock.time == 0
    # first synchronize the clock to the receiver
    next_event = await clock_manager.distribute_time(clock_man.time)
    await tasks_complete_or_sleeping(container_man)
    # this is to early, as we did not wait a whole roundtrip
    assert receiver._scheduler.clock.time == 0
    # increase the time, triggering an action in the caller
    clock_man.set_time(10)
    # distribute the new time to the clock_manager
    next_event = await clock_manager.distribute_time()
    # wait until everything is done
    await tasks_complete_or_sleeping(container_man)
    # also wait for the result in the agent container
    next_event = await clock_manager.distribute_time()
    assert receiver._scheduler.clock.time == 10
    # now the response should be received
    await tasks_complete_or_sleeping(container_man)
    assert caller.i == 1, "received one message"
    clock_man.set_time(15)
    next_event = await clock_manager.distribute_time()
    await tasks_complete_or_sleeping(container_man)
    next_event = await clock_manager.distribute_time()
    # the clock_manager distributed the time to the other container
    assert clock_ag.time == 15
    clock_man.set_time(1000)
    next_event = await clock_manager.distribute_time()
    next_event = await clock_manager.distribute_time()
    # did work the second time too
    assert clock_ag.time == 1000

    # finally shut down
    await asyncio.gather(
        container_man.shutdown(),
        container_ag.shutdown(),
    )


async def send_current_time_test_case(connection_type, codec=None):
    init_addr = ("localhost", 1555) if connection_type == "tcp" else "c1"
    repl_addr = ("localhost", 1556) if connection_type == "tcp" else "c2"

    broker = ("localhost", 1883, 60)
    mqtt_kwargs_1 = {
        "client_id": "container_1",
        "broker_addr": broker,
        "transport": "tcp",
    }

    mqtt_kwargs_2 = {
        "client_id": "container_2",
        "broker_addr": broker,
        "transport": "tcp",
    }

    clock_man = ExternalClock(5)
    container_man = await create_container(
        connection_type=connection_type,
        codec=codec,
        addr=init_addr,
        mqtt_kwargs=mqtt_kwargs_1,
        clock=clock_man,
    )
    clock_ag = ExternalClock()
    container_ag = await create_container(
        connection_type=connection_type,
        codec=codec,
        addr=repl_addr,
        mqtt_kwargs=mqtt_kwargs_2,
        clock=clock_ag,
    )

    clock_agent = DistributedClockAgent(container_ag)
    clock_manager = DistributedClockManager(
        container_man, receiver_clock_addresses=[(repl_addr, "clock_agent")]
    )
    receiver = Receiver(container_ag, init_addr, "agent0")
    caller = Caller(container_man, repl_addr, receiver.aid)
    await tasks_complete_or_sleeping(container_man)

    assert receiver._scheduler.clock.time == 0
    # first synchronize the clock to the receiver
    await clock_manager.send_current_time()
    # just waiting until it is done is not enough
    await tasks_complete_or_sleeping(container_man)
    # as we return to soon and did not yet have set the time
    assert receiver._scheduler.clock.time == 0
    # increase the time, triggering an action in the caller
    clock_man.set_time(10)
    # distribute the new time to the clock_manager
    await clock_manager.send_current_time()
    # and wait until everything is done
    await tasks_complete_or_sleeping(container_man)
    # also wait for the result in the agent container
    next_event = await clock_manager.get_next_event()
    assert receiver._scheduler.clock.time == 10
    # now the response should be received
    assert caller.i == 1, "received one message"
    clock_man.set_time(15)
    await clock_manager.send_current_time()
    next_event = await clock_manager.get_next_event()
    # the clock_manager distributed the time to the other container
    assert clock_ag.time == 15
    clock_man.set_time(1000)
    await clock_manager.send_current_time()
    next_event = await clock_manager.get_next_event()
    # did work the second time too
    assert clock_ag.time == 1000

    # finally shut down
    await asyncio.gather(
        container_man.shutdown(),
        container_ag.shutdown(),
    )


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
