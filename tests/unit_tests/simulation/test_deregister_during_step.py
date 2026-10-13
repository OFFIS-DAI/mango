"""An agent deregistered during a step is not stepped again, not even later in that step."""

import pytest

from mango import (
    Agent,
    Area2D,
    DefaultEnvironment,
    Position2D,
    Role,
    RoleAgent,
    create_world,
)

CALLS: list = []


class Fox(Agent):
    def __init__(self, prey_aid):
        super().__init__()
        self.prey_aid = prey_aid

    def on_step(self, env, clock, step_size_s):
        CALLS.append((clock.time, self.aid))
        if clock.time == 1.0:
            self.context.deregister(self.prey_aid)
            env.space._positions.pop(self.prey_aid, None)


class Rabbit(Agent):
    def on_step(self, env, clock, step_size_s):
        env.space.location(self)  # raises once the rabbit has left the space
        CALLS.append((clock.time, self.aid))


class Steps(Role):
    def on_step(self, env, clock, step_size_s):
        CALLS.append((clock.time, "role"))


class Quitter(RoleAgent):
    """Deregisters itself in its own on_step: its roles are not stepped then."""

    def on_step(self, env, clock, step_size_s):
        CALLS.append((clock.time, self.aid))
        if clock.time == 1.0:
            self.context.deregister(self.aid)


@pytest.mark.asyncio
async def test_prey_retired_by_an_earlier_stepper_is_skipped_in_the_same_step():
    CALLS.clear()
    space = Area2D(width=10.0, height=10.0)
    world = create_world(seed=1, environment=DefaultEnvironment(space=space))
    world.register(Fox("rabbit_0"), "fox")  # steps first (registration order)
    rabbits = [world.register(Rabbit(), f"rabbit_{i}") for i in range(2)]
    for rabbit in rabbits:
        space.move(rabbit, Position2D(1.0, 1.0))
    async with world:
        for _ in range(3):
            await world.step(1.0)
    assert (1.0, "rabbit_0") not in CALLS
    assert [c for c in CALLS if c[1] == "rabbit_0"] == [(0.0, "rabbit_0")]
    assert (2.0, "rabbit_1") in CALLS


@pytest.mark.asyncio
async def test_an_agent_that_leaves_in_on_step_does_not_step_its_roles():
    CALLS.clear()
    world = create_world(seed=1)
    agent = world.register(Quitter(), "q")
    agent.add_role(Steps())
    async with world:
        for _ in range(3):
            await world.step(1.0)
    assert CALLS == [(0.0, "q"), (0.0, "role"), (1.0, "q")]


@pytest.mark.asyncio
async def test_a_global_event_handler_may_retire_another_agent():
    seen = []

    class Listener(Agent):
        def on_global_event(self, event):
            seen.append(self.aid)
            if self.aid == "a":
                self.context.deregister("b")

    world = create_world(seed=1)
    for aid in ("a", "b", "c"):
        world.register(Listener(), aid)
    async with world:
        world.environment.emit_global_event("go")
    assert seen == ["a", "c"]
