import asyncio

import pytest

from mango import Agent, create_container
from mango.messages.codecs import JSON
from mango.util.clock import ExternalClock
from mango.util.distributed_clock import DistributedClockAgent, DistributedClockManager
from mango.util.termination_detection import tasks_complete_or_sleeping

JSON_CODEC = JSON()


async def setup_and_run_test_case(connection_type, codec):
    comm_topic = "test_topic"
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

    clock_man = ExternalClock()
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

    # increasing the time
    clock_man.set_time(100)
    # first distribute the time - then wait for the agent to finish
    next_event = await clock_manager.distribute_time()
    # here no second distribute to wait for retrieval is needed
    # the clock_manager distributed the time to the other container
    assert clock_ag.time == 100
    clock_man.set_time(1000)
    next_event = await clock_manager.distribute_time()
    # here no second distribute to wait for retrieval is needed

    assert clock_ag.time == 1000
    clock_man.set_time(2000)
    # distribute the new time
    await clock_manager.distribute_time()
    # did work the second time too
    assert clock_ag.time == 2000

    await clock_manager.shutdown()
    await clock_agent.shutdown()

    # finally shut down
    await asyncio.gather(
        container_man.shutdown(),
        container_ag.shutdown(),
    )


@pytest.mark.asyncio
async def test_tcp_json():
    await setup_and_run_test_case("tcp", JSON_CODEC)


@pytest.mark.asyncio
@pytest.mark.mqtt
async def test_mqtt_json():
    await setup_and_run_test_case("mqtt", JSON_CODEC)
