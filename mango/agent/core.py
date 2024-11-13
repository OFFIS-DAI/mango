"""
This module implements the base class for agents (:class:`Agent`).

Every agent must live in a container. Containers are responsible for making
 connections to other agents.
"""

import asyncio
import logging
from abc import ABC
from enum import Enum
from typing import Any

from ..messages.message import AgentAddress
from ..util.clock import Clock
from ..util.scheduling import ScheduledProcessTask, ScheduledTask, Scheduler

logger = logging.getLogger(__name__)


class State(Enum):
    NORMAL = 0  # normal neighbor
    INACTIVE = (
        1  # neighbor link exists but link is not active (could be activated/used)
    )
    BROKEN = 2  # neighbor link exists but link is not usable (can not be activated)


class TopologyService:
    state_to_neighbors: dict[State, list] = dict()

    def neighbors(self, state: State = State.NORMAL):
        return [f() for f in self.state_to_neighbors.get(state, [])]


class AgentContext:
    def __init__(self, container) -> None:
        self._container = container

    @property
    def current_timestamp(self) -> float:
        """
        Method that returns the current unix timestamp given the clock within the container
        """
        return self._container.clock.time

    @property
    def clock(self) -> Clock:
        return self._container.clock

    @property
    def addr(self):
        return self._container.addr

    def register(self, agent, suggested_aid):
        return self._container.register(agent, suggested_aid=suggested_aid)

    def deregister(self, aid):
        if self._container.running:
            self._container.deregister(aid)

    async def send_message(
        self,
        content,
        receiver_addr: AgentAddress,
        sender_id: None | str = None,
        **kwargs,
    ):
        """
        See container.send_message(...)
        """
        return await self._container.send_message(
            content, receiver_addr=receiver_addr, sender_id=sender_id, **kwargs
        )


class AgentDelegates:
    def __init__(self) -> None:
        self.context: AgentContext = None
        self.scheduler: Scheduler = None
        self._aid = None
        self._services = {}

    def on_start(self):
        """Called when container started in which the agent is contained"""

    def on_ready(self):
        """Called when all container has been started using activate(...)."""

    @property
    def current_timestamp(self) -> float:
        """
        Method that returns the current unix timestamp given the clock within the container
        """
        return self.context.current_timestamp

    @property
    def aid(self):
        return self._aid

    @property
    def addr(self):
        """Return the address of the agent as AgentAddress

        Returns:
            _type_: AgentAddress
        """
        if self.context is None:
            return None
        return AgentAddress(self.context.addr, self.aid)

    async def send_message(
        self,
        content,
        receiver_addr: AgentAddress,
        **kwargs,
    ):
        """
        See container.send_message(...)
        """
        return await self.context.send_message(
            content, receiver_addr=receiver_addr, sender_id=self.aid, **kwargs
        )

    def schedule_instant_message(
        self,
        content,
        receiver_addr: AgentAddress,
        **kwargs,
    ):
        """
        Schedules sending a message without any delay. This is equivalent to using the schedulers 'schedule_instant_task' with the coroutine created by
        'container.send_message'.

        :param content: The content of the message
        :param receiver_addr: The address passed to the container
        :param kwargs: Additional parameters to provide protocol specific settings
        :returns: asyncio.Task for the scheduled coroutine
        """

        return self.schedule_instant_task(
            self.send_message(content, receiver_addr=receiver_addr, **kwargs)
        )

    def schedule_conditional_process_task(
        self,
        coroutine_creator,
        condition_func,
        lookup_delay=0.1,
        on_stop=None,
        src=None,
    ):
        """Schedule a process task when a specified condition is met.

        :param coroutine_creator: coroutine_creator creating coroutine to be scheduled
        :type coroutine_creator: coroutine_creator
        :param condition_func: function for determining whether the confition is fullfiled
        :type condition_func: lambda () -> bool
        :param lookup_delay: delay between checking the condition
        :type lookup_delay: float
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_conditional_process_task(
            coroutine_creator=coroutine_creator,
            condition_func=condition_func,
            lookup_delay=lookup_delay,
            on_stop=on_stop,
            src=src,
        )

    def schedule_conditional_task(
        self, coroutine, condition_func, lookup_delay=0.1, on_stop=None, src=None
    ):
        """Schedule a task when a specified condition is met.

        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param condition_func: function for determining whether the confition is fullfiled
        :type condition_func: lambda () -> bool
        :param lookup_delay: delay between checking the condition
        :type lookup_delay: float
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_conditional_task(
            coroutine=coroutine,
            condition_func=condition_func,
            lookup_delay=lookup_delay,
            on_stop=on_stop,
            src=src,
        )

    def schedule_timestamp_task(
        self, coroutine, timestamp: float, on_stop=None, src=None
    ):
        """Schedule a task at specified  unix timestamp.

        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param timestamp: timestamp defining when the task should start
        :type timestamp: timestamp
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_timestamp_task(
            coroutine=coroutine, timestamp=timestamp, on_stop=on_stop, src=src
        )

    def schedule_timestamp_process_task(
        self, coroutine_creator, timestamp: float, on_stop=None, src=None
    ):
        """Schedule a task at specified unix timestamp dispatched to another process.

        :param coroutine_creator: coroutine_creator creating coroutine to be scheduled
        :type coroutine_creator: coroutine_creator
        :param timestamp: unix timestamp defining when the task should start
        :type timestamp: float
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_timestamp_process_task(
            coroutine_creator=coroutine_creator,
            timestamp=timestamp,
            on_stop=on_stop,
            src=src,
        )

    def schedule_periodic_process_task(
        self, coroutine_creator, delay, on_stop=None, src=None
    ):
        """Schedule an open end periodically executed task in another process.

        :param coroutine_creator: coroutine function creating coros to be scheduled
        :type coroutine_creator:  Coroutine Function
        :param delay: delay in between the cycles
        :type delay: float
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_periodic_process_task(
            coroutine_creator=coroutine_creator, delay=delay, on_stop=on_stop, src=src
        )

    def schedule_periodic_task(self, coroutine_func, delay, on_stop=None, src=None):
        """Schedule an open end peridocally executed task.

        :param coroutine_func: coroutine function creating coros to be scheduled
        :type coroutine_func:  Coroutine Function
        :param delay: delay in between the cycles
        :type delay: float
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_periodic_task(
            coroutine_func=coroutine_func, delay=delay, on_stop=on_stop, src=src
        )

    def schedule_recurrent_process_task(
        self, coroutine_creator, recurrency, on_stop=None, src=None
    ):
        """Schedule a task using a fine-grained recurrency rule in another process.

        :param coroutine_creator: coroutine function creating coros to be scheduled
        :type coroutine_creator:  Coroutine Function
        :param recurrency: recurrency rule to calculate next event
        :type recurrency: dateutil.rrule.rrule
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_recurrent_process_task(
            coroutine_creator=coroutine_creator,
            recurrency=recurrency,
            on_stop=on_stop,
            src=src,
        )

    def schedule_recurrent_task(
        self, coroutine_func, recurrency, on_stop=None, src=None
    ):
        """Schedule a task using a fine-grained recurrency rule in another process.

        :param coroutine_creator: coroutine function creating coros to be scheduled
        :type coroutine_creator:  Coroutine Function
        :param recurrency: recurrency rule to calculate next event
        :type recurrency: dateutil.rrule.rrule
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_recurrent_task(
            coroutine_func=coroutine_func,
            recurrency=recurrency,
            on_stop=on_stop,
            src=src,
        )

    def schedule_instant_process_task(self, coroutine_creator, on_stop=None, src=None):
        """Schedule an instantly executed task in another processes.

        :param coroutine_creator: coroutine_creator creating coroutine to be scheduled
        :type coroutine_creator:
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_instant_process_task(
            coroutine_creator=coroutine_creator, on_stop=on_stop, src=src
        )

    def schedule_instant_task(self, coroutine, on_stop=None, src=None):
        """Schedule an instantly executed task.

        :param coroutine: coroutine to be scheduled
        :type coroutine:
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_instant_task(
            coroutine=coroutine, on_stop=on_stop, src=src
        )

    def schedule_process_task(self, task: ScheduledProcessTask, src=None):
        """Schedule a task with asyncio in another process. When the task is finished, if finite, its automatically
        removed afterwards. For scheduling options see the subclasses of ScheduledScheduledProcessTaskTask.

        :param task: task to be scheduled
        :param src: object, which represents the source of the task (for example the object in which the task got created)
        """
        return self.scheduler.schedule_process_task(task, src=src)

    def schedule_task(self, task: ScheduledTask, src=None):
        """Schedule a task with asyncio. When the task is finished, if finite, its automatically
        removed afterwards. For scheduling options see the subclasses of ScheduledTask.

        :param task: task to be scheduled
        :param src: object, which represents the source of the task (for example the object in which the task got created)
        """
        return self.scheduler.schedule_task(task, src=src)

    async def tasks_complete(self, timeout=1):
        """Wait for all scheduled tasks to complete using a timeout.

        :param timeout: waiting timeout. Defaults to 1.
        """
        await self.scheduler.tasks_complete(timeout=timeout)

    def service_of_type(self, type: type, default: Any = None) -> Any:
        """Return the service of the type ``type`` or set the default as service and return it.

        :param type: the type of the service
        :type type: type
        :param default: the default if applicable
        :type default: Any (optional)
        :return: the service
        :rtype: Any
        """
        if type not in self._services:
            self._services[type] = default
        return self._services[type]

    def neighbors(self, state: State = State.NORMAL) -> list[AgentAddress]:
        """Return the neighbors of the agent (controlled by the topology api).

        :return: the list of agent addresses filtered by state
        :rtype: list[AgentAddress]
        """
        return self.service_of_type(TopologyService).neighbors(state)


class Agent(ABC, AgentDelegates):
    """Base class for all agents."""

    def __init__(
        self,
    ):
        """
        Initialize an agent
        """

        super().__init__()

        self.inbox = asyncio.Queue()

    @property
    def observable_tasks(self):
        return self.scheduler.observable

    @observable_tasks.setter
    def observable_tasks(self, value: bool):
        self.scheduler.observable = value

    @property
    def suspendable_tasks(self):
        return self.scheduler.suspendable

    @suspendable_tasks.setter
    def suspendable_tasks(self, value: bool):
        self.scheduler.suspendable = value

    def on_register(self):
        """
        Hook-in to define behavior of the agent directly after it got registered by a container
        """

    def _do_register(self, container, aid):
        self._aid = aid
        self.context = AgentContext(container)
        self.scheduler = Scheduler(
            suspendable=True, observable=True, clock=container.clock
        )
        self.on_register()

    def _do_start(self):
        self._check_inbox_task = asyncio.create_task(self._check_inbox())
        self._check_inbox_task.add_done_callback(self._raise_exceptions)
        self._stopped = asyncio.Future()

        self.on_start()

    def _raise_exceptions(self, fut: asyncio.Future):
        """
        Inline function used as a callback to raise exceptions
        :param fut: The Future object of the task
        """
        if fut.exception() is not None:
            logger.error(
                "Agent %s: Caught the following exception in _check_inbox: ",
                self.aid,
                fut.exception(),
            )
            raise fut.exception()

    async def _check_inbox(self):
        """Task for waiting on new message in the inbox"""

        try:
            logger.debug("Agent %s: Start waiting for messages", self.aid)
            while True:
                # run in infinite loop until it is cancelled from outside
                message = await self.inbox.get()
                logger.debug("Agent %s: Received message;%s", self.aid, message)

                # message should be tuples of (priority, content, meta)
                priority, content, meta = message
                meta["priority"] = priority

                self.handle_message(content=content, meta=meta)

                # signal to the Queue that the message is handled
                self.inbox.task_done()
        except Exception:
            logger.exception("The check inbox task of %s failed!", self.aid)

    def handle_message(self, content, meta: dict[str, Any]):
        """

        Has to be implemented by the user.
        This method is called when a message is received at the agents inbox.
        :param content: The deserialized message object
        :param meta: Meta details of the message. In case of mqtt this dict
        includes at least the field 'topic'
        """
        raise NotImplementedError

    async def shutdown(self):
        """Shutdown all tasks that are running
        and deregister from the container"""

        if not self._stopped.done():
            self._stopped.set_result(True)
        self.context.deregister(self.aid)
        try:
            # Shutdown reactive inbox task
            self._check_inbox_task.remove_done_callback(self._raise_exceptions)
            self._check_inbox_task.cancel()
            await self._check_inbox_task
        except asyncio.CancelledError:
            pass
        try:
            await self.scheduler.stop()
        except asyncio.CancelledError:
            pass
        try:
            await self.scheduler.shutdown()
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("Agent %s: Shutdown successful", self.aid)
