import asyncio
import logging
import pytest

from mango.core.container import Container
from mango.core.agent import Agent
from mango.messages import other_proto_msgs_pb2 as other_proto_msg
from mango.messages import acl_message_pb2 as acl_proto_msg
from mango.messages.message import ACLMessage as ACLMessage, \
    Performatives

FIRST_GREETING_META = {
    'reply_by': 'greeting',
    'conversation_id': 'greeting_1',
}

GREETING_ANSWER_META = {
    'in_reply_to': 'greeting',
    'reply_by': 'greet_back',
    'conversation_id': 'greeting_1',
}

SHUTDOWN_META = {

}

class SimpleMQTTAgent(Agent):
    def __init__(self, container, connection_type='mqtt', codec ='json',
                 other_aid=None ,other_addr=None, acl=True):
        super().__init__(container)
        self.other_aid = other_aid
        self.other_addr = other_addr
        self.connection_type = connection_type.lower()
        if self.connection_type == 'mqtt':
            self.my_topic = self._container.inbox_topic
        self.codec = codec.lower()
        self.acl = acl

    async def subscribe_topic(self, topic: str, expected_class=None):
        await self._container.subscribe_for_agent(
            aid=self.aid, topic=topic, expected_class=expected_class)

    def set_sender_addr(self, topic):
        self.my_topic = topic

    async def send_greeting(self):
        assert self.other_aid is not None
        if self.codec == 'json':
            if not self.acl:
                assert False,'Non ACL messages are not supported in json'
            message_content = 'Hi there'
            performative = Performatives.inform
        elif self.codec == 'protobuf':
            message_content = other_proto_msg.GenericMsg()
            message_content.text = 'Hi there'
            message_content.my_topic = self.my_topic
            performative = 7
        else:
            assert False

        message_meta = {'sender_id': self._aid, 'performative': performative}
        message_meta.update(FIRST_GREETING_META)
        if self.connection_type == 'mqtt':
            message_meta.update({'sender_addr': self.my_topic})
            if self.acl:
                await self._container.send_message(
                    content=message_content, receiver_addr=self.other_addr,
                    receiver_id=self.other_aid, acl_metadata=message_meta,
                    create_acl=True )
            else:
                await self._container.send_message(
                    content=message_content, receiver_addr=self.other_addr
                )

        elif self.connection_type == 'tcp':
            pass
        else:
            assert False

    def handle_msg(self, content, meta):
        """
        decide which actions shall be performed as reaction to message
        :param content: message
        :param meta: metadata of the message
        """

        if self.connection_type =='mqtt':
            assert meta['network_protocol'] == 'mqtt'
            assert meta['topic'] == self.my_topic
            assert meta['qos'] == 0
            assert meta['retain'] == False
            if self.codec == 'protobuf':
                content_text = content.text
            else:
                content_text = content
            # so far we only expect and react to greetings
            if content_text == 'Hi there':
                t = asyncio.create_task(self.react_to_greeting(content, meta))
                t.add_done_callback(self.raise_exceptions)
            elif content_text == 'Thanks for your greeting':
                t = asyncio.create_task(self.react_to_backgreeting(content, meta))
                t.add_done_callback(self.raise_exceptions)
            elif content_text == 'Good Bye':
                assert SHUTDOWN_META.items() <= meta.items()
                t = asyncio.create_task(self.shutdown())
                t.add_done_callback(self.raise_exceptions)
            else:
                assert False, f'Received unexpected content {content}'
                # should not happen
        elif self.connection_type == 'tcp':
            pass


    async def react_to_greeting(self, content_in, meta_in):
        """

        :param content_in:
        :param meta_in:
        :return:
        """
        if self.acl:
            assert FIRST_GREETING_META.items() <= meta_in.items()
            conversation_id = meta_in['conversation_id']
            reply_by = meta_in['reply_by']
            sender_id = meta_in['sender_id']
            sender_addr = meta_in['sender_addr']

        # greet back
        if self.codec == 'json':
            if not self.acl:
                assert False, 'Non-ACL Messages are not supported in JSON'
            assert meta_in['performative'] == Performatives.inform
            message_out_content = 'Thanks for your greeting'
            message = ACLMessage()
            message.content = message_out_content
            message.sender_id = self._aid
            message.sender_addr = self.my_topic
            message.receiver_id = sender_id
            message.in_reply_to = reply_by
            message.reply_by = GREETING_ANSWER_META['reply_by']
            message.conversation_id = conversation_id
            message.performative = Performatives.inform
        elif self.codec == 'protobuf':
            if not self.acl:
                assert meta_in['topic'] == self.my_topic
                sender_addr = content_in.my_topic
                message = other_proto_msg.GenericMsg()
                message.text = 'Thanks for your greeting'
            else:
                assert meta_in['performative'] == 7
                message_out_content = 'Thanks for your greeting'
                message = acl_proto_msg.ACLMessage()
                message.sender_id = self._aid
                message.sender_addr = self.my_topic
                message.receiver_id = sender_id
                message.in_reply_to = reply_by
                message.reply_by = GREETING_ANSWER_META['reply_by']
                message.conversation_id = conversation_id
                message.performative = 7
                sub_msg = other_proto_msg.GenericMsg()
                sub_msg.text = message_out_content
                message.content_class = type(sub_msg).__name__
                message.content = sub_msg.SerializeToString()
        else:
            assert False,'Only json or protobuf are allowed'

        if self.connection_type == 'mqtt':
            await self._container.send_message(receiver_addr=sender_addr,
                                               content=message)
        elif self.connection_type == 'tcp':
            # TODO
            pass
        else:
            assert False

    async def react_to_backgreeting(self, content_in, meta_in):
        if self.acl:
            assert GREETING_ANSWER_META.items() <= meta_in.items()
        # back greeting received, send good bye
        if self.codec == 'json':
            if not self.acl:
                assert False, 'Non-ACL messages not supported in json'
            assert meta_in['performative'] == Performatives.inform
            message_out_content = 'Good Bye'
        elif self.codec == 'protobuf':
            if self.acl:
                assert meta_in['performative'] == 7
            message_out_content = other_proto_msg.GenericMsg()
            message_out_content.text = 'Good Bye'
        if self.connection_type == 'mqtt':
            if self.acl:
                await self._container.send_message(
                    content=message_out_content, receiver_id=meta_in['sender_id'],
                    receiver_addr=meta_in['sender_addr'], create_acl=True )
            else:
                await self._container.send_message(
                    content=message_out_content, receiver_addr=self.other_addr)
        await self.shutdown()

async def simple_mqtt_agents_setup(no_container, broker, inbox_1='inbox_1',
                                   inbox_2='inbox_2', add_topic_1 = None,
                                   add_topic_2=None, sender_addr_1=None,
                                   sender_addr_2=None,
                                   codec='json', acl=True):
    """

    :param no_container:
    :param broker:
    :return:
    """
    if codec == 'protobuf':
        proto_msgs = other_proto_msg
    else:
        proto_msgs = None

    if acl:
        expected_class = None
    else:
        expected_class = proto_msgs.GenericMsg
    mqtt_kwargs_1 = {
        'client_id': 'container_1',
        'broker_addr': broker,
        'transport': 'tcp',
    }
    mqtt_kwargs_2 = {
        'client_id': 'container_2',
        'broker_addr': broker,
        'clean_session': False,
    }

    container1 = await Container.factory(
        connection_type='mqtt', addr=inbox_1,
        codec=codec, proto_msgs_module=proto_msgs, mqtt_kwargs=mqtt_kwargs_1)


    agent_a = SimpleMQTTAgent(container1, codec=codec, acl=acl)
    if add_topic_1:
        await agent_a.subscribe_topic(add_topic_1, expected_class)
    if sender_addr_1:
        agent_a.set_sender_addr(sender_addr_1)
    if no_container > 1:
        container2 = await Container.factory(connection_type='mqtt', addr=inbox_2, codec=codec,
            proto_msgs_module=proto_msgs, mqtt_kwargs=mqtt_kwargs_2)
    else:
        container2 = container1

    agent_b = SimpleMQTTAgent(container2, other_addr=agent_a.my_topic,
                              other_aid=agent_a.aid, codec=codec, acl=acl)
    if add_topic_2:
        await agent_b.subscribe_topic(add_topic_2, expected_class)
    if sender_addr_2:
        agent_b.set_sender_addr(sender_addr_2)

    await agent_b.send_greeting()
    try:
        # wait for all agents to be shutdown in the container
        await asyncio.wait_for(
            container1._no_agents_running, 3)
        if no_container == 2:
            await asyncio.wait_for(
                container2._no_agents_running, 3)
    except KeyboardInterrupt:
        print('KeyboardInterrupt')
    except TimeoutError:
        print('TimeoutError')
    finally:
        print(f'going to shutdown container...', end='')
        await container1.shutdown()
        if no_container == 2:
            await container2.shutdown()
        print(f' done.')

@pytest.mark.asyncio
async def test_one_container_mqtt_json():
    await simple_mqtt_agents_setup(1, ("localhost", 1883, 60))

@pytest.mark.asyncio
async def test_two_container_mqtt_json():
    await simple_mqtt_agents_setup(2, ("localhost", 1883, 60))

# @pytest.mark.asyncio
# async def test_two_container_remote_broker():
#     await simple_mqtt_agents_setup(2, 'mqtt.eclipse.org')

@pytest.mark.asyncio
async def test_two_container_add_topics_json():
    await simple_mqtt_agents_setup(2, ("localhost", 1883, 60),
                                   add_topic_2='Hello/you/a',)

@pytest.mark.asyncio
async def test_two_container_no_inbox_json():
    await simple_mqtt_agents_setup(2, ("localhost", 1883, 60),
                                   inbox_1=None, inbox_2=None,
                                   add_topic_1='Whatever/#',
                                   add_topic_2='Hello/+/abc',
                                   sender_addr_1='Whatever/12',
                                   sender_addr_2='Hello/test/abc')

@pytest.mark.asyncio
async def test_one_container_mqtt_proto():
    await simple_mqtt_agents_setup(1, ("localhost", 1883, 60),
                                   codec='protobuf')


@pytest.mark.asyncio
async def test_one_container_mqtt_proto():
    await simple_mqtt_agents_setup(1, ("localhost", 1883, 60),
                                   codec='protobuf')

@pytest.mark.asyncio
async def test_two_container_mqtt_proto():
    await simple_mqtt_agents_setup(2, ("localhost", 1883, 60),
                                   codec='protobuf')

@pytest.mark.asyncio
async def test_two_container_mqtt_proto_no_acl():
    await simple_mqtt_agents_setup(2, ("localhost", 1883, 60),
                                   codec='protobuf', acl=False,
                                   add_topic_1='Whatever/#',
                                   add_topic_2='Hello/+/abc',
                                   sender_addr_1='Whatever/12',
                                   sender_addr_2='Hello/test/abc'
                                   )




