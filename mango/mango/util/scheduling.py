"""
Module for commonly used time based scheduled task executed inside one agent.
"""
from abc import abstractmethod
import asyncio
import datetime


class ScheduledTask:
    """Base class for scheduled tasks in mango. Within this class its possible to
    define what to do on exceution and on stop. In most cases the logic should get
    passed as lambda while the scheduling logic is inside of class inherting from this one.
    """

    def __init__(self) -> None:
        pass

    @abstractmethod
    async def run(self):
        """Called via asyncio as asyncio.task.

        Raises:
            NotImplementedError: must be overriden
        """
        raise NotImplementedError

    def on_stop(self, fut: asyncio.Future = None):
        """Called when the task is cancelled of finished.
        """


class PeriodicScheduledTask(ScheduledTask):
    """Class for periodic scheduled tasks. It enables to create a scheduable task for an agent
    which will get executed periodically with a specified delay.
    """

    def __init__(self, coroutine_func, delay):
        super().__init__()

        self._stopped = False
        self._coroutine_func = coroutine_func
        self._delay = delay

    async def run(self):
        while not self._stopped:
            await self._coroutine_func()
            await asyncio.sleep(self._delay)


class DateTimeScheduledTask(ScheduledTask):
    """DateTime based one-shot task. This task will get executed using a given datetime-object.
    """

    def __init__(self, coroutine, date_time: datetime):
        super().__init__()

        self._delay = date_time
        self._coro = coroutine

    async def _wait(self, date_time: datetime):
        await asyncio.sleep((date_time - datetime.datetime.now()).total_seconds())

    async def run(self):
        await self._wait(self._delay)
        return await self._coro


class InstantScheduledTask(DateTimeScheduledTask):
    """One-shot task, which will get executed instantly.
    """

    def __init__(self, coroutine):
        super().__init__(coroutine, datetime.datetime.now())

class ConditionalTask(ScheduledTask):
    """Task which will get executed as soon as the given condition is fullfiled.
    """
    def __init__(self, coroutine, condition_func, lookup_delay=0.1):
        super().__init__()

        self._condition = condition_func
        self._coro = coroutine
        self._delay = lookup_delay

    async def _wait(self, date_time: datetime):
        await asyncio.sleep((date_time - datetime.datetime.now()).total_seconds())

    async def run(self):
        while not self._condition():
            await asyncio.sleep(self._delay)

        return await self._coro

class Scheduler:
    """Scheduler for executing tasks.
    """

    def __init__(self):
        self._scheduled_tasks = []

    def schedule_task(self, task: ScheduledTask):
        """Schedule a task with asyncio. When the task is finished, if finite, its automatically
        removed afterwards. For scheduling options see the subclasses of ScheduledTask.

        Args:
            task (ScheduledTask): task to be scheduled
        """
        l_task = asyncio.create_task(task.run())
        l_task.add_done_callback(task.on_stop)
        l_task.add_done_callback(self._remove_task)
        self._scheduled_tasks.append(l_task)
        return l_task

    def _remove_task(self, fut=asyncio.Future):
        self._scheduled_tasks.remove(fut)

    async def stop(self):
        """Cancel all not finished scheduled tasks
        """
        # Cancel all not finished time scheduled tasks
        for task in self._scheduled_tasks:
            task.cancel()
            await task

    async def tasks_complete(self, timeout=1):
        """Finish all pending tasks using a timeout.

        Args:
            timeout (int, optional): waiting timeout. Defaults to 1.
        """
        for task in self._scheduled_tasks:
            await asyncio.wait_for(task, timeout=timeout)
