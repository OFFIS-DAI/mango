"""
Module for commonly used time based scheduled task executed inside one agent.
"""
from abc import abstractmethod
from typing import List, Tuple, Any
import asyncio
import datetime
import concurrent.futures
import warnings
from multiprocessing import Manager

from mango.util.clock import Clock, AsyncioClock, ExternalClock


class Suspendable:
    """
    Wraps a coroutine, intercepting __await__ to add the functionality of suspending.
    """

    def __init__(self, coro, ext_contr_event=None):
        self._coro = coro

        if ext_contr_event is not None:
            self._can_run = ext_contr_event
        else:
            self._can_run = asyncio.Event()
            self._can_run.set()

    def __await__(self):
        coro_iter = self._coro.__await__()
        iter_send, iter_throw = coro_iter.send, coro_iter.throw
        send, message = iter_send, None
        while True:
            try:
                while not self._can_run.is_set():
                    if isinstance(self._can_run, asyncio.Event):
                        # essentially same as 'await self._can_run.wait()',
                        # not allowed here as this is not an async method
                        yield from self._can_run.wait().__await__()
                    else:
                        self._can_run.wait()
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
        """
        Suspend the wrapped coroutine (have to executed as task externally)
        """
        self._can_run.clear()

    def is_suspended(self):
        """
        Return whether the coro is suspended

        :return: True if suspended, False otherwise
        :rtype: bool
        """
        return not self._can_run.is_set()

    def resume(self):
        """
        Resume the coroutine
        """
        self._can_run.set()

    @property
    def coro(self):
        """
        Return the coroutine

        :return: the coroutine
        :rtype: a coroutine
        """
        return self._coro


# asyncio tasks


class ScheduledTask:
    """
    Base class for scheduled tasks in mango. Within this class it is possible to
    define what to do on execution and on stop. In most cases the logic should get
    passed as lambda while the scheduling logic is inside of class inheriting from this one.
    """

    def __init__(self, clock: Clock = None) -> None:
        self.clock = clock if clock is not None else AsyncioClock()

    @abstractmethod
    async def run(self):
        """Called via asyncio as asyncio.task.

        Raises:
            NotImplementedError: must be overwritten
        """
        raise NotImplementedError

    def on_stop(self, fut: asyncio.Future = None):
        """
        Called when the task is cancelled of finished.
        """


class TimestampScheduledTask(ScheduledTask):
    """
    Timestamp based one-shot task. This task will get executed when a given unix timestamp is reached.
    """

    def __init__(self, coroutine, timestamp: float, clock=None):
        super().__init__(clock)
        self._timestamp = timestamp
        self._coro = coroutine

    async def _wait(self, timestamp: float):
        await self.clock.sleep(timestamp - self.clock.time)

    async def run(self):
        await self._wait(self._timestamp)
        return await self._coro


class InstantScheduledTask(TimestampScheduledTask):
    """
    One-shot task, which will get executed instantly.
    """

    def __init__(self, coroutine, clock: Clock = None):
        if clock is None:
            clock = AsyncioClock()
        super().__init__(coroutine, clock.time, clock=clock)


class PeriodicScheduledTask(ScheduledTask):
    """
    Class for periodic scheduled tasks. It enables to create a task for an agent
    which will get executed periodically with a specified delay.
    """

    def __init__(self, coroutine_func, delay, clock: Clock = None):
        super().__init__(clock)

        self._stopped = False
        self._coroutine_func = coroutine_func
        self._delay = delay

    async def run(self):
        while not self._stopped:
            await self._coroutine_func()
            await self.clock.sleep(self._delay)


class ConditionalTask(ScheduledTask):
    """Task which will get executed as soon as the given condition is fulfilled.
    """

    def __init__(self, coroutine, condition_func, lookup_delay=0.1, clock: Clock = None):
        super().__init__(clock=clock)

        self._condition = condition_func
        self._coro = coroutine
        self._delay = lookup_delay

    async def run(self):
        while not self._condition():
            await self.clock.sleep(self._delay)
        return await self._coro


class DateTimeScheduledTask(ScheduledTask):
    """
    DateTime based one-shot task. This task will get executed using a given datetime-object.
    """

    def __init__(self, coroutine, date_time: datetime.datetime, clock=None):
        super().__init__(clock)
        warnings.warn('DateTimeScheduleTask is deprecated. Use TimestampScheduledTask instead.', DeprecationWarning)
        self._datetime = date_time
        self._coro = coroutine

    async def _wait(self, date_time: datetime.datetime):
        await self.clock.sleep(date_time.timestamp() - self.clock.time)

    async def run(self):
        await self._wait(self._datetime)
        return await self._coro


# process tasks


class ScheduledProcessTask(ScheduledTask):
    # Mark class as task for an external process

    """
    This alt-name marks a ScheduledTask as process compatible.
    This is necessary due to the fact that not everything can be transferred to other processes i.e. coroutines are
    bound to the current event-loop resp the current thread, so they won't work in other processes.
    Furthermore, when using a ProcessTask you have to ensure, that the coroutine functions should not be bound to
    complex objects, meaning they should be static or bound to simple objects, which are transferable
    via pythons IPC implementation.
    """

    def __init__(self, clock: Clock):
        if isinstance(clock, ExternalClock):
            raise ValueError('Process Tasks do currently not work with external clocks')
        super().__init__(clock=clock)


class TimestampScheduledProcessTask(TimestampScheduledTask, ScheduledProcessTask):
    """
    Timestamp based one-shot task.
    """

    def __init__(self, coroutine_creator, timestamp: float, clock=None):
        super().__init__(coroutine_creator, timestamp, clock)

    async def run(self):
        await self._wait(self._timestamp)
        return await self._coro()


class InstantScheduledProcessTask(TimestampScheduledProcessTask):
    """One-shot task, which will get executed instantly.
    """

    def __init__(self, coroutine_creator, clock: Clock = None):
        if clock is None:
            clock = AsyncioClock()
        super().__init__(coroutine_creator, timestamp=clock.time, clock=clock)


class PeriodicScheduledProcessTask(PeriodicScheduledTask, ScheduledProcessTask):
    def __init__(self, coroutine_func, delay, clock: Clock = None):
        super().__init__(coroutine_func, delay, clock)


class ConditionalProcessTask(ConditionalTask, ScheduledProcessTask):
    """
    Task which will get executed as soon as the given condition is fulfilled.
    """

    def __init__(self, coro_func, condition_func, lookup_delay=0.1, clock: Clock = None):
        super().__init__(coro_func, condition_func, lookup_delay, clock=clock)

    async def run(self):
        while not self._condition():
            await self.clock.sleep(self._delay)
        return await self._coro()


class DateTimeScheduledProcessTask(DateTimeScheduledTask, ScheduledProcessTask):
    """
    DateTime based one-shot task. This task will get executed using a given datetime-object.
    """

    def __init__(self, coroutine_creator, date_time: datetime.datetime, clock=None):
        super().__init__(coroutine_creator, date_time, clock)

    async def run(self):
        await self._wait(self._datetime)
        return await self._coro()


def _create_asyncio_context():
    asyncio.set_event_loop(asyncio.new_event_loop())


class Scheduler:
    """Scheduler for executing tasks.
    """

    def __init__(self, clock: Clock = None, num_process_parallel=16):
        # List of Tuples with asyncio.Future, Suspendable coro, Source
        self._scheduled_tasks: List[Tuple[asyncio.Future, Suspendable, Any]] = []
        self.clock = clock if clock is not None else AsyncioClock()
        self._scheduled_process_tasks = []
        self._process_pool_exec = concurrent.futures.ProcessPoolExecutor(max_workers=num_process_parallel,
                                                                         initializer=_create_asyncio_context)

    @staticmethod
    def _run_task_in_p_context(task, suspend_event):
        try:
            coro = Suspendable(task.run(), ext_contr_event=suspend_event)

            return asyncio.get_event_loop().run_until_complete(coro)
        finally:
            task.on_stop(coro)

    async def sleep(self, t: float):
        """
        :param t: The time to sleep [s]
        """
        return await self.clock.sleep(t)

    # conv methods for asyncio Tasks

    def schedule_task(self, task: ScheduledTask, src=None) -> asyncio.Task:
        """
        Schedule a task with asyncio. When the task is finished, if finite, its automatically
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

    def schedule_timestamp_task(self, coroutine, timestamp: float, src=None):
        """Schedule a task at specified datetime.

        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param timestamp: timestamp defining when the task should start (unix timestamp)
        :type timestamp: float
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_task(TimestampScheduledTask(coroutine=coroutine, timestamp=timestamp, clock=self.clock),
                                  src=src)

    def schedule_instant_task(self, coroutine, src=None):
        """Schedule an instantly executed task.

        :param coroutine: coroutine to be scheduled
        :type coroutine:
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_task(InstantScheduledTask(coroutine=coroutine, clock=self.clock), src=src)

    def schedule_periodic_task(self, coroutine_func, delay, src=None):
        """
        Schedule an open end periodically executed task.
        :param coroutine_func: coroutine function creating coros to be scheduled
        :type coroutine_func:  Coroutine Function
        :param delay: delay in between the cycles
        :type delay: float
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_task(PeriodicScheduledTask(coroutine_func=coroutine_func, delay=delay, clock=self.clock),
                                  src=src)

    def schedule_conditional_task(self, coroutine, condition_func, lookup_delay: float = 0.1, src=None):
        """
        Schedule a task when a specified condition is met.
        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param condition_func: function for determining whether the condition is fulfilled
        :type condition_func: lambda () -> bool
        :param lookup_delay: delay between checking the condition [s]
        :type lookup_delay: float
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_task(ConditionalTask(coroutine=coroutine, condition_func=condition_func, clock=self.clock,
                                                  lookup_delay=lookup_delay), src=src)

    def schedule_datetime_task(self, coroutine, date_time: datetime.datetime, src=None):
        """Schedule a task at specified datetime.

        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param date_time: datetime defining when the task should start
        :type date_time: datetime
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_task(DateTimeScheduledTask(coroutine=coroutine, date_time=date_time, clock=self.clock),
                                  src=src)

    # conv. methods for process tasks

    def schedule_process_task(self, task: ScheduledProcessTask, src=None):
        """
        Schedule as task with asyncio in a different process managed by a ProcessWorkerPool in this Scheduler-object.
        For scheduling options see the subclasses of ScheduledProcessTask.

        :param task: task you want to schedule
        :type task: ScheduledProcessTask
        :return: future to check whether the task is done and to finally retrieve the result
        :rtype: _type_
        :param src: creator of the task
        :type src: Object
        """

        loop = asyncio.get_running_loop()
        manager = Manager()
        event = manager.Event()
        event.set()
        l_task = asyncio.ensure_future(
            loop.run_in_executor(self._process_pool_exec, Scheduler._run_task_in_p_context, task, event))
        l_task.add_done_callback(self._remove_process_task)
        self._scheduled_process_tasks.append((l_task, event, src))
        return l_task

    def schedule_timestamp_process_task(self, coroutine_creator, timestamp: float, src=None):
        """Schedule a task at specified datetime dispatched to another process.

        :param coroutine_creator: coroutine_creator creating coroutine to be scheduled
        :type coroutine_creator: coroutine_creator
        :param timestamp: unix timestamp defining when the task should start
        :type timestamp: float
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_process_task(TimestampScheduledProcessTask(coroutine_creator=coroutine_creator,
                                                                        timestamp=timestamp, clock=self.clock), src=src)

    def schedule_instant_process_task(self, coroutine_creator, src=None):
        """
        Schedule an instantly executed task dispatched to another process.
        :param coroutine_creator: coroutine_creator to be scheduled
        :type coroutine_creator:
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_process_task(InstantScheduledProcessTask(coroutine_creator=coroutine_creator), src=src)

    def schedule_periodic_process_task(self, coroutine_creator, delay, src=None):
        """Schedule an open end periodically executed task dispatched to another process.

        :param coroutine_creator: coroutine function creating coros to be scheduled
        :type coroutine_creator: Coroutine Function
        :param delay: delay in between the cycles
        :type delay: float
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_process_task(PeriodicScheduledProcessTask(coroutine_func=coroutine_creator,
                                                                       delay=delay, clock=self.clock), src=src)

    def schedule_conditional_process_task(self, coroutine_creator, condition_func, lookup_delay: float = 0.1, src=None):
        """
        Schedule a task when a specified condition is met.
        :param coroutine_creator: coroutine_creator creating coroutine to be scheduled
        :type coroutine_creator: coroutine_creator
        :param condition_func: function for determining whether the condition is fulfilled
        :type condition_func: lambda () -> bool
        :param lookup_delay: delay between checking the condition [s]
        :type lookup_delay: float
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_process_task(
            ConditionalProcessTask(
                coro_func=coroutine_creator,
                condition_func=condition_func,
                lookup_delay=lookup_delay, clock=self.clock),
            src=src)

    def schedule_datetime_process_task(self, coroutine_creator, date_time: datetime.datetime, src=None):
        """Schedule a task at specified datetime dispatched to another process.

        :param coroutine_creator: coroutine_creator creating coroutine to be scheduled
        :type coroutine_creator: coroutine_creator
        :param date_time: datetime defining when the task should start
        :type date_time: datetime
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_process_task(DateTimeScheduledProcessTask(coroutine_creator=coroutine_creator,
                                                                       date_time=date_time, clock=self.clock), src=src)

    # methods to suspend or resume tasks

    def suspend(self, given_src):
        """Suspend a set of tasks triggered by the given src object.

        :param given_src: the src object
        :type given_src: object
        """
        for _, coro, src in self._scheduled_tasks:
            if src == given_src and coro is not None:
                coro.suspend()
        for _, event, src in self._scheduled_process_tasks:
            if src == given_src and event is not None:
                event.clear()

    def resume(self, given_src):
        """Resume a set of tasks triggered by the given src object.

        :param given_src: the src object
        :type given_src: object
        """
        for _, coro, src in self._scheduled_tasks:
            if src == given_src and coro is not None:
                coro.resume()
        for _, event, src in self._scheduled_process_tasks:
            if src == given_src and event is not None:
                event.set()

    def _remove_process_task(self, fut=asyncio.Future):
        for i in range(len(self._scheduled_process_tasks)):
            task, event, _ = self._scheduled_process_tasks[i]
            if task == fut:
                del self._scheduled_process_tasks[i]
                event.set()
                break

    # methods for removing tasks, stopping or shutting down

    def _remove_task(self, fut=asyncio.Future):
        self._remove_generic_task(self._scheduled_tasks, fut=fut)

    def _remove_generic_task(self, target_list, fut=asyncio.Future):
        for i in range(len(target_list)):
            task, _, _ = target_list[i]
            if task == fut:
                del target_list[i]
                break

    async def stop(self):
        """
        Cancel all not finished scheduled tasks
        """
        for task, _, _ in self._scheduled_tasks + self._scheduled_process_tasks:
            task.cancel()
            await task

    async def tasks_complete(self, timeout=1):
        """Finish all pending tasks using a timeout.

        Args:
            timeout (int, optional): waiting timeout. Defaults to 1.
        """
        for task, _, _ in self._scheduled_tasks + self._scheduled_process_tasks:
            await asyncio.wait_for(task, timeout=timeout)

    def shutdown(self):
        """
        Shutdown internal process executor pool.
        """
        # resume all process so they can get shutdown
        for _, event, _ in self._scheduled_process_tasks:
            if event is not None:
                event.set()
        self._process_pool_exec.shutdown()
