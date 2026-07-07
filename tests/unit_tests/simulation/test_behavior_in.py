from typing import Any

import pytest

from mango import (
    Agent,
    RoleAgent,
    behavior_in,
    create_world,
    run_with_simulation,
    step_simulation,
)
from mango.agent.role import Role


class SimpleAgent(Agent):
    def __init__(self):
        super().__init__()
        self.messages: list[tuple[Any, dict]] = []

    def handle_message(self, content, meta):
        self.messages.append((content, meta))


class TypedMessage:
    def __init__(self, value):
        self.value = value


class OtherMessage:
    pass


class AlarmEvent:
    pass


class OtherEvent:
    pass


@pytest.mark.asyncio
async def test_on_message_all_agents():
    received = []
    world = create_world()
    a1 = world.register(SimpleAgent())
    a2 = world.register(SimpleAgent())
    sender = world.register(SimpleAgent())

    behavior_in(
        world,
        lambda agent, content, meta: received.append((agent.aid, content)),
        on_message=TypedMessage,
    )

    async with world:
        msg = TypedMessage(42)
        await world.send_message(msg, receiver_addr=a1.addr, sender_id=sender.aid)
        await world.send_message(msg, receiver_addr=a2.addr, sender_id=sender.aid)
        await step_simulation(world, step_size_s=1.0)

    assert any(aid == a1.aid for aid, _ in received)
    assert any(aid == a2.aid for aid, _ in received)


@pytest.mark.asyncio
async def test_on_message_type_filter():
    received = []
    world = create_world()
    a = world.register(SimpleAgent())
    sender = world.register(SimpleAgent())

    behavior_in(
        world,
        lambda agent, content, meta: received.append(content),
        on_message=TypedMessage,
    )

    async with world:
        await world.send_message(
            TypedMessage(1), receiver_addr=a.addr, sender_id=sender.aid
        )
        await world.send_message(
            OtherMessage(), receiver_addr=a.addr, sender_id=sender.aid
        )
        await step_simulation(world, step_size_s=1.0)

    assert len(received) == 1
    assert isinstance(received[0], TypedMessage)


@pytest.mark.asyncio
async def test_on_message_agent_type_filter():
    received = []

    class SpecialAgent(SimpleAgent):
        pass

    world = create_world()
    normal = world.register(SimpleAgent())
    special = world.register(SpecialAgent())
    sender = world.register(SimpleAgent())

    behavior_in(
        world,
        lambda agent, content, meta: received.append(agent.aid),
        on_message=TypedMessage,
        agent_types=SpecialAgent,
    )

    async with world:
        msg = TypedMessage(99)
        await world.send_message(msg, receiver_addr=normal.addr, sender_id=sender.aid)
        await world.send_message(msg, receiver_addr=special.addr, sender_id=sender.aid)
        await step_simulation(world, step_size_s=1.0)

    assert special.aid in received
    assert normal.aid not in received


@pytest.mark.asyncio
async def test_on_message_match_name():
    received = []
    world = create_world()
    a = world.register(SimpleAgent())
    other = world.register(SimpleAgent())
    sender = world.register(SimpleAgent())
    a.update_description(name="target")

    behavior_in(
        world,
        lambda agent, content, meta: received.append(agent.aid),
        on_message=TypedMessage,
        match_names="target",
    )

    async with world:
        msg = TypedMessage(1)
        await world.send_message(msg, receiver_addr=a.addr, sender_id=sender.aid)
        await world.send_message(msg, receiver_addr=other.addr, sender_id=sender.aid)
        await step_simulation(world, step_size_s=1.0)

    assert a.aid in received
    assert other.aid not in received


@pytest.mark.asyncio
async def test_on_message_match_color():
    received = []
    world = create_world()
    a = world.register(SimpleAgent())
    other = world.register(SimpleAgent())
    sender = world.register(SimpleAgent())
    a.update_description(color="blue")

    behavior_in(
        world,
        lambda agent, content, meta: received.append(agent.aid),
        on_message=TypedMessage,
        match_colors="blue",
    )

    async with world:
        msg = TypedMessage(1)
        await world.send_message(msg, receiver_addr=a.addr, sender_id=sender.aid)
        await world.send_message(msg, receiver_addr=other.addr, sender_id=sender.aid)
        await step_simulation(world, step_size_s=1.0)

    assert a.aid in received
    assert other.aid not in received


@pytest.mark.asyncio
async def test_on_global_event_all_agents():
    received = []
    world = create_world()
    a1 = world.register(SimpleAgent())
    a2 = world.register(SimpleAgent())

    behavior_in(
        world,
        lambda agent, event: received.append((agent.aid, event)),
        on_global_event=AlarmEvent,
    )

    async with world:
        world.environment.emit_global_event(AlarmEvent())
        world.environment.emit_global_event(OtherEvent())

    assert len(received) == 2
    assert {aid for aid, _ in received} == {a1.aid, a2.aid}


@pytest.mark.asyncio
async def test_on_global_event_type_filter():
    received = []
    world = create_world()
    world.register(SimpleAgent())

    behavior_in(
        world, lambda agent, event: received.append(event), on_global_event=AlarmEvent
    )

    async with world:
        world.environment.emit_global_event(OtherEvent())

    assert received == []


@pytest.mark.asyncio
async def test_on_global_event_agent_type_filter():
    received = []

    class SpecialAgent(SimpleAgent):
        pass

    world = create_world()
    normal = world.register(SimpleAgent())
    special = world.register(SpecialAgent())

    behavior_in(
        world,
        lambda agent, event: received.append(agent.aid),
        on_global_event=AlarmEvent,
        agent_types=SpecialAgent,
    )

    async with world:
        world.environment.emit_global_event(AlarmEvent())

    assert special.aid in received
    assert normal.aid not in received


@pytest.mark.asyncio
async def test_on_agent_event():
    received = []
    world = create_world()
    a = world.register(SimpleAgent())
    world.environment.install(a, agent_id=a.aid)

    behavior_in(
        world,
        lambda agent, event: received.append((agent.aid, event)),
        on_agent_event=AlarmEvent,
    )

    async with world:
        world.environment.emit_agent_event(AlarmEvent(), a.aid)
        world.environment.emit_agent_event(OtherEvent(), a.aid)

    assert len(received) == 1
    assert received[0][0] == a.aid
    assert isinstance(received[0][1], AlarmEvent)


@pytest.mark.asyncio
async def test_has_roles_filter():
    received = []

    class MyRole(Role):
        def setup(self):
            pass

    world = create_world()
    plain = world.register(SimpleAgent())
    roled = world.register(RoleAgent())
    roled.add_role(MyRole())
    sender = world.register(SimpleAgent())

    behavior_in(
        world,
        lambda agent, content, meta: received.append(agent.aid),
        on_message=TypedMessage,
        has_roles=MyRole,
    )

    async with world:
        msg = TypedMessage(1)
        await world.send_message(msg, receiver_addr=plain.addr, sender_id=sender.aid)
        await world.send_message(msg, receiver_addr=roled.addr, sender_id=sender.aid)
        await step_simulation(world, step_size_s=1.0)

    assert roled.aid in received
    assert plain.aid not in received


@pytest.mark.asyncio
async def test_role_types_message():
    received = []

    class CoordRole(Role):
        def setup(self):
            pass

        def handle_message(self, content, meta):
            pass

    world = create_world()
    agent = world.register(RoleAgent())
    role = CoordRole()
    agent.add_role(role)
    sender = world.register(SimpleAgent())

    behavior_in(
        world,
        lambda r, content, meta: received.append((r, content)),
        on_message=TypedMessage,
        role_types=CoordRole,
    )

    async with world:
        msg = TypedMessage(7)
        await world.send_message(msg, receiver_addr=agent.addr, sender_id=sender.aid)
        await step_simulation(world, step_size_s=1.0)

    assert len(received) == 1
    assert received[0][0] is role
    assert isinstance(received[0][1], TypedMessage)


@pytest.mark.asyncio
async def test_role_types_global_event():
    received = []

    class CoordRole(Role):
        def setup(self):
            pass

    world = create_world()
    agent = world.register(RoleAgent())
    role = CoordRole()
    agent.add_role(role)

    behavior_in(
        world,
        lambda r, event: received.append((r, event)),
        on_global_event=AlarmEvent,
        role_types=CoordRole,
    )

    async with world:
        world.environment.emit_global_event(AlarmEvent())

    assert len(received) == 1
    assert received[0][0] is role
    assert isinstance(received[0][1], AlarmEvent)


@pytest.mark.asyncio
async def test_no_filter_matches_all():
    received = []
    world = create_world()
    a1 = world.register(SimpleAgent())
    a2 = world.register(SimpleAgent())

    behavior_in(
        world,
        lambda agent, event: received.append(agent.aid),
        on_global_event=AlarmEvent,
    )

    async with world:
        world.environment.emit_global_event(AlarmEvent())

    assert a1.aid in received
    assert a2.aid in received


@pytest.mark.asyncio
async def test_behavior_in_does_not_suppress_handle_message():
    """behavior_in fires in addition to handle_message, not instead of."""
    behavior_fired = []
    world = create_world()
    a = world.register(SimpleAgent())
    sender = world.register(SimpleAgent())

    behavior_in(
        world,
        lambda agent, content, meta: behavior_fired.append(content),
        on_message=TypedMessage,
    )

    async with world:
        await world.send_message(
            TypedMessage(1), receiver_addr=a.addr, sender_id=sender.aid
        )
        await step_simulation(world, step_size_s=1.0)

    assert len(behavior_fired) == 1
    assert len(a.messages) == 1


@pytest.mark.asyncio
async def test_run_with_simulation_behavior_in():
    received = []

    async with run_with_simulation(SimpleAgent(), SimpleAgent()) as world:
        behavior_in(
            world,
            lambda agent, event: received.append(agent.aid),
            on_global_event=AlarmEvent,
        )
        world.environment.emit_global_event(AlarmEvent())

    assert len(received) == 2
