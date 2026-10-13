"""Beginner traps in mango.Agent give errors that name the fix."""

import asyncio

import pytest

from mango import Agent, create_world


class Plain(Agent):
    def handle_message(self, content, meta):
        pass


@pytest.mark.parametrize(
    ("name", "hint"),
    [
        ("color", "update_description(color=...)"),
        ("name", "update_description(name=...)"),
        ("category", "update_description(category=...)"),
        ("description", "update_description(...)"),
        ("uid", "choose another attribute name"),
        ("aid", "suggested_aid"),
        ("addr", "choose another attribute name"),
        ("inbox", "message queue"),
        ("context", "container context"),
        ("scheduler", "task scheduler"),
    ],
)
def test_assigning_a_reserved_name_says_it_is_reserved(name, hint):
    class Resident(Agent):
        def __init__(self, value):
            super().__init__()
            setattr(self, name, value)

        def handle_message(self, content, meta):
            pass

    with pytest.raises(AttributeError) as info:
        Resident(["red"])
    message = str(info.value)
    assert f"{name!r} is reserved by mango.Agent" in message
    assert hint in message


@pytest.mark.asyncio
async def test_color_example_from_the_docs_works():
    class Resident(Agent):
        def __init__(self, team):
            super().__init__()
            self.team = team

        def handle_message(self, content, meta):
            pass

        def on_register(self):
            self.update_description(color=self.team)

    world = create_world(seed=1)
    resident = world.register(Resident("red"), "r")
    assert resident.color == "red"


def test_values_of_the_right_type_can_still_be_swapped():
    agent = Plain()
    queue = asyncio.Queue()
    agent.inbox = queue
    assert agent.inbox is queue
    agent.scheduler = None
    assert agent.scheduler is None
    agent.context = None
    assert agent.context is None


@pytest.mark.asyncio
async def test_missing_super_init_names_the_fix():
    class NoSuper(Agent):
        def __init__(self):
            self.x = 1

        def handle_message(self, content, meta):
            pass

    world = create_world(seed=1)
    with pytest.raises(
        AttributeError, match=r"Call super\(\).__init__\(\) first in NoSuper.__init__"
    ):
        world.register(NoSuper(), "n")


def test_missing_attribute_of_an_initialised_agent_keeps_the_plain_message():
    agent = Plain()
    with pytest.raises(AttributeError) as info:
        agent.does_not_exist  # noqa: B018
    assert "super()" not in str(info.value)
    assert not hasattr(agent, "roles")


@pytest.mark.asyncio
async def test_rng_is_cached_per_registration():
    world = create_world(seed=3)
    agent = world.register(Plain(), "a")
    assert agent.rng is agent.rng
    assert agent.rng is world.rng("agent/a")
    other = create_world(seed=3)
    moved = Plain()
    other.register(moved, "b")
    assert moved.rng is other.rng("agent/b")


def test_assigning_a_roles_context_says_it_is_reserved():
    from mango import Role

    class Remembers(Role):
        def __init__(self):
            super().__init__()
            self.context = {}

    with pytest.raises(AttributeError) as info:
        Remembers()
    message = str(info.value)
    assert "'context' is reserved by mango.Role" in message
    assert "self.situation" in message


def test_a_role_keeps_its_context_after_being_added():
    from mango import Role, RoleAgent

    role = Role()
    agent = RoleAgent()
    agent.add_role(role)
    assert role.context is agent._role_context
