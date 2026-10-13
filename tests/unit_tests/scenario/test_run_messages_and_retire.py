"""Scenario runs: delivered messages are counted, not kept (unless opted in);
a retired agent is not stepped again, not even later in the same step."""

import asyncio

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("pandas")

from mango import Agent, Area2D, DefaultEnvironment, Position2D  # noqa: E402
from mango.scenario import Scenario  # noqa: E402
from mango.scenario.runtime import run_sync  # noqa: E402


class Pinger(Agent):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.received = 0

    def handle_message(self, content, meta):
        self.received += 1

    def on_step(self, env, clock, step_size_s):
        for other in self.ctx.agents("node"):
            if other is not self:
                self.schedule_instant_message({"big": list(range(50))}, other.addr)


def pinging():
    scenario = Scenario("pings")
    scenario.time(horizon=4, step=1)
    scenario.group("node", 3, lambda e: Pinger(e.ctx))
    scenario.record("received", "node", attr="received")
    return scenario


def test_scenario_runs_count_but_do_not_keep_delivered_messages():
    result = pinging().run(seed=1)
    world = result.world
    assert result.stats["messages_delivered"] == world.messages_delivered == 24
    assert world.record_messages is False
    assert world.container.recorded_messages == []
    with pytest.raises(
        RuntimeError, match=r"scenario.run\(\.\.\., record_messages=True\)"
    ):
        world.recorded_messages  # noqa: B018


def test_a_run_opts_in_to_keep_delivered_messages():
    run = pinging().build(seed=1)
    run.record_messages = True
    result = run_sync(run.execute)
    assert len(result.world.recorded_messages) == 24
    assert result.checksum() == pinging().run(seed=1).checksum()
    kept = pinging().run(seed=1, record_messages=True)
    assert len(kept.world.recorded_messages) == 24
    assert pinging().build(seed=1, record_messages=True).record_messages is True


def test_the_record_world_docstring_example_counts_messages():
    scenario = pinging()
    scenario.record_world("n_msgs", lambda ctx: ctx.world.messages_delivered)
    result = scenario.run(seed=1)
    assert result.ts("n_msgs").iloc[-1] == 24


CALLS: list = []


class Fox(Agent):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx

    def on_step(self, env, clock, step_size_s):
        if clock.time == 1:
            self.ctx.retire(self.ctx.entity("rabbit_0"))


class Rabbit(Agent):
    def on_step(self, env, clock, step_size_s):
        env.space.location(self)  # a retired rabbit has left the space
        CALLS.append((clock.time, self.aid))


def test_a_retired_agent_is_not_stepped_later_in_the_same_step():
    CALLS.clear()
    scenario = Scenario("meadow")
    scenario.time(horizon=3, step=1)
    scenario.environment(lambda ctx: DefaultEnvironment(space=Area2D(10, 10)))
    scenario.group("fox", 1, lambda e: Fox(e.ctx))  # declared before the prey
    scenario.group(
        "rabbit",
        2,
        lambda e: Rabbit(),
        position=lambda e: Position2D(float(e.index), 0.0),
    )
    scenario.run(seed=1)
    assert CALLS == [
        (0.0, "rabbit_0"),
        (0.0, "rabbit_1"),
        (1.0, "rabbit_1"),
        (2.0, "rabbit_1"),
    ]


def test_the_opt_in_works_with_async_with():
    # the example of the ScenarioRun docstring
    run = pinging().build(seed=1)
    run.record_messages = True

    async def main():
        async with run:
            await run.run()
            return len(run.world.recorded_messages)

    assert asyncio.run(main()) == 24
