import asyncio
import time

import pytest

from mango.util.clock import AsyncioClock, ExternalClock
from mango.util.scheduling import Scheduler


async def example_coro(name, t_start, results_dict):
    results_dict[name] = time.time() - t_start


async def increase_clock(c, increase_time, wait: float = 0, amount=1):
    for i in range(amount):
        await asyncio.sleep(wait)
        c.set_time(c.time + increase_time)


@pytest.mark.asyncio
async def test_sleep():
    clock = ExternalClock(start_time=100)
    scheduler = Scheduler(clock=clock)
    task = asyncio.create_task(increase_clock(clock, 1, 0.1, 5))
    await scheduler.sleep(4)
    #: which clock step the sleep returns on pins the behaviour exactly,
    #: while the elapsed real time drifts under load (flaky on macOS CI).
    woke_up_at = clock.time
    await task
    assert woke_up_at == 104


@pytest.mark.asyncio
async def test_external_clock_simple():
    async def track_time(start_time):
        return time.time() - start_time

    clock = ExternalClock()
    scheduler = Scheduler(clock=clock)
    t_start = time.time()
    first_task = scheduler.schedule_timestamp_task(
        timestamp=0.1, coroutine=track_time(t_start)
    )
    second_task = scheduler.schedule_timestamp_task(
        timestamp=100, coroutine=track_time(t_start)
    )
    await asyncio.sleep(0.2)
    clock.set_time(0.1)
    await asyncio.sleep(0.1)
    clock.set_time(99)
    await asyncio.sleep(0.1)
    clock.set_time(1000)
    results = await asyncio.gather(first_task, second_task)
    assert round(results[0], 1) == 0.2
    assert round(results[1], 1) == 0.4


#: asyncio decides a timer is due when its deadline lies within one clock
#: resolution, and that clock resolves to ~16 ms on Windows against ~1 ns on
#: Linux. So a single wait can end measurably before its deadline, and a chain
#: of waits drifts past it by the same granularity per step.
_CLOCK_SLACK = max(8 * time.get_clock_info("monotonic").resolution, 0.01)


def assert_os_close(va, bound, tol=0.1):
    assert va >= bound - _CLOCK_SLACK, f"{va} is more than {_CLOCK_SLACK} below {bound}"
    assert va <= bound + tol + _CLOCK_SLACK, (
        f"{va} is more than {tol + _CLOCK_SLACK} above {bound}"
    )


@pytest.mark.asyncio
async def test_schedule_timestamp_task():
    test_tasks = [4, 8, 2, 6, 8, 9, 0, 2.2, 1]
    external_clock = ExternalClock()
    scheduler_external = Scheduler(clock=external_clock)
    scheduler_asyncio = Scheduler(clock=AsyncioClock())

    t_1 = time.time()
    results_dict_external = {}
    results_dict_asyncio = {}
    increase_time_task = asyncio.create_task(
        increase_clock(c=external_clock, increase_time=1, wait=0.1, amount=10),
        name="Increase Time",
    )
    for task_no in test_tasks:
        scheduler_external.schedule_timestamp_task(
            timestamp=task_no,
            coroutine=example_coro(task_no, t_1, results_dict_external),
        )
        scheduler_asyncio.schedule_timestamp_task(
            timestamp=time.time() + task_no / 10,
            coroutine=example_coro(task_no / 10, t_1, results_dict_asyncio),
        )
    await increase_time_task

    for task_no in test_tasks:
        assert task_no / 10 in results_dict_asyncio.keys(), (
            f"results_dict_asyncio {results_dict_asyncio}"
        )
        assert task_no in results_dict_external.keys()

    for simulation_time, real_time in results_dict_external.items():
        if int(simulation_time) < simulation_time:
            sim_time = int(simulation_time) + 1
        else:
            sim_time = simulation_time
        assert_os_close(real_time, sim_time / 10)

    for simulation_time, real_time in results_dict_asyncio.items():
        assert_os_close(real_time, simulation_time)


@pytest.mark.asyncio
async def test_schedule_instant_task():
    num_tasks = 22
    external_clock = ExternalClock()
    scheduler_external = Scheduler(clock=external_clock)
    scheduler_asyncio = Scheduler(clock=AsyncioClock())
    t_1 = time.time()
    results_dict_external = {}
    results_dict_asyncio = {}
    for i in range(num_tasks):
        scheduler_external.schedule_instant_task(
            example_coro(i, t_1, results_dict_external)
        )
        scheduler_asyncio.schedule_instant_task(
            example_coro(i, t_1, results_dict_asyncio)
        )
    await asyncio.sleep(0.1)

    assert len(results_dict_asyncio.keys()) == num_tasks
    assert len(results_dict_external.keys()) == num_tasks
    for i in range(num_tasks):
        assert round(results_dict_asyncio.get(i, None), 1) == 0
        assert round(results_dict_external.get(i, None), 1) == 0


@pytest.mark.asyncio
async def test_conditional_task():
    n_tasks = 10
    lookup_delay = 0.1
    external_clock = ExternalClock()
    scheduler_external = Scheduler(clock=external_clock)
    scheduler_asyncio = Scheduler(clock=AsyncioClock())
    conditions = [False] * n_tasks
    asyncio_ran = set()
    external_runs = {}

    async def record_asyncio_run(i):
        asyncio_ran.add(i)

    async def record_external_run(i):
        external_runs[i] = external_clock.time

    asyncio_tasks = []
    external_tasks = []
    for i in range(n_tasks):
        asyncio_tasks.append(
            scheduler_asyncio.schedule_conditional_task(
                coroutine=record_asyncio_run(i),
                condition_func=lambda i=i: conditions[i],
                lookup_delay=lookup_delay,
            )
        )
        external_tasks.append(
            scheduler_external.schedule_conditional_task(
                coroutine=record_external_run(i),
                condition_func=lambda i=i: conditions[i],
                lookup_delay=lookup_delay,
            )
        )

    for i in range(n_tasks):
        await asyncio.sleep(lookup_delay)
        assert asyncio_ran.isdisjoint(range(i, n_tasks))
        conditions[i] = True
    await asyncio.wait_for(asyncio.gather(*asyncio_tasks), timeout=10)
    assert asyncio_ran == set(range(n_tasks))

    #: the conditions have been true for a while now, but an external clock
    #: task only re-checks once the clock advances by a full lookup_delay.
    assert external_runs == {}
    external_clock.set_time(0.05)
    await asyncio.sleep(0.05)
    assert external_runs == {}
    external_clock.set_time(0.1)
    await asyncio.wait_for(asyncio.gather(*external_tasks), timeout=10)
    assert external_runs == dict.fromkeys(range(n_tasks), 0.1)


@pytest.mark.asyncio
async def test_periodic_task():
    delay = 0.1
    n_asyncio_runs = 10
    external_clock = ExternalClock()
    scheduler_external = Scheduler(clock=external_clock)
    scheduler_asyncio = Scheduler(clock=AsyncioClock())
    asyncio_runs = []
    external_runs = []
    asyncio_done = asyncio.Event()
    external_ran = asyncio.Event()

    async def record_asyncio_run():
        asyncio_runs.append(time.monotonic())
        if len(asyncio_runs) == n_asyncio_runs:
            asyncio_done.set()

    async def record_external_run():
        external_runs.append(external_clock.time)
        external_ran.set()

    open_tasks = [
        scheduler_asyncio.schedule_periodic_task(
            coroutine_func=record_asyncio_run, delay=delay
        ),
        scheduler_external.schedule_periodic_task(
            coroutine_func=record_external_run, delay=delay
        ),
    ]

    await asyncio.wait_for(asyncio_done.wait(), timeout=10)
    external_ran.clear()
    external_clock.set_time(0.05)
    await asyncio.sleep(0.05)
    assert external_runs == [0]
    external_clock.set_time(0.1)
    await asyncio.wait_for(external_ran.wait(), timeout=10)

    for task in open_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    #: only the lower bound is guaranteed; a loaded runner can delay any
    #: wake-up, and since the period restarts after each run the delays add up.
    for earlier, later in zip(asyncio_runs, asyncio_runs[1:]):
        assert later - earlier >= delay - _CLOCK_SLACK
    assert external_runs == [0, 0.1]
