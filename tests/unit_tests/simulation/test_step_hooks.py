"""on_step hooks of agents and roles: only overridden hooks are called, and
agents registered or roles added during a run are picked up."""

import pytest

from mango import Agent, Role, RoleAgent, create_world


class Stepper(Agent):
    def __init__(self):
        super().__init__()
        self.steps = 0

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        self.steps += 1


class Quiet(Agent):
    def handle_message(self, content, meta):
        pass


class SteppingRole(Role):
    def __init__(self):
        super().__init__()
        self.steps = 0

    def on_step(self, env, clock, step_size_s):
        self.steps += 1


@pytest.mark.asyncio
async def test_hooks_of_agents_and_roles_added_during_the_run_are_called():
    world = create_world(seed=1)
    first = world.register(Stepper(), "first")
    holder = world.register(RoleAgent(), "holder")
    world.register(Quiet(), "quiet")
    async with world:
        await world.step(step_size_s=1.0)
        assert [agent for agent, _, _ in world._steppers()] == [first]
        later = world.register(Stepper(), "later")
        role = SteppingRole()
        holder.add_role(role)
        await world.step(step_size_s=1.0)
        assert (first.steps, later.steps, role.steps) == (2, 1, 1)
        holder.remove_role(role)
        world.deregister("later")
        await world.step(step_size_s=1.0)
        assert (first.steps, later.steps, role.steps) == (3, 1, 1)


@pytest.mark.asyncio
async def test_an_instance_level_hook_set_before_registration_is_called():
    world = create_world(seed=1)
    quiet = Quiet()
    calls = []
    quiet.on_step = lambda env, clock, step_size_s: calls.append(step_size_s)
    world.register(quiet, "quiet")
    async with world:
        await world.step(step_size_s=2.0)
    assert calls == [2.0]
