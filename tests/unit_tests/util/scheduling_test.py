import pytest
import asyncio
import datetime
import time

from mango.util.scheduling import ConditionalProcessTask, DateTimeScheduledTask, InstantScheduledProcessTask, \
    InstantScheduledTask, Scheduler, PeriodicScheduledTask, TimestampScheduledTask

@pytest.mark.asyncio
async def test_periodic():
    # GIVEN
    scheduler = Scheduler()
    l = []
    async def increase_counter():
        l.append(1)

    # WHEN
    t = scheduler.schedule_task(PeriodicScheduledTask(increase_counter, 0.2))
    try: 
        await asyncio.wait_for(t, timeout=0.3)
    except asyncio.exceptions.TimeoutError:
        pass

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
    try: 
        await asyncio.wait_for(t, timeout=0.3)
    except asyncio.exceptions.TimeoutError:
        pass

    # THEN
    assert len(l) == 2

@pytest.mark.asyncio
@pytest.mark.filterwarnings('ignore::RuntimeWarning') # this test will stop the coro before scheduler awaits for it
async def test_one_shot_timeouted():
    # GIVEN
    scheduler = Scheduler()
    l = []
    async def increase_counter():
        l.append(1)

    # WHEN
    t = scheduler.schedule_task(DateTimeScheduledTask(increase_counter(), datetime.datetime.now() + datetime.timedelta(0,0.3)))
    try: 
        await asyncio.wait_for(t, timeout=0.2)
    except asyncio.exceptions.TimeoutError:
        pass

    # THEN
    assert len(l) == 0

@pytest.mark.asyncio
@pytest.mark.filterwarnings('ignore::RuntimeWarning') # this test will stop the coro before scheduler awaits for it
async def test_one_shot_timeouted_conv():
    # GIVEN
    scheduler = Scheduler()
    l = []
    async def increase_counter():
        l.append(1)

    # WHEN
    t = scheduler.schedule_datetime_task(increase_counter(), datetime.datetime.now() + datetime.timedelta(0,0.3))
    try: 
        await asyncio.wait_for(t, timeout=0.2)
    except asyncio.exceptions.TimeoutError:
        pass

    # THEN
    assert len(l) == 0

@pytest.mark.asyncio
async def test_one_shot():
    # GIVEN
    scheduler = Scheduler()
    l = []
    async def increase_counter():
        l.append(1)

    # WHEN
    t = scheduler.schedule_task(DateTimeScheduledTask(increase_counter(), datetime.datetime.now() +
                                                      datetime.timedelta(0, 0.1)))
    try: 
        await asyncio.wait_for(t, timeout=0.2)
    except asyncio.exceptions.TimeoutError:
        pass

    # THEN
    assert len(l) == 1


@pytest.mark.asyncio
async def test_one_shot_timestamp():
    # GIVEN
    scheduler = Scheduler()
    l = []

    async def increase_counter():
        l.append(1)

    # WHEN
    t = scheduler.schedule_task(TimestampScheduledTask(increase_counter(), time.time() + 0.1))
    try:
        await asyncio.wait_for(t, timeout=0.2)
    except asyncio.exceptions.TimeoutError:
        pass

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
    try:
        await asyncio.wait_for(t, timeout=0.2)
    except asyncio.exceptions.TimeoutError:
        pass

    # THEN
    assert len(l) == 1


@pytest.mark.asyncio
async def test_suspend():
    # GIVEN
    scheduler = Scheduler()
    l = []
    async def increase_counter():
        l.append(1)

    # WHEN
    t = scheduler.schedule_task(DateTimeScheduledTask(increase_counter(), datetime.datetime.now() + datetime.timedelta(0,0.3)))
    scheduler.suspend(None)
    assert len(l) == 0
    try: 
        await asyncio.wait_for(t, timeout=0.4)
    except asyncio.exceptions.TimeoutError:
        pass

    # THEN
    assert len(l) == 0

@pytest.mark.asyncio
async def test_suspend_then_resume():
    # GIVEN
    scheduler = Scheduler()
    l = []
    async def increase_counter():
        l.append(1)

    # WHEN
    t = scheduler.schedule_task(PeriodicScheduledTask(increase_counter, 0.2))
    scheduler.suspend(None)
    assert len(l) == 0
    await asyncio.sleep(0.5)
    scheduler.resume(None)
    await asyncio.sleep(0.5)

    # THEN
    assert len(l) == 3


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
    result = await asyncio.wait_for(scheduler.schedule_process_task(InstantScheduledProcessTask(do_exp_stuff)), timeout=100)
    result2 = await asyncio.wait_for(scheduler.schedule_process_task(InstantScheduledProcessTask(do_exp_stuff)), timeout=100)
    result3 = await asyncio.wait_for(scheduler.schedule_process_task(InstantScheduledProcessTask(do_exp_stuff)), timeout=100)
    result4 = await asyncio.wait_for(scheduler.schedule_process_task(InstantScheduledProcessTask(SimpleObj().do_exp_stuff)), timeout=100)

    # THEN
    assert result == 1337
    assert result2 == 1337
    assert result3 == 1337
    assert result4 == 1337

async def do_exp_stuff_mult_steps():
    result = 45
    await asyncio.sleep(1)
    result += 1
    await asyncio.sleep(1)
    return result

def cond():
    return True

@pytest.mark.asyncio
async def test_cond_task_as_process():
    # GIVEN
    scheduler = Scheduler(num_process_parallel=16)


    # WHEN
    result = await asyncio.wait_for(scheduler.schedule_process_task(ConditionalProcessTask(do_exp_stuff, cond)), timeout=100)
    result2 = await asyncio.wait_for(scheduler.schedule_process_task(ConditionalProcessTask(do_exp_stuff, cond)), timeout=100)
    result3 = await asyncio.wait_for(scheduler.schedule_process_task(ConditionalProcessTask(do_exp_stuff, cond)), timeout=100)
    result4 = await asyncio.wait_for(scheduler.schedule_process_task(ConditionalProcessTask(SimpleObj().do_exp_stuff, cond)), timeout=100)

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
    task = scheduler.schedule_process_task(InstantScheduledProcessTask(do_exp_stuff_mult_steps), marker)
    scheduler.suspend(marker)

    time.sleep(3)

    scheduler.resume(marker)


    assert await asyncio.wait_for(task, timeout=3) == 46

@pytest.mark.asyncio
async def test_task_as_process_suspend():
    # GIVEN
    scheduler = Scheduler(num_process_parallel=16)
    marker = 155

    # WHEN
    task = scheduler.schedule_process_task(InstantScheduledProcessTask(do_exp_stuff_mult_steps), marker)
    scheduler.suspend(marker)

    # THEN
    try:
        await asyncio.wait_for(task, timeout=3)
        pytest.fail()
    except asyncio.exceptions.TimeoutError as err:
        pass

    scheduler.resume(marker)
        
    scheduler.shutdown()
    
