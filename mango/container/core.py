import asyncio
import copy
import logging
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from ..agent.core import Agent, AgentAddress
from ..messages.codecs import Codec
from ..util.clock import Clock
from .mp import (
    MainContainerProcessManager,
    MirrorContainerProcessManager,
    cancel_and_wait_for_task,
)

logger = logging.getLogger(__name__)

AGENT_PATTERN_NAME_PRE = "agent"

A = TypeVar("A")


class Container(ABC):
    """Superclass for a mango container"""

    def __init__(
        self,
        *,
        addr,
        name: str,
        codec,
        clock: Clock,
        copy_internal_messages=False,
        mirror_data=None,
        **kwargs,
    ):
        self.name: str = name
        self.addr = addr
        self.clock = clock
        self._copy_internal_messages = copy_internal_messages

        self.codec: Codec = codec

        # dict of agents. aid: agent instance
        self._agents: dict[str, Agent] = {}
        self._aid_counter: int = 0  # counter for aids

        self.running: bool = False  # True until self.shutdown() is called
        self.ready: bool = False  # True after self.on_ready() is called

        # inbox for all incoming messages
        self.inbox: asyncio.Queue = None

        # task that processes the inbox.
        self._check_inbox_task: asyncio.Task = None

        # multiprocessing
        self._kwargs = kwargs
        self._mirror_data = mirror_data

        # multiprocessing
        if self._mirror_data is not None:
            self._container_process_manager = MirrorContainerProcessManager(
                self, self._mirror_data
            )
        else:
            self._container_process_manager = MainContainerProcessManager(self)

    def _all_aids(self):
        all_aids = list(self._agents.keys()) + self._container_process_manager.aids

        return set(all_aids)

    def _check_agent_aid_pattern_match(self, aid):
        return (
            aid.startswith(AGENT_PATTERN_NAME_PRE)
            and aid[len(AGENT_PATTERN_NAME_PRE) :].isnumeric()
        )

    def is_aid_available(self, aid):
        """
        Check if the aid is available and allowed.
        It is not possible to register aids matching the regular pattern "agentX".
        :param aid: the aid you want to check
        :return True if the aid is available, False if it is not
        """
        return aid not in self._all_aids() and not self._check_agent_aid_pattern_match(
            aid
        )

    def _reserve_aid(self, suggested_aid=None):
        aid = self._container_process_manager.pre_hook_reserve_aid(
            suggested_aid=suggested_aid
        )
        if aid is not None:
            return aid

        if suggested_aid is not None:
            if self.is_aid_available(suggested_aid):
                return suggested_aid
            else:
                logger.warning(
                    "The suggested aid could not be reserved, either it is not available or it is not allowed (pattern %sX);%s",
                    AGENT_PATTERN_NAME_PRE,
                    suggested_aid,
                )

        aid = f"{AGENT_PATTERN_NAME_PRE}{self._aid_counter}"
        self._aid_counter += 1
        return aid

    def register(self, agent: Agent, suggested_aid: str = None):
        """
        Register *agent* and return the agent id

        :param agent: The agent instance
        :param suggested_aid: (Optional) suggested aid, if the aid is already taken, a generated aid is used.
            Using the generated aid-style ("agentX") is not allowed.

        :return The agent ID
        """
        aid = self._reserve_aid(suggested_aid)
        if agent.context:
            raise ValueError("Agent is already registered to a container")
        self._agents[aid] = agent
        agent._do_register(self, aid)
        logger.debug("Successfully registered agent;%s", aid)
        if self.running:
            agent._do_start()

        if self.ready:
            agent.on_ready()
        return agent

    def _get_aid(self, agent):
        for aid, a in self._agents.items():
            if id(a) == id(agent):
                return aid
        return None

    def deregister(self, aid):
        """
        Deregister an agent

        :param aid:
        :return:
        """
        del self._agents[aid]

    @abstractmethod
    async def send_message(
        self,
        content: Any,
        receiver_addr: AgentAddress,
        sender_id: None | str = None,
        **kwargs,
    ) -> bool:
        """
        The Container sends a message to an agent according the container protocol.

        :param content: The content of the message
        :param receiver_addr: The address the message is sent to, should be constructed using
                              agent_address(protocol_addr, aid) or address(agent) on sending messages,
                              and sender_address(meta) on replying to messages.
        :param kwargs: Can contain additional meta information
        """
        raise NotImplementedError

    def _send_internal_message(
        self,
        message,
        receiver_id,
        priority=0,
        default_meta=None,
        inbox=None,
    ) -> bool:
        target_inbox = inbox

        # first delegate to process manager to possibly reroute the message
        if receiver_id not in self._agents:
            (
                result,
                inbox_overwrite,
            ) = self._container_process_manager.pre_hook_send_internal_message(
                message, receiver_id, priority, default_meta
            )
            if result is not None:
                return result
            if inbox_overwrite is not None:
                target_inbox = inbox_overwrite

        meta = {}
        message_to_send = (
            copy.deepcopy(message) if self._copy_internal_messages else message
        )

        if target_inbox is None:
            receiver = self._agents.get(receiver_id, None)
            if receiver is None:
                logger.warning(
                    "Sending internal message not successful, receiver id unknown;%s",
                    receiver_id,
                )
                return False
            target_inbox = receiver.inbox

        if hasattr(message_to_send, "split_content_and_meta"):
            content, meta = message_to_send.split_content_and_meta()
        else:
            content = message_to_send
        meta.update(default_meta)

        target_inbox.put_nowait((priority, content, meta))
        return True

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
                logger.warning("Exception in _check_inbox_task.")
                raise exception

        while True:
            data = await self.inbox.get()
            priority, content, meta = data
            task = asyncio.create_task(
                self._handle_message(priority=priority, content=content, meta=meta)
            )
            task.add_done_callback(raise_exceptions)
            self.inbox.task_done()  # signals that the queue object is
            # processed

    async def _handle_message(self, *, priority: int, content, meta: dict[str, Any]):
        """
        This is called as a separate task for every message that is read
        :param priority: priority of the message
        :param content: Deserialized content of the message
        :param meta: Dict with additional information (e.g. topic)
        """

        logger.debug("Received message with content and meta;%s;%s", content, meta)
        receiver_id = meta.get("receiver_id", None)
        if receiver_id and receiver_id in self._agents.keys():
            receiver = self._agents[receiver_id]
            await receiver.inbox.put((priority, content, meta))
        else:
            self._container_process_manager.handle_message_in_sp(
                content, receiver_id, priority, meta
            )

    def _create_mirror_container(self):
        """Returns the Container specific creation function for a new mirror container"""
        raise NotImplementedError

    async def as_agent_process(self, agent_creator, mirror_container_creator=None):
        """Spawn a new process with a container, mirroring the current container, and
        1 to n agents, created by `agent_creator`. Can be used to introduce real
        parallelization using the agents as unit to divide.

        Internally this will create a process with its own asyncio loop and an own
        container, which will mostly imitate the behavior of this container. For this,
        IPC is used (async pipes). The agent in the process will behave exactly like
        it would in the parent process and in the main container (this).

        :param agent_creator: function, which creates 1...n agents, has exactly one argument
            the mirror container
        :type agent_creator: Function(Container)
        :param mirror_container_creator: function, which creates the mirror container, generally
            this parameter is set by the subclasses of container
        :type mirror_container_creator: Function(ContainerData, AsyncioLoop, AioDuplex, AioDuplex, Event)
        :return: a handle for the created process. It contains the pid as property 'pid' and can be awaited
            to make sure the initialization of the agents in the subprocess is actually done.
        :rtype: AgentProcessHandle
        """
        if not mirror_container_creator:
            mirror_container_creator = self._create_mirror_container()
        return await self._container_process_manager.create_agent_process(
            agent_creator=agent_creator,
            container=self,
            mirror_container_creator=mirror_container_creator,
        )

    def as_agent_process_lazy(self, agent_creator, mirror_container_creator=None):
        """
        Similar to as_agent_process, but does not wait for the agent process to be initialized.
        Does also not need a running event loop, making it suitable to add agent processes without an asyncio context.
        """
        if not mirror_container_creator:
            mirror_container_creator = self._create_mirror_container()
        self._container_process_manager.create_agent_process_lazy(
            agent_creator=agent_creator,
            container=self,
            mirror_container_creator=mirror_container_creator,
        )

    def dispatch_to_agent_process(self, pid: int, coro_func, *args):
        """Dispatch a coroutine function and its necessary arguments to the process 'pid'.
        This allows the user to execute arbitrary code in the agent subprocesses.

        The ``coro_func`` accepts ``coro_func(Container, *args)``.

        :param pid: the pid in which the coro func should get dispatched
        :type pid: int
        :param coro_func: async function(Container, ...)
        :type coro_func: async function(Container, ...)
        """
        self._container_process_manager.dispatch_to_agent_process(pid, coro_func, *args)

    async def start(self):
        if self.running:
            raise RuntimeError("Container is already running")
        self.running: bool = True  # True until self.shutdown() is called

        # inbox for all incoming messages
        self.inbox: asyncio.Queue = asyncio.Queue()

        # task that processes the inbox.
        self._check_inbox_task: asyncio.Task = asyncio.create_task(self._check_inbox())

        await self._container_process_manager.start()

        """Start the container. It totally depends on the implementation for what is actually happening."""
        for agent in self._agents.values():
            agent._do_start()

    def on_ready(self):
        if self.ready:
            raise RuntimeError("Container is already ready")
        self.ready = True
        for agent in self._agents.values():
            agent.on_ready()

    async def shutdown(self):
        """Shutdown all agents in the container and the container itself"""
        self.running = False
        futs = []
        for agent in self._agents.values():
            # shutdown all running agents
            futs.append(agent.shutdown())
        await asyncio.gather(*futs)

        await self._container_process_manager.shutdown()

        # cancel check inbox task
        if self._check_inbox_task is not None:
            await cancel_and_wait_for_task(self._check_inbox_task)

        logger.info("Successfully shutdown")
