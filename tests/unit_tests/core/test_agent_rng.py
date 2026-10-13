import asyncio
import random
import time

import numpy as np
import pytest

from mango import (
    Agent,
    Role,
    RoleAgent,
    SeedTree,
    activate,
    create_ec_container,
    create_tcp_container,
)
from mango.util.seeding import name_key


class PlainAgent(Agent):
    def handle_message(self, content, meta):
        pass


def test_container_seed_kwarg_and_rng():
    container = create_tcp_container("127.0.0.1:5590", seed=42)
    assert container.seed_tree == SeedTree(42)
    assert container.rng("x") is container.rng("x")
    assert container.rng("x").random() == SeedTree(42).rng("x").random()


def test_container_uses_seed_tree_instance_as_is():
    tree = SeedTree(7)
    container = create_ec_container(addr="ec", seed=tree)
    assert container.seed_tree is tree
    assert container.rng("a") is tree.rng("a")


def test_container_mapping_seed():
    container = create_ec_container(addr="ec", seed={"entropy": 3, "spawn_key": [1]})
    assert container.seed_tree == SeedTree(3, spawn_key=(1,))


def test_container_without_seed_has_random_tree():
    a = create_ec_container(addr="ec")
    b = create_ec_container(addr="ec")
    assert isinstance(a.seed_tree, SeedTree)
    assert a.seed_tree != b.seed_tree


def test_seed_is_not_forwarded_as_protocol_kwarg():
    container = create_tcp_container("127.0.0.1:5591", seed=1)
    assert "seed" not in container._kwargs


def test_agent_rng_is_stream_of_aid():
    container = create_ec_container(addr="ec", seed=5)
    agent = container.register(PlainAgent(), suggested_aid="ev_1")
    assert isinstance(agent.rng, np.random.Generator)
    assert agent.rng is agent.rng
    assert agent.rng is container.rng("agent/ev_1")
    assert agent.context.rng("agent/ev_1") is agent.rng


def test_agent_rng_for_sub_stream():
    container = create_ec_container(addr="ec", seed=5)
    agent = container.register(PlainAgent(), suggested_aid="ev_1")
    sub = agent.rng_for("demand")
    assert sub is agent.rng_for("demand")
    assert sub is container.rng("agent/ev_1/demand")
    assert sub is not agent.rng
    assert agent.rng_for("a").random() != agent.rng_for("b").random()


def test_agent_rng_before_registration_raises():
    agent = PlainAgent()
    with pytest.raises(RuntimeError, match="aid"):
        _ = agent.rng
    with pytest.raises(RuntimeError, match="registered"):
        agent.rng_for("x")


def test_adding_agents_does_not_change_streams():
    small = create_ec_container(addr="ec", seed=9)
    a1 = small.register(PlainAgent(), suggested_aid="target")
    big = create_ec_container(addr="ec", seed=9)
    for i in range(5):
        big.register(PlainAgent(), suggested_aid=f"other_{i}").rng.random()
    a2 = big.register(PlainAgent(), suggested_aid="target")
    assert np.array_equal(a1.rng.random(10), a2.rng.random(10))


def test_different_aids_different_streams():
    container = create_ec_container(addr="ec", seed=9)
    a = container.register(PlainAgent(), suggested_aid="a")
    b = container.register(PlainAgent(), suggested_aid="b")
    assert a.rng.random() != b.rng.random()


class DrawingRole(Role):
    pass


def test_role_rng_is_agent_rng():
    container = create_ec_container(addr="ec", seed=2)
    agent = RoleAgent()
    role = DrawingRole()
    agent.add_role(role)
    container.register(agent, suggested_aid="ra")
    assert role.rng is agent.rng
    assert role.context.rng is agent.rng
    assert role.context.rng_for("x") is agent.rng_for("x")


def test_role_rng_before_add_raises():
    with pytest.raises(RuntimeError, match="added to an agent"):
        _ = DrawingRole().rng


def test_role_rng_before_agent_registration_raises():
    agent = RoleAgent()
    role = DrawingRole()
    agent.add_role(role)
    with pytest.raises(RuntimeError, match="registered"):
        _ = role.rng


class ProcessAgent(Agent):
    def __init__(self, target):
        super().__init__()
        self.target = target

    def on_ready(self):
        self.schedule_instant_message(
            content=[float(self.rng.random()), float(self.rng_for("x").random())],
            receiver_addr=self.target,
        )

    def handle_message(self, content, meta):
        pass


class Collector(Agent):
    def __init__(self):
        super().__init__()
        self.received = None

    def handle_message(self, content, meta):
        self.received = content


@pytest.mark.asyncio
async def test_mirror_container_shares_seed_identity():
    container = create_tcp_container(addr=("127.0.0.1", 5594), seed=1234)
    collector = container.register(Collector(), suggested_aid="collector")
    target = collector.addr

    async with activate(container):
        await container.as_agent_process(
            agent_creator=lambda c: c.register(
                ProcessAgent(target), suggested_aid="in_process"
            )
        )
        deadline = time.perf_counter() + 10
        while collector.received is None:
            assert time.perf_counter() < deadline, "no message from agent process"
            await asyncio.sleep(0.01)

    expected = SeedTree(1234)
    assert collector.received == [
        expected.rng("agent/in_process").random(),
        expected.rng("agent/in_process/x").random(),
    ]


class SelfSeeded(Agent):
    def __init__(self):
        self.rng = np.random.default_rng(0)  # set before super().__init__()
        super().__init__()

    def handle_message(self, content, meta):
        pass


class SelfSeededRole(Role):
    def __init__(self):
        super().__init__()
        self.rng = np.random.default_rng(1)


def test_assigning_rng_overrides_stream_for_backwards_compatibility():
    container = create_ec_container(addr="ec", seed=2)
    agent = SelfSeeded()
    own = agent.rng
    assert isinstance(own, np.random.Generator)
    container.register(agent, suggested_aid="s")
    assert agent.rng is own
    del agent.rng
    assert agent.rng is container.rng("agent/s")
    del agent.rng  # deleting without an override is a no-op
    assert agent.rng is container.rng("agent/s")


def test_assigning_role_rng_overrides_only_the_role():
    container = create_ec_container(addr="ec", seed=2)
    agent = RoleAgent()
    role = SelfSeededRole()
    plain = DrawingRole()
    agent.add_role(role)
    agent.add_role(plain)
    container.register(agent, suggested_aid="r")
    assert role.rng is not agent.rng
    assert plain.rng is agent.rng
    del role.rng
    assert role.rng is agent.rng


def test_agent_override_is_seen_by_its_roles():
    container = create_ec_container(addr="ec", seed=2)
    agent = RoleAgent()
    role = DrawingRole()
    agent.add_role(role)
    container.register(agent, suggested_aid="r")
    custom = np.random.default_rng(5)
    agent.rng = custom
    assert role.rng is custom


def test_unseeded_container_leaves_global_random_untouched():
    import random

    random.seed(3)
    expected = random.random()
    random.seed(3)
    create_ec_container(addr="ec")
    assert random.random() == expected


def test_invalid_seed_raises_on_construction():
    with pytest.raises(ValueError):
        create_ec_container(addr="ec", seed=-1)


class LazyAgent(Agent):
    """The pre-seed-tree lazy init pattern: ``self.rng = None`` then create it."""

    def __init__(self):
        super().__init__()
        self.rng = None
        self.checked_in_init = self.rng is None

    def handle_message(self, content, meta):
        pass

    def on_register(self):
        if self.rng is None:
            self.rng = random.Random(1)
        self.value = self.rng.gauss(0, 1)


class LazyRole(Role):
    def __init__(self):
        super().__init__()
        self.rng = None

    def setup(self):
        if self.rng is None:
            self.rng = random.Random(2)


def test_assigning_none_behaves_like_a_plain_attribute():
    container = create_ec_container(addr="ec", seed=2)
    agent = LazyAgent()
    assert agent.checked_in_init is True
    container.register(agent, suggested_aid="lazy")
    assert isinstance(agent.rng, random.Random)
    assert agent.value == random.Random(1).gauss(0, 1)
    del agent.rng
    assert agent.rng is container.rng("agent/lazy")


def test_role_assigning_none_behaves_like_a_plain_attribute():
    container = create_ec_container(addr="ec", seed=2)
    agent = RoleAgent()
    role = LazyRole()
    agent.add_role(role)
    container.register(agent, suggested_aid="r")
    assert isinstance(role.rng, random.Random)
    role.rng = None
    assert role.rng is None
    del role.rng
    assert role.rng is agent.rng


def test_role_rng_for_delegates_to_agent():
    container = create_ec_container(addr="ec", seed=4)
    agent = RoleAgent()
    role = DrawingRole()
    agent.add_role(role)
    container.register(agent, suggested_aid="r")
    assert role.rng_for("x") is agent.rng_for("x")
    assert role.rng_for("x") is container.rng("agent/r/x")
    with pytest.raises(RuntimeError, match="added to an agent"):
        DrawingRole().rng_for("x")


def test_aid_is_a_single_stream_segment():
    container = create_ec_container(addr="ec", seed=4)
    g = container.register(PlainAgent(), suggested_aid="g")
    gx = container.register(PlainAgent(), suggested_aid="g/x")
    assert g.rng_for("x") is not gx.rng
    assert gx.rng is container.rng("agent", name_key("g/x"))
    # aids without "/" keep the documented path name
    assert g.rng is container.rng("agent/g")
    assert g.rng_for("x") is container.rng("agent/g/x")


def test_aid_with_trailing_slash_has_a_stream():
    container = create_ec_container(addr="ec", seed=4)
    agent = container.register(PlainAgent(), suggested_aid="ev/")
    assert isinstance(agent.rng, np.random.Generator)
    assert agent.rng is agent.rng
