"""Exceptions escaping agent tasks fail the step in which they happen."""

import logging

import pytest

from mango import (
    Agent,
    AgentAddress,
    AgentError,
    AgentTaskError,
    Role,
    RoleAgent,
    create_world,
    record_world,
)


class Sink(Agent):
    """Spawns async work per message; the work raises."""

    def __init__(self):
        super().__init__()
        self.received = 0

    def handle_message(self, content, meta):
        self.received += 1

        async def reply():
            raise AttributeError("'str' object has no attribute 'aid'")

        self.schedule_instant_task(reply())


class Source(Agent):
    def __init__(self, target_aid, at=1.0):
        super().__init__()
        self.target_aid = target_aid
        self.at = at

    def handle_message(self, content, meta):
        pass

    def on_ready(self):
        self.schedule_timestamp_task(self._send(), self.at)

    async def _send(self):
        target = self.context._container._agents[self.target_aid]
        await self.send_message("ping", target.addr)


class PeriodicFailure(Agent):
    def handle_message(self, content, meta):
        pass

    def on_ready(self):
        self.schedule_periodic_task(self.tick, 2.0)

    async def tick(self):
        if self.scheduler.clock.time >= 2.0:
            raise ValueError(f"tick at {self.scheduler.clock.time}")


@pytest.mark.asyncio
async def test_async_handler_error_fails_the_step_after_recording():
    world = create_world(seed=1)
    sink = world.register(Sink(), "sink")
    world.register(Source("sink"), "src")
    record_world(world, "received", lambda: sink.received)
    async with world:
        await world.step(step_size_s=0.5)  # nothing happens yet
        with pytest.raises(AgentTaskError) as info:
            await world.step(step_size_s=1.0)
    error = info.value
    assert "Agent 'sink'" in str(error)
    assert "Sink.handle_message.<locals>.reply" in str(error)
    assert "raise_agent_errors=False" in str(error)
    assert isinstance(error.__cause__, AttributeError)
    assert [type(e) for e in error.errors] == [AgentError]
    assert error.errors[0].aid == "sink"
    assert error.errors[0].time == 1.5
    # the failing step completed: time advanced and it was recorded
    assert world.clock.time == 1.5
    assert world.data_collections["received"].timeseries[-1] == 1


@pytest.mark.asyncio
async def test_periodic_task_error_fails_the_step():
    world = create_world(seed=1)
    world.register(PeriodicFailure(), "p")
    async with world:
        await world.step(step_size_s=1.0)
        with pytest.raises(AgentTaskError, match=r"PeriodicFailure.tick.*t=2 s"):
            await world.step(step_size_s=1.0)


@pytest.mark.asyncio
async def test_discrete_event_run_stops_at_the_failing_step():
    world = create_world(seed=1)
    world.register(PeriodicFailure(), "p")
    async with world:
        with pytest.raises(AgentTaskError):
            await world.step_until(10.0)
        assert world.clock.time == 2.0


@pytest.mark.asyncio
async def test_role_coroutine_error_fails_the_step():
    class Failing(Role):
        def on_ready(self):
            self.context.schedule_timestamp_task(self.work(), 1.0)

        async def work(self):
            raise KeyError("missing")

    world = create_world(seed=1)
    agent = RoleAgent()
    agent.add_role(Failing())
    world.register(agent, "with_role")
    async with world:
        with pytest.raises(AgentTaskError, match="with_role") as info:
            await world.step(step_size_s=1.0)
    assert isinstance(info.value.__cause__, KeyError)


@pytest.mark.asyncio
async def test_error_in_a_task_started_on_ready_is_raised_on_entering():
    class FailsAtStart(Agent):
        def handle_message(self, content, meta):
            pass

        def on_ready(self):
            self.schedule_instant_task(self.boom())

        async def boom(self):
            raise RuntimeError("start")

    world = create_world(seed=1)
    world.register(FailsAtStart(), "f")
    with pytest.raises(AgentTaskError, match="start"):
        async with world:
            pass


@pytest.mark.asyncio
async def test_further_errors_of_the_same_step_are_counted():
    world = create_world(seed=1)
    world.register(Sink(), "sink")
    for i in range(3):
        world.register(Source("sink"), f"src{i}")
    async with world:
        with pytest.raises(AgentTaskError, match=r"2 further task errors") as info:
            await world.step(step_size_s=1.0)
    assert len(info.value.errors) == 3


@pytest.mark.asyncio
async def test_opt_out_only_logs_and_collects(caplog):
    world = create_world(seed=1, raise_agent_errors=False)
    world.register(Sink(), "sink")
    world.register(Source("sink"), "src")
    with caplog.at_level(logging.ERROR, logger="mango.util.scheduling"):
        async with world:
            await world.step_until(5.0)
    assert [e.aid for e in world.agent_errors] == ["sink"]
    assert isinstance(world.agent_errors[0].exception, AttributeError)
    assert "got exception in scheduled event" in caplog.text


@pytest.mark.asyncio
async def test_errors_are_reported_once():
    world = create_world(seed=1)
    world.register(Sink(), "sink")
    world.register(Source("sink"), "src")
    async with world:
        with pytest.raises(AgentTaskError):
            await world.step(step_size_s=1.0)
        await world.step(step_size_s=1.0)  # nothing new failed
    assert len(world.agent_errors) == 1


@pytest.mark.asyncio
async def test_synchronous_handler_error_still_stops_the_world():
    class Raises(Agent):
        def handle_message(self, content, meta):
            raise AttributeError("sync boom")

    world = create_world(seed=1)
    world.register(Raises(), "sink")
    world.register(Source("sink"), "src")
    async with world:
        with pytest.raises(RuntimeError, match="stopped processing messages"):
            await world.step(step_size_s=1.0)


class Shouter(Agent):
    def __init__(self, n):
        super().__init__()
        self.n = n

    def on_step(self, env, clock, step_size_s):
        for i in range(self.n):
            self.schedule_instant_message("hi", AgentAddress("simulation", f"leaf_{i}"))


@pytest.mark.asyncio
async def test_handle_message_parameters_may_have_any_name():
    class Short(Agent):
        def __init__(self):
            super().__init__()
            self.got = []

        def handle_message(self, c, m):
            self.got.append((c, m["sender_id"]))

    world = create_world(seed=1)
    leaf = world.register(Short(), "leaf_0")
    world.register(Shouter(1), "root")
    async with world:
        await world.step(step_size_s=1.0)
    assert leaf.got == [("hi", "root")]


@pytest.mark.asyncio
async def test_many_failing_handlers_give_one_report(caplog):
    class Narrow(Agent):
        def handle_message(self, content):  # meta forgotten
            pass

    world = create_world(seed=1)
    for i in range(40):
        world.register(Narrow(), f"leaf_{i}")
    world.register(Shouter(40), "root")
    caplog.set_level(logging.DEBUG)
    async with world:
        with pytest.raises(RuntimeError) as info:
            await world.step(step_size_s=1.0)
    message = str(info.value)
    assert "Agent 'leaf_0' stopped processing messages" in message
    assert "39 more agents failed the same way ('leaf_1', 'leaf_2', 'leaf_3', ...)" in (
        message
    )
    assert "def handle_message(self, content, meta)" in message
    assert isinstance(info.value.__cause__, TypeError)
    loud = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert loud == []  # the world raises it: no traceback per agent
