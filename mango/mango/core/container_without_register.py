"""
This module implements the base class for containers (:class:`Container`).

Every agent must live in a container.  A container can contain one ore more
agents.  Containers are responsible for making connections to other containers
and agents.
"""
import asyncio
import logging
from typing import Optional, Union, Tuple, Dict, Any, List

from ..util import m_util as ut
from .container_protocols import ContainerProtocol
from ..messages.message import ACLMessage, MType, Performatives
from .agent import Agent




class TCP_Container:
    """Container for agents.

       The container allows its agents to send messages to other agents
       (via :meth:`send_message()`).
       TODO write more!
       """

    @classmethod
    async def factory(cls, addr, *,  log_level=logging.DEBUG):
        """
        Instantiate a container, create a server socket for it and return the
         container instance.
        This function is a classmethod

        :param addr: The address that the server socket is bound to.It is a
        ``(host, port)`` tuple for a TCP socket.
        :param log_level: The log level to use
        :return The container instance
        """


        # initialize container
        container = cls(addr=addr, log_level=log_level)

        # create a TCP server bound to host and port that uses the specified
        # protocol
        loop = asyncio.get_running_loop()
        container.server = await loop.create_server(
            lambda: ContainerProtocol(container=container, loop=loop),
            addr[0], addr[1])

        return container

    def __init__(self, *, addr, log_level):
        """
        Initializes a container. Do not directly call this method but use
        the factory method instead
        :param addr: The container address
        :param log_level: The log level to use
        """

        self.addr = addr
        self.log_level = log_level
        self.log = ut.configure_logger(f'{self.addr}', log_level)

        self.server = None  # will be set within the factory method

        self._agents = {}  # dict of all agents aid: agent instance
        self._aid_counter = 0  # counter for aids

        # TODO do we need this priorityqueue at this place?
        self.inbox = asyncio.PriorityQueue()  # inbox for all incoming messages
        self._check_inbox_task = None  # task that processes the inbox. Will
        # be created in container.run()
        self.running = False  # True when self.run() is called

        # TODO do we need this?
        self._all_agents_shutdown = asyncio.Future().set_result(
            True)  # signals that no agent lives in this container

    async def run(self):
        """
        Will start the check inbox task and register at the register_addr if
        it exists
        """
        self.log.debug(f'Start running...')
        self._check_inbox_task = asyncio.create_task(self._check_inbox())
        self.running = True

    async def _check_inbox(self):
        """
        Task that checks, if there is a message in inbox and then creates a
        task to handle message
        """

        def raise_exceptions(result):
            """
            Inline function used as a callback to tasks to raise exceptions
            :param result: result object of the task
            """
            exception = result.exception()
            if exception is not None:
                self.log.info('EXCEPTION!')
                raise exception

        self.log.debug(f'Start waiting for messages')

        while True:
            data = await self.inbox.get()
            # self.log.debug(f'Received {data}')
            priority, msg = data
            # self.log.debug(f'Received a message {msg}')
            task = asyncio.create_task(
                self.handle_msg(priority=priority, msg=msg))
            task.add_done_callback(raise_exceptions)
            self.inbox.task_done()  # signals that the queue object is
            # processed

    async def handle_msg(self, priority, msg):
        """
        This is called as a seperate task for every message that is read
        :param priority: priority of the msg
        :param msg: message Object
        """

        self.log.debug(f'going to handle the msg {msg}')

        if msg.message_type == MType.agent:
            # A message for an agent
            receiver_id, receiver_addr = msg.receivers
            if receiver_id in self._agents.keys():
                self.log.debug(f'receiver is known')
                receiver = self._agents[receiver_id]
                await receiver.inbox.put((priority, msg))

        elif msg.message_type == MType.container:
            # This should not happen here
            raise NotImplementedError



    async def send_message(self, message, receiver_id, receiver_addr):
        """
        container sends the message of one of its own agents to another agent
        :param message:
        """
        message.message_type = MType.agent
        # self.log.debug(f'Container at {self.addr} shall send a message {
        # message} to {sender_id}')
        futs = []
        if receiver_addr == self.addr:
            if receiver_id in self._agents.keys():
                # The receiver is an agent in the same container
                self.send_internal_message(receiver_id, message)
        else:
            futs.append(self.send_external_message(receiver_addr, message))
            await asyncio.gather(*futs)

    def send_internal_message(self, receiver_id, message):
        """
        Sends a message to an agent that lives in the same container
        :param receiver_id: ID of the reciever
        :param message:
        :return:
        """

        receiver = self._agents.get(receiver_id, None)
        if receiver is None:
            raise KeyError(
                f'Receiver ID {receiver_id} is not known to the container '
                f'{self.cid}')
        # TODO priority assignment could be specified in config,
        # depending on UC or even change dynamically
        priority = message.performative.value
        if priority is None:
            priority = 0
        # self.log.debug(f'Container will send an internal message')
        receiver.inbox.put_nowait((priority, message))

    def _lookup_address(self, receiver_id):
        container_id = receiver_id.split('/')[0]
        addr = None
        if container_id in self._all_containers:
            addr = self._all_containers[container_id]
        return addr

    async def send_external_message(self, addr, message):
        """

        :param addr: Tuple of (host, port)
        :param message: The message
        :return:
        """
        # if addr is None:
        #     raise Exception('not a valid address')
        # Get a reference to the event loop as we plan to use
        # low-level APIs
        self.log.debug(f'Sending {message}')
        loop = asyncio.get_running_loop()
        # on_con_lost = loop.create_future()
        try:
            transport, protocol = await loop.create_connection(
                lambda: ContainerProtocol(container=self, loop=loop),
                addr[0],
                addr[1])
            # self.log.info('Connection established')
            protocol.write(message.encode())
            # self.log.info('message sent')
            await protocol.shutdown()
            # self.log.info('protocol shutdown complete')
        except Exception:
            print(f'address {addr} is not valid')
            # TODO: handle properly (send notification to agent?)

    def register_agent(self, agent):
        """
        Register *agent* and return the agent id
        :param agent: The agent instance
        :return The agent ID
        """
        if not self._all_agents_shutdown or self._all_agents_shutdown.done():
            self._all_agents_shutdown = asyncio.Future()
        self.log.debug('got a register request')

        full_aid = f'{self.addr}/agent{self._aid_counter}'
        self._aid_counter += 1
        self.log.debug(f'{full_aid} is registered now')
        self._agents[full_aid] = agent
        return full_aid

    def deregister_agent(self, aid):
        """
        Deregister an agent
        :param aid:
        :return:

        """
        del self._agents[aid]
        if len(self._agents) == 0:
            self._all_agents_shutdown.set_result(True)

    async def shutdown(self):
        """Shutdown all agents in the container and the container itself"""
        self.running = False
        futs = []
        for aid, agent in self._agents.items():
            # shutdown all running agents
            futs.append(agent.shutdown())
        await asyncio.gather(*futs)
        self.server.close()
        await self.server.wait_closed()


        # TODO check if we need this
        if self._check_inbox_task is not None:
            self.log.debug('check inbox task will be cancelled')
            self._check_inbox_task.cancel()
            try:
                await self._check_inbox_task
            except asyncio.CancelledError:
                pass


