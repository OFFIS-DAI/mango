from typing import Dict, Any
import asyncio
import pytest
from mango.messages.message import ACLMessage
from mango.core.container import Container, MosaikAgentMessage
from mango.core.agent import Agent
from mango.util.clock import ExternalClock


@pytest.mark.asyncio
async def test_init():
    mosaik_container = await Container.factory(addr="mosaik_eid_1234", connection_type='mosaik')
    assert mosaik_container.addr == "mosaik_eid_1234"
    assert isinstance(mosaik_container.clock, ExternalClock)
    await mosaik_container.shutdown()


@pytest.mark.asyncio
async def test_send_msg():
    mosaik_container = await Container.factory(addr="mosaik_eid_1234", connection_type='mosaik')
    await mosaik_container.send_message(
        content='test', receiver_addr='eid321', receiver_id='Agent0', create_acl=True,
    )
    assert len(mosaik_container.message_buffer) == 1
    mosaik_agent_msg: MosaikAgentMessage = mosaik_container.message_buffer[0]
    assert mosaik_agent_msg.receiver == 'eid321'
    decoded_msg = mosaik_container.codec.decode(mosaik_agent_msg.message)
    assert decoded_msg.content == 'test'
    assert decoded_msg.receiver_addr == 'eid321'
    assert decoded_msg.receiver_id == 'Agent0'
    await mosaik_container.shutdown()


@pytest.mark.asyncio
async def test_step():
    mosaik_container = await Container.factory(addr="mosaik_eid_1234", connection_type='mosaik')
    await mosaik_container.send_message(
        content='test', receiver_addr='eid321', receiver_id='Agent0', create_acl=True,
    )
    step_output = await mosaik_container.step(simulation_time=12, incoming_messages=[])
    assert mosaik_container.message_buffer == []
    assert mosaik_container.clock.time == 12
    assert 0 < step_output.duration < 0.01
    assert len(step_output.messages) == 1
    mosaik_msg = step_output.messages[0]
    assert 0 < mosaik_msg.time < 0.01
    assert mosaik_msg.receiver == 'eid321'
    decoded_msg = mosaik_container.codec.decode(mosaik_msg.message)
    assert decoded_msg.content == 'test'
    assert decoded_msg.receiver_addr == 'eid321'
    assert decoded_msg.receiver_id == 'Agent0'
    await mosaik_container.shutdown()


class ReplyAgent(Agent):

    def __init__(self, container):
        super().__init__(container)
        self.current_ping = 0
        self.tasks = []
        self.tasks.append(self.schedule_periodic_task(self.send_ping, delay=10))

    async def send_ping(self):
        await self._container.send_message(receiver_addr='ping_receiver_addr', receiver_id='ping_receiver_id',
                                           create_acl=True, content=f'ping{self.current_ping}')
        self.current_ping += 1

    def handle_msg(self, content, meta: Dict[str, Any]):
        self.schedule_instant_task(self.sleep_and_answer(content, meta))

    async def sleep_and_answer(self, content, meta):
        await self._container.send_message(receiver_addr=meta['sender_addr'], receiver_id=['sender_id'],
                                           create_acl=True, content=f'I received {content}',
                                           )
        await asyncio.sleep(0.1)
        await self._container.send_message(receiver_addr=meta['sender_addr'], receiver_id=['sender_id'],
                                           create_acl=True, content=f'Thanks for sending {content}',
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
        self.schedule_conditional_task(condition_func=lambda: self.received_msg,
                                       coroutine=self.print_cond_task_finished(), lookup_delay=1)

    async def print_cond_task_finished(self):
        print('Conditional task is done')

    def handle_msg(self, content, meta: Dict[str, Any]):
        self.received_msg = True

@pytest.mark.asyncio
async def test_step_with_cond_task():
    mosaik_container = await Container.factory(addr="mosaik_eid_1", connection_type='mosaik')
    agent_1 = WaitForMessageAgent(mosaik_container)
    current_time = 0

    for _ in range(10):
        current_time += 1
        # advance time without anything happening
        return_values = await mosaik_container.step(simulation_time=current_time, incoming_messages=[])
        assert return_values.next_activity == current_time + 1 and return_values.messages == []

    # create and send message in next step
    message = mosaik_container._create_acl(content='', receiver_addr=mosaik_container.addr, receiver_id=agent_1.aid)
    encoded_msg = mosaik_container.codec.encode(message)
    # advance time only by 0.5 so that in the next cycle the conditional task will be done
    current_time += 0.5
    return_values = await mosaik_container.step(simulation_time=current_time, incoming_messages=[encoded_msg])

    # the conditional task should still be running and next activity should be in 0.5 seconds
    assert return_values.next_activity == current_time + 0.5 and len(return_values.messages) == 0
    current_time += 0.5
    return_values = await mosaik_container.step(simulation_time=current_time, incoming_messages=[])

    # now everything should be done
    assert return_values.next_activity is None and len(return_values.messages) == 0

    await mosaik_container.shutdown()


@pytest.mark.asyncio
async def test_step_with_replying_agent():
    mosaik_container = await Container.factory(addr="mosaik_eid_1", connection_type='mosaik')
    reply_agent = ReplyAgent(container=mosaik_container)
    new_acl_msg = ACLMessage()
    new_acl_msg.content = 'hello you'
    new_acl_msg.receiver_addr = 'mosaik_eid_1'
    new_acl_msg.receiver_id = reply_agent.aid
    new_acl_msg.sender_id = 'Agent0'
    new_acl_msg.sender_addr = 'mosaik_eid_2'
    encoded_msg = mosaik_container.codec.encode(new_acl_msg)
    container_output = await mosaik_container.step(simulation_time=10, incoming_messages=[encoded_msg])
    assert len(container_output.messages) == 3, f'output messages: {container_output.messages}'
    assert container_output.messages[0].time < container_output.messages[1].time < mosaik_container.clock.time + 0.1
    assert container_output.messages[2].time > mosaik_container.clock.time + 0.1  # since we had a sleep of 0.1 seconds
    assert container_output.next_activity == mosaik_container.clock.time + 10
    container_output = await mosaik_container.step(simulation_time=20, incoming_messages=[])
    assert len(container_output.messages) == 1
    assert container_output.next_activity == mosaik_container.clock.time + 10
    await reply_agent.stop_tasks()

    await mosaik_container.shutdown()
