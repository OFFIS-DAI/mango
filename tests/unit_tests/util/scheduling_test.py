import asyncio
import datetime
import logging
import time

import pytest
from dateutil import rrule

from mango.util.clock import ExternalClock
from mango.util.scheduling import (
    ConditionalProcessTask,
    InstantScheduledProcessTask,
    InstantScheduledTask,
    PeriodicScheduledTask,
    RecurrentScheduledTask,
    Scheduler,
    TimestampScheduledTask,
)


@pytest.mark.asyncio
async def test_recurrent():
    # GIVEN
    start = datetime.datetime(2023, 1, 1)
    end = datetime.datetime(2023, 1, 3)
    clock = ExternalClock()
    scheduler = Scheduler(clock=clock)
    l = []

    async def increase_counter():
        l.append(1)

    recurrency = rrule.rrule(rrule.DAILY, interval=1, dtstart=start, until=end)
    # WHEN
    scheduler.schedule_task(RecurrentScheduledTask(increase_counter, recurrency, clock))
    clock.set_time(start.timestamp())
    await asyncio.sleep(0)
    # THEN
    # job was just scheduled
    assert len(l) == 0

    # WHEN
    new_time = start + datetime.timedelta(days=1)
    clock.set_time(new_time.timestamp())
    # sleep to let job be executed
    await asyncio.sleep(0)
    # THEN
    assert len(l) == 1

    # WHEN
    new_time = start + datetime.timedelta(days=2)
    clock.set_time(new_time.timestamp())
    await asyncio.sleep(0)
    # THEN
    assert len(l) == 2


@pytest.mark.asyncio
async def test_recurrent_conv():
    # GIVEN
    scheduler = Scheduler()
    l = []

    async def increase_counter():
        l.append(1)

    now = datetime.datetime.now()
    recurrency = rrule.rrule(
        rrule.SECONDLY,
        interval=1,
        dtstart=now,
        count=3,
    )

    # WHEN
    t = scheduler.schedule_recurrent_task(increase_counter, recurrency=recurrency)
    await asyncio.sleep(0)
    task = scheduler._scheduled_tasks[0][0]

    # THEN
    assert task._is_sleeping.done()

    assert len(l) == 0
    await asyncio.sleep(1)
    assert len(l) == 1
    await asyncio.sleep(1)
    assert task._is_done.done()
    assert len(l) == 2


@pytest.mark.asyncio
async def test_recurrent_wait():
    # GIVEN
    start = datetime.datetime(2023, 1, 1)
    end = datetime.datetime(2023, 1, 3)
    clock = ExternalClock(start.timestamp())
    scheduler = Scheduler(clock=clock)
    l = []

    async def increase_counter():
        l.append(clock._time)

    tomorrow = start + datetime.timedelta(days=1)
    aftertomorrow = start + datetime.timedelta(days=2)
    recurrency = rrule.rrule(rrule.DAILY, interval=1, dtstart=tomorrow, until=end)

    # WHEN
    t = scheduler.schedule_task(
        RecurrentScheduledTask(increase_counter, recurrency, clock)
    )
    task = scheduler._scheduled_tasks[0][0]
    clock.set_time(start.timestamp())
    await asyncio.sleep(0)
    assert task._is_sleeping.done()
    assert len(l) == 0
    clock.set_time(tomorrow.timestamp())
    await asyncio.sleep(0)
    assert task._is_sleeping.done()
    assert len(l) == 1
    clock.set_time(aftertomorrow.timestamp())
    await asyncio.sleep(0.1)
    assert task._is_done.done()

    # THEN
    assert len(l) == 2
    assert l[0] == tomorrow.timestamp()
    assert l[1] == aftertomorrow.timestamp()


class _EarlyWakeClock(ExternalClock):
    """Resolves every sleep a little before its deadline.

    Models what a coarse OS timer does: the event loop considers a timer due
    while the clock still reads just short of the deadline.
    """

    def __init__(self, start_time: float, skew: float):
        super().__init__(start_time)
        self._skew = skew

    def sleep(self, t: float) -> asyncio.Future:
        return super().sleep(max(t - self._skew, 0))


@pytest.mark.asyncio
async def test_recurrent_naive_rule_is_timezone_independent():
    """A rule built from naive datetimes must fire when the clock reaches that
    local time, whatever the machine's UTC offset is.

    Reading the clock as UTC while the rule means local time shifts every
    occurrence by the offset, which only looks correct where the offset is zero.
    """
    # GIVEN
    start = datetime.datetime(2023, 6, 1, 12, 0, 0)
    first = start + datetime.timedelta(seconds=60)
    clock = ExternalClock(start.timestamp())
    scheduler = Scheduler(clock=clock)
    fired = []

    async def note():
        fired.append(clock.time)

    recurrency = rrule.rrule(rrule.MINUTELY, interval=1, dtstart=first, count=1)

    # WHEN
    scheduler.schedule_task(RecurrentScheduledTask(note, recurrency, clock))
    await asyncio.sleep(0)
    assert fired == []
    clock.set_time(first.timestamp())
    await asyncio.sleep(0)

    # THEN
    assert fired == [first.timestamp()]


@pytest.mark.asyncio
async def test_recurrent_does_not_repeat_occurrence_on_early_wakeup():
    """Each occurrence must run once even if the wait ends before its deadline."""
    # GIVEN
    start = datetime.datetime(2023, 6, 1, 12, 0, 0)
    first = start + datetime.timedelta(seconds=60)
    skew = 5
    clock = _EarlyWakeClock(start.timestamp(), skew=skew)
    scheduler = Scheduler(clock=clock)
    fired = []

    async def note():
        fired.append(clock.time)

    recurrency = rrule.rrule(rrule.MINUTELY, interval=1, dtstart=first, count=2)

    # WHEN
    scheduler.schedule_task(RecurrentScheduledTask(note, recurrency, clock))
    await asyncio.sleep(0)
    clock.set_time(first.timestamp() - skew)
    await asyncio.sleep(0)

    # THEN
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_periodic():
    # GIVEN
    scheduler = Scheduler()
    l = []

    async def increase_counter():
        l.append(1)

    # WHEN
    t = scheduler.schedule_task(PeriodicScheduledTask(increase_counter, 0.2))

    with pytest.raises(asyncio.exceptions.TimeoutError):
        await asyncio.wait_for(t, timeout=0.3)

    # THEN
    assert len(l) == 2


@pytest.mark.asyncio
async def test_periodic_conv():
    # GIVEN
    scheduler = Scheduler()
    l = []

    async def increase_counter():
        l.append(1)

    # WHEN
    t = scheduler.schedule_periodic_task(increase_counter, 0.2)
    with pytest.raises(asyncio.exceptions.TimeoutError):
        await asyncio.wait_for(t, timeout=0.3)

    # THEN
    assert len(l) == 2


@pytest.mark.asyncio
@pytest.mark.filterwarnings(
    "ignore::RuntimeWarning"
)  # this test will stop the coro before scheduler awaits for it
async def test_one_shot_timeouted_conv():
    # GIVEN
    scheduler = Scheduler()
    l = []

    async def increase_counter():
        l.append(1)

    # WHEN
    t = scheduler.schedule_timestamp_task(
        increase_counter(),
        (datetime.datetime.now() + datetime.timedelta(seconds=0.3)).timestamp(),
    )
    with pytest.raises(asyncio.exceptions.TimeoutError):
        await asyncio.wait_for(t, timeout=0.2)

    await scheduler.shutdown()

    # THEN
    assert len(l) == 0


@pytest.mark.asyncio
async def test_one_shot_timestamp():
    # GIVEN
    scheduler = Scheduler()
    l = []

    async def increase_counter():
        l.append(1)

    # WHEN
    t = scheduler.schedule_task(
        TimestampScheduledTask(increase_counter(), time.time() + 0.1)
    )
    await asyncio.wait_for(t, timeout=0.2)

    # THEN
    assert len(l) == 1


@pytest.mark.asyncio
async def test_one_shot_timestamp_conv():
    # GIVEN
    scheduler = Scheduler()
    l = []

    async def increase_counter():
        l.append(1)

    # WHEN
    t = scheduler.schedule_timestamp_task(increase_counter(), time.time() + 0.1)
    await asyncio.wait_for(t, timeout=0.2)

    # THEN
    assert len(l) == 1


@pytest.mark.asyncio
async def test_suspend_then_resume():
    # GIVEN
    scheduler = Scheduler()
    l = []

    async def increase_counter():
        l.append(1)

    # WHEN
    t = scheduler.schedule_task(PeriodicScheduledTask(increase_counter, 0.1))
    scheduler.suspend(None)
    assert len(l) == 0
    await asyncio.sleep(0.25)
    scheduler.resume(None)
    await asyncio.sleep(0.25)

    # THEN
    assert len(l) == 3
    t.cancel()


async def do_exp_stuff():
    await asyncio.sleep(0.1)
    return 1337


class SimpleObj:
    async def do_exp_stuff(self):
        await asyncio.sleep(0.1)
        return 1337


@pytest.mark.asyncio
async def test_task_as_process():
    # GIVEN
    scheduler = Scheduler(num_process_parallel=16)

    # WHEN
    result = await asyncio.wait_for(
        scheduler.schedule_process_task(InstantScheduledProcessTask(do_exp_stuff)),
        timeout=100,
    )
    result2 = await asyncio.wait_for(
        scheduler.schedule_process_task(InstantScheduledProcessTask(do_exp_stuff)),
        timeout=100,
    )
    result3 = await asyncio.wait_for(
        scheduler.schedule_process_task(InstantScheduledProcessTask(do_exp_stuff)),
        timeout=100,
    )
    result4 = await asyncio.wait_for(
        scheduler.schedule_process_task(
            InstantScheduledProcessTask(SimpleObj().do_exp_stuff)
        ),
        timeout=100,
    )

    # THEN
    assert result == 1337
    assert result2 == 1337
    assert result3 == 1337
    assert result4 == 1337


async def do_exp_stuff_mult_steps():
    result = 45
    await asyncio.sleep(0.1)
    result += 1
    await asyncio.sleep(0.1)
    return result


def cond():
    return True


@pytest.mark.asyncio
async def test_cond_task_as_process():
    # GIVEN
    scheduler = Scheduler(num_process_parallel=16)

    # WHEN
    result = await asyncio.wait_for(
        scheduler.schedule_process_task(ConditionalProcessTask(do_exp_stuff, cond)),
        timeout=100,
    )
    result2 = await asyncio.wait_for(
        scheduler.schedule_process_task(ConditionalProcessTask(do_exp_stuff, cond)),
        timeout=100,
    )
    result3 = await asyncio.wait_for(
        scheduler.schedule_process_task(ConditionalProcessTask(do_exp_stuff, cond)),
        timeout=100,
    )
    result4 = await asyncio.wait_for(
        scheduler.schedule_process_task(
            ConditionalProcessTask(SimpleObj().do_exp_stuff, cond)
        ),
        timeout=100,
    )

    # THEN
    assert result == 1337

    assert result2 == 1337
    assert result3 == 1337
    assert result4 == 1337


@pytest.mark.asyncio
async def test_task_as_process_suspend_and_resume():
    # GIVEN
    scheduler = Scheduler(num_process_parallel=16)
    marker = 155

    # WHEN
    task = scheduler.schedule_process_task(
        InstantScheduledProcessTask(do_exp_stuff_mult_steps), marker
    )
    scheduler.suspend(marker)

    time.sleep(0.3)

    scheduler.resume(marker)

    # Generous: the point is that resume() lets the task finish at all. Starting
    # a worker process costs far more than the task itself where multiprocessing
    # spawns instead of forking.
    assert await asyncio.wait_for(task, timeout=60) == 46


@pytest.mark.asyncio
async def test_task_as_process_suspend():
    # GIVEN
    scheduler = Scheduler(num_process_parallel=16)
    marker = 155

    # WHEN
    task = scheduler.schedule_process_task(
        InstantScheduledProcessTask(do_exp_stuff_mult_steps), marker
    )
    scheduler.suspend(marker)

    # THEN
    with pytest.raises(asyncio.exceptions.TimeoutError):
        await asyncio.wait_for(task, timeout=0.3)

    scheduler.resume(marker)

    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_future_wait_task():
    # GIVEN
    scheduler = Scheduler()
    l = []

    fut = asyncio.Future()
    input = [1]

    async def do_something():
        await asyncio.sleep(0.1)
        input[0] = 10
        fut.set_result(True)

    async def increase_counter():
        assert input[0] == 10
        l.append(1)

    # WHEN
    t = scheduler.schedule_awaiting_task(
        coroutine=increase_counter(), awaited_coroutine=fut
    )

    asyncio.create_task(do_something())
    await asyncio.wait_for(t, timeout=1)

    # THEN
    assert len(l) == 1
    assert input[0] == 10


@pytest.mark.asyncio
async def test_tasks_complete():
    # GIVEN
    scheduler = Scheduler()
    l = []

    async def increase_counter():
        await asyncio.sleep(0.1)
        l.append(1)

    scheduler.schedule_instant_task(coroutine=increase_counter())
    scheduler.schedule_instant_task(coroutine=increase_counter())

    assert len(l) == 0

    # WHEN
    await scheduler.tasks_complete()

    # THEN
    assert len(l) == 2


@pytest.mark.asyncio
async def test_tasks_complete_spawning_no_rec():
    # GIVEN
    scheduler = Scheduler()
    l = []

    async def increase_counter():
        await asyncio.sleep(0.1)
        l.append(1)
        if len(l) == 2:
            scheduler.schedule_instant_task(coroutine=increase_counter())

    scheduler.schedule_instant_task(coroutine=increase_counter())
    scheduler.schedule_instant_task(coroutine=increase_counter())

    assert len(l) == 0

    # WHEN
    await scheduler.tasks_complete()

    # THEN
    assert len(l) == 2

    # THEN another task is scheduled
    await scheduler.tasks_complete()

    assert len(l) == 3


@pytest.mark.asyncio
async def test_tasks_complete_spawning_rec():
    # GIVEN
    scheduler = Scheduler()
    l = []

    async def increase_counter():
        await asyncio.sleep(0.1)
        l.append(1)
        if len(l) == 2 or len(l) == 3:
            scheduler.schedule_instant_task(coroutine=increase_counter())

    scheduler.schedule_instant_task(coroutine=increase_counter())
    scheduler.schedule_instant_task(coroutine=increase_counter())

    assert len(l) == 0

    # WHEN
    await scheduler.tasks_complete(recursive=True)

    # THEN
    assert len(l) == 4


@pytest.mark.asyncio
async def test_task_on_stop():
    # GIVEN
    scheduler = Scheduler()
    l = []

    async def increase_counter():
        await asyncio.sleep(0.1)
        l.append(1)

    def on_stop(fut):
        l.append(2)

    scheduler.schedule_instant_task(coroutine=increase_counter(), on_stop=on_stop)

    # WHEN
    await scheduler.tasks_complete()

    # THEN
    assert len(l) == 2
    assert l[1] == 2


class MyException(Exception):
    pass


@pytest.mark.asyncio
async def test_exception(caplog):
    # GIVEN
    scheduler = Scheduler()
    l = []

    async def increase_counter():
        raise MyException()

    # WHEN
    t = scheduler.schedule_task(PeriodicScheduledTask(increase_counter, 0.2))

    # THEN
    with caplog.at_level(logging.ERROR):
        with pytest.raises(MyException):
            await asyncio.wait_for(t, timeout=0.3)

    assert "got exception in scheduled event" in caplog.text


@pytest.mark.asyncio
async def test_recurrent_aware_rule_is_read_as_utc():
    """A rule built from tz-aware datetimes means absolute time, so the
    clock has to be read as UTC — reading it as local would shift every
    occurrence by the machine's UTC offset.

    Counterpart to ``test_recurrent_naive_rule_is_timezone_independent``.
    """
    # GIVEN
    start = datetime.datetime(2023, 6, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    first = start + datetime.timedelta(seconds=60)
    clock = ExternalClock(start.timestamp())
    scheduler = Scheduler(clock=clock)
    fired = []

    async def note():
        fired.append(clock.time)

    recurrency = rrule.rrule(rrule.MINUTELY, interval=1, dtstart=first, count=1)

    # WHEN
    scheduler.schedule_task(RecurrentScheduledTask(note, recurrency, clock))
    await asyncio.sleep(0)
    assert fired == []
    clock.set_time(first.timestamp())
    await asyncio.sleep(0)

    # THEN
    assert fired == [first.timestamp()]


@pytest.mark.asyncio
async def test_non_suspendable_scheduler_still_runs_and_stops_tasks():
    """With ``suspendable=False`` the scheduler skips the Suspendable
    wrapper and uses a plain task; the on_stop callback and task
    bookkeeping must work the same way."""
    # GIVEN
    scheduler = Scheduler()
    scheduler.suspendable = False
    stopped = []

    async def work():
        await asyncio.sleep(0)
        return 42

    # WHEN
    task = scheduler.schedule_task(
        InstantScheduledTask(work(), on_stop=lambda fut: stopped.append(fut.result()))
    )
    await task

    # THEN
    assert task.result() == 42
    await asyncio.sleep(0)
    assert stopped == [42]
    assert scheduler._scheduled_tasks == []
