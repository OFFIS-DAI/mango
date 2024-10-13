import pytest

from mango import activate, addr
from mango.messages.codecs import JSON
from mango.util.distributed_clock import DistributedClockAgent, DistributedClockManager

from . import create_test_container

JSON_CODEC = JSON()


async def setup_and_run_test_case(connection_type, codec):
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

    async with activate(container_man, container_ag) as cl:
        # increasing the time
        container_man.clock.set_time(100)
        # first distribute the time - then wait for the agent to finish
        next_event = await clock_manager.distribute_time()
        # here no second distribute to wait for retrieval is needed
        # the clock_manager distributed the time to the other container
        assert container_ag.clock.time == 100
        container_man.clock.set_time(1000)
        next_event = await clock_manager.distribute_time()
        # here no second distribute to wait for retrieval is needed

        assert container_ag.clock.time == 1000
        container_man.clock.set_time(2000)
        # distribute the new time
        await clock_manager.distribute_time()
        # did work the second time too
        assert container_ag.clock.time == 2000


@pytest.mark.asyncio
async def test_tcp_json():
    await setup_and_run_test_case("tcp", JSON_CODEC)


@pytest.mark.asyncio
@pytest.mark.mqtt
async def test_mqtt_json():
    await setup_and_run_test_case("mqtt", JSON_CODEC)
