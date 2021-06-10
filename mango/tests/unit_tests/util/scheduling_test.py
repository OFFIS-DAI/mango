
import pytest
import asyncio, datetime

from mango.util.scheduling import DateTimeScheduledTask, Scheduler, PeriodicScheduledTask

@pytest.mark.asyncio
async def test_periodic():
    scheduler = Scheduler()
    l = []
    async def increase_counter():
        l.append(1)
    t = scheduler.schedule_task(PeriodicScheduledTask(increase_counter, 2))
    try: 
        await asyncio.wait_for(t, timeout=3)
    except asyncio.exceptions.TimeoutError:
        pass

    assert len(l) == 2

@pytest.mark.asyncio
async def test_one_shot_timeouted():
    scheduler = Scheduler()
    l = []
    async def increase_counter():
        l.append(1)
    t = scheduler.schedule_task(DateTimeScheduledTask(increase_counter(), datetime.datetime.now() + datetime.timedelta(0,3)))
    try: 
        await asyncio.wait_for(t, timeout=2)
    except asyncio.exceptions.TimeoutError:
        pass

    assert len(l) == 0


@pytest.mark.asyncio
async def test_one_shot():
    scheduler = Scheduler()
    l = []
    async def increase_counter():
        l.append(1)
    t = scheduler.schedule_task(DateTimeScheduledTask(increase_counter(), datetime.datetime.now() + datetime.timedelta(0,3)))
    try: 
        await asyncio.wait_for(t, timeout=4)
    except asyncio.exceptions.TimeoutError:
        pass

    assert len(l) == 1