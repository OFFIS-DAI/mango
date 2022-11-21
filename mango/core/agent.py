"""
This module implements the base class for agents (:class:`Agent`).

Every agent must live in a container. Containers are responsible for making
 connections to other agents.
"""
import asyncio
import logging
import warnings
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Optional, Tuple, Union
from ..util.scheduling import ScheduledProcessTask, ScheduledTask, Scheduler

logger = logging.getLogger(__name__)


class Agent(ABC):
    """Base class for all agents."""

    def __init__(self, container, suggested_aid: str = None):
        """Initialize an agent and register it with its container
        :param container: The container that the agent lives in. Must be a Container
        :param suggested_aid: (Optional) suggested aid, if the aid is already taken, a generated aid is used. 
                              Using the generated aid-style ("agentX") is not allowed.
        """
        # if not isinstance(container, mango.core.container.Container):
        #     raise TypeError('"container" must be a "Container" instance but '
        #                     'is {}'.format(container))
        aid = container._register_agent(self, suggested_aid=suggested_aid)
        self._container = container
        self._aid = aid
        self.inbox = asyncio.Queue()
        self._scheduler = Scheduler(clock=container.clock)
        self._check_inbox_task = asyncio.create_task(self._check_inbox())
        self._check_inbox_task.add_done_callback(self.raise_exceptions)
        self.stopped = asyncio.Future()

        logger.info('Agent %s: start running in container %s', aid, container.addr)

    @property
    def current_timestamp(self) -> float:
        """
        Method that returns the current unix timestamp given the clock within the container
        """
        return self._container.clock.time

    async def send_message(self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        receiver_id: Optional[str] = None,
        **kwargs):
        """
        See container.send_message(...)
        """
        return await self._container.send_message(content, receiver_addr=receiver_addr, receiver_id=receiver_id, **kwargs)

    async def send_acl_message(self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        receiver_id: Optional[str] = None,
        acl_metadata: Optional[Dict[str, Any]] = None,
        **kwargs):
        """
        See container.send_acl_message(...)
        """
        return await self._container.send_acl_message(content, receiver_addr=receiver_addr, receiver_id=receiver_id, acl_metadata=acl_metadata, **kwargs)

    def schedule_instant_message(self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        receiver_id: Optional[str] = None,
        **kwargs):
        """
        Schedules sending a message without any delay. This is equivalent to using the schedulers 'schedule_instant_task' with the coroutine created by
        'container.send_message'.
        
        :param content: The content of the message
        :param receiver_addr: The address passed to the container
        :param receiver_id: The agent id of the receiver
        :param kwargs: Additional parameters to provide protocol specific settings 
        :returns: asyncio.Task for the scheduled coroutine
        """

        return self.schedule_instant_task(self.send_message(content, receiver_addr=receiver_addr, receiver_id=receiver_id, **kwargs))


    def schedule_instant_acl_message(self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        receiver_id: Optional[str] = None,
        acl_metadata: Optional[Dict[str, Any]] = None,
        **kwargs):
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

        return self.schedule_instant_task(self.send_acl_message(content, receiver_addr=receiver_addr, receiver_id=receiver_id, acl_metadata=acl_metadata, **kwargs))

    def schedule_conditional_process_task(self, coroutine_creator, condition_func, lookup_delay=0.1, src=None):
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
            coroutine_creator=coroutine_creator, condition_func=condition_func, lookup_delay=lookup_delay, src=src)

    def schedule_conditional_task(self, coroutine, condition_func, lookup_delay=0.1, src=None):
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
        return self._scheduler.schedule_conditional_task(coroutine=coroutine, condition_func=condition_func,
                                                         lookup_delay=lookup_delay, src=src)

    def schedule_datetime_process_task(self, coroutine_creator, date_time: datetime, src=None):
        """Schedule a task at specified datetime in another process.

        :param coroutine_creator: coroutine_creator creating couroutine to be scheduled
        :type coroutine_creator: Coroutine-creator
        :param date_time: datetime defining when the task should start
        :type date_time: datetime
        :param src: creator of the task
        :type src: Object
        """
        return self._scheduler.schedule_datetime_process_task(coroutine_creator=coroutine_creator,
                                                              date_time=date_time, src=src)

    def schedule_datetime_task(self, coroutine, date_time: datetime, src=None):
        """Schedule a task at specified datetime.

        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param date_time: datetime defining when the task should start
        :type date_time: datetime
        :param src: creator of the task
        :type src: Object
        """
        return self._scheduler.schedule_datetime_task(coroutine=coroutine, date_time=date_time, src=src)

    def schedule_timestamp_task(self, coroutine, timestamp: float, src=None):
        """Schedule a task at specified timestamp.

        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param timestamp: timestamp defining when the task should start
        :type timestamp: timestamp
        :param src: creator of the task
        :type src: Object
        """
        return self._scheduler.schedule_timestamp_task(coroutine=coroutine, timestamp=timestamp, src=src)

    def schedule_timestamp_process_task(self, coroutine_creator, timestamp: float, src=None):
        """Schedule a task at specified datetime dispatched to another process.

        :param coroutine_creator: coroutine_creator creating coroutine to be scheduled
        :type coroutine_creator: coroutine_creator
        :param timestamp: unix timestamp defining when the task should start
        :type timestamp: float
        :param src: creator of the task
        :type src: Object
        """
        return self._scheduler.schedule_timestamp_process_task(
            coroutine_creator=coroutine_creator, timestamp=timestamp, src=src)

    def schedule_periodic_process_task(self, coroutine_creator, delay, src = None):
        """Schedule an open end periodically executed task in another process.

        :param coroutine_creator: coroutine function creating coros to be scheduled
        :type coroutine_creator:  Coroutine Function
        :param delay: delay in between the cycles
        :type dealy: float
        :param src: creator of the task
        :type src: Object
        """
        return self._scheduler.schedule_periodic_process_task(coroutine_creator=coroutine_creator, delay=delay, src=src)

    def schedule_periodic_task(self, coroutine_func, delay, src = None):
        """Schedule an open end peridocally executed task.

        :param coroutine_func: coroutine function creating coros to be scheduled
        :type coroutine_func:  Coroutine Function
        :param delay: delay in between the cycles
        :type dealy: float
        :param src: creator of the task
        :type src: Object
        """
        return self._scheduler.schedule_periodic_task(coroutine_func=coroutine_func, delay=delay, src=src)

    def schedule_instant_process_task(self, coroutine_creator, src = None):
        """Schedule an instantly executed task in another processes.

        :param coroutine_creator: coroutine_creator creating coroutine to be scheduled
        :type coroutine_creator: 
        :param src: creator of the task
        :type src: Object
        """
        return self._scheduler.schedule_instant_process_task(coroutine_creator=coroutine_creator, src=src)

    def schedule_instant_task(self, coroutine, src=None):
        """Schedule an instantly executed task.

        :param coroutine: coroutine to be scheduled
        :type coroutine: 
        :param src: creator of the task
        :type src: Object
        """
        return self._scheduler.schedule_instant_task(coroutine=coroutine, src=src)

    def schedule_process_task(self, task: ScheduledProcessTask, src = None):
        """Schedule a task with asyncio in another process. When the task is finished, if finite, its automatically
        removed afterwards. For scheduling options see the subclasses of ScheduledScheduledProcessTaskTask.

        :param task: task to be scheduled
        :param src: object, which represents the source of the task (for example the object in which the task got created)
        """
        return self._scheduler.schedule_process_task(task, src=src)

    def schedule_task(self, task: ScheduledTask, src = None):
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

    def raise_exceptions(self, fut: asyncio.Future):
        """
        Inline function used as a callback to raise exceptions
        :param fut: The Future object of the task
        """
        if fut.exception() is not None:
            logger.error('Agent %s: Caught the following exception in _check_inbox: %s', self.aid, fut.exception())
            raise fut.exception()

    @property
    def aid(self):
        """Return the agents ID"""
        return self._aid

    async def _check_inbox(self):
        """Task for waiting on new message in the inbox"""

        logger.debug('Agent %s: Start waiting for messages', self.aid)
        while True:
            # run in infinite loop until it is cancelled from outside
            message = await self.inbox.get()
            logger.debug('Agent %s: Received message;%s}', self.aid, str(message))

            # message should be tuples of (priority, content, meta)
            priority, content, meta = message
            meta['priority'] = priority
            try:
                self.handle_message(content=content, meta=meta)
            except NotImplementedError:
                self.handle_msg(content=content, meta=meta)
                warnings.warn("The function handle_msg is renamed and is now called handle_message."
                              "The use of handle_msg will be removed in the next release.", DeprecationWarning)

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

    def handle_msg(self, content, meta: Dict[str, Any]):
        """
        .. deprecated:: 0.4.0
            Use 'agent.handle_message' instead. In the next version this method
            will be dropped entirely, and it will be mandatory to overwrite handle_message.

        This method is called when a message is received.
        This is a blocking call, if non-blocking message handling is desired,
        one should call asyncio.create_task() in order to handle more than
        one message at a time
        :param content: The deserialized message object
        :param meta: Meta details of the message. In case of mqtt this dict
        includes at least the field 'topic'
        """
        raise NotImplementedError

    async def shutdown(self):
        """Shutdown all tasks that are running
         and deregister from the container"""

        if not self.stopped.done():
            self.stopped.set_result(True)
        if self._container.running:
            self._container.deregister_agent(self._aid)
        try:
            # Shutdown reactive inbox task
            self._check_inbox_task.remove_done_callback(self.raise_exceptions)
            self._check_inbox_task.cancel()
            await self._check_inbox_task

            await self._scheduler.stop()
        except asyncio.CancelledError:
            pass
        finally:
            logger.info('Agent %s: Shutdown successful', self.aid)
