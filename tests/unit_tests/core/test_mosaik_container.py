from typing import Dict, Any
import asyncio
import pytest
from mango.messages.message import ACLMessage
from mango.core.container import Container
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
    elapsed_time, receiver_addr, encoded_msg = mosaik_container.message_buffer[0]
    assert receiver_addr == 'eid321'
    decoded_msg = mosaik_container.codec.decode(encoded_msg)
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
    return_values = await mosaik_container.step(simulation_time=12, msgs=[])
    assert mosaik_container.message_buffer == []
    assert mosaik_container.clock.time == 12
    assert 0 < return_values[0] < 0.01
    assert len(return_values[1]) == 1
    elapsed_time, receiver_addr, encoded_msg = return_values[1][0]
    assert elapsed_time == 0
    assert receiver_addr == 'eid321'
    decoded_msg = mosaik_container.codec.decode(encoded_msg)
    assert decoded_msg.content == 'test'
    assert decoded_msg.receiver_addr == 'eid321'
    assert decoded_msg.receiver_id == 'Agent0'
    await mosaik_container.shutdown()


class ReplyAgent(Agent):

    def __init__(self, container):
        super().__init__(container)

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

@pytest.mark.asyncio
async def test_step_with_replying_agent():
    mosaik_container = await Container.factory(addr="mosaik_eid_1", connection_type='mosaik')
    reply_agent = ReplyAgent(container=mosaik_container)
    new_acl_msg = ACLMessage()
    new_acl_msg.content = 'hello you'
    new_acl_msg.receiver_addr='mosaik_eid_1'
    new_acl_msg.receiver_id=reply_agent.aid
    new_acl_msg.sender_id='Agent0'
    new_acl_msg.sender_addr='mosaik_eid_2'
    encoded_msg = mosaik_container.codec.encode(new_acl_msg)
    return_values = await mosaik_container.step(simulation_time=12, msgs=[encoded_msg])
    print(return_values)
    print (mosaik_container.message_buffer)
    await mosaik_container.shutdown()



