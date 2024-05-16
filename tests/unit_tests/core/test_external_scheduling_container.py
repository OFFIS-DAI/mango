import asyncio
from typing import Any, Dict

import pytest

import mango.container.factory as container_factory
from mango.agent.core import Agent
from mango.container.factory import EXTERNAL_CONNECTION
from mango.container.external_coupling import ExternalAgentMessage
from mango.messages.message import ACLMessage
from mango.util.clock import ExternalClock


@pytest.mark.asyncio
async def test_init():
    external_scheduling_container = await container_factory.create(
        addr="external_eid_1234", connection_type=EXTERNAL_CONNECTION
    )
    assert external_scheduling_container.addr == "external_eid_1234"
    assert isinstance(external_scheduling_container.clock, ExternalClock)
    await external_scheduling_container.shutdown()


@pytest.mark.asyncio
async def test_send_msg():
    external_scheduling_container = await container_factory.create(
        addr="external_eid_1234", connection_type=EXTERNAL_CONNECTION
    )
    await external_scheduling_container.send_acl_message(
        content="test", receiver_addr="eid321", receiver_id="Agent0"
    )
    assert len(external_scheduling_container.message_buffer) == 1
    external_agent_msg: ExternalAgentMessage = external_scheduling_container.message_buffer[0]
    assert external_agent_msg.receiver == "eid321"
    decoded_msg = external_scheduling_container.codec.decode(external_agent_msg.message)
    assert decoded_msg.content == "test"
    assert decoded_msg.receiver_addr == "eid321"
    assert decoded_msg.receiver_id == "Agent0"
    await external_scheduling_container.shutdown()


@pytest.mark.asyncio
async def test_step():
    external_scheduling_container = await container_factory.create(
        addr="external_eid_1234", connection_type=EXTERNAL_CONNECTION
    )
    await external_scheduling_container.send_acl_message(
        content="test", receiver_addr="eid321", receiver_id="Agent0"
    )
    step_output = await external_scheduling_container.step(simulation_time=12, incoming_messages=[])
    assert external_scheduling_container.message_buffer == []
    assert external_scheduling_container.clock.time == 12
    assert 0 < step_output.duration < 0.01
    assert len(step_output.messages) == 1
    external_msg = step_output.messages[0]
    assert 0 < external_msg.time < 0.01
    assert external_msg.receiver == "eid321"
    decoded_msg = external_scheduling_container.codec.decode(external_msg.message)
    assert decoded_msg.content == "test"
    assert decoded_msg.receiver_addr == "eid321"
    assert decoded_msg.receiver_id == "Agent0"
    await external_scheduling_container.shutdown()


class ReplyAgent(Agent):
    def __init__(self, container):
        super().__init__(container)
        self.current_ping = 0
        self.tasks = []
        self.tasks.append(self.schedule_periodic_task(self.send_ping, delay=10))

    async def send_ping(self):
        await self.send_acl_message(
            content=f"ping{self.current_ping}",
            receiver_addr="ping_receiver_addr",
            receiver_id="ping_receiver_id",
        )
        self.current_ping += 1

    def handle_message(self, content, meta: Dict[str, Any]):
        self.schedule_instant_task(self.sleep_and_answer(content, meta))

    async def sleep_and_answer(self, content, meta):
        await self.send_acl_message(
            content=f"I received {content}",
            receiver_addr=meta["sender_addr"],
            receiver_id=["sender_id"],
        )
        await asyncio.sleep(0.1)
        await self.send_acl_message(
            content=f"Thanks for sending {content}",
            receiver_addr=meta["sender_addr"],
            receiver_id=["sender_id"],
        )

    async def stop_tasks(self):
        for task in self.tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class WaitForMessageAgent(Agent):
    def __init__(self, container):
        super().__init__(container)

        self.received_msg = False
        self.schedule_conditional_task(
            condition_func=lambda: self.received_msg,
            coroutine=self.print_cond_task_finished(),
            lookup_delay=1,
        )

    async def print_cond_task_finished(self):
        pass

    def handle_message(self, content, meta: Dict[str, Any]):
        self.received_msg = True


@pytest.mark.asyncio
async def test_step_with_cond_task():
    external_scheduling_container = await container_factory.create(
        addr="external_eid_1", connection_type=EXTERNAL_CONNECTION
    )
    agent_1 = WaitForMessageAgent(external_scheduling_container)
    print("Agent init")

    current_time = 0

    for _ in range(10):
        current_time += 1
        # advance time without anything happening
        print("starting step")
        return_values = await asyncio.wait_for(
            external_scheduling_container.step(simulation_time=current_time, incoming_messages=[]),
            timeout=1,
        )

        print("One step done")
        assert (
            return_values.next_activity == current_time + 1
            and return_values.messages == []
        )

    # create and send message in next step
    message = external_scheduling_container._create_acl(
        content="", receiver_addr=external_scheduling_container.addr, receiver_id=agent_1.aid
    )
    encoded_msg = external_scheduling_container.codec.encode(message)
    print("created message")

    # advance time only by 0.5 so that in the next cycle the conditional task will be done
    current_time += 0.5
    return_values = await external_scheduling_container.step(
        simulation_time=current_time, incoming_messages=[encoded_msg]
    )
    print("next step done")

    # the conditional task should still be running and next activity should be in 0.5 seconds
    assert (
        return_values.next_activity == current_time + 0.5
        and len(return_values.messages) == 0
    )
    current_time += 0.5
    return_values = await external_scheduling_container.step(
        simulation_time=current_time, incoming_messages=[]
    )

    # now everything should be done
    assert return_values.next_activity is None and len(return_values.messages) == 0

    await external_scheduling_container.shutdown()


class SelfSendAgent(Agent):
    def __init__(self, container, final_number=3):
        super().__init__(container)

        self.no_received_msg = 0
        self.final_no = final_number

    def handle_message(self, content, meta: Dict[str, Any]):
        self.no_received_msg += 1
        # pretend to be really busy
        i = 0
        while i < 1000000:
            i += 1
        # send message to yourself if necessary
        if self.no_received_msg < self.final_no:
            self.schedule_instant_acl_message(
                receiver_addr=self.addr, receiver_id=self.aid, content=content
            )
        else:
            self.schedule_instant_acl_message(
                receiver_addr="AnyOtherAddr", receiver_id="AnyOtherId", content=content
            )


@pytest.mark.asyncio
async def test_send_internal_messages():
    external_scheduling_container = await container_factory.create(
        addr="external_eid_1", connection_type=EXTERNAL_CONNECTION
    )
    agent_1 = SelfSendAgent(container=external_scheduling_container, final_number=3)
    message = external_scheduling_container._create_acl(
        content="", receiver_addr=external_scheduling_container.addr, receiver_id=agent_1.aid
    )
    encoded_msg = external_scheduling_container.codec.encode(message)
    return_values = await external_scheduling_container.step(
        simulation_time=1, incoming_messages=[encoded_msg]
    )
    assert len(return_values.messages) == 1

    await external_scheduling_container.shutdown()


@pytest.mark.asyncio
async def test_step_with_replying_agent():
    external_scheduling_container = await container_factory.create(
        addr="external_eid_1", connection_type=EXTERNAL_CONNECTION
    )
    reply_agent = ReplyAgent(container=external_scheduling_container)
    new_acl_msg = ACLMessage()
    new_acl_msg.content = "hello you"
    new_acl_msg.receiver_addr = "external_eid_1"
    new_acl_msg.receiver_id = reply_agent.aid
    new_acl_msg.sender_id = "Agent0"
    new_acl_msg.sender_addr = "external_eid_2"
    encoded_msg = external_scheduling_container.codec.encode(new_acl_msg)
    container_output = await external_scheduling_container.step(
        simulation_time=10, incoming_messages=[encoded_msg]
    )
    assert (
        len(container_output.messages) == 3
    ), f"output messages: {container_output.messages}"
    assert (
        container_output.messages[0].time
        < container_output.messages[1].time
        < external_scheduling_container.clock.time + 0.1
    )
    assert (
        container_output.messages[2].time > external_scheduling_container.clock.time + 0.1
    )  # since we had a sleep of 0.1 seconds
    assert container_output.next_activity == external_scheduling_container.clock.time + 10
    container_output = await external_scheduling_container.step(
        simulation_time=20, incoming_messages=[]
    )
    assert len(container_output.messages) == 1
    assert container_output.next_activity == external_scheduling_container.clock.time + 10
    await reply_agent.stop_tasks()

    await external_scheduling_container.shutdown()
