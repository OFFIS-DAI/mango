import datetime

import pytest
import asyncio
import time

from mango.util.scheduling import Scheduler
from mango.util.clock import AsyncioClock, ExternalClock


async def example_coro(name, t_start, results_dict):
    results_dict[name] = datetime.datetime.now().timestamp() - t_start


async def increase_clock(c, increase_time, wait: float = 0, amount=1):
    for i in range(amount):
        await(asyncio.sleep(wait))
        c.set_time(c.time + increase_time)


@pytest.mark.asyncio
async def test_schedule_datetime_task():
    test_tasks = [4, 8, 2, 6, 8, 9, 0, 2.2, 1]
    external_clock = ExternalClock()
    scheduler_external = Scheduler(clock=external_clock)
    scheduler_asyncio = Scheduler(clock=AsyncioClock())

    t_1 = time.time()
    results_dict_external = {}
    results_dict_asyncio = {}
    increase_time_task = asyncio.create_task(increase_clock(c=external_clock, increase_time=1, wait=0.1, amount=10))
    for task_no in test_tasks:
        scheduler_external.schedule_datetime_task(date_time=datetime.datetime.fromtimestamp(task_no),
                                                  coroutine=example_coro(task_no, t_1, results_dict_external))
        scheduler_asyncio.schedule_datetime_task(date_time=datetime.datetime.fromtimestamp(datetime.datetime.now().timestamp() + task_no / 10),
                                                 coroutine=example_coro(task_no / 10, t_1, results_dict_asyncio))
    await increase_time_task

    for task_no in test_tasks:
        assert task_no/10 in results_dict_asyncio.keys(), f'results_dict_asyncio {results_dict_asyncio}'
        assert task_no in results_dict_external.keys()

    for simulation_time, real_time in results_dict_external.items():
        if int(simulation_time) < simulation_time:
            simulation_time = int(simulation_time) + 1
        assert round(simulation_time / 10, 1) == round(real_time, 1)

    for simulation_time, real_time in results_dict_asyncio.items():
        assert round(simulation_time, 1) == round(real_time, 1)

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
        scheduler_external.schedule_instant_task(example_coro(i, t_1, results_dict_external))
        scheduler_asyncio.schedule_instant_task(example_coro(i, t_1, results_dict_asyncio))
    await asyncio.sleep(0.1)

    assert len(results_dict_asyncio.keys()) == num_tasks
    assert len(results_dict_external.keys()) == num_tasks
    for i in range(num_tasks):
        assert round(results_dict_asyncio.get(i, None), 2) == 0
        assert round(results_dict_external.get(i, None), 2) == 0


@pytest.mark.asyncio
async def test_conditional_task():
    n_tasks = 10
    external_clock = ExternalClock()
    condition_variables = [False] * n_tasks
    scheduler_external = Scheduler(clock=external_clock)
    increase_time_task = asyncio.create_task(increase_clock(c=external_clock, increase_time=0.05, wait=0.6, amount=2))
    scheduler_asyncio = Scheduler(clock=AsyncioClock())
    results_dict = {}
    for i in range(n_tasks):
        def create_condition_func(num):
            return lambda: condition_variables[num]
        scheduler_asyncio.schedule_conditional_task(coroutine=example_coro(f'asyncio_{i}', time.time(), results_dict),
                                                    condition_func=create_condition_func(i), lookup_delay=0.1)
        scheduler_external.schedule_conditional_task(coroutine=example_coro(f'external_{i}', time.time(), results_dict),
                                                     condition_func=create_condition_func(i), lookup_delay=0.1)
    for i in range(n_tasks):
        await asyncio.sleep(0.1)
        condition_variables[i] = True

    await increase_time_task

    print(results_dict)
    for i in range(n_tasks):
        assert round(results_dict[f'external_{i}'], 1) == 1.2
        assert round(results_dict[f'asyncio_{i}'], 1) == round(0.1 + i / 10, 1)


@pytest.mark.asyncio
async def test_periodic_task():
    external_clock = ExternalClock()
    scheduler_external = Scheduler(clock=external_clock)
    increase_time_task = asyncio.create_task(increase_clock(c=external_clock, increase_time=0.05, wait=0.2, amount=2))
    scheduler_asyncio = Scheduler(clock=AsyncioClock())
    results_dict = {'asyncio': [], 'external': []}
    open_tasks = []
    t_start = time.time()

    async def example_periodic_coro_asyncio():
        results_dict['asyncio'].append(datetime.datetime.now().timestamp() - t_start)

    async def example_periodic_coro_external():
        results_dict['external'].append(datetime.datetime.now().timestamp() - t_start)

    open_tasks.append(scheduler_asyncio.schedule_periodic_task(coroutine_func=example_periodic_coro_asyncio,
                                                               delay=0.1))
    open_tasks.append(scheduler_external.schedule_periodic_task(coroutine_func=example_periodic_coro_external,
                                                                delay=0.1))

    await asyncio.sleep(1)
    await increase_time_task
    for task in open_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    for i in range(10):
        assert round(results_dict['asyncio'][i], 1) == round(0.1 * i, 1)
    assert len(results_dict['external']) == 2
    for i, duration in enumerate(results_dict['external']):
        assert round(duration, 1) == i * 0.4




