import asyncio
import copy
import logging
import warnings
import os
from dataclasses import dataclass
from multiprocessing import Process, Event
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple, Union, List

from ..messages.codecs import ACLMessage, Codec
from ..util.clock import Clock
from ..util.multiprocessing import aioduplex, AioDuplex, PipeToWriteQueue


import dill  # do not remove! Necessary for the auto loaded pickle reg extensions

logger = logging.getLogger(__name__)

AGENT_PATTERN_NAME_PRE = "agent"
WAIT_STEP = 0.01


class IPCEventType(enumerate):
    """Available IPC event types for event process container communication"""

    AIDS = 1
    DISPATCH = 2


@dataclass
class IPCEvent:
    """IPCEvent data container."""

    type: IPCEventType
    data: object
    pid: int


@dataclass
class ContainerMirrorData:
    """Container for the data necessary for setting up a mirror container in another process"""

    message_pipe: AioDuplex
    event_pipe: AioDuplex
    terminate_event: Event
    main_queue: asyncio.Queue


@dataclass
class ContainerData:
    """Container for the data neccessary for the creation of all container implementations"""

    addr: object
    codec: Codec
    clock: Clock
    kwargs: dict


async def cancel_and_wait_for_task(task):
    """Utility to cancel and wait for a task.

    :param task: task to be canceled
    :type task: asyncio.Task
    """
    try:
        task.cancel()
        await task
    except asyncio.CancelledError:
        pass
    except EOFError:
        pass


def create_agent_process_environment(
    container_data: ContainerData,
    agent_creator,
    mirror_container_creator,
    message_pipe: AioDuplex,
    main_queue: asyncio.Queue,
    event_pipe: AioDuplex,
    terminate_event: Event,
    process_initialized_event: Event,
):
    """Create the agent process environment for using agent subprocesses
    in a mango container. This routine will create a new event loop and run
    the so-called agent-loop, which will
    1. initialize the mirror-container and the agents
    2. will wait and return to the event loop until there is a terminate signal
      * while this step, the container and its agents are responsive
    3. shutdown the mirror container

    :param container_data: data for the mirror container creation
    :type container_data: ContainerData
    :param agent_creator: function, which will be called with the mirror container to create and initialize all agents
    :type agent_creator: Function(Container)
    :param mirror_container_creator: function, which will create a mirror container
    :type mirror_container_creator: Function(ContainerData, AsyncIoEventLoop, AioDuplex, AioDuplex, )
    :param message_pipe: Pipe for messages
    :type message_pipe: AioDuplex
    :param event_pipe: Pipe for events
    :type event_pipe: AioDuplex
    :param terminate_event: Event which signals termination of the main container
    :type terminate_event: Event
    :param process_initialized_event: Event signaling to the main container, that the environment is done with initializing
    :type process_initialized_event: Event
    """
    asyncio.set_event_loop(asyncio.new_event_loop())

    async def start_agent_loop():
        container = mirror_container_creator(
            container_data,
            asyncio.get_event_loop(),
            message_pipe,
            main_queue,
            event_pipe,
            terminate_event,
        )
        agent_creator(container)
        process_initialized_event.set()

        while not terminate_event.is_set():
            await asyncio.sleep(WAIT_STEP)
        await container.shutdown()

    asyncio.run(start_agent_loop())


@dataclass
class AgentProcessHandle:
    """Represents the handle for the agent process. It is awaitable for
    the initializing of agent process. Further it contains the pid of the process.
    """

    init: asyncio.Task
    pid: int

    def __await__(self):
        return self.init.__await__()


class BaseContainerProcessManager:
    """Base class for the two different container process manager types: mirror process manager, and main process manager.
    These process manager change the flow of the container to add the subprocess feature natively in the container
    itself. For these purposes this base class defines some hook in's which are commonly used by all implementations.

    However, there are some methods exclusive to one of the two types of process managers. These methods will either,
    return None, or raise a NotImplementedError.
    """

    @property
    def aids(self):
        """
        List of aids living in subprocesses.
        """
        return []

    def handle_message_in_sp(self, message, receiver_id, priority, meta):
        """Called when a message should be handled by the process manager. This
        happens when the receiver id is unknown to the main container itself.

        :param message: the message
        :type message: Any
        :param receiver_id: aid of the receiver
        :type receiver_id: str
        :param priority: prio
        :type priority: int_
        :param meta: meta
        :type meta: dict
        :raises NotImplementedError: generally not implemented in mirror container manager
        """

        raise NotImplementedError(
            f"{self}-{receiver_id}-{self._container._agents.keys()}"
        )

    def create_agent_process(self, agent_creator, container, mirror_container_creator):
        """Creates a process with an agent, which is created by agent_creator. Further,
        the mirror_container_creator will create a mirror container, which replaces the
        original container for all agents which live in its process.

        :param agent_creator: function with one argument 'container', which creates all agents, which
        shall live in the process
        :type agent_creator: Function(Container)
        :param container: the main container
        :type container: Container
        :param mirror_container_creator: function, which creates a mirror container, given container
        data and IPC connection data
        :type mirror_container_creator: Function(ContainerData, AsyncioLoop, AioDuplex, AioDuplex, Event)
        :raises NotImplementedError: generally raised if the manager is a mirror manager
        """
        raise NotImplementedError()

    def pre_hook_send_internal_message(
        self, message, receiver_id, priority, default_meta
    ):
        """Hook in before an internal message is sent. Capable of preventing the default
        send_internal_message call.
        Therefore this method is able to reroute messages without side effects.

        :param message: the message
        :type message: Any
        :param receiver_id: aid
        :type receiver_id: str
        :param priority: prio
        :type priority: 0
        :param default_meta: meta
        :type default_meta: dict
        :return: Tuple, first the status (True, False = successful, unsuccessful and prevent
        the original send_internal_message, None = Continue original call), second the Queue-like
        inbox, in which the message should be redirected in
        :rtype: Tuple[Boolean, Queue-like]
        """
        return None, None

    def pre_hook_reserve_aid(self, suggested_aid=None):
        """Hook in before an aid is reserved. Capable of preventing the default
        reserve_aid call.

        :param suggested_aid: the aid, defaults to None
        :type suggested_aid: str, optional
        :return: aid, can be None if the original reserve_aid should be executed
        :rtype: str
        """
        return None

    def dispatch_to_agent_process(self, pid: int, coro_func, *args):
        """Dispatches a coroutine function to another process. The coroutine_func
        and its arguments are serialized and sent to the agent process, in which it
        is executed with the Container as first argument (followed by the defined arguments).

        :param pid: the pid
        :type pid: int
        :param coro_func: coro function, which shall be executed
        :type coro_func: async def
        :raises NotImplementedError: raises a NotImplementedError if mirror manager
        """
        raise NotImplementedError()

    async def shutdown(self):
        """Clean up all process related stuff.

        :raises NotImplementedError: Should never be raised
        """
        raise NotImplementedError()


class MirrorContainerProcessManager(BaseContainerProcessManager):
    """Internal Manager class, responsible for the implementation of operations necessary for the agent processes
    in the mirror container.
    """

    def __init__(
        self,
        container,
        mirror_data: ContainerMirrorData,
    ) -> None:
        self._container = container
        self._mirror_data = mirror_data
        self._out_queue = asyncio.Queue()

        self._fetch_from_ipc_task = asyncio.create_task(
            self._move_incoming_messages_to_inbox(
                self._mirror_data.message_pipe,
                self._mirror_data.terminate_event,
            )
        )
        self._send_to_message_pipe_task = asyncio.create_task(
            self._send_to_message_pipe(
                self._mirror_data.message_pipe,
                self._mirror_data.terminate_event,
            )
        )
        self._execute_dispatch_events_task = asyncio.create_task(
            self._execute_dispatch_event(self._mirror_data.event_pipe)
        )

    async def _execute_dispatch_event(self, event_pipe: AioDuplex):
        try:
            async with event_pipe.open_readonly() as rx:
                while True:
                    event: IPCEvent = await rx.read_object()
                    if event.type == IPCEventType.DISPATCH:
                        coro_func, args = event.data
                        try:
                            await coro_func(self._container, *args)
                        except Exception:
                            logger.exception("A dispatched coroutine has failed!")
        except EOFError:
            # other side disconnected -> task not necessry anymore
            pass
        except Exception:
            logger.exception("The Dispatch Event Loop has failed!")

    async def _move_incoming_messages_to_inbox(
        self, message_pipe: AioDuplex, terminate_event: Event
    ):
        try:
            async with message_pipe.open_readonly() as rx:
                while not terminate_event.is_set():
                    priority, message, meta = await rx.read_object()

                    receiver = self._container._agents.get(meta["receiver_id"], None)
                    if receiver is None:
                        logger.error(
                            f"A message was routed to the wrong process, as the {meta} doesn't contain a known receiver-id"
                        )
                    target_inbox = receiver.inbox
                    target_inbox.put_nowait((priority, message, meta))

        except Exception:
            logger.exception("The Move Message Task Loop has failed!")

    async def _send_to_message_pipe(
        self, message_pipe: AioDuplex, terminate_event: Event
    ):
        try:
            async with message_pipe.open_writeonly() as tx:
                while not terminate_event.is_set():
                    data = await self._out_queue.get()

                    tx.write_object(data)
                    await tx.drain()

        except Exception:
            logger.exception("The Send Message Task Loop has failed!")

    def pre_hook_send_internal_message(
        self, message, receiver_id, priority, default_meta
    ):
        self._out_queue.put_nowait((message, receiver_id, priority, default_meta))
        return True, None

    def pre_hook_reserve_aid(self, suggested_aid=None):
        ipc_event = IPCEvent(IPCEventType.AIDS, suggested_aid, os.getpid())
        self._mirror_data.event_pipe.write_connection.send(ipc_event)
        return self._mirror_data.event_pipe.read_connection.recv()

    async def shutdown(self):
        await cancel_and_wait_for_task(self._fetch_from_ipc_task)
        await cancel_and_wait_for_task(self._execute_dispatch_events_task)
        await cancel_and_wait_for_task(self._send_to_message_pipe_task)


class MainContainerProcessManager(BaseContainerProcessManager):
    """Internal Manager class, responsible for the implementation of operations necessary for the agent processes
    in the main container.
    """

    def __init__(
        self,
        container,
    ) -> None:
        self._active = False
        self._container = container

    def _init_mp(self):
        # For agent multiprocessing support
        self._agent_processes = []
        self._terminate_sub_processes = Event()
        self._pid_to_message_pipe = {}
        self._pid_to_pipe = {}
        self._pid_to_aids = {}
        self._handle_process_events_tasks: List[asyncio.Task] = []
        self._handle_sp_messages_tasks: List[asyncio.Task] = []
        self._main_queue = None

    @property
    def aids(self):
        all_aids = []
        if self._active:
            for _, aids in self._pid_to_aids.items():
                all_aids += aids
        return all_aids

    async def _handle_process_events(self, pipe):
        try:
            async with pipe.open() as (rx, tx):
                while True:
                    event: IPCEvent = await rx.read_object()
                    if event.type == IPCEventType.AIDS:
                        aid = self._container._reserve_aid(event.data)
                        if event.pid not in self._pid_to_aids:
                            self._pid_to_aids[event.pid] = set()
                        self._pid_to_aids[event.pid].add(aid)
                        tx.write_object(aid)
                        await tx.drain()
        except EOFError:
            # other side disconnected -> task not necessry anymore
            pass
        except Exception:
            logger.exception("The Process Event Loop has failed!")

    async def _handle_process_message(self, pipe: AioDuplex):
        try:
            async with pipe.open_readonly() as rx:
                while True:
                    (
                        message,
                        receiver_id,
                        prio,
                        meta,
                    ) = await rx.read_object()

                    self._container._send_internal_message(
                        message=message,
                        receiver_id=receiver_id,
                        priority=prio,
                        default_meta=meta,
                    )

        except EOFError:
            # other side disconnected -> task not necessry anymore
            pass
        except Exception:
            logger.exception("The Process Message Loop has failed!")

    def pre_hook_send_internal_message(
        self, message, receiver_id, priority, default_meta
    ):
        target_inbox = None
        if self._active:
            target_inbox = self._find_sp_queue(receiver_id)
            default_meta["receiver_id"] = receiver_id
        return None, target_inbox

    def _find_sp_queue(self, aid):
        for pid, aids in self._pid_to_aids.items():
            if aid in aids:
                return PipeToWriteQueue(self._pid_to_message_pipe[pid])
        raise ValueError(f"The aid '{aid}' does not exist in any subprocess.")

    def create_agent_process(self, agent_creator, container, mirror_container_creator):
        if not self._active:
            self._init_mp()
            self._active = True

        from_pipe_message, to_pipe_message = aioduplex()
        from_pipe, to_pipe = aioduplex()
        process_initialized = Event()
        with to_pipe.detach() as to_pipe, to_pipe_message.detach() as to_pipe_message:
            agent_process = Process(
                target=create_agent_process_environment,
                args=(
                    ContainerData(
                        addr=container.addr,
                        codec=container.codec,
                        clock=container.clock,
                        kwargs=container._kwargs,
                    ),
                    agent_creator,
                    mirror_container_creator,
                    to_pipe_message,
                    self._main_queue,
                    to_pipe,
                    self._terminate_sub_processes,
                    process_initialized,
                ),
            )
            self._agent_processes.append(agent_process)
            agent_process.daemon = True
            agent_process.start()

        self._pid_to_message_pipe[agent_process.pid] = from_pipe_message
        self._pid_to_pipe[agent_process.pid] = from_pipe
        self._handle_process_events_tasks.append(
            asyncio.create_task(self._handle_process_events(from_pipe))
        )
        self._handle_sp_messages_tasks.append(
            asyncio.create_task(self._handle_process_message(from_pipe_message))
        )

        async def wait_for_process_initialized():
            while not process_initialized.is_set():
                await asyncio.sleep(WAIT_STEP)

        return AgentProcessHandle(
            asyncio.create_task(wait_for_process_initialized()), agent_process.pid
        )

    def dispatch_to_agent_process(self, pid: int, coro_func, *args):
        assert pid in self._pid_to_pipe

        ipc_event = IPCEvent(IPCEventType.DISPATCH, (coro_func, args), pid)
        self._pid_to_pipe[pid].write_connection.send(ipc_event)

    def handle_message_in_sp(self, message, receiver_id, priority, meta):
        sp_queue_of_agent = self._find_sp_queue(receiver_id)
        if sp_queue_of_agent is None:
            logger.warning("Received a message for an unknown receiver;%s", receiver_id)
        else:
            sp_queue_of_agent.put_nowait((priority, message, meta))

    async def shutdown(self):
        if self._active:
            # send a signal to all sub processes to terminate their message feed in's
            self._terminate_sub_processes.set()

            for task in self._handle_process_events_tasks:
                await cancel_and_wait_for_task(task)

            for task in self._handle_sp_messages_tasks:
                await cancel_and_wait_for_task(task)

            # wait for and tidy up processes
            for process in self._agent_processes:
                process.join()
                process.terminate()
                process.close()


class Container(ABC):
    """Superclass for a mango container"""

    def __init__(
        self,
        *,
        addr,
        name: str,
        codec,
        loop,
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
        self.loop: asyncio.AbstractEventLoop = loop

        # dict of agents. aid: agent instance
        self._agents: Dict = {}
        self._aid_counter: int = 0  # counter for aids

        self.running: bool = True  # True until self.shutdown() is called
        self._no_agents_running: asyncio.Future = asyncio.Future()
        self._no_agents_running.set_result(
            True
        )  # signals that currently no agent lives in this container

        # inbox for all incoming messages
        self.inbox: asyncio.Queue = asyncio.Queue()

        # task that processes the inbox.
        self._check_inbox_task: asyncio.Task = asyncio.create_task(self._check_inbox())

        # multiprocessing
        self._kwargs = kwargs
        if mirror_data is not None:
            self._container_process_manager = MirrorContainerProcessManager(
                self, mirror_data
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

        if suggested_aid is None or not self.is_aid_available(suggested_aid):
            aid = f"{AGENT_PATTERN_NAME_PRE}{self._aid_counter}"
            self._aid_counter += 1
        else:
            aid = suggested_aid
        return aid

    def register_agent(self, agent, suggested_aid: str = None):
        """
        Register *agent* and return the agent id
        :param agent: The agent instance
        :param suggested_aid: (Optional) suggested aid, if the aid is already taken, a generated aid is used.
                              Using the generated aid-style ("agentX") is not allowed.
        :return The agent ID
        """
        if not self._no_agents_running or self._no_agents_running.done():
            self._no_agents_running = asyncio.Future()
        aid = self._reserve_aid(suggested_aid)
        self._agents[aid] = agent
        logger.debug("Successfully registered agent;%s", aid)
        return aid

    def deregister_agent(self, aid):
        """
        Deregister an agent
        :param aid:
        :return:

        """
        del self._agents[aid]
        if len(self._agents) == 0:
            self._no_agents_running.set_result(True)

    @abstractmethod
    async def send_message(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        *,
        receiver_id: Optional[str] = None,
        **kwargs,
    ) -> bool:
        """
        The Container sends a message to an agent according the container protocol.

        :param content: The content of the message
        :param receiver_addr: In case of TCP this is a tuple of host, port
            In case of MQTT this is the topic to publish to.
        :param receiver_id: The agent id of the receiver
        :param kwargs: Additional parameters to provide protocol specific settings
        """
        raise NotImplementedError

    async def send_acl_message(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        *,
        receiver_id: Optional[str] = None,
        acl_metadata: Optional[Dict[str, Any]] = None,
        is_anonymous_acl=False,
        **kwargs,
    ) -> bool:
        """
        The Container sends a message, wrapped in an ACL message, to an agent according the container protocol.

        :param content: The content of the message
        :param receiver_addr: In case of TCP this is a tuple of host, port
        In case of MQTT this is the topic to publish to.
        :param receiver_id: The agent id of the receiver
        :param acl_metadata: metadata for the acl_header.
        :param is_anonymous_acl: If set to True, the sender information won't be written in the ACL header
        :param kwargs: Additional parameters to provide protocol specific settings
        """
        return await self.send_message(
            self._create_acl(
                content,
                receiver_addr=receiver_addr,
                receiver_id=receiver_id,
                acl_metadata=acl_metadata,
                is_anonymous_acl=is_anonymous_acl,
            ),
            receiver_addr=receiver_addr,
            receiver_id=receiver_id,
            **kwargs,
        )

    def _create_acl(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        receiver_id: Optional[str] = None,
        acl_metadata: Optional[Dict[str, Any]] = None,
        is_anonymous_acl=False,
    ):
        """
        :param content:
        :param receiver_addr:
        :param receiver_id:
        :param acl_metadata:
        :return:
        """
        acl_metadata = {} if acl_metadata is None else acl_metadata.copy()
        # analyse and complete acl_metadata
        if "receiver_addr" not in acl_metadata.keys():
            acl_metadata["receiver_addr"] = receiver_addr
        elif acl_metadata["receiver_addr"] != receiver_addr:
            warnings.warn(
                f"The argument receiver_addr ({receiver_addr}) is not equal to "
                f"acl_metadata['receiver_addr'] ({acl_metadata['receiver_addr']}). \
                            For consistency, the value in acl_metadata['receiver_addr'] "
                f"was overwritten with receiver_addr.",
                UserWarning,
            )
            acl_metadata["receiver_addr"] = receiver_addr
        if receiver_id:
            if "receiver_id" not in acl_metadata.keys():
                acl_metadata["receiver_id"] = receiver_id
            elif acl_metadata["receiver_id"] != receiver_id:
                warnings.warn(
                    f"The argument receiver_id ({receiver_id}) is not equal to "
                    f"acl_metadata['receiver_id'] ({acl_metadata['receiver_id']}). \
                               For consistency, the value in acl_metadata['receiver_id'] "
                    f"was overwritten with receiver_id.",
                    UserWarning,
                )
                acl_metadata["receiver_id"] = receiver_id
        # add sender_addr if not defined and not anonymous
        if not is_anonymous_acl:
            if "sender_addr" not in acl_metadata.keys() and self.addr is not None:
                acl_metadata["sender_addr"] = self.addr

        message = ACLMessage()
        message.content = content

        for key, value in acl_metadata.items():
            setattr(message, key, value)
        return message

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

    async def _handle_message(self, *, priority: int, content, meta: Dict[str, Any]):
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

    def as_agent_process(self, agent_creator, mirror_container_creator):
        """Spawn a new process with a container, mirroring the current container, and
        1 to n agents, created by 'agent_creator'. Can be used to introduce real
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
        return self._container_process_manager.create_agent_process(
            agent_creator=agent_creator,
            container=self,
            mirror_container_creator=mirror_container_creator,
        )

    def dispatch_to_agent_process(self, pid: int, coro_func, *args):
        """Dispatch a coroutine function and its necessary arguments to the process 'pid'.
        This allows the user to execute arbitrary code in the agent subprocesses.

        The coro_func accepts coro_func(Container, *args).

        :param pid: the pid in which the coro func should get dispatched
        :type pid: int
        :param coro_func: async function(Container, *args)
        :type coro_func: async function(Container, *args)
        """
        self._container_process_manager.dispatch_to_agent_process(pid, coro_func, *args)

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
