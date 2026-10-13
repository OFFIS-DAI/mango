"""Settling waits only for running tasks (O(active)), drains the loop on request."""

import asyncio
import logging

import pytest

import mango.util.termination_detection as td
from mango import Agent, create_world
from mango.util.scheduling import Scheduler


class Idle(Agent):
    """Owns one periodic task that is never due within the tests."""

    def handle_message(self, content, meta):
        pass

    def on_ready(self):
        async def start():
            self.schedule_periodic_task(self.tick, 1e6)

        self.schedule_timestamp_task(start(), 1e5)

    async def tick(self):
        pass


class Ticker(Agent):
    def __init__(self):
        super().__init__()
        self.ticks = 0

    def handle_message(self, content, meta):
        pass

    def on_ready(self):
        self.schedule_periodic_task(self.tick, 1.0)

    async def tick(self):
        self.ticks += 1


def _count_waits(monkeypatch) -> list:
    calls = []
    original = asyncio.wait

    async def counting_wait(*args, **kwargs):
        calls.append(args)
        return await original(*args, **kwargs)

    monkeypatch.setattr(td.asyncio, "wait", counting_wait)
    return calls


@pytest.mark.asyncio
async def test_sleeping_tasks_of_idle_agents_are_not_awaited(monkeypatch):
    world = create_world(seed=1)
    ticker = world.register(Ticker(), "ticker")
    for i in range(200):
        world.register(Idle(), f"idle{i}")
    async with world:
        waits = _count_waits(monkeypatch)
        for _ in range(5):
            await world.step(step_size_s=1.0)
    assert ticker.ticks == 6  # at t=0 and after each of the five steps
    # a few waits per woken tick at most, never one per sleeping idle task
    assert len(waits) <= 5 * 2


@pytest.mark.asyncio
async def test_settle_waits_for_tasks_spawned_while_settling():
    world = create_world(seed=1)
    done = []

    class Chain(Agent):
        def handle_message(self, content, meta):
            pass

        def on_ready(self):
            self.schedule_instant_task(self.step(0))

        async def step(self, i):
            await asyncio.sleep(0)
            done.append(i)
            if i < 20:
                self.schedule_instant_task(self.step(i + 1))

    world.register(Chain(), "chain")
    async with world:
        pass  # entering the world settles the agents
    assert done == list(range(21))


@pytest.mark.asyncio
async def test_draining_settle_runs_queued_callbacks_of_foreign_tasks():
    world = create_world(seed=1)
    world.register(Ticker(), "ticker")
    flag = []

    async def foreign():
        for _ in range(50):
            await asyncio.sleep(0)
        flag.append(True)

    async with world:
        asyncio.ensure_future(foreign())
        await td.tasks_complete_or_sleeping(world.container, drain_loop=True)
        assert flag == [True]


@pytest.mark.asyncio
async def test_draining_settle_is_bounded_when_the_loop_never_quiets(monkeypatch):
    monkeypatch.setattr(td, "MAX_QUIET_TURNS", 25)
    world = create_world(seed=1)
    world.register(Ticker(), "ticker")
    stop = asyncio.Event()

    async def spinner():
        while not stop.is_set():
            await asyncio.sleep(0)

    async with world:
        spin = asyncio.ensure_future(spinner())
        await asyncio.wait_for(
            td.tasks_complete_or_sleeping(world.container, drain_loop=True), 5
        )
        stop.set()
        await spin


@pytest.mark.asyncio
async def test_unfinished_task_count_counts_running_tasks_only():
    world = create_world(seed=1)
    world.register(Idle(), "idle")
    async with world:
        assert td.unfinished_task_count(world.container) == 0


@pytest.mark.asyncio
async def test_scheduler_settle_waits_for_its_own_running_tasks():
    scheduler = Scheduler()
    finished = []

    async def work():
        await asyncio.sleep(0.01)
        finished.append(True)

    scheduler.schedule_instant_task(work())
    await scheduler.tasks_complete_or_sleeping()
    assert finished == [True]
    await scheduler.shutdown()


# ------------------------------------------------------------- error hook


@pytest.mark.asyncio
async def test_scheduler_error_hook_receives_task_and_exception(caplog):
    scheduler = Scheduler()
    seen = []
    scheduler.on_task_error = lambda task, exc: seen.append((task, exc))

    async def boom():
        raise ValueError("boom")

    with caplog.at_level(logging.ERROR, logger="mango.util.scheduling"):
        fut = scheduler.schedule_instant_task(boom())
        with pytest.raises(ValueError):
            await fut
        await asyncio.sleep(0)
    assert len(seen) == 1
    assert isinstance(seen[0][1], ValueError)
    assert "got exception in scheduled event" in caplog.text  # still logged
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_scheduler_error_hook_returning_true_suppresses_the_log(caplog):
    scheduler = Scheduler()
    scheduler.on_task_error = lambda task, exc: True

    async def boom():
        raise ValueError("boom")

    with caplog.at_level(logging.ERROR, logger="mango.util.scheduling"):
        fut = scheduler.schedule_instant_task(boom())
        with pytest.raises(ValueError):
            await fut
        await asyncio.sleep(0)
    assert "got exception in scheduled event" not in caplog.text
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_cancelled_tasks_are_not_reported_as_errors():
    scheduler = Scheduler()
    seen = []
    scheduler.on_task_error = lambda task, exc: seen.append(exc)

    async def forever():
        await asyncio.sleep(100)

    fut = scheduler.schedule_instant_task(forever())
    await asyncio.sleep(0)
    await scheduler.shutdown()
    assert fut.cancelled()
    assert seen == []


# ------------------------------------------------ only active agents are looked at


def _count_scanned(monkeypatch) -> list:
    """Record the scheduler of every agent a settle looks at."""
    seen = []
    original = td._collect_pending

    def counting(scheduler, except_sources, pending):
        seen.append(scheduler)
        return original(scheduler, except_sources, pending)

    monkeypatch.setattr(td, "_collect_pending", counting)
    return seen


async def _scanned_per_step(monkeypatch, idle: int) -> int:
    world = create_world(seed=1)
    world.register(Ticker(), "ticker")
    for i in range(idle):
        world.register(Idle(), f"idle_{i}")
    async with world:
        await world.step(step_size_s=1.0)  # every agent starts and settles once
        seen = _count_scanned(monkeypatch)
        for _ in range(5):
            await world.step(step_size_s=1.0)
    monkeypatch.undo()
    return len(seen)


@pytest.mark.asyncio
async def test_settles_look_only_at_agents_with_work(monkeypatch):
    few = await _scanned_per_step(monkeypatch, 10)
    many = await _scanned_per_step(monkeypatch, 500)
    assert few == many  # independent of the number of idle agents
    assert 0 < many <= 5 * 3  # the ticker, a few settles per step


@pytest.mark.asyncio
async def test_messages_and_tasks_mark_their_agent_active():
    world = create_world(seed=1)
    a = world.register(Idle(), "a")
    b = world.register(Idle(), "b")
    activity = world._container._activity
    async with world:
        await world.step(step_size_s=1.0)
        assert activity.candidates() == []
        await a.send_message("hi", b.addr)
        await world._container._deliver_messages_due(world.clock.time)
        assert activity.candidates() == [b]  # its inbox has a message
        await world.step(step_size_s=1.0)
        assert activity.candidates() == []

        async def work():
            await asyncio.sleep(0)

        a.schedule_instant_task(work())
        assert activity.candidates() == [a]  # a running task
        await world.step(step_size_s=1.0)
        assert activity.candidates() == []


class Counter(Agent):
    def __init__(self):
        super().__init__()
        self.received = 0

    def handle_message(self, content, meta):
        self.received += 1


@pytest.mark.asyncio
async def test_a_swapped_plain_queue_inbox_is_still_waited_for():
    world = create_world(seed=1)
    sender = world.register(Counter(), "sender")
    early = Counter()
    early.inbox = asyncio.Queue()  # before registration: no on_put hook
    world.register(early, "early")
    late = world.register(Counter(), "late")
    late.inbox = asyncio.Queue()  # after registration, before the start
    async with world:
        await world.step(step_size_s=1.0)
        for receiver in (early, late):
            await sender.send_message("x", receiver.addr)
        await world.step(step_size_s=1.0)
        assert (early.received, late.received) == (1, 1)
    untracked = world._container._activity._untracked
    assert set(untracked.values()) == {early, late}  # looked at in every settle


@pytest.mark.asyncio
async def test_deregistered_agents_leave_the_activity_tracker():
    world = create_world(seed=1)
    world.register(Ticker(), "ticker")
    gone = world.register(Idle(), "gone")
    async with world:
        await world.step(step_size_s=1.0)
        world.deregister("gone")
        gone.inbox.put_nowait((0, "late", {}))  # no longer reported
        assert gone not in world._container._activity.candidates()
        await world.step(step_size_s=1.0)


def test_external_clock_wakes_sleepers_in_time_then_sleep_order():
    from mango.util.clock import ExternalClock

    async def main():
        clock = ExternalClock()
        order = []
        for name, delay in (("b", 2.0), ("a1", 1.0), ("c", 3.0), ("a2", 1.0)):
            clock.sleep(delay).add_done_callback(lambda _, n=name: order.append(n))
        assert clock.get_next_activity() == 1.0
        clock.set_time(2.0)
        await asyncio.sleep(0)
        assert order == ["a1", "a2", "b"]
        assert clock.get_next_activity() == 3.0

    asyncio.run(main())
