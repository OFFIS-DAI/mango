import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing import get_context
from multiprocessing.synchronize import Event as MultiprocessingEvent

import dill  # noqa F401 # do not remove! Necessary for the auto loaded pickle reg extensions

from ..messages.codecs import Codec
from ..util.clock import Clock
from ..util.multiprocessing import AioDuplex, PipeToWriteQueue, aioduplex

logger = logging.getLogger(__name__)

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
    terminate_event: MultiprocessingEvent
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
    terminate_event: MultiprocessingEvent,
    process_initialized_event: MultiprocessingEvent,
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
    container_data.codec = dill.loads(container_data.codec)
    agent_creator = dill.loads(agent_creator)
    mirror_container_creator = dill.loads(mirror_container_creator)

    async def start_agent_loop():
        container = mirror_container_creator(
            container_data,
            asyncio.get_event_loop(),
            message_pipe.dup(),
            main_queue,
            event_pipe.dup(),
            terminate_event,
        )
        message_pipe.close()
        event_pipe.close()
        if asyncio.iscoroutinefunction(agent_creator):
            await agent_creator(container)
        else:
            agent_creator(container)
        process_initialized_event.set()

        for agent in container._agents.values():
            agent._do_start()

        container.running = True
        container.on_ready()

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
        :type priority: int
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
        """Dispatches a coroutine function to another process. The `coroutine_func`
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
            # other side disconnected -> task not necessary anymore
            pass
        except Exception:
            logger.exception("The Dispatch Event Loop has failed!")

    async def _move_incoming_messages_to_inbox(
        self, message_pipe: AioDuplex, terminate_event: MultiprocessingEvent
    ):
        try:
            async with message_pipe.open_readonly() as rx:
                while not terminate_event.is_set():
                    priority, message, meta = await rx.read_object()

                    receiver = self._container._agents.get(meta["receiver_id"], None)
                    if receiver is None:
                        logger.error(
                            "A message was routed to the wrong process, as the %s doesn't contain a known receiver-id",
                            meta,
                        )
                    target_inbox = receiver.inbox
                    target_inbox.put_nowait((priority, message, meta))

        except Exception:
            logger.exception("The Move Message Task Loop has failed!")

    async def _send_to_message_pipe(
        self, message_pipe: AioDuplex, terminate_event: MultiprocessingEvent
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
        self._mp_enabled = False
        self._ctx = get_context("spawn")
        self._agent_process_init_list = []
        self._started = False

    def _init_mp(self):
        # For agent multiprocessing support
        self._agent_processes = []
        self._terminate_sub_processes = self._ctx.Event()
        self._pid_to_message_pipe = {}
        self._pid_to_pipe = {}
        self._pid_to_aids = {}
        self._handle_process_events_tasks: list[asyncio.Task] = []
        self._handle_sp_messages_tasks: list[asyncio.Task] = []
        self._main_queue = None
        self._mp_enabled = True

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
        if not self._mp_enabled:
            return None
        for pid, aids in self._pid_to_aids.items():
            if aid in aids:
                return PipeToWriteQueue(self._pid_to_message_pipe[pid])
        raise ValueError(f"The aid '{aid}' does not exist in any subprocess.")

    def create_agent_process_lazy(
        self, agent_creator: Callable, container, mirror_container_creator: Callable
    ):
        """
        Adds information to create a new agent process to a list.
        The agent process will be created when the container is started.
        If the container is already started, the agent process will be created immediately.
        :param agent_creator: The agent creator function.
        :type agent_creator: Callable
        :param container: The container instance.
        :type container: Container
        :param mirror_container_creator: The mirror container creator function.
        :type mirror_container_creator: Callable
        """
        # dill must be dumped on creation, otherwise the later variable state would be stored
        agent_creator = dill.dumps(agent_creator)
        mirror_container_creator = dill.dumps(mirror_container_creator)
        if not self._started:
            self._agent_process_init_list.append(
                (agent_creator, container, mirror_container_creator)
            )
        else:
            self._create_agent_process_bytes(
                agent_creator, container, mirror_container_creator
            )

    async def create_agent_process(
        self, agent_creator: Callable, container, mirror_container_creator: Callable
    ):
        """
        Creates a new agent process and awaits the creation of the AgentProcess.
        This function requires to have a running event loop.
        """
        agent_creator = dill.dumps(agent_creator)
        mirror_container_creator = dill.dumps(mirror_container_creator)
        return await self._create_agent_process_bytes(
            agent_creator, container, mirror_container_creator
        )

    def _create_agent_process_bytes(
        self, agent_creator: bytes, container, mirror_container_creator: bytes
    ):
        """
        Creates a new agent process and starts it in a separate process.
        Uses the serialized agent creator and mirror container creator function.

        :param agent_creator: dill dump of the agent creator function.
        :type agent_creator: bytes
        :param container: The container for which the agent process is mirrored.
        :type container: Container
        :param mirror_container_creator: The dill dump of the mirror container creator function.
        :type mirror_container_creator: bytes
        :return: The handle to the mirror container process.
        """
        if not self._active:
            self._init_mp()
            self._active = True

        from_pipe_message, to_pipe_message = aioduplex(self._ctx)
        from_pipe, to_pipe = aioduplex(self._ctx)
        process_initialized = self._ctx.Event()
        with to_pipe.detach() as to_pipe, to_pipe_message.detach() as to_pipe_message:
            agent_process = self._ctx.Process(
                target=create_agent_process_environment,
                args=(
                    ContainerData(
                        addr=container.addr,
                        codec=dill.dumps(container.codec),
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

        from_pipe_message_dup = from_pipe_message.dup()
        from_pipe_dup = from_pipe.dup()
        from_pipe_message.close()
        from_pipe.close()
        self._pid_to_message_pipe[agent_process.pid] = from_pipe_message_dup
        self._pid_to_pipe[agent_process.pid] = from_pipe_dup
        self._handle_process_events_tasks.append(
            asyncio.create_task(self._handle_process_events(from_pipe_dup))
        )
        self._handle_sp_messages_tasks.append(
            asyncio.create_task(self._handle_process_message(from_pipe_message_dup))
        )

        async def wait_for_process_initialized():
            while not process_initialized.is_set():
                await asyncio.sleep(WAIT_STEP)

            await asyncio.sleep(0)

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

    async def start(self):
        if not self._started:
            self._started = True
            processes = []
            for a, c, m in self._agent_process_init_list:
                processes.append(self._create_agent_process_bytes(a, c, m))
            for process in processes:
                await process

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
