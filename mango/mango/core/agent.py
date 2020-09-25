"""
This module implements the base class for agents (:class:`Agent`).

Every agent must live in a container. Containers are responsible for making
 connections to other agents.
"""
import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict

from ..util import m_util as ut


class Agent(ABC):
    """Base class for all agents."""

    def __init__(self, container):
        """Initialize an agent and register it with its container
        :param container:
        """
        # if not isinstance(container, Container):
        #     raise TypeError('"container" must be a "Container" instance but '
        #                     'is {}'.format(container))
        aid = container.register_agent(self)
        self._container = container
        self._aid = aid

        # get customized logger
        self.agent_logger = ut.configure_logger(f'{self._aid}',
                                                self._container.log_level)

        self.inbox = asyncio.Queue()

        self._check_inbox_task = asyncio.create_task(self._check_inbox())

        self._check_inbox_task.add_done_callback(self.raise_exceptions)

        self.stopped = asyncio.Future()
        self.agent_logger.debug('Start running')

    def raise_exceptions(self, result):
        """
        Inline function used as a callback to raise exceptions
        :param result: result of the task
        """
        exception = result.exception()
        if exception is not None:
            self.agent_logger.info('Caught exception in _check_inbox.')
            raise exception

    @property
    def aid(self):
        """Return the agents ID"""
        return self._aid

    async def _check_inbox(self):
        """Task for waiting on new message in the inbox"""

        # self.agent_logger.debug('Start waiting for msgs')
        while True:
            # run in infinite loop until it is cancelled from outside
            msg = await self.inbox.get()
            self.agent_logger.debug(f'Received {msg}.')

            # msgs should be tuples of (priority, content)
            priority, content, meta = msg
            meta['priority'] = priority
            self.handle_msg(content=content, meta=meta)

            # signal to the Queue that the message is handled
            self.inbox.task_done()

    @abstractmethod
    def handle_msg(self, content, meta: Dict[str, Any]):
        """

        Has to be implemented by the user.
        This method is called when a message is received.
        The message with the lowest priority number
        in the que is handled first.
        This is a blocking call, if non-blocking message handling is desired,
        one should call asyncio.create_task() in order to handle more than
        one message at a time
        :param content: The deserialized message object
        :param meta: Meta details of the msg. In case of mqtt this dict
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
            self._check_inbox_task.remove_done_callback(self.raise_exceptions)
            self._check_inbox_task.cancel()
            await self._check_inbox_task
        except asyncio.CancelledError:
            pass
        finally:
            self.agent_logger.info('Have successfully shutdown.')
