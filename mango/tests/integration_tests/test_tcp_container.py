import asyncio
import pytest

from mango.core.agent import Agent
from mango.core.container import Container
from mango.messages.message import ACLMessage as ACLMessage_json, Performatives
from mango.messages.acl_message_pb2 import ACLMessage as ACLMessage_proto
from mango.messages import other_proto_msgs_pb2 as other_proto_msg


class SimpleAgent(Agent):

    def __init__(self, container, other_aid=None ,other_addr=None,
                 codec='json'):
        super().__init__(container)
        self.other_aid = other_aid
        self.other_addr = other_addr
        self.conversations = []
        self.codec = codec
        if isinstance(container.addr, (list, tuple)):
            self.addr_str = f'{container.addr[0]}:{container.addr[1]}'
        else:
            self.addr_str = container.addr

    async def send_greeting(self):
        # if we have the address of another agent we send a greeting
        if self.other_aid is not None:
            if self.codec == 'json':
                message_content = 'Hi there'
            elif self.codec == 'protobuf':
                message_content = other_proto_msg.GenericMsg()
                message_content.text = 'Hi there'
            acl_meta = {
                'reply_by':'greeting',
                'conversation_id': f'{self.aid}_1',
                'performative': Performatives.inform.value,
                'sender_id': self.aid,
            }
            await self._container.send_message(
                message_content, self.other_addr, receiver_id=self.other_aid,
                create_acl=True, acl_metadata=acl_meta
            )

    def handle_msg(self, content, meta):
        """
        decide which actions shall be performed as reaction to message
        :param content: the content of the mssage
        :param meta: meta information
        """
        # so far we only expect and react to greetings
        t = asyncio.create_task(self.react_to_greeting(content, meta))
        t.add_done_callback(self.raise_exceptions)


    async def react_to_greeting(self, msg_in, meta_in):
        conversation_id = meta_in['conversation_id']
        reply_by = meta_in['reply_by']
        sender_id = meta_in['sender_id']
        sender_addr = meta_in['sender_addr']
        if not reply_by:
            # No reply necessary - remove conversation id
            if conversation_id in self.conversations:
                self.conversations.remove(conversation_id)
        else:
            # prepare a reply
            if conversation_id not in self.conversations:
                self.conversations.append(conversation_id)
            if reply_by == 'greeting':
                # greet back
                message_out_content = 'Hi there too'
                reply_key = 'greeting2'
            elif reply_by == 'greeting2':
                # back greeting received, send good bye
                message_out_content = 'Good Bye'
                # end conversation
                reply_key = None
                self.conversations.remove(conversation_id)
            else:
                assert False, f'got strange reply_by: {reply_by}'
            if self.codec == 'json':
                message = ACLMessage_json(
                    sender_id=self._aid,
                    sender_addr=self.addr_str,
                    receiver_id=sender_id,
                    receiver_addr=sender_addr,
                    content=message_out_content,
                    in_reply_to=reply_by,
                    reply_by=reply_key,
                    conversation_id=conversation_id,
                    performative=Performatives.inform)
            elif self.codec == 'protobuf':
                message = ACLMessage_proto()
                message.sender_id = self._aid
                message.sender_addr = self.addr_str
                message.receiver_id = sender_id
                message.in_reply_to = reply_by
                if reply_key:
                    message.reply_by=reply_key
                message.conversation_id=conversation_id
                message.performative=Performatives.inform.value
                sub_msg = other_proto_msg.GenericMsg()
                sub_msg.text = message_out_content
                message.content_class = type(sub_msg).__name__
                message.content = sub_msg.SerializeToString()
            await self._container.send_message(message, sender_addr)

        # shutdown if no more open conversations
        if len(self.conversations) == 0:
            await self.shutdown()


async def setup_tcp_agents(*, addr1, addr2, codec='json', proto_msgs_module=None):
    container1 = await Container.factory(connection_type='tcp',
                                         codec=codec, addr=addr1,
                                         proto_msgs_module=proto_msgs_module
                                         )
    # start the container
    if addr2 != addr1:

        # the second container gets to know the first container for registration
        container2 = await Container.factory(connection_type='tcp',
                                             codec=codec, addr=addr2,
                                             proto_msgs_module=proto_msgs_module)
    else:
        container2 = container1

    # initialize an agent in container1
    agent_a = SimpleAgent(container1, codec=codec)

    agent_id1 = agent_a._aid

    # initialize an agent in container2 and tell him the id of the first agent
    agent_b = SimpleAgent(container2, agent_id1, addr1, codec=codec)

    # let agent_b start a conversation by sending a greeting
    await agent_b.send_greeting()

    try:
        # wait for all agents to be shutdown in the container
        await asyncio.wait_for(asyncio.gather(
            container1._no_agents_running, container2._no_agents_running),
        timeout=3)
    except KeyboardInterrupt:
        print('KeyboardInterrupt')
    finally:

        print(f'[{addr1}]: Shutting down container')
        await container1.shutdown()
        if container1 != container2:
            print(f'[{addr2}]: Shutting down container')
            await container2.shutdown()

@pytest.mark.asyncio
async def test_one_container():
    await setup_tcp_agents(addr1=('127.0.0.1', 5555),
                           addr2=('127.0.0.1', 5555),
                           )

@pytest.mark.asyncio
async def test_two_container():
    await setup_tcp_agents(addr1=('127.0.0.1', 5555),
                           addr2=('127.0.0.1', 5556),
                           )

@pytest.mark.asyncio
async def test_one_container_proto():
    await setup_tcp_agents(addr1=('127.0.0.1', 5555),
                           addr2=('127.0.0.1', 5555),
                           codec='protobuf',
                           proto_msgs_module=other_proto_msg
                           )

@pytest.mark.asyncio
async def test_two_container_proto():
    await setup_tcp_agents(addr1=('127.0.0.1', 5555),
                           addr2=('127.0.0.1', 5556),
                           codec='protobuf',
                           proto_msgs_module=other_proto_msg
                           )