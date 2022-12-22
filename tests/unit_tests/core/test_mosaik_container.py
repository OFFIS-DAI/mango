from typing import Dict, Any
import asyncio
import pytest
from mango.messages.message import ACLMessage
from mango.container.mosaik import MosaikAgentMessage
import mango.container.factory as container_factory
from mango.agent.core import Agent
from mango.util.clock import ExternalClock


@pytest.mark.asyncio
async def test_init():
    mosaik_container = await container_factory.create(addr="mosaik_eid_1234", connection_type='mosaik')
    assert mosaik_container.addr == "mosaik_eid_1234"
    assert isinstance(mosaik_container.clock, ExternalClock)
    await mosaik_container.shutdown()


@pytest.mark.asyncio
async def test_send_msg():
    mosaik_container = await container_factory.create(addr="mosaik_eid_1234", connection_type='mosaik')
    await mosaik_container.send_message(content='test', receiver_addr='eid321', receiver_id='Agent0', create_acl=True)
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
    mosaik_container = await container_factory.create(addr="mosaik_eid_1234", connection_type='mosaik')
    await mosaik_container.send_message(content='test', receiver_addr='eid321', receiver_id='Agent0', create_acl=True)
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
        await self._container.send_message(content=f'ping{self.current_ping}', receiver_addr='ping_receiver_addr',
                                           receiver_id='ping_receiver_id', create_acl=True)
        self.current_ping += 1

    def handle_message(self, content, meta: Dict[str, Any]):
        self.schedule_instant_task(self.sleep_and_answer(content, meta))

    async def sleep_and_answer(self, content, meta):
        await self._container.send_message(content=f'I received {content}', receiver_addr=meta['sender_addr'],
                                           receiver_id=['sender_id'], create_acl=True)
        await asyncio.sleep(0.1)
        await self._container.send_message(content=f'Thanks for sending {content}', receiver_addr=meta['sender_addr'],
                                           receiver_id=['sender_id'], create_acl=True)

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
        pass

    def handle_message(self, content, meta: Dict[str, Any]):
        self.received_msg = True


@pytest.mark.asyncio
async def test_step_with_cond_task():
    mosaik_container = await container_factory.create(addr="mosaik_eid_1", connection_type='mosaik')
    agent_1 = WaitForMessageAgent(mosaik_container)
    print('Agent init')

    current_time = 0

    for _ in range(10):
        current_time += 1
        # advance time without anything happening
        print('starting step')
        return_values = await asyncio.wait_for(mosaik_container.step(simulation_time=current_time, incoming_messages=[]), timeout=1)

        print('One step done')


        assert return_values.next_activity == current_time + 1 and return_values.messages == []


    # create and send message in next step
    message = mosaik_container._create_acl(content='', receiver_addr=mosaik_container.addr, receiver_id=agent_1.aid)
    encoded_msg = mosaik_container.codec.encode(message)
    print('created message')

    # advance time only by 0.5 so that in the next cycle the conditional task will be done
    current_time += 0.5
    return_values = await mosaik_container.step(simulation_time=current_time, incoming_messages=[encoded_msg])
    print('next step done')

    # the conditional task should still be running and next activity should be in 0.5 seconds
    assert return_values.next_activity == current_time + 0.5 and len(return_values.messages) == 0
    current_time += 0.5
    return_values = await mosaik_container.step(simulation_time=current_time, incoming_messages=[])

    # now everything should be done
    assert return_values.next_activity is None and len(return_values.messages) == 0

    await mosaik_container.shutdown()


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
            self.schedule_instant_acl_message(receiver_addr=self._container.addr, receiver_id=self.aid, content=content)
        else:
            self.schedule_instant_acl_message(receiver_addr='AnyOtherAddr', receiver_id='AnyOtherId', content=content)


@pytest.mark.asyncio
async def test_send_internal_messages():
    mosaik_container = await container_factory.create(addr="mosaik_eid_1", connection_type='mosaik')
    agent_1 = SelfSendAgent(container=mosaik_container, final_number=3)
    message = mosaik_container._create_acl(content='', receiver_addr=mosaik_container.addr, receiver_id=agent_1.aid)
    encoded_msg = mosaik_container.codec.encode(message)
    return_values = await mosaik_container.step(simulation_time=1, incoming_messages=[encoded_msg])
    assert len(return_values.messages) == 1

    await mosaik_container.shutdown()


@pytest.mark.asyncio
async def test_step_with_replying_agent():
    mosaik_container = await container_factory.create(addr="mosaik_eid_1", connection_type='mosaik')
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
