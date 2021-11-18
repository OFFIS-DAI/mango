"""
Module for commonly used time based scheduled task executed inside one agent.
"""
from abc import abstractmethod
import asyncio
import datetime

class Suspendable:
    """Wraps a coroutine, intercepting __await__ to add the functionality of suspending.
    """
    def __init__(self, coro):
        self._coro = coro
        self._can_run = asyncio.Event()
        self._can_run.set()

    def __await__(self):
        coro_iter = self._coro.__await__()
        iter_send, iter_throw = coro_iter.send, coro_iter.throw
        send, message = iter_send, None
        while True:
            try:
                while not self._can_run.is_set():
                    # essentially same as 'await self._can_run.wait()', not allowed here as this is not an async method
                    yield from self._can_run.wait().__await__()
            except BaseException as err:
                send, message = iter_throw, err

            try:
                # throw error or resume coroutine
                signal = send(message)
            except StopIteration as err:
                return err.value
            else:
                send = iter_send
            try:
                # pass signal via yielding it
                message = yield signal
            except BaseException as err:
                send, message = iter_throw, err

    def suspend(self):
        """Suspend the wrapped coroutine (have to executed as task externally)
        """
        self._can_run.clear()

    def is_suspended(self):
        """Return whether the coro is suspended

        :return: True if suspendend, False otherwise
        :rtype: bool
        """
        return not self._can_run.is_set()

    def resume(self):
        """Resume the coroutine
        """
        self._can_run.set()

    @property
    def coro(self):
        """Return the coroutine

        :return: the coroutine
        :rtype: a coroutine
        """
        return self._coro

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

    def schedule_conditional_task(self, coroutine, condition_func, lookup_delay=0.1, src = None):
        """Schedule a task when a specified condition is met.

        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param condition_func: function for determining whether the confition is fullfiled
        :type confition_func: lambda () -> bool
        :param lookup_delay: delay between checking the condition
        :type lookup_delay: float
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_task(ConditionalTask(coroutine=coroutine, condition_func=condition_func, lookup_delay=lookup_delay), src=src)

    def schedule_datetime_task(self, coroutine, date_time: datetime, src = None):
        """Schedule a task at specified datetime.

        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param date_time: datetime defining when the task should start
        :type date_time: datetime
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_task(DateTimeScheduledTask(coroutine=coroutine, date_time=date_time), src=src)

    def schedule_periodic_task(self, coroutine_func, delay, src = None):
        """Schedule an open end peridocally executed task.

        :param coroutine_func: coroutine function creating coros to be scheduled
        :type coroutine_func:  Coroutine Function
        :param delay: delay in between the cycles
        :type dealy: float
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_task(PeriodicScheduledTask(coroutine_func=coroutine_func, delay=delay), src=src)

    def schedule_instant_task(self, coroutine, src = None):
        """Schedule an instantly executed task.

        :param coroutine: coroutine to be scheduled
        :type coroutine: 
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_task(InstantScheduledTask(coroutine=coroutine), src=src)

    def schedule_task(self, task: ScheduledTask, src = None):
        """Schedule a task with asyncio. When the task is finished, if finite, its automatically
        removed afterwards. For scheduling options see the subclasses of ScheduledTask.

        :param task: task to be scheduled
        :type task: ScheduledTask
        :param src: creator of the task
        :type: Object
        """
        susp_coro = Suspendable(task.run())
        l_task = asyncio.ensure_future(susp_coro)
        l_task.add_done_callback(task.on_stop)
        l_task.add_done_callback(self._remove_task)
        self._scheduled_tasks.append((l_task, susp_coro, src))
        return l_task

    def suspend(self, given_src):
        """Suspend a set of tasks triggered by the given src object.

        :param given_src: the src object
        :type given_src: object
        """
        for _, coro, src in self._scheduled_tasks:
            if src == given_src:
                coro.suspend()

    def resume(self, given_src):
        """Resume a set of tasks triggered by the given src object.

        :param given_src: the src object
        :type given_src: object
        """
        for _, coro, src in self._scheduled_tasks:
            if src == given_src:
                coro.resume()

    def _remove_task(self, fut=asyncio.Future):
        for i in range(len(self._scheduled_tasks)): 
            task, _, _ = self._scheduled_tasks[i]
            if task == fut:
                del self._scheduled_tasks[i]
                break

    async def stop(self):
        """Cancel all not finished scheduled tasks
        """
        # Cancel all not finished time scheduled tasks
        for task, _, _ in self._scheduled_tasks:
            task.cancel()
            await task

    async def tasks_complete(self, timeout=1):
        """Finish all pending tasks using a timeout.

        Args:
            timeout (int, optional): waiting timeout. Defaults to 1.
        """
        for task, _, _ in self._scheduled_tasks:
            await asyncio.wait_for(task, timeout=timeout)
