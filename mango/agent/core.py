"""
This module implements the base class for agents (:class:`Agent`).

Every agent must live in a container. Containers are responsible for making
 connections to other agents.
"""
import asyncio
import logging
from abc import ABC
from datetime import datetime
from typing import Any, Dict, Optional, Tuple, Union

from ..container.core import Container
from ..util.scheduling import ScheduledProcessTask, ScheduledTask, Scheduler

logger = logging.getLogger(__name__)


class AgentContext:
    def __init__(self, container) -> None:
        self._container: Container = container

    @property
    def current_timestamp(self) -> float:
        """
        Method that returns the current unix timestamp given the clock within the container
        """
        return self._container.clock.time

    @property
    def addr(self):
        return self._container.addr

    def register_agent(self, agent, suggested_aid):
        return self._container.register_agent(agent, suggested_aid=suggested_aid)

    def deregister_agent(self, aid):
        if self._container.running:
            self._container.deregister_agent(aid)

    async def send_message(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        receiver_id: Optional[str] = None,
        **kwargs,
    ):
        """
        See container.send_message(...)
        """
        return await self._container.send_message(
            content, receiver_addr=receiver_addr, receiver_id=receiver_id, **kwargs
        )

    async def send_acl_message(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        receiver_id: Optional[str] = None,
        acl_metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        See container.send_acl_message(...)
        """
        return await self._container.send_acl_message(
            content,
            receiver_addr=receiver_addr,
            receiver_id=receiver_id,
            acl_metadata=acl_metadata,
            **kwargs,
        )


class AgentDelegates:
    def __init__(self, context, scheduler) -> None:
        self._context: AgentContext = context
        self._scheduler: Scheduler = scheduler

    async def send_message(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        receiver_id: Optional[str] = None,
        **kwargs,
    ):
        """
        See container.send_message(...)
        """
        return await self._context.send_message(
            content, receiver_addr=receiver_addr, receiver_id=receiver_id, **kwargs
        )

    async def send_acl_message(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        receiver_id: Optional[str] = None,
        acl_metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        See container.send_acl_message(...)
        """
        return await self._context.send_acl_message(
            content,
            receiver_addr=receiver_addr,
            receiver_id=receiver_id,
            acl_metadata=acl_metadata,
            **kwargs,
        )

    def schedule_instant_message(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        receiver_id: Optional[str] = None,
        **kwargs,
    ):
        """
        Schedules sending a message without any delay. This is equivalent to using the schedulers 'schedule_instant_task' with the coroutine created by
        'container.send_message'.

        :param content: The content of the message
        :param receiver_addr: The address passed to the container
        :param receiver_id: The agent id of the receiver
        :param kwargs: Additional parameters to provide protocol specific settings
        :returns: asyncio.Task for the scheduled coroutine
        """

        return self.schedule_instant_task(
            self.send_message(
                content, receiver_addr=receiver_addr, receiver_id=receiver_id, **kwargs
            )
        )

    def schedule_instant_acl_message(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        receiver_id: Optional[str] = None,
        acl_metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        Schedules sending an acl message without any delay. This is equivalent to using the schedulers 'schedule_instant_task' with the coroutine created by
        'container.send_acl_message'.

        :param content: The content of the message
        :param receiver_addr: The address passed to the container
        :param receiver_id: The agent id of the receiver
        :param acl_metadata: Metadata for the acl message
        :param kwargs: Additional parameters to provide protocol specific settings
        :returns: asyncio.Task for the scheduled coroutine
        """

        return self.schedule_instant_task(
            self.send_acl_message(
                content,
                receiver_addr=receiver_addr,
                receiver_id=receiver_id,
                acl_metadata=acl_metadata,
                **kwargs,
            )
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
        return self._scheduler.schedule_conditional_process_task(
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
        return self._scheduler.schedule_conditional_task(
            coroutine=coroutine,
            condition_func=condition_func,
            lookup_delay=lookup_delay,
            on_stop=on_stop,
            src=src,
        )

    def schedule_datetime_process_task(
        self, coroutine_creator, date_time: datetime, on_stop=None, src=None
    ):
        """Schedule a task at specified datetime in another process.

        :param coroutine_creator: coroutine_creator creating couroutine to be scheduled
        :type coroutine_creator: Coroutine-creator
        :param date_time: datetime defining when the task should start
        :type date_time: datetime
        :param src: creator of the task
        :type src: Object
        """
        return self._scheduler.schedule_datetime_process_task(
            coroutine_creator=coroutine_creator,
            date_time=date_time,
            on_stop=on_stop,
            src=src,
        )

    def schedule_datetime_task(
        self, coroutine, date_time: datetime, on_stop=None, src=None
    ):
        """Schedule a task at specified datetime.

        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param date_time: datetime defining when the task should start
        :type date_time: datetime
        :param src: creator of the task
        :type src: Object
        """
        return self._scheduler.schedule_datetime_task(
            coroutine=coroutine, date_time=date_time, on_stop=on_stop, src=src
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
        return self._scheduler.schedule_timestamp_task(
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
        return self._scheduler.schedule_timestamp_process_task(
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
        :param src: creator of the task
        :type src: Object
        """
        return self._scheduler.schedule_periodic_process_task(
            coroutine_creator=coroutine_creator, delay=delay, on_stop=on_stop, src=src
        )

    def schedule_periodic_task(self, coroutine_func, delay, on_stop=None, src=None):
        """Schedule an open end peridocally executed task.

        :param coroutine_func: coroutine function creating coros to be scheduled
        :type coroutine_func:  Coroutine Function
        :param delay: delay in between the cycles
        :type delay: float
        :param src: creator of the task
        :type src: Object
        """
        return self._scheduler.schedule_periodic_task(
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
        :param src: creator of the task
        :type src: Object
        """
        return self._scheduler.schedule_recurrent_process_task(
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
        :param src: creator of the task
        :type src: Object
        """
        return self._scheduler.schedule_recurrent_task(
            coroutine_func=coroutine_func,
            recurrency=recurrency,
            on_stop=on_stop,
            src=src,
        )

    def schedule_instant_process_task(self, coroutine_creator, on_stop=None, src=None):
        """Schedule an instantly executed task in another processes.

        :param coroutine_creator: coroutine_creator creating coroutine to be scheduled
        :type coroutine_creator:
        :param src: creator of the task
        :type src: Object
        """
        return self._scheduler.schedule_instant_process_task(
            coroutine_creator=coroutine_creator, on_stop=on_stop, src=src
        )

    def schedule_instant_task(self, coroutine, on_stop=None, src=None):
        """Schedule an instantly executed task.

        :param coroutine: coroutine to be scheduled
        :type coroutine:
        :param src: creator of the task
        :type src: Object
        """
        return self._scheduler.schedule_instant_task(
            coroutine=coroutine, on_stop=on_stop, src=src
        )

    def schedule_process_task(self, task: ScheduledProcessTask, src=None):
        """Schedule a task with asyncio in another process. When the task is finished, if finite, its automatically
        removed afterwards. For scheduling options see the subclasses of ScheduledScheduledProcessTaskTask.

        :param task: task to be scheduled
        :param src: object, which represents the source of the task (for example the object in which the task got created)
        """
        return self._scheduler.schedule_process_task(task, src=src)

    def schedule_task(self, task: ScheduledTask, src=None):
        """Schedule a task with asyncio. When the task is finished, if finite, its automatically
        removed afterwards. For scheduling options see the subclasses of ScheduledTask.

        :param task: task to be scheduled
        :param src: object, which represents the source of the task (for example the object in which the task got created)
        """
        return self._scheduler.schedule_task(task, src=src)

    async def tasks_complete(self, timeout=1):
        """Wait for all scheduled tasks to complete using a timeout.

        :param timeout: waiting timeout. Defaults to 1.
        """
        await self._scheduler.tasks_complete(timeout=timeout)


class Agent(ABC, AgentDelegates):
    """Base class for all agents."""

    def __init__(self, container: Container, suggested_aid: str = None):
        """Initialize an agent and register it with its container
        :param container: The container that the agent lives in. Must be a Container
        :param suggested_aid: (Optional) suggested aid, if the aid is already taken, a generated aid is used.
                              Using the generated aid-style ("agentX") is not allowed.
        """
        scheduler = Scheduler(clock=container.clock)
        context = AgentContext(container)
        self.aid = context.register_agent(self, suggested_aid)
        self.inbox = asyncio.Queue()

        super().__init__(context, scheduler)

        self._check_inbox_task = asyncio.create_task(self._check_inbox())
        self._check_inbox_task.add_done_callback(self.raise_exceptions)
        self._stopped = asyncio.Future()

        logger.info(f"Agent {self.aid}: start running in container {container.addr}")

    def raise_exceptions(self, fut: asyncio.Future):
        """
        Inline function used as a callback to raise exceptions
        :param fut: The Future object of the task
        """
        if fut.exception() is not None:
            logger.error(
                f"Agent {self.aid}: Caught the following exception in _check_inbox: {fut.exception()}"
            )
            raise fut.exception()

    async def _check_inbox(self):
        """Task for waiting on new message in the inbox"""

        logger.debug(f"Agent {self.aid}: Start waiting for messages")
        while True:
            # run in infinite loop until it is cancelled from outside
            message = await self.inbox.get()
            logger.debug(f"Agent {self.aid}: Received message;{message}")

            # message should be tuples of (priority, content, meta)
            priority, content, meta = message
            meta["priority"] = priority
            
            self.handle_message(content=content, meta=meta)

            # signal to the Queue that the message is handled
            self.inbox.task_done()

    def handle_message(self, content, meta: Dict[str, Any]):
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
        self._context.deregister_agent(self.aid)
        try:
            # Shutdown reactive inbox task
            self._check_inbox_task.remove_done_callback(self.raise_exceptions)
            self._check_inbox_task.cancel()
            await self._check_inbox_task

            await self._scheduler.stop()
        except asyncio.CancelledError:
            pass
        finally:
            logger.info(f"Agent {self.aid}: Shutdown successful")
