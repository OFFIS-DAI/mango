"""
Module for commonly used time based scheduled task executed inside one agent.
"""

import asyncio
import concurrent.futures
import datetime
from abc import abstractmethod
from multiprocessing import Manager, Event
from typing import Any, List, Tuple
from dataclasses import dataclass
from multiprocessing.synchronize import Event as MultiprocessingEvent

from dateutil.rrule import rrule

from mango.util.clock import AsyncioClock, Clock, ExternalClock
from asyncio import Future


@dataclass
class ScheduledProcessControl:
    run_task_event: MultiprocessingEvent
    kill_process_event: MultiprocessingEvent

    def kill_process(self):
        self.kill_process_event.set()

    def init_process(self):
        self.kill_process_event.clear()

    def resume_task(self):
        self.run_task_event.set()

    def suspend_task(self):
        self.run_task_event.clear()


class Suspendable:
    """
    Wraps a coroutine, intercepting __await__ to add the functionality of suspending.
    """

    def __init__(self, coro, ext_contr_event=None, kill_event=None):
        self._coro = coro

        self._kill_event = kill_event
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

            if self._kill_event is not None and self._kill_event.is_set():
                return None

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


def _close_coro(coro):
    try:
        coro.close()
    except:
        pass


class ScheduledTask:
    """
    Base class for scheduled tasks in mango. Within this class it is possible to
    define what to do on execution and on stop. In most cases the logic should get
    passed as lambda while the scheduling logic is inside of class inheriting from this one.
    """

    def __init__(self, clock: Clock = None, observable=True, on_stop=None) -> None:
        self.clock = clock if clock is not None else AsyncioClock()
        self._on_stop_hook_in = on_stop
        self._is_observable = observable
        if self._is_observable:
            self._is_sleeping = asyncio.Future()
            self._is_done = asyncio.Future()

    def notify_sleeping(self):
        if self._is_observable:
            self._is_sleeping.set_result(True)

    def notify_running(self):
        if self._is_observable:
            self._is_sleeping = asyncio.Future()

    @abstractmethod
    async def run(self):
        """Called via asyncio as asyncio.task.

        Raises:
            NotImplementedError: must be overwritten
        """
        raise NotImplementedError

    def on_stop(self, fut: asyncio.Future = None):
        """
        Called when the task is cancelled or finished.
        """
        if self._on_stop_hook_in is not None:
            self._on_stop_hook_in(fut)
        if self._is_observable:
            self._is_done.set_result(True)
        self.close()

    def close(self):
        """Perform closing actions"""
        pass


class TimestampScheduledTask(ScheduledTask):
    """
    Timestamp based one-shot task. This task will get executed when a given unix timestamp is reached.
    """

    def __init__(
        self, coroutine, timestamp: float, clock=None, on_stop=None, observable=True
    ):
        super().__init__(clock, on_stop=on_stop, observable=observable)
        self._timestamp = timestamp
        self._coro = coroutine

    async def _wait(self, timestamp: float):
        sleep_future: asyncio.Future = self.clock.sleep(timestamp - self.clock.time)
        self.notify_sleeping()
        await sleep_future
        self.notify_running()

    async def run(self):
        await self._wait(self._timestamp)
        return await self._coro

    def close(self):
        _close_coro(self._coro)


class AwaitingTask(ScheduledTask):
    """
    Awaiting task. This task will execute a given coroutine after another given coroutine has been awaited.
    Can be useful if you want to execute something after a Future has finished.
    """

    def __init__(
        self, coroutine, awaited_coroutine, clock=None, on_stop=None, observable=True
    ):
        super().__init__(clock, on_stop=on_stop, observable=observable)
        self._coroutine = coroutine
        self._awaited_coroutine = awaited_coroutine

    async def run(self):
        self.notify_sleeping()
        await self._awaited_coroutine
        self.notify_running()
        return await self._coroutine

    def close(self):
        _close_coro(self._awaited_coroutine)
        _close_coro(self._coroutine)


class InstantScheduledTask(TimestampScheduledTask):
    """
    One-shot task, which will get executed instantly.
    """

    def __init__(self, coroutine, clock: Clock = None, on_stop=None, observable=True):
        if clock is None:
            clock = AsyncioClock()
        super().__init__(
            coroutine, clock.time, clock=clock, on_stop=on_stop, observable=observable
        )

    def close(self):
        _close_coro(self._coro)


class PeriodicScheduledTask(ScheduledTask):
    """
    Class for periodic scheduled tasks. It enables to create a task for an agent
    which will get executed periodically with a specified delay.
    """

    def __init__(
        self, coroutine_func, delay, clock: Clock = None, on_stop=None, observable=True
    ):
        super().__init__(clock, on_stop=on_stop, observable=observable)

        self._stopped = False
        self._coroutine_func = coroutine_func
        self._delay = delay

    async def run(self):
        while not self._stopped:
            await self._coroutine_func()
            sleep_future: asyncio.Future = self.clock.sleep(self._delay)
            self.notify_sleeping()
            await sleep_future
            self.notify_running()


class RecurrentScheduledTask(ScheduledTask):
    """
    Class for periodic scheduled tasks. It enables to create a task for an agent
    which will get executed periodically with a specified delay.
    """

    def __init__(
        self,
        coroutine_func,
        recurrency: rrule,
        clock: Clock = None,
        on_stop=None,
        observable=True,
    ):
        super().__init__(clock, on_stop=on_stop, observable=observable)
        self._recurrency_rule = recurrency
        self._stopped = False
        self._coroutine_func = coroutine_func

    async def run(self):
        while not self._stopped:
            current_time = datetime.datetime.fromtimestamp(self.clock.time)
            after = self._recurrency_rule.after(current_time)
            # after can be None, if until or count was set on the rrule
            if after is None:
                self._stopped = True
            else:
                delay = (after - current_time).total_seconds()
                sleep_future: asyncio.Future = self.clock.sleep(delay)
                self.notify_sleeping()
                await sleep_future
                self.notify_running()
                await self._coroutine_func()


class ConditionalTask(ScheduledTask):
    """Task which will get executed as soon as the given condition is fulfilled."""

    def __init__(
        self,
        coroutine,
        condition_func,
        lookup_delay=0.1,
        clock: Clock = None,
        on_stop=None,
        observable=True,
    ):
        super().__init__(clock=clock, on_stop=on_stop, observable=observable)
        assert coroutine is not None
        self._condition = condition_func
        self._coro = coroutine
        self._delay = lookup_delay

    async def run(self):
        while not self._condition():
            sleep_future: asyncio.Future = self.clock.sleep(self._delay)
            self.notify_sleeping()
            await sleep_future
            self.notify_running()
        return await self._coro

    def close(self):
        _close_coro(self._coro)


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

    def __init__(self, clock: Clock, on_stop=None, observable=False):
        if isinstance(clock, ExternalClock):
            raise ValueError("Process Tasks do currently not work with external clocks")
        super().__init__(clock=clock, observable=observable, on_stop=on_stop)


class TimestampScheduledProcessTask(TimestampScheduledTask, ScheduledProcessTask):
    """
    Timestamp based one-shot task.
    """

    def __init__(self, coroutine_creator, timestamp: float, clock=None, on_stop=None):
        super().__init__(
            coroutine_creator, timestamp, clock, on_stop=on_stop, observable=False
        )

    async def run(self):
        await self._wait(self._timestamp)
        return await self._coro()


class AwaitingProcessTask(AwaitingTask, ScheduledProcessTask):
    """
    Await a coroutine, then execute another.
    """

    def __init__(
        self, coroutine_creator, awaited_coroutine_creator, clock=None, on_stop=None
    ):
        super().__init__(
            coroutine_creator,
            awaited_coroutine_creator,
            clock,
            on_stop=on_stop,
            observable=False,
        )

    async def run(self):
        await self._awaited_coroutine()
        return await self._coroutine()


class InstantScheduledProcessTask(TimestampScheduledProcessTask):
    """One-shot task, which will get executed instantly."""

    def __init__(self, coroutine_creator, clock: Clock = None, on_stop=None):
        if clock is None:
            clock = AsyncioClock()
        super().__init__(
            coroutine_creator,
            timestamp=clock.time,
            clock=clock,
            on_stop=on_stop,
        )


class PeriodicScheduledProcessTask(PeriodicScheduledTask, ScheduledProcessTask):
    def __init__(self, coroutine_func, delay, clock: Clock = None, on_stop=None):
        super().__init__(coroutine_func, delay, clock, on_stop=on_stop)


class RecurrentScheduledProcessTask(RecurrentScheduledTask, ScheduledProcessTask):
    def __init__(
        self, coroutine_func, recurrency: rrule, clock: Clock = None, on_stop=None
    ):
        super().__init__(
            coroutine_func, recurrency, clock, on_stop=on_stop, observable=False
        )


class ConditionalProcessTask(ConditionalTask, ScheduledProcessTask):
    """
    Task which will get executed as soon as the given condition is fulfilled.
    """

    def __init__(
        self,
        coro_func,
        condition_func,
        lookup_delay=0.1,
        clock: Clock = None,
        on_stop=None,
    ):
        super().__init__(
            coro_func,
            condition_func,
            lookup_delay,
            clock=clock,
            on_stop=on_stop,
            observable=False,
        )

    async def run(self):
        while not self._condition():
            await self.clock.sleep(self._delay)
        return await self._coro()


def _create_asyncio_context():
    asyncio.set_event_loop(asyncio.new_event_loop())


class Scheduler:
    """Scheduler for executing tasks."""

    def __init__(
        self,
        clock: Clock = None,
        num_process_parallel=16,
        suspendable=True,
        observable=True,
    ):
        # List of Tuples with asyncio.Future, ScheduledTask, Suspendable coro, Source
        self._scheduled_tasks: List[
            Tuple[ScheduledTask, asyncio.Future, Suspendable, Any]
        ] = []
        self.clock = clock if clock is not None else AsyncioClock()
        self._scheduled_process_tasks: List[
            Tuple[ScheduledProcessTask, Future, ScheduledProcessControl, Any]
        ] = []
        self._manager = None
        self._num_process_parallel = num_process_parallel
        self._process_pool_exec = None
        self._suspendable = suspendable
        self._observable = observable

    @staticmethod
    def _run_task_in_p_context(
        task, scheduled_process_control: ScheduledProcessControl
    ):
        try:
            coro = Suspendable(
                task.run(),
                ext_contr_event=scheduled_process_control.run_task_event,
                kill_event=scheduled_process_control.kill_process_event,
            )

            return asyncio.get_event_loop().run_until_complete(coro)
        finally:
            pass

    async def sleep(self, t: float):
        """
        :param t: The time to sleep [s]
        """
        return await self.clock.sleep(t)

    # conv methods for asyncio Tasks

    def schedule_task(self, task: ScheduledTask, src=None) -> asyncio.Task:
        """
        Schedule a task with asyncio. When the task is finished, if finite, its automatically removed afterwards.
        For scheduling options see the subclasses of ScheduledTask.

        :param task: task to be scheduled
        :type task: ScheduledTask
        :param src: creator of the task
        :type: Object
        """
        l_task = None
        if self._suspendable:
            coro = Suspendable(task.run())
            l_task = asyncio.ensure_future(coro)
        else:
            coro = task.run()
            l_task = asyncio.create_task(coro)
        l_task.add_done_callback(task.on_stop)
        l_task.add_done_callback(self._remove_task)
        self._scheduled_tasks.append((task, l_task, coro, src))
        return l_task

    def schedule_timestamp_task(
        self, coroutine, timestamp: float, on_stop=None, src=None
    ):
        """Schedule a task at specified unix timestamp.

        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param timestamp: timestamp defining when the task should start (unix timestamp)
        :type timestamp: float
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_task(
            TimestampScheduledTask(
                coroutine=coroutine,
                timestamp=timestamp,
                clock=self.clock,
                on_stop=on_stop,
                observable=self._observable,
            ),
            src=src,
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
        return self.schedule_task(
            InstantScheduledTask(
                coroutine=coroutine,
                clock=self.clock,
                on_stop=on_stop,
                observable=self._observable,
            ),
            src=src,
        )

    def schedule_periodic_task(self, coroutine_func, delay, on_stop=None, src=None):
        """
        Schedule an open end periodically executed task.
        :param coroutine_func: coroutine function creating coros to be scheduled
        :type coroutine_func:  Coroutine Function
        :param delay: delay in between the cycles
        :type delay: float
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_task(
            PeriodicScheduledTask(
                coroutine_func=coroutine_func,
                delay=delay,
                clock=self.clock,
                on_stop=on_stop,
                observable=self._observable,
            ),
            src=src,
        )

    def schedule_recurrent_task(
        self, coroutine_func, recurrency, on_stop=None, src=None
    ):
        """Schedule a task using a fine-grained recurrency rule.

        :param coroutine_func: coroutine function creating coros to be scheduled
        :type coroutine_func:  Coroutine Function
        :param recurrency: recurrency rule to calculate next event
        :type recurrency: dateutil.rrule.rrule
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_task(
            RecurrentScheduledTask(
                coroutine_func=coroutine_func,
                recurrency=recurrency,
                clock=self.clock,
                on_stop=on_stop,
                observable=self._observable,
            ),
            src=src,
        )

    def schedule_conditional_task(
        self,
        coroutine,
        condition_func,
        lookup_delay: float = 0.1,
        on_stop=None,
        src=None,
    ):
        """
        Schedule a task when a specified condition is met.
        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param condition_func: function for determining whether the condition is fulfilled
        :type condition_func: lambda () -> bool
        :param lookup_delay: delay between checking the condition [s]
        :type lookup_delay: float
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_task(
            ConditionalTask(
                coroutine=coroutine,
                condition_func=condition_func,
                clock=self.clock,
                lookup_delay=lookup_delay,
                on_stop=on_stop,
                observable=self._observable,
            ),
            src=src,
        )

    def schedule_awaiting_task(
        self, coroutine, awaited_coroutine, on_stop=None, src=None
    ):
        """Schedule a task after future of other task returned.

        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param awaited_coroutine: datetime defining when the task should start
        :type awaited_coroutine: asyncio.Future
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_task(
            AwaitingTask(
                coroutine=coroutine,
                awaited_coroutine=awaited_coroutine,
                clock=self.clock,
                on_stop=on_stop,
                observable=self._observable,
            ),
            src=src,
        )

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

        if self._process_pool_exec is None:
            self._process_pool_exec = concurrent.futures.ProcessPoolExecutor(
                max_workers=self._num_process_parallel,
                initializer=_create_asyncio_context,
            )
        loop = asyncio.get_running_loop()
        if self._manager is None:
            self._manager = Manager()

        scheduled_process_control = ScheduledProcessControl(
            run_task_event=self._manager.Event(),
            kill_process_event=self._manager.Event(),
        )
        scheduled_process_control.init_process()
        scheduled_process_control.resume_task()

        l_task = asyncio.ensure_future(
            loop.run_in_executor(
                self._process_pool_exec,
                Scheduler._run_task_in_p_context,
                task,
                scheduled_process_control,
            )
        )
        l_task.add_done_callback(self._remove_process_task)
        l_task.add_done_callback(task.on_stop)
        self._scheduled_process_tasks.append(
            (task, l_task, scheduled_process_control, src)
        )
        return l_task

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
        return self.schedule_process_task(
            TimestampScheduledProcessTask(
                coroutine_creator=coroutine_creator,
                timestamp=timestamp,
                clock=self.clock,
                on_stop=on_stop,
            ),
            src=src,
        )

    def schedule_instant_process_task(self, coroutine_creator, on_stop=None, src=None):
        """
        Schedule an instantly executed task dispatched to another process.
        :param coroutine_creator: coroutine_creator to be scheduled
        :type coroutine_creator:
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_process_task(
            InstantScheduledProcessTask(
                coroutine_creator=coroutine_creator, on_stop=on_stop
            ),
            src=src,
        )

    def schedule_periodic_process_task(
        self, coroutine_creator, delay, on_stop=None, src=None
    ):
        """Schedule an open end periodically executed task dispatched to another process.

        :param coroutine_creator: coroutine function creating coros to be scheduled
        :type coroutine_creator: Coroutine Function
        :param delay: delay in between the cycles
        :type delay: float
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_process_task(
            PeriodicScheduledProcessTask(
                coroutine_func=coroutine_creator,
                delay=delay,
                clock=self.clock,
                on_stop=on_stop,
            ),
            src=src,
        )

    def schedule_recurrent_process_task(
        self, coroutine_creator, recurrency, on_stop=None, src=None
    ):
        """Schedule an open end periodically executed task dispatched to another process.

        :param coroutine_creator: coroutine function creating coros to be scheduled
        :type coroutine_creator: Coroutine Function
        :param recurrency: rrule object which gets executed
        :type recurrency: dateutil.rrule.rrule
        :param src: creator of the task
        :type src: Object
        """
        return self.schedule_process_task(
            RecurrentScheduledProcessTask(
                coroutine_func=coroutine_creator,
                recurrency=recurrency,
                clock=self.clock,
                on_stop=on_stop,
            ),
            src=src,
        )

    def schedule_conditional_process_task(
        self,
        coroutine_creator,
        condition_func,
        lookup_delay: float = 0.1,
        on_stop=None,
        src=None,
    ):
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
                lookup_delay=lookup_delay,
                on_stop=on_stop,
                clock=self.clock,
            ),
            src=src,
        )

    # methods to suspend or resume tasks

    def suspend(self, given_src):
        """Suspend a set of tasks triggered by the given src object.

        :param given_src: the src object
        :type given_src: object
        """
        if not self._suspendable:
            raise Exception("The scheduler is configured as non-suspendable!")

        for _, _, coro, src in self._scheduled_tasks:
            if src == given_src and coro is not None:
                coro.suspend()
        for _, _, scheduled_process_control, src in self._scheduled_process_tasks:
            if src == given_src:
                scheduled_process_control.suspend_task()

    def resume(self, given_src):
        """Resume a set of tasks triggered by the given src object.

        :param given_src: the src object
        :type given_src: object
        """
        if not self._suspendable:
            raise Exception("The scheduler is configured as non-suspendable!")

        for _, _, coro, src in self._scheduled_tasks:
            if src == given_src and coro is not None:
                coro.resume()
        for _, _, scheduled_process_control, src in self._scheduled_process_tasks:
            if src == given_src:
                scheduled_process_control.resume_task()

    def _remove_process_task(self, fut=asyncio.Future):
        for i in range(len(self._scheduled_process_tasks)):
            _, task, scheduled_process_control, _ = self._scheduled_process_tasks[i]
            if task == fut:
                scheduled_process_control.resume_task()
                scheduled_process_control.kill_process()
                del self._scheduled_process_tasks[i]
                break

    # methods for removing tasks, stopping or shutting down

    def _remove_task(self, fut=asyncio.Future):
        self._remove_generic_task(self._scheduled_tasks, fut=fut)

    def _remove_generic_task(self, target_list, fut=asyncio.Future):
        for i in range(len(target_list)):
            _, task, _, _ = target_list[i]
            if task == fut:
                del target_list[i]
                break

    async def stop(self):
        """
        Cancel all not finished scheduled tasks
        """
        for _, task, _, _ in self._scheduled_tasks + self._scheduled_process_tasks:
            task.cancel()
            await task

    async def tasks_complete(self, timeout=1, recursive=False):
        """Finish all pending tasks using a timeout.

        Args:
            timeout (int, optional): waiting timeout. Defaults to 1.
        """
        for _, task, _, _ in self._scheduled_tasks + self._scheduled_process_tasks:
            await asyncio.wait_for(task, timeout=timeout)

        # As it might happen that tasks spawn new tasks, one might want to finish these tasks
        # as well. Caution: this can result in an infinte loop, f.e. if a tasks spawns itself
        # after completion
        if recursive and len(self._scheduled_tasks + self._scheduled_process_tasks) > 0:
            await self.tasks_complete(timeout=timeout, recursive=recursive)

    async def tasks_complete_or_sleeping(self):
        """ """
        sleeping_tasks = []
        # we need to use the while loop here, as new tasks may have been scheduled while waiting for other tasks
        while len(self._scheduled_tasks + self._scheduled_process_tasks) > len(
            sleeping_tasks
        ):
            for scheduled_task, task, _, _ in (
                self._scheduled_tasks + self._scheduled_process_tasks
            ):
                await asyncio.wait(
                    [scheduled_task._is_sleeping, scheduled_task._is_done],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if (
                    scheduled_task._is_sleeping.done()
                    and scheduled_task not in sleeping_tasks
                ):
                    # we need to recognize how many sleeping tasks we have in order to find out if all tasks are done
                    sleeping_tasks.append(scheduled_task)

    async def shutdown(self):
        """
        Shutdown internal process executor pool.
        """
        # resume all process so they can get shutdown
        for _, _, scheduled_process_control, _ in self._scheduled_process_tasks:
            scheduled_process_control.kill_process()
        for task, _, _, _ in self._scheduled_tasks:
            task.close()
        await self.stop()
        if self._process_pool_exec is not None:
            self._process_pool_exec.shutdown()
