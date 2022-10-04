from mango.core.agent import Agent
from mango.core.container import Container
from mango.messages.message import ACLMessage as ACLMessage_json, Performatives
import mango.messages.codecs
import mango_library.negotiation.util as util
from mango.role.core import RoleAgent

import numpy as np

import asyncio




class PVAgent(Agent):
    def __init__(self, container):
        # We must pass a reference of the container to "mango.Agent":
        super().__init__(container)
        self.conversations = []
        print(f"Hello I am a PV agent! My id is {self._aid}.")


    def handle_msg(self, content, meta):
        """
        decide which actions shall be performed as reaction to message
        :param content: the content of the message
        :param meta: meta information
        """
        print(f"Received message: {content} with meta {meta}")

        t = asyncio.create_task(self.react_to_get_feed_in(content, meta))
        t.add_done_callback(self.raise_exceptions)

    async def react_to_get_feed_in(self, msg_in, meta_in):
        conversation_id = meta_in['conversation_id']
        reply_by = meta_in['reply_by']
        sender_id = meta_in['sender_id']
        sender_addr = meta_in['sender_addr']
        feed_in = np.random.uniform(0.0, 10.0)

        # prepare a reply
        if conversation_id not in self.conversations:
            self.conversations.append(conversation_id)
        if reply_by == 'get_feed_in':
            # answer
            message_out_content = f"My feed_in is: {feed_in}"
        elif reply_by == 'end':
            # end received, send good bye
            message_out_content = 'Good Bye'
            # end conversation
            reply_key = None
            self.conversations.remove(conversation_id)
        else:
            assert False, f'got strange reply_by: {reply_by}'
        message = ACLMessage_json(
            sender_id=self.aid,
            sender_addr=self.addr_str,
            receiver_id=sender_id,
            receiver_addr=sender_addr,
            content=message_out_content,
            in_reply_to=reply_by,
            reply_by=reply_key,
            conversation_id=conversation_id,
            performative=Performatives.inform)
        self.agent_logger.debug(f'Going to send {message}')
        await self._container.send_message(message, sender_addr)

        if len(self.conversations) == 0:
            await self.shutdown()

    async def react_to_set_max_feed_in(self, msg_in, meta_in, max_feed_in):
        conversation_id = meta_in['conversation_id']
        reply_by = meta_in['reply_by']
        sender_id = meta_in['sender_id']
        sender_addr = meta_in['sender_addr']

        # prepare a reply
        if conversation_id not in self.conversations:
            self.conversations.append(conversation_id)
        if reply_by == 'set_max_feed_in':
            # answer
            message_out_content = f"I set my max feed in to: {max_feed_in}"
        elif reply_by == 'end':
            # end received, send good bye
            message_out_content = 'Good Bye'
            # end conversation
            reply_key = None
            self.conversations.remove(conversation_id)
        else:
            assert False, f'got strange reply_by: {reply_by}'
        message = ACLMessage_json(
            sender_id=self.aid,
            sender_addr=self.addr_str,
            receiver_id=sender_id,
            receiver_addr=sender_addr,
            content=message_out_content,
            in_reply_to=reply_by,
            reply_by=reply_key,
            conversation_id=conversation_id,
            performative=Performatives.inform)
        self.agent_logger.debug(f'Going to send {message}')
        await self._container.send_message(message, sender_addr)

        if len(self.conversations) == 0:
            await self.shutdown()


class ControllerAgent(Agent):
    def __init__(self, container, other_aid, other_addr):
        # We must pass a reference of the container to "mango.Agent":
        super().__init__(container)
        self.other_aid = other_aid
        self.other_addr = other_addr
        self.conversations = []
        print(f"Hello I am a controller agent! My id is {self.aid}.")
        self.schedule_instant_task(coroutine=self._container.send_message(
            receiver_addr=other_addr,
            receiver_id=other_aid,
            content="Hello I am a controller agent!",
            create_acl=True)
        )

    async def get_feed_in(self):
        if self.other_aid is not None:
            message_content = "What is your feed in?"
            acl_meta = {
                'reply_by': 'get feed in',
                'conversation_id': f'{self.aid}_1',
                'performative': Performatives.inform.value,
                'sender_id': self.aid,
            }
            await self._container.send_message(
                message_content, self.other_addr, receiver_id=self.other_aid,
                create_acl=True, acl_metadata=acl_meta
            )


    async def set_max_feed_in(self, max_feed_in):
        if self.other_aid is not None:
            message_content = f'Set your max feed in to: {max_feed_in}'
            acl_meta = {
                'reply_by': 'feed in',
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
        :param content: the content of the message
        :param meta: meta information
        """
        print(f'Received message: {content} with meta {meta}')

        t = asyncio.create_task(self.get_feed_in())
        t.add_done_callback(self.raise_exceptions)


async def run_container_and_controller():
    # As we already know Containers need to be started via a factory function.
    # This method is a coroutine, so it needs to be called from a coroutine using the await statement
    # create one container + the controller

    pv_and_controller_container = await Container.factory(addr=('localhost', 5555))

    first_pv_agent = PVAgent(pv_and_controller_container)
    second_pv_agent = PVAgent(pv_and_controller_container)
    controller_agent = ControllerAgent(pv_and_controller_container, other_aid=first_pv_agent.aid, other_addr=pv_and_controller_container.addr)

    await controller_agent.get_feed_in()
    # await controller_agent.get_feed_in(pv_and_controller_container.addr, second_pv_agent.aid)
    # TODO address correct agent?

    await asyncio.sleep(1)
    if len(controller_agent.conversations) == 0:
        print("starting shutdowns")
        await first_pv_agent.shutdown()
        await second_pv_agent.shutdown()
        await controller_agent.shutdown()
        await pv_and_controller_container.shutdown()

    # TODO making message exchange correct




class LoadPlanAgent(Agent):
    def __init__(self, container):
        # We must pass a reference of the container to "mango.Agent":
        super().__init__(container)
        self.conversations = []
        print(f"Hello I am a LoadPlanAgent agent! My id is {self.aid}.")

    async def react_to_get_loadplan(self, msg_in, meta_in):
        conversation_id = meta_in['conversation_id']
        reply_by = meta_in['reply_by']
        sender_id = meta_in['sender_id']
        sender_addr = meta_in['sender_addr']
        loadplan = np.random.uniform(2.0, 20.0)

        # prepare a reply
        if conversation_id not in self.conversations:
            self.conversations.append(conversation_id)
        if reply_by == 'get_loadplan':
            # answer
            message_out_content = f"My loadplan is: {loadplan}"
        elif reply_by == 'end':
            # end received, send good bye
            message_out_content = 'Good Bye'
            # end conversation
            reply_key = None
            self.conversations.remove(conversation_id)
        else:
            assert False, f'got strange reply_by: {reply_by}'
        message = ACLMessage_json(
            sender_id=self.aid,
            sender_addr=self.addr_str,
            receiver_id=sender_id,
            receiver_addr=sender_addr,
            content=message_out_content,
            in_reply_to=reply_by,
            reply_by=reply_key,
            conversation_id=conversation_id,
            performative=Performatives.inform)
        self.agent_logger.debug(f'Going to send {message}')
        await self._container.send_message(message, sender_addr)

        if len(self.conversations) == 0:
            await self.shutdown()

    def handle_msg(self, content, meta):
        """
        decide which actions shall be performed as reaction to message
        :param content: the content of the message
        :param meta: meta information
        """
        print(f'Received message: {content} with meta {meta}')

        t = asyncio.create_task(self.send_loadplan())
        t.add_done_callback(self.raise_exceptions)


    async def send_loadplan(self):
        pass


class CHPAgent(Agent):
    def __init__(self, container,  other_aid, other_addr):
        # We must pass a reference of the container to "mango.Agent":
        super().__init__(container)
        self.other_aid = other_aid
        self.other_addr = other_addr
        self.conversations = []
        print(f"Hello I am a CHP agent! My id is {self.aid}.")
        self.schedule_instant_task(coroutine=self._container.send_message(
            receiver_addr=other_addr,
            receiver_id=other_aid,
            content="Hello I am a CHP agent!",
            create_acl=True)
        )

    def handle_msg(self, content, meta):
        # This method defines what the agent will do with incoming messages.
        f'{self.aid} received a message with content {content} and' f'meta {meta}'




# Copy of the normal controler, so that only one controller exists in the final code.
class Adapted_controller(ControllerAgent):
    def __init__(self, container, other_aid, other_addr):
        # We must pass a reference of the container to "mango.Agent":
        super().__init__(container)
        self.other_aid = other_aid
        self.other_addr = other_addr
        self.conversations = []
        print(f"Hello I am a controller agent! My id is {self.aid}.")
        self.schedule_instant_task(coroutine=self._container.send_message(
            receiver_addr=other_addr,
            receiver_id=other_aid,
            content="Hello I am a controller agent!",
            create_acl=True)
        )

    async def get_feed_in(self):
        if self.other_aid is not None:
            message_content = "What is your feed in?"
            acl_meta = {
                'reply_by': 'get feed in',
                'conversation_id': f'{self.aid}_1',
                'performative': Performatives.inform.value,
                'sender_id': self.aid,
            }
            await self._container.send_message(
                message_content, self.other_addr, receiver_id=self.other_aid,
                create_acl=True, acl_metadata=acl_meta
            )


    async def set_max_feed_in(self, max_feed_in):
        if self.other_aid is not None:
            message_content = f'Set your max feed in to: {max_feed_in}'
            acl_meta = {
                'reply_by': 'feed in',
                'conversation_id': f'{self.aid}_1',
                'performative': Performatives.inform.value,
                'sender_id': self.aid,
            }
            await self._container.send_message(
                message_content, self.other_addr, receiver_id=self.other_aid,
                create_acl=True, acl_metadata=acl_meta
            )

    async def get_loadplan(self):
        if self.other_aid is not None:
            message_content = f'What is the current load plan?'
            acl_meta = {
                'reply_by': 'load plan',
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
        :param content: the content of the message
        :param meta: meta information
        """

        print(f'Received message: {content} with meta {meta}')

        t = asyncio.create_task(self.get_feed_in())
        t.add_done_callback(self.raise_exceptions)



async def run_adapted_controller(self):
    # TODO codecs
    # At this time we introduce a CHP in a second container
    # Due to the second container, data is exchanged between the two containers.
    # For this reason we introduce codecs in this step
    # Codecs enable the container to encode and decode known data types to send them as messages.

    # create containers
    codec = mango.messages.codecs.JSON()
    codec2 = mango.messages.codecs.JSON()
    for serializer in util.extra_serializers:
        codec.add_serializer(*serializer())
        codec2.add_serializer(*serializer())
    container_1 = await Container.factory(addr=('127.0.0.2', 5555), codec=codec)
    container_2 = await Container.factory(addr=('127.0.0.2', 5556), codec=codec2)

    s_array = [[[1, 1, 1, 1, 1], [4, 3, 3, 3, 3], [6, 6, 6, 6, 6], [9, 8, 8, 8, 8], [11, 11, 11, 11, 11]]]

    # create agents
    agents = []
    addrs = []

    # TODO ...





    # TODO roles
    # TODO scheduling




if __name__ == '__main__':
    asyncio.run(run_container_and_controller())

