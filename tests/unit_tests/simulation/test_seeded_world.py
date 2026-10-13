import random

import networkx as nx
import numpy as np
import pytest

from mango import (
    Agent,
    AgentAddress,
    Area2D,
    Behavior,
    DefaultEnvironment,
    DelayProviderCommunicationSimulation,
    Role,
    RoleAgent,
    SeedTree,
    SimpleCommunicationSimulation,
    create_distribution_based_com_sim,
    create_world,
    record_agent,
    record_world,
    run_with_simulation,
)
from mango.simulation.communication import MessagePackage
from mango.simulation.environment import Environment


class Idle(Agent):
    def handle_message(self, content, meta):
        pass


def _package(sender="a", receiver="b"):
    return MessagePackage(
        sender_id=sender, receiver_id=receiver, sent_time=0, content=None
    )


# ---------------------------------------------------------------------------
# world seed and streams
# ---------------------------------------------------------------------------


def test_world_seed_tree_and_provenance():
    world = create_world(seed=42)
    assert world.seed_tree == SeedTree(42)
    assert world.seed == {"entropy": 42, "spawn_key": []}
    assert world.rng("x") is world.rng("x")
    assert world.rng("x") is world.container.rng("x")


def test_world_seed_replay_from_seed_dict():
    first = create_world()
    replay = create_world(seed=first.seed)
    assert replay.seed_tree == first.seed_tree
    assert replay.rng("x").random() == first.rng("x").random()


def test_world_seed_none_reproducible_with_global_random():
    random.seed(1)
    first = create_world()
    random.seed(1)
    second = create_world()
    assert first.seed == second.seed


def test_world_seed_tree_instance_is_shared():
    tree = SeedTree(3)
    world = create_world(seed=tree)
    assert world.seed_tree is tree
    assert world.rng("a") is tree.rng("a")


@pytest.mark.asyncio
async def test_agent_rng_is_world_stream():
    world = create_world(seed=1)
    agent = world.register(Idle(), suggested_aid="ev_0")
    assert agent.rng is world.rng("agent/ev_0")
    assert agent.rng is agent.context.rng("agent/ev_0")


@pytest.mark.asyncio
async def test_role_rng_is_world_stream():
    world = create_world(seed=1)
    agent = RoleAgent()
    role = Role()
    agent.add_role(role)
    world.register(agent, suggested_aid="ra")
    assert role.rng is agent.rng is world.rng("agent/ra")


# ---------------------------------------------------------------------------
# binding protocol
# ---------------------------------------------------------------------------


class RandomBehavior(Behavior):
    def __init__(self, rng=None):
        self.rng = rng


class RandomEnvironment(Environment):
    def __init__(self):
        self.rng = None

    def initialize(self, agents, clock):
        pass

    def initialized(self):
        return True

    def step(self, clock, step_size_s):
        pass

    def emit_global_event(self, event):
        pass

    def emit_agent_event(self, event, agent_id):
        pass

    def add_observer(self, observer):
        pass


def test_world_binds_unbound_components():
    space = Area2D()
    behavior = RandomBehavior()
    comm = SimpleCommunicationSimulation()
    world = create_world(
        seed=5,
        communication_sim=comm,
        environment=DefaultEnvironment(space=space, behavior=behavior),
    )
    assert comm.rng is world.rng("comm")
    assert space.rng is world.rng("space")
    assert behavior.rng is world.rng("behavior")


def test_world_binds_default_comm_sim():
    world = create_world(seed=5)
    assert world.communication_sim.rng is world.rng("comm")


def test_world_binds_other_environment():
    env = RandomEnvironment()
    world = create_world(seed=5, environment=env)
    assert env.rng is world.rng("environment")


def test_explicit_rngs_are_respected():
    space_rng = np.random.default_rng(0)
    comm_rng = random.Random(0)
    space = Area2D(rng=space_rng)
    comm = SimpleCommunicationSimulation(rng=comm_rng)
    create_world(seed=5, communication_sim=comm, environment=DefaultEnvironment(space))
    assert space.rng is space_rng
    assert comm.rng is comm_rng


def test_components_without_rng_attribute_are_untouched():
    class Plain(Behavior):
        pass

    behavior = Plain()
    create_world(seed=1, environment=DefaultEnvironment(behavior=behavior))
    assert not hasattr(behavior, "rng")


def test_communication_sim_setter_binds():
    world = create_world(seed=5)
    new_sim = DelayProviderCommunicationSimulation()
    world.communication_sim = new_sim
    assert new_sim.rng is world.rng("comm")
    assert world.communication_sim is new_sim
    explicit = SimpleCommunicationSimulation(rng=np.random.default_rng(1))
    explicit_rng = explicit.rng
    world.communication_sim = explicit
    assert explicit.rng is explicit_rng


def _loss_pattern(sim):
    result = sim.calculate_communication(0, [_package() for _ in range(40)])
    return [r.reached for r in result.package_results]


@pytest.mark.parametrize(
    "make_sim",
    [
        lambda: SimpleCommunicationSimulation(loss_percent=0.5),
        lambda: DelayProviderCommunicationSimulation(
            default_delay_s_provider=lambda rng: rng.normal(1.0, 0.1),
            loss_percent=0.5,
        ),
    ],
)
def test_component_reused_across_worlds_follows_each_worlds_seed(make_sim):
    shared = make_sim()
    patterns = {}
    for seed in (1, 2, 1):
        world = create_world(seed=seed, communication_sim=shared)
        assert shared.rng is world.rng("comm")
        patterns.setdefault(seed, []).append(_loss_pattern(shared))
    fresh = make_sim()
    create_world(seed=1, communication_sim=fresh)
    assert patterns[1][0] == patterns[1][1] == _loss_pattern(fresh)
    assert patterns[1][0] != patterns[2][0]


def test_space_and_behavior_reused_across_worlds_are_rebound():
    space, behavior = Area2D(), RandomBehavior()
    for seed in (3, 4):
        world = create_world(
            seed=seed, environment=DefaultEnvironment(space=space, behavior=behavior)
        )
        assert space.rng is world.rng("space")
        assert behavior.rng is world.rng("behavior")


def test_reused_component_keeps_a_user_assigned_rng():
    sim = SimpleCommunicationSimulation()
    create_world(seed=1, communication_sim=sim)
    own = np.random.default_rng(9)
    sim.rng = own
    create_world(seed=2, communication_sim=sim)
    assert sim.rng is own


def test_communication_sim_setter_rebinds_sim_of_another_world():
    first = create_world(seed=1)
    sim = first.communication_sim
    second = create_world(seed=2)
    second.communication_sim = sim
    assert sim.rng is second.rng("comm")


def test_seed_tree_first_pattern_shares_streams():
    tree = SeedTree(42)
    space = Area2D(10, 10, rng=tree.rng("space"))
    world = create_world(seed=tree, environment=DefaultEnvironment(space=space))
    assert space.rng is world.rng("space")


# ---------------------------------------------------------------------------
# Area2D
# ---------------------------------------------------------------------------


async def _positions(seed, aids):
    world = create_world(seed=seed, environment=DefaultEnvironment(space=Area2D(10, 5)))
    agents = [world.register(Idle(), suggested_aid=aid) for aid in aids]
    async with world:
        return {a.aid: world.environment.space.location(a) for a in agents}


@pytest.mark.asyncio
async def test_area2d_same_seed_same_positions():
    assert await _positions(7, ["a", "b", "c"]) == await _positions(7, ["a", "b", "c"])
    assert await _positions(7, ["a", "b"]) != await _positions(8, ["a", "b"])


@pytest.mark.asyncio
async def test_area2d_positions_within_bounds():
    for pos in (await _positions(3, [f"x{i}" for i in range(20)])).values():
        assert 0 <= pos.x <= 10
        assert 0 <= pos.y <= 5


def test_area2d_explicit_rng_used():
    rng = np.random.default_rng(0)
    expected = np.random.default_rng(0).random(2)
    space = Area2D(10, 10, rng=rng)
    agent = Idle()
    agent._aid = "a"
    space.initialize([agent], None)
    pos = space.location(agent)
    assert (pos.x, pos.y) == (expected[0] * 10, expected[1] * 10)


def test_area2d_standalone_falls_back_to_random_module():
    def place():
        space = Area2D(10, 10)
        agent = Idle()
        agent._aid = "a"
        space.initialize([agent], None)
        return space.location(agent)

    random.seed(4)
    first = place()
    random.seed(4)
    assert place() == first


# ---------------------------------------------------------------------------
# communication simulations
# ---------------------------------------------------------------------------


def test_simple_comm_rng_and_legacy_alias():
    sim = SimpleCommunicationSimulation(loss_percent=0.5)
    assert sim.rng is None
    assert sim._rng is random
    rng = np.random.default_rng(3)
    sim.rng = rng
    assert sim._rng is rng
    # assigning the legacy alias forwards to ``rng`` (old subclasses did this)
    other = random.Random(1)
    sim._rng = other
    assert sim.rng is other
    sim._rng = random
    assert sim.rng is None


def test_simple_comm_legacy_subclass_assigning_private_rng():
    class MySim(SimpleCommunicationSimulation):
        def __init__(self, seed):
            super().__init__(loss_percent=0.5)
            self._rng = random.Random(seed)

    def losses():
        sim = MySim(3)
        create_world(seed=1, communication_sim=sim)  # explicit rng is kept
        result = sim.calculate_communication(0, [_package() for _ in range(20)])
        return [r.reached for r in result.package_results]

    assert losses() == losses()
    assert isinstance(MySim(3).rng, random.Random)


def test_components_without_super_init_fall_back():
    class MyArea(Area2D):
        def __init__(self, w, h):
            self.width, self.height, self._positions = w, h, {}

    class MyDelaySim(DelayProviderCommunicationSimulation):
        def __init__(self):
            self.default_delay_s_provider = lambda rng: 0.5
            self.delay_s_directed_edge_dict = {}

    class FakeAgent:
        aid = "x"

    space = MyArea(5, 5)
    agent = FakeAgent()
    space.initialize([agent], None)
    assert space.has_position(agent)
    result = MyDelaySim().calculate_communication(0, [_package()])
    assert result.package_results[0].delay_s == 0.5


def test_simple_comm_loss_reproducible():
    def losses(seed):
        sim = SimpleCommunicationSimulation(
            loss_percent=0.5, rng=SeedTree(seed).rng("comm")
        )
        result = sim.calculate_communication(0, [_package() for _ in range(50)])
        return [r.reached for r in result.package_results]

    assert losses(1) == losses(1)
    assert losses(1) != losses(2)
    assert 0 < sum(losses(1)) < 50


def test_delay_provider_legacy_zero_arg():
    sim = DelayProviderCommunicationSimulation(
        default_delay_s_provider=lambda: 0.25,
        delay_s_directed_edge_dict={("a", "c"): lambda: -1.0},
    )
    result = sim.calculate_communication(0, [_package(), _package("a", "c")])
    assert [r.delay_s for r in result.package_results] == [0.25, 0.0]
    assert all(r.reached for r in result.package_results)
    # no generator is needed (or created) for zero-argument providers
    assert sim._fallback_rng is None


def test_delay_provider_one_arg_gets_rng():
    rng = np.random.default_rng(5)
    seen = []

    def provider(r):
        seen.append(r)
        return r.normal(0.1, 0.01)

    sim = DelayProviderCommunicationSimulation(
        default_delay_s_provider=provider, rng=rng
    )
    result = sim.calculate_communication(0, [_package()])
    assert seen == [rng]
    assert result.package_results[0].delay_s == pytest.approx(
        np.random.default_rng(5).normal(0.1, 0.01)
    )


def test_delay_provider_arity_detected_once():
    sim = DelayProviderCommunicationSimulation(default_delay_s_provider=lambda rng: 0.1)
    sim.rng = np.random.default_rng(0)
    sim.calculate_communication(0, [_package(), _package()])
    assert len(sim._arity_cache) == 1


class CallableProvider:
    def __call__(self, rng):
        return 0.5


def test_delay_provider_callable_object_and_optional_args():
    sim = DelayProviderCommunicationSimulation(
        default_delay_s_provider=CallableProvider(),
        delay_s_directed_edge_dict={("a", "c"): lambda scale=2.0: scale},
        rng=np.random.default_rng(0),
    )
    result = sim.calculate_communication(0, [_package(), _package("a", "c")])
    assert [r.delay_s for r in result.package_results] == [0.5, 2.0]


def test_delay_provider_keyword_only_rng():
    rng = np.random.default_rng(5)
    seen = []

    def provider(*, rng):
        seen.append(rng)
        return 0.3

    sim = DelayProviderCommunicationSimulation(
        default_delay_s_provider=provider, rng=rng
    )
    result = sim.calculate_communication(0, [_package()])
    assert seen == [rng]
    assert result.package_results[0].delay_s == 0.3


def test_delay_provider_two_required_args_incl_keyword_only_rejected():
    sim = DelayProviderCommunicationSimulation(
        default_delay_s_provider=lambda a, *, rng: 0
    )
    with pytest.raises(TypeError, match="one argument"):
        sim.calculate_communication(0, [_package()])


def test_delay_provider_two_args_rejected():
    sim = DelayProviderCommunicationSimulation(default_delay_s_provider=lambda a, b: 0)
    with pytest.raises(TypeError, match="one argument"):
        sim.calculate_communication(0, [_package()])


def test_delay_provider_loss():
    def results(seed):
        sim = DelayProviderCommunicationSimulation(
            default_delay_s_provider=lambda rng: rng.uniform(0, 1),
            rng=SeedTree(seed).rng("comm"),
            loss_percent=0.3,
        )
        res = sim.calculate_communication(0, [_package() for _ in range(100)])
        return [(r.reached, r.delay_s) for r in res.package_results]

    first = results(1)
    assert first == results(1)
    assert first != results(2)
    lost = sum(not reached for reached, _ in first)
    assert 10 < lost < 60


def test_delay_provider_no_loss_draws_nothing_for_loss():
    rng = np.random.default_rng(0)
    sim = DelayProviderCommunicationSimulation(rng=rng)
    sim.calculate_communication(0, [_package() for _ in range(5)])
    assert rng.random() == np.random.default_rng(0).random()


def test_delay_provider_standalone_fallback_seeded_from_random():
    def run():
        sim = DelayProviderCommunicationSimulation(
            default_delay_s_provider=lambda rng: rng.random()
        )
        return sim.calculate_communication(0, [_package()]).package_results[0].delay_s

    random.seed(9)
    first = run()
    random.seed(9)
    assert run() == first


def test_distribution_based_com_sim_with_rng_providers():
    g = nx.path_graph(["a0", "a1", "a2"])

    def build(seed):
        return create_distribution_based_com_sim(
            g,
            default_delay_per_edge_ms=20.0,
            distribution_provider=lambda mean_ms: (
                lambda rng: max(0.0, rng.normal(mean_ms, mean_ms * 0.1)) / 1000.0
            ),
            rng=SeedTree(seed).rng("comm"),
            loss_percent=0.1,
        )

    def draw(sim):
        res = sim.calculate_communication(0, [_package("a0", "a2") for _ in range(20)])
        return [(r.reached, r.delay_s) for r in res.package_results]

    sim = build(1)
    assert sim.loss_percent == 0.1
    first = draw(sim)
    assert first == draw(build(1))
    assert first != draw(build(2))
    delays = [d for _, d in first]
    assert 0.03 < sum(delays) / len(delays) < 0.05


def test_distribution_based_com_sim_default_constant_and_legacy_providers():
    g = nx.path_graph(["a0", "a1"])
    sim = create_distribution_based_com_sim(g, default_delay_per_edge_ms=10.0)
    assert sim.rng is None
    res = sim.calculate_communication(0, [_package("a0", "a1")])
    assert res.package_results[0].delay_s == pytest.approx(0.01)
    legacy = create_distribution_based_com_sim(
        g, distribution_provider=lambda mean_ms: lambda: mean_ms / 2000.0
    )
    res = legacy.calculate_communication(0, [_package("a0", "a1")])
    assert res.package_results[0].delay_s == pytest.approx(0.005)


def test_distribution_based_com_sim_bound_by_world():
    g = nx.path_graph(["a0", "a1"])
    sim = create_distribution_based_com_sim(g)
    world = create_world(seed=3, communication_sim=sim)
    assert sim.rng is world.rng("comm")


# ---------------------------------------------------------------------------
# full reproducible run
# ---------------------------------------------------------------------------


class Gossip(Agent):
    def __init__(self, peers):
        super().__init__()
        self.peers = peers
        self.received = []
        self.values = []

    def on_ready(self):
        self.schedule_periodic_task(self._tick, delay=1.0)

    async def _tick(self):
        value = float(self.rng.normal())
        self.values.append(value)
        target = self.peers[int(self.rng_for("peer").integers(len(self.peers)))]
        if target != self.aid:
            await self.send_message(
                value, receiver_addr=AgentAddress(self.context.addr, target)
            )

    def handle_message(self, content, meta):
        self.received.append((self.context.clock.time, meta["sender_id"], content))


async def _full_run(seed, extra_agents=0):
    aids = [f"node_{i}" for i in range(4)]
    world = create_world(
        seed=seed,
        communication_sim=DelayProviderCommunicationSimulation(
            default_delay_s_provider=lambda rng: rng.uniform(0.1, 0.9),
            loss_percent=0.2,
        ),
        environment=DefaultEnvironment(space=Area2D(100, 100)),
    )
    agents = [world.register(Gossip(aids), suggested_aid=aid) for aid in aids]
    for i in range(extra_agents):
        world.register(Gossip(aids), suggested_aid=f"extra_{i}")
    record_agent(world, "last", lambda a: a.values[-1] if a.values else None)
    record_world(world, "delivered", lambda: len(world.recorded_messages))
    async with world:
        await world.step_until(max_advance_time_s=10.0)
    positions = {a.aid: world.environment.space.location(a) for a in agents}
    return (
        positions,
        [a.values for a in agents],
        [a.received for a in agents],
        world.to_records(keys=["delivered"]),
        [
            (m.sender_id, m.receiver_id, m.sent_time, m.arriving_time)
            for m in world.recorded_messages
            if not m.sender_id.startswith("extra")
            and not m.receiver_id.startswith("extra")
        ],
    )


@pytest.mark.asyncio
async def test_full_seeded_run_is_bit_for_bit_reproducible():
    first = await _full_run(11)
    second = await _full_run(11)
    assert first == second
    positions, values, received, _, messages = first
    assert all(len(v) >= 9 for v in values)
    assert messages


@pytest.mark.asyncio
async def test_different_seed_changes_the_run():
    first = await _full_run(11)
    other = await _full_run(12)
    assert first[0] != other[0]
    assert first[1] != other[1]


@pytest.mark.asyncio
async def test_adding_agents_keeps_other_agents_streams():
    base = await _full_run(11)
    grown = await _full_run(11, extra_agents=3)
    # own draws and positions of the original agents are unchanged
    assert base[1] == grown[1]
    assert base[0] == grown[0]


@pytest.mark.asyncio
async def test_run_with_simulation_seed_and_calendar():
    async with run_with_simulation(
        (Idle(), {"aid": "a"}), seed=8, start_datetime="2024-06-01T00:00"
    ) as world:
        assert world.seed_tree == SeedTree(8)
        assert world.clock.has_calendar
        assert world["a"].rng is world.rng("agent/a")
