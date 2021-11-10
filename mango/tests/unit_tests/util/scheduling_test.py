
import pytest
import asyncio, datetime

from mango.util.scheduling import DateTimeScheduledTask, Scheduler, PeriodicScheduledTask

@pytest.mark.asyncio
async def test_periodic():
    # GIVEN
    scheduler = Scheduler()
    l = []
    async def increase_counter():
        l.append(1)

    # WHEN
    t = scheduler.schedule_task(PeriodicScheduledTask(increase_counter, 2))
    try: 
        await asyncio.wait_for(t, timeout=3)
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
    t = scheduler.schedule_task(DateTimeScheduledTask(increase_counter(), datetime.datetime.now() + datetime.timedelta(0,3)))
    try: 
        await asyncio.wait_for(t, timeout=2)
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
    t = scheduler.schedule_task(DateTimeScheduledTask(increase_counter(), datetime.datetime.now() + datetime.timedelta(0,3)))
    try: 
        await asyncio.wait_for(t, timeout=4)
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
    t = scheduler.schedule_task(DateTimeScheduledTask(increase_counter(), datetime.datetime.now() + datetime.timedelta(0,3)))
    scheduler.suspend(None)
    assert len(l) == 0
    try: 
        await asyncio.wait_for(t, timeout=4)
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
    t = scheduler.schedule_task(PeriodicScheduledTask(increase_counter, 2))
    scheduler.suspend(None)
    assert len(l) == 0
    await asyncio.sleep(5)
    scheduler.resume(None)
    await asyncio.sleep(5)

    # THEN
    assert len(l) == 3