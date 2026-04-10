from typing import Any
from unittest.mock import MagicMock

import pytest

import mango
from mango import (
    Agent,
    AgentAddress,
    AgentDescription,
    Area2D,
    Behavior,
    DefaultEnvironment,
    DelayProviderCommunicationSimulation,
    ForwardingRule,
    Position2D,
    SimpleCommunicationSimulation,
    create_world,
    discrete_step_until,
    position_history,
    record_agent,
    record_agent_having,
    record_position,
    record_world,
    run_with_simulation,
    step_simulation,
)
from mango.simulation.communication import (
    CommunicationSimulationResult,
    MessagePackage,
    PackageResult,
)
from mango.simulation.world import (
    DISCRETE_EVENT,
    AgentsRecording,
    MessageTransaction,
    WorldRecording,
    collect_agent_data,
    collect_data,
)


class SimpleAgent(Agent):
    def __init__(self):
        super().__init__()
        self.messages: list[tuple[Any, dict]] = []
        self.step_calls: list[tuple] = []
        self.global_events: list[Any] = []
        self.agent_events: list[Any] = []
        self.ready_called = False

    def handle_message(self, content, meta: dict):
        self.messages.append((content, meta))

    def on_ready(self):
        self.ready_called = True

    def on_step(self, env, clock, step_size_s: float) -> None:
        self.step_calls.append((clock.time, step_size_s))

    def on_global_event(self, event: Any) -> None:
        self.global_events.append(event)

    def on_agent_event(self, event: Any) -> None:
        self.agent_events.append(event)


class ReplyAgent(Agent):
    """Sends a reply when it receives a message."""

    def __init__(self):
        super().__init__()
        self.messages: list[tuple[Any, dict]] = []

    def handle_message(self, content, meta: dict):
        self.messages.append((content, meta))
        self.schedule_instant_task(self.reply_to("pong", meta))


class TestPackageResult:
    def test_fields(self):
        r = PackageResult(reached=True, delay_s=0.5)
        assert r.reached is True
        assert r.delay_s == 0.5

    def test_frozen(self):
        r = PackageResult(reached=False, delay_s=0.0)
        with pytest.raises(Exception):
            r.reached = True  # type: ignore[misc]


class TestMessagePackage:
    def test_fields(self):
        pkg = MessagePackage(
            sender_id="a1", receiver_id="a2", sent_time=1.0, content=("hello", {})
        )
        assert pkg.sender_id == "a1"
        assert pkg.receiver_id == "a2"
        assert pkg.sent_time == 1.0

    def test_sender_id_none(self):
        pkg = MessagePackage(
            sender_id=None, receiver_id="a2", sent_time=0.0, content=None
        )
        assert pkg.sender_id is None


class TestCommunicationSimulationResult:
    def test_fields(self):
        results = [PackageResult(reached=True, delay_s=0.1)]
        r = CommunicationSimulationResult(package_results=results)
        assert len(r.package_results) == 1
        assert r.package_results[0].reached is True


class TestSimpleCommunicationSimulation:
    def test_default_params(self):
        sim = SimpleCommunicationSimulation()
        assert sim.loss_percent == 0.0
        assert sim.default_delay_s == 0.0
        assert sim.delay_s_directed_edge_dict == {}

    def test_no_loss_no_delay(self):
        sim = SimpleCommunicationSimulation()
        pkg = MessagePackage("a", "b", 0.0, None)
        result = sim.calculate_communication(0.0, [pkg])
        assert result.package_results[0].reached is True
        assert result.package_results[0].delay_s == 0.0

    def test_custom_delay(self):
        sim = SimpleCommunicationSimulation(default_delay_s=2.0)
        pkg = MessagePackage("a", "b", 0.0, None)
        result = sim.calculate_communication(0.0, [pkg])
        assert result.package_results[0].delay_s == 2.0

    def test_directed_edge_delay(self):
        sim = SimpleCommunicationSimulation(
            default_delay_s=1.0,
            delay_s_directed_edge_dict={("a", "b"): 5.0},
        )
        pkg = MessagePackage("a", "b", 0.0, None)
        result = sim.calculate_communication(0.0, [pkg])
        assert result.package_results[0].delay_s == 5.0

    def test_default_delay_for_other_pair(self):
        sim = SimpleCommunicationSimulation(
            default_delay_s=1.0,
            delay_s_directed_edge_dict={("a", "b"): 5.0},
        )
        pkg = MessagePackage("x", "y", 0.0, None)
        result = sim.calculate_communication(0.0, [pkg])
        assert result.package_results[0].delay_s == 1.0

    def test_full_loss(self):
        sim = SimpleCommunicationSimulation(loss_percent=1.0)
        pkg = MessagePackage("a", "b", 0.0, None)
        result = sim.calculate_communication(0.0, [pkg])
        assert result.package_results[0].reached is False

    def test_multiple_packages_in_one_call(self):
        sim = SimpleCommunicationSimulation(loss_percent=0.0)
        pkgs = [MessagePackage("a", "b", 0.0, None) for _ in range(3)]
        result = sim.calculate_communication(0.0, pkgs)
        assert len(result.package_results) == 3
        assert all(r.reached for r in result.package_results)

    def test_empty_message_list(self):
        sim = SimpleCommunicationSimulation()
        result = sim.calculate_communication(0.0, [])
        assert result.package_results == []

    def test_none_sender_key(self):
        sim = SimpleCommunicationSimulation(
            delay_s_directed_edge_dict={(None, "b"): 3.0}
        )
        pkg = MessagePackage(None, "b", 0.0, None)
        result = sim.calculate_communication(0.0, [pkg])
        assert result.package_results[0].delay_s == 3.0


class TestDelayProviderCommunicationSimulation:
    def test_default_provider(self):
        sim = DelayProviderCommunicationSimulation()
        pkg = MessagePackage("a", "b", 0.0, None)
        result = sim.calculate_communication(0.0, [pkg])
        assert result.package_results[0].reached is True
        assert result.package_results[0].delay_s == 0.0

    def test_custom_default_provider(self):
        sim = DelayProviderCommunicationSimulation(
            default_delay_s_provider=lambda: 3.14
        )
        pkg = MessagePackage("a", "b", 0.0, None)
        result = sim.calculate_communication(0.0, [pkg])
        assert result.package_results[0].delay_s == 3.14

    def test_directed_edge_provider(self):
        sim = DelayProviderCommunicationSimulation(
            default_delay_s_provider=lambda: 1.0,
            delay_s_directed_edge_dict={("a", "b"): lambda: 9.0},
        )
        pkg = MessagePackage("a", "b", 0.0, None)
        result = sim.calculate_communication(0.0, [pkg])
        assert result.package_results[0].delay_s == 9.0

    def test_negative_delay_clamped(self):
        sim = DelayProviderCommunicationSimulation(
            default_delay_s_provider=lambda: -5.0
        )
        pkg = MessagePackage("a", "b", 0.0, None)
        result = sim.calculate_communication(0.0, [pkg])
        assert result.package_results[0].delay_s == 0.0

    def test_provider_called_per_message(self):
        calls = []
        sim = DelayProviderCommunicationSimulation(
            default_delay_s_provider=lambda: calls.append(1) or 0.5
        )
        pkgs = [MessagePackage("a", "b", 0.0, None) for _ in range(3)]
        result = sim.calculate_communication(0.0, pkgs)
        assert len(calls) == 3
        assert all(r.delay_s == 0.5 for r in result.package_results)


class TestPosition2D:
    def test_fields(self):
        p = Position2D(x=1.0, y=2.0)
        assert p.x == 1.0
        assert p.y == 2.0

    def test_repr(self):
        p = Position2D(x=3.5, y=-1.0)
        assert "3.5" in repr(p)
        assert "-1.0" in repr(p)


class TestArea2D:
    def _make_agent(self, aid: str):
        a = MagicMock()
        a.aid = aid
        return a

    def test_default_dimensions(self):
        space = Area2D()
        assert space.width == 10.0
        assert space.height == 10.0

    def test_custom_dimensions(self):
        space = Area2D(width=100.0, height=200.0)
        assert space.width == 100.0
        assert space.height == 200.0

    def test_move_and_location(self):
        space = Area2D()
        agent = self._make_agent("a1")
        pos = Position2D(3.0, 4.0)
        space.move(agent, pos)
        assert space.location(agent) == pos

    def test_has_position_true(self):
        space = Area2D()
        agent = self._make_agent("a1")
        space.move(agent, Position2D(0.0, 0.0))
        assert space.has_position(agent) is True

    def test_has_position_false(self):
        space = Area2D()
        agent = self._make_agent("a1")
        assert space.has_position(agent) is False

    def test_initialize_assigns_random_positions(self):
        space = Area2D(width=50.0, height=50.0)
        agents = [self._make_agent(f"a{i}") for i in range(3)]
        clock = MagicMock()
        space.initialize(agents, clock)
        for agent in agents:
            assert space.has_position(agent)
            pos = space.location(agent)
            assert 0.0 <= pos.x <= 50.0
            assert 0.0 <= pos.y <= 50.0

    def test_initialize_skips_existing_position(self):
        space = Area2D()
        agent = self._make_agent("a1")
        preset = Position2D(1.0, 2.0)
        space.move(agent, preset)
        space.initialize([agent], MagicMock())
        # Preset position should be preserved exactly
        assert space.location(agent) == preset

    def test_install_is_noop(self):
        space = Area2D()
        agent = self._make_agent("a1")
        space.install(agent)  # should not raise


class TestDefaultEnvironment:
    def test_default_space_and_behavior(self):
        env = DefaultEnvironment()
        assert isinstance(env.space, Area2D)
        assert env.initialized() is False

    def test_custom_space(self):
        space = Area2D(width=200.0, height=200.0)
        env = DefaultEnvironment(space=space)
        assert env.space is space

    def test_custom_behavior(self):
        class MyBehavior(Behavior):
            pass

        b = MyBehavior()
        env = DefaultEnvironment(behavior=b)
        assert env.behavior is b

    def test_initialize_sets_flag(self):
        env = DefaultEnvironment()
        agents = []
        clock = MagicMock()
        env.initialize(agents, clock)
        assert env.initialized() is True

    def test_add_observer_and_emit_global_event(self):
        env = DefaultEnvironment()
        received = []

        class Obs(mango.simulation.environment.WorldObserver):
            def dispatch_global_event(self, clock, event):
                received.append(event)

        env.add_observer(Obs())
        env.emit_global_event("hello")
        assert received == ["hello"]

    def test_emit_global_event_multiple_observers(self):
        env = DefaultEnvironment()
        received = []

        class Obs(mango.simulation.environment.WorldObserver):
            def dispatch_global_event(self, clock, event):
                received.append(event)

        env.add_observer(Obs())
        env.add_observer(Obs())
        env.emit_global_event("boom")
        assert len(received) == 2

    def test_emit_agent_event_unknown_id(self):
        env = DefaultEnvironment()
        # Should not raise when agent_id not found
        env.emit_agent_event("ev", "nonexistent")

    @pytest.mark.asyncio
    async def test_install_and_emit_agent_event(self):
        env = DefaultEnvironment()
        received = []

        class EventAgent(Agent):
            def handle_message(self, content, meta):
                pass

            def on_agent_event(self, event):
                received.append(event)

        a = EventAgent()
        world = create_world(environment=env)
        world.register(a)
        env.install(a, agent_id=a.aid)
        async with world:
            env.emit_agent_event("ping", a.aid)
        assert received == ["ping"]

    def test_step_calls_behavior(self):
        called = []

        class TrackingBehavior(Behavior):
            def on_step(self, environment, clock, step_size_s):
                called.append(step_size_s)

        env = DefaultEnvironment(behavior=TrackingBehavior())
        env.step(MagicMock(), 5.0)
        assert called == [5.0]


class TestCreateWorld:
    def test_default_world(self):
        world = create_world()
        assert world.clock.time == 0.0
        assert isinstance(world.communication_sim, SimpleCommunicationSimulation)
        assert isinstance(world.environment, DefaultEnvironment)

    def test_custom_start_time(self):
        world = create_world(start_time=100.0)
        assert world.clock.time == 100.0

    def test_custom_communication_sim(self):
        custom_sim = SimpleCommunicationSimulation(default_delay_s=2.0)
        world = create_world(communication_sim=custom_sim)
        assert world.communication_sim is custom_sim

    def test_custom_environment(self):
        env = DefaultEnvironment(space=Area2D(width=500.0, height=500.0))
        world = create_world(environment=env)
        assert world.environment is env


class TestSimulationWorldRegister:
    @pytest.mark.asyncio
    async def test_register_returns_agent(self):
        world = create_world()
        agent = SimpleAgent()
        returned = world.register(agent)
        assert returned is agent
        await world.shutdown()

    @pytest.mark.asyncio
    async def test_register_assigns_aid(self):
        world = create_world()
        a = world.register(SimpleAgent())
        assert a.aid == "agent0"
        await world.shutdown()

    @pytest.mark.asyncio
    async def test_register_increments_aid(self):
        world = create_world()
        a1 = world.register(SimpleAgent())
        a2 = world.register(SimpleAgent())
        assert a1.aid == "agent0"
        assert a2.aid == "agent1"
        await world.shutdown()

    @pytest.mark.asyncio
    async def test_register_suggested_aid(self):
        world = create_world()
        agent = SimpleAgent()
        world.register(agent, suggested_aid="my_agent")
        assert agent.aid == "my_agent"
        await world.shutdown()

    @pytest.mark.asyncio
    async def test_suggested_aid_unavailable_falls_back(self):
        world = create_world()
        a1 = world.register(SimpleAgent(), suggested_aid="agent0")
        # "agent0" clashes with the auto-naming pattern – falls back
        a2 = world.register(SimpleAgent(), suggested_aid="agent0")
        assert a1.aid == "agent0"
        # Second registration should get a different aid
        assert a2.aid != "agent0"
        await world.shutdown()

    @pytest.mark.asyncio
    async def test_register_twice_raises(self):
        world = create_world()
        agent = SimpleAgent()
        world.register(agent)
        with pytest.raises(ValueError):
            world.register(agent)
        await world.shutdown()

    def test_is_aid_available_true(self):
        world = create_world()
        world.running = False  # prevent _do_start calls in sync context
        assert world.is_aid_available("custom_id") is True

    @pytest.mark.asyncio
    async def test_is_aid_available_false_when_taken(self):
        world = create_world()
        world.register(SimpleAgent(), suggested_aid="taken")
        assert world.is_aid_available("taken") is False
        await world.shutdown()

    def test_is_aid_available_false_for_pattern_clash(self):
        world = create_world()
        world.running = False
        assert world.is_aid_available("agent42") is False

    @pytest.mark.asyncio
    async def test_deregister(self):
        world = create_world()
        a = world.register(SimpleAgent())
        world.deregister(a.aid)
        assert a.aid not in world._agents
        await world.shutdown()

    def test_deregister_nonexistent_is_noop(self):
        world = create_world()
        world.running = False
        world.deregister("ghost")  # must not raise


class TestSimulationWorldGetItem:
    @pytest.mark.asyncio
    async def test_getitem_by_string(self):
        world = create_world()
        a = world.register(SimpleAgent())
        assert world[a.aid] is a
        await world.shutdown()

    @pytest.mark.asyncio
    async def test_getitem_by_int(self):
        world = create_world()
        a = world.register(SimpleAgent())
        assert world[0] is a
        await world.shutdown()

    def test_getitem_missing_string_raises(self):
        world = create_world()
        with pytest.raises(KeyError):
            _ = world["ghost"]

    def test_getitem_missing_int_raises(self):
        world = create_world()
        with pytest.raises(IndexError):
            _ = world[99]


@pytest.mark.asyncio
async def test_world_context_manager_calls_on_ready():
    world = create_world()
    agent = world.register(SimpleAgent())
    async with world:
        assert agent.ready_called is True


@pytest.mark.asyncio
async def test_step_simulation_fixed_step():
    world = create_world()
    agent = world.register(SimpleAgent())
    async with world:
        result = await step_simulation(world, step_size_s=5.0)
    assert result is not None
    assert result.step_size_s == 5.0
    assert result.time_elapsed_s == 5.0
    assert world.clock.time == 5.0


@pytest.mark.asyncio
async def test_step_simulation_calls_on_step_hook():
    world = create_world()
    agent = world.register(SimpleAgent())
    async with world:
        await step_simulation(world, step_size_s=3.0)
    assert len(agent.step_calls) == 1
    _, step_size = agent.step_calls[0]
    assert step_size == 3.0


@pytest.mark.asyncio
async def test_step_simulation_discrete_event_no_events_returns_none():
    world = create_world()
    world.register(SimpleAgent())
    async with world:
        result = await step_simulation(world, step_size_s=DISCRETE_EVENT)
    assert result is None


@pytest.mark.asyncio
async def test_step_simulation_max_advance_time_respected():
    world = create_world()

    class PeriodicAgent(Agent):
        def handle_message(self, content, meta):
            pass

        def on_ready(self):
            self.schedule_periodic_task(self._tick, 10.0)

        async def _tick(self):
            pass

    world.register(PeriodicAgent())
    async with world:
        # periodic task wakes up at t=10; limit to 5 seconds → returns None
        result = await step_simulation(
            world, step_size_s=DISCRETE_EVENT, max_advance_time_s=5.0
        )
    assert result is None


@pytest.mark.asyncio
async def test_message_delivery_and_recorded_messages():
    world = create_world()
    sender = world.register(SimpleAgent())
    receiver = world.register(SimpleAgent())

    async with world:
        await world.send_message(
            "hello", receiver_addr=receiver.addr, sender_id=sender.aid
        )
        result = await step_simulation(world, step_size_s=1.0)

    assert result is not None
    assert result.messages_delivered == 1
    assert len(receiver.messages) == 1
    assert receiver.messages[0][0] == "hello"
    assert len(world.recorded_messages) == 1
    tx = world.recorded_messages[0]
    assert isinstance(tx, MessageTransaction)
    assert tx.sender_id == sender.aid
    assert tx.receiver_id == receiver.aid
    assert tx.content == "hello"


@pytest.mark.asyncio
async def test_message_with_delay_delivered_on_next_step():
    comm = SimpleCommunicationSimulation(default_delay_s=2.0)
    world = create_world(communication_sim=comm)
    sender = world.register(SimpleAgent())
    receiver = world.register(SimpleAgent())

    async with world:
        await world.send_message(
            "delayed", receiver_addr=receiver.addr, sender_id=sender.aid
        )
        # Step only 1 second – message not yet due (delay=2s)
        result1 = await step_simulation(world, step_size_s=1.0)
        assert result1.messages_delivered == 0
        assert len(receiver.messages) == 0

        # Step another 2 seconds – message is now due
        result2 = await step_simulation(world, step_size_s=2.0)
        assert result2.messages_delivered == 1
        assert len(receiver.messages) == 1


@pytest.mark.asyncio
async def test_message_loss():
    comm = SimpleCommunicationSimulation(loss_percent=1.0)
    world = create_world(communication_sim=comm)
    sender = world.register(SimpleAgent())
    receiver = world.register(SimpleAgent())

    async with world:
        reached = await world.send_message(
            "lost", receiver_addr=receiver.addr, sender_id=sender.aid
        )
        result = await step_simulation(world, step_size_s=1.0)

    assert reached is False
    assert len(receiver.messages) == 0
    assert result.messages_delivered == 0


@pytest.mark.asyncio
async def test_message_unknown_receiver_dropped():
    world = create_world()
    world.register(SimpleAgent())  # just to have an agent
    # Manually inject a message with unknown receiver
    async with world:
        await world._deliver_messages_due.__func__(world, 999.0) if False else None  # noqa: this branch is tested via direct queue
        # Insert directly into pending with bogus receiver
        import bisect

        world._pending_messages.clear()
        bisect.insort(
            world._pending_messages,
            (0.0, 0, 0.0, "msg", {"receiver_id": "ghost", "sender_id": None}),
        )
        delivered = await world._deliver_messages_due(10.0)
    assert delivered == 0  # dropped, not counted


@pytest.mark.asyncio
async def test_discrete_step_until_runs_to_completion():
    world = create_world()

    class ScheduledAgent(Agent):
        def __init__(self):
            super().__init__()
            self.ticks = 0

        def handle_message(self, content, meta):
            pass

        def on_ready(self):
            self.schedule_periodic_task(self._tick, 1.0)

        async def _tick(self):
            self.ticks += 1

    agent = world.register(ScheduledAgent())
    async with world:
        results = await discrete_step_until(world, max_advance_time_s=5.0)

    assert len(results) >= 1
    assert world.clock.time <= 5.0
    assert agent.ticks >= 1


@pytest.mark.asyncio
async def test_shutdown_sets_running_false():
    world = create_world()
    world.register(SimpleAgent())
    async with world:
        pass
    assert world.running is False


@pytest.mark.asyncio
async def test_record_world():
    world = create_world()
    world.register(SimpleAgent())
    record_world(world, "agent_count", lambda: len(world._agents))
    async with world:
        await step_simulation(world, step_size_s=1.0)
        await step_simulation(world, step_size_s=1.0)

    rec = world.data_collections["agent_count"]
    assert isinstance(rec, WorldRecording)
    # recorded at t=0 (init) + 2 steps = 3 entries
    assert len(rec.timeseries) == 3
    assert all(v == 1 for v in rec.timeseries)


@pytest.mark.asyncio
async def test_collect_data():
    world = create_world()
    world.register(SimpleAgent())
    collected = []

    def my_collector(w, rec):
        rec.timeseries.append(w.clock.time)
        rec.time.append(w.clock.time)
        collected.append(w.clock.time)

    collect_data(world, "times", my_collector)
    async with world:
        await step_simulation(world, step_size_s=2.0)

    assert "times" in world.data_collections
    assert world.clock.time in collected


@pytest.mark.asyncio
async def test_record_agent():
    world = create_world()
    a1 = world.register(SimpleAgent())
    a2 = world.register(SimpleAgent())
    record_agent(world, "aid_len", lambda a: len(a.aid))
    async with world:
        await step_simulation(world, step_size_s=1.0)

    rec = world.data_agent_collections["aid_len"]
    assert isinstance(rec, AgentsRecording)
    assert a1.aid in rec.timeseries
    assert a2.aid in rec.timeseries


@pytest.mark.asyncio
async def test_record_agent_with_filter():
    class SpecialAgent(SimpleAgent):
        pass

    world = create_world()
    normal = world.register(SimpleAgent())
    special = world.register(SpecialAgent())
    record_agent(
        world,
        "special_only",
        lambda a: 1,
        filter_fn=lambda a: isinstance(a, SpecialAgent),
    )
    async with world:
        await step_simulation(world, step_size_s=1.0)

    rec = world.data_agent_collections["special_only"]
    assert special.aid in rec.timeseries
    assert normal.aid not in rec.timeseries


@pytest.mark.asyncio
async def test_collect_agent_data():
    world = create_world()
    a = world.register(SimpleAgent())

    values = []

    def collector(w, agent, rec):
        values.append(agent.aid)

    collect_agent_data(world, "aids", collector)
    async with world:
        await step_simulation(world, step_size_s=1.0)

    assert a.aid in values


@pytest.mark.asyncio
async def test_record_agent_having():
    from mango import RoleAgent
    from mango.agent.role import Role

    class EnergyRole(Role):
        energy = 42

    world = create_world()
    role_agent = world.register(RoleAgent())
    role_agent.add_role(EnergyRole())
    plain = world.register(SimpleAgent())

    record_agent_having(world, "energy", EnergyRole, lambda a: a.roles[0].energy)
    async with world:
        await step_simulation(world, step_size_s=1.0)

    rec = world.data_agent_collections["energy"]
    assert role_agent.aid in rec.timeseries
    assert plain.aid not in rec.timeseries


@pytest.mark.asyncio
async def test_record_position_and_position_history():
    world = create_world()
    a = world.register(SimpleAgent())
    record_position(world)
    async with world:
        await step_simulation(world, step_size_s=1.0)

    history = position_history(world)
    assert a.aid in history.timeseries
    pos = history.timeseries[a.aid][0]
    assert isinstance(pos, Position2D)


@pytest.mark.asyncio
async def test_record_position_with_filter():
    class SpecialAgent(SimpleAgent):
        pass

    world = create_world()
    normal = world.register(SimpleAgent())
    special = world.register(SpecialAgent())
    record_position(world, filter_fn=lambda a: isinstance(a, SpecialAgent))
    async with world:
        await step_simulation(world, step_size_s=1.0)

    history = position_history(world)
    assert special.aid in history.timeseries
    assert normal.aid not in history.timeseries


@pytest.mark.asyncio
async def test_global_event_dispatched_to_all_agents():
    world = create_world()
    a1 = world.register(SimpleAgent())
    a2 = world.register(SimpleAgent())

    async with world:
        world.environment.emit_global_event("broadcast")

    assert "broadcast" in a1.global_events
    assert "broadcast" in a2.global_events


@pytest.mark.asyncio
async def test_agent_event_dispatched_to_target_only():
    world = create_world()
    a1 = world.register(SimpleAgent())
    a2 = world.register(SimpleAgent())

    # Install agents in environment so emit_agent_event can look them up
    world.environment.install(a1, agent_id=a1.aid)
    world.environment.install(a2, agent_id=a2.aid)

    async with world:
        world.environment.emit_agent_event("targeted", a1.aid)

    assert "targeted" in a1.agent_events
    assert a2.agent_events == []


@pytest.mark.asyncio
async def test_role_on_step_called():
    from mango import RoleAgent
    from mango.agent.role import Role

    class StepRole(Role):
        def __init__(self):
            super().__init__()
            self.step_calls = []

        def on_step(self, env, clock, step_size_s):
            self.step_calls.append(step_size_s)

    world = create_world()
    agent = world.register(RoleAgent())
    role = StepRole()
    agent.add_role(role)

    async with world:
        await step_simulation(world, step_size_s=7.0)

    assert role.step_calls == [7.0]


@pytest.mark.asyncio
async def test_role_on_global_event_called():
    from mango import RoleAgent
    from mango.agent.role import Role

    class EventRole(Role):
        def __init__(self):
            super().__init__()
            self.events = []

        def on_global_event(self, event):
            self.events.append(event)

    world = create_world()
    agent = world.register(RoleAgent())
    role = EventRole()
    agent.add_role(role)

    async with world:
        world.environment.emit_global_event("test_event")

    assert "test_event" in role.events


@pytest.mark.asyncio
async def test_role_on_agent_event_called():
    from mango import RoleAgent
    from mango.agent.role import Role

    class EventRole(Role):
        def __init__(self):
            super().__init__()
            self.events = []

        def on_agent_event(self, event):
            self.events.append(event)

    world = create_world()
    agent = world.register(RoleAgent())
    role = EventRole()
    agent.add_role(role)
    world.environment.install(agent, agent_id=agent.aid)

    async with world:
        world.environment.emit_agent_event("role_event", agent.aid)

    assert "role_event" in role.events


def test_agent_description_defaults():
    desc = AgentDescription()
    assert desc.name == ""
    assert desc.category == "agent"
    assert desc.color == "gray"
    assert len(desc.uid) > 0  # UUID string


def test_agent_description_uid_unique():
    d1 = AgentDescription()
    d2 = AgentDescription()
    assert d1.uid != d2.uid


def test_agent_description_custom_fields():
    desc = AgentDescription(name="Alice", color="blue", category="worker", uid="fixed")
    assert desc.name == "Alice"
    assert desc.color == "blue"
    assert desc.category == "worker"
    assert desc.uid == "fixed"


def test_update_description_name():
    agent = SimpleAgent()
    agent.update_description(name="Bob")
    assert agent.name == "Bob"
    assert agent.color == "gray"  # unchanged


def test_update_description_color():
    agent = SimpleAgent()
    agent.update_description(color="red")
    assert agent.color == "red"


def test_update_description_category():
    agent = SimpleAgent()
    agent.update_description(category="worker")
    assert agent.category == "worker"


def test_update_description_all_fields():
    agent = SimpleAgent()
    agent.update_description(name="Carol", color="green", category="manager")
    assert agent.name == "Carol"
    assert agent.color == "green"
    assert agent.category == "manager"


def test_update_description_none_fields_unchanged():
    agent = SimpleAgent()
    agent.update_description(name="Dave")
    agent.update_description()  # all None – nothing changes
    assert agent.name == "Dave"


def test_forwarding_rule_defaults():
    addr_a = AgentAddress("sim", "a")
    addr_b = AgentAddress("sim", "b")
    rule = ForwardingRule(from_addr=addr_a, to_addr=addr_b)
    assert rule.forward_replies is False


def test_forwarding_rule_forward_replies():
    addr_a = AgentAddress("sim", "a")
    addr_b = AgentAddress("sim", "b")
    rule = ForwardingRule(from_addr=addr_a, to_addr=addr_b, forward_replies=True)
    assert rule.forward_replies is True


def test_add_forwarding_rule():
    agent = SimpleAgent()
    addr_a = AgentAddress("sim", "a")
    addr_b = AgentAddress("sim", "b")
    agent.add_forwarding_rule(addr_a, addr_b)
    assert len(agent._forwarding_rules) == 1
    assert agent._forwarding_rules[0].from_addr == addr_a
    assert agent._forwarding_rules[0].to_addr == addr_b


def test_add_forwarding_rule_with_replies():
    agent = SimpleAgent()
    addr_a = AgentAddress("sim", "a")
    addr_b = AgentAddress("sim", "b")
    agent.add_forwarding_rule(addr_a, addr_b, forward_replies=True)
    assert agent._forwarding_rules[0].forward_replies is True


def test_delete_forwarding_rule_specific_to_addr():
    agent = SimpleAgent()
    addr_a = AgentAddress("sim", "a")
    addr_b = AgentAddress("sim", "b")
    addr_c = AgentAddress("sim", "c")
    agent.add_forwarding_rule(addr_a, addr_b)
    agent.add_forwarding_rule(addr_a, addr_c)
    agent.delete_forwarding_rule(addr_a, to_addr=addr_b)
    assert len(agent._forwarding_rules) == 1
    assert agent._forwarding_rules[0].to_addr == addr_c


def test_delete_forwarding_rule_all_matching_from():
    agent = SimpleAgent()
    addr_a = AgentAddress("sim", "a")
    addr_b = AgentAddress("sim", "b")
    addr_c = AgentAddress("sim", "c")
    agent.add_forwarding_rule(addr_a, addr_b)
    agent.add_forwarding_rule(addr_a, addr_c)
    agent.delete_forwarding_rule(addr_a)  # to_addr=None → remove all with from_addr=a
    assert agent._forwarding_rules == []


def test_delete_forwarding_rule_nonexistent_is_noop():
    agent = SimpleAgent()
    addr_a = AgentAddress("sim", "a")
    addr_b = AgentAddress("sim", "b")
    agent.delete_forwarding_rule(addr_a, addr_b)  # no rules at all – must not raise


@pytest.mark.asyncio
async def test_send_tracked_message_without_handler():
    world = create_world()
    sender = world.register(SimpleAgent())
    receiver = world.register(SimpleAgent())

    async with world:
        await sender.send_tracked_message("ping", receiver_addr=receiver.addr)
        await step_simulation(world, step_size_s=1.0)

    assert len(receiver.messages) == 1
    _, meta = receiver.messages[0]
    assert "tracking_id" in meta
    # No handler registered → transaction_handlers still empty after send
    assert len(sender._transaction_handlers) == 0


@pytest.mark.asyncio
async def test_send_tracked_message_with_handler():
    world = create_world()
    handler_calls = []

    class HandlerAgent(Agent):
        def handle_message(self, content, meta):
            self.schedule_instant_task(self.reply_to("pong", meta))

    sender = world.register(SimpleAgent())
    receiver = world.register(HandlerAgent())

    async with world:
        await sender.send_tracked_message(
            "ping",
            receiver_addr=receiver.addr,
            response_handler=lambda c, m: handler_calls.append(c),
        )
        # Deliver ping to receiver
        await step_simulation(world, step_size_s=1.0)
        # Deliver pong back to sender
        await step_simulation(world, step_size_s=1.0)

    assert handler_calls == ["pong"]
    # Handler consumed – no leftover entries
    assert len(sender._transaction_handlers) == 0


@pytest.mark.asyncio
async def test_reply_to_sends_back_to_sender():
    world = create_world()
    sender = world.register(SimpleAgent())
    receiver = world.register(ReplyAgent())

    async with world:
        await sender.send_message("ping", receiver_addr=receiver.addr)
        await step_simulation(world, step_size_s=1.0)
        await step_simulation(world, step_size_s=1.0)

    assert len(sender.messages) == 1
    assert sender.messages[0][0] == "pong"


def test_handle_tracked_reply_returns_false_no_tracking_id():
    agent = SimpleAgent()
    result = agent._handle_tracked_reply("content", {})
    assert result is False


def test_handle_tracked_reply_returns_false_reply_false():
    agent = SimpleAgent()
    tid = "some-id"
    agent._transaction_handlers[tid] = (lambda c, m: None,)
    result = agent._handle_tracked_reply(
        "content", {"tracking_id": tid, "reply": False}
    )
    assert result is False
    # Handler should still be present
    assert tid in agent._transaction_handlers


def test_handle_tracked_reply_returns_false_no_handler():
    agent = SimpleAgent()
    result = agent._handle_tracked_reply(
        "content", {"tracking_id": "unknown-id", "reply": True}
    )
    assert result is False


def test_handle_tracked_reply_calls_handler_and_removes():
    agent = SimpleAgent()
    called = []
    tid = "test-tid"
    agent._transaction_handlers[tid] = (lambda c, m: called.append(c),)
    result = agent._handle_tracked_reply(
        "reply_content", {"tracking_id": tid, "reply": True}
    )
    assert result is True
    assert called == ["reply_content"]
    assert tid not in agent._transaction_handlers


@pytest.mark.asyncio
async def test_run_with_simulation_basic():
    a1 = SimpleAgent()
    a2 = SimpleAgent()

    async with run_with_simulation(a1, a2) as world:
        assert a1.aid is not None
        assert a2.aid is not None
        await step_simulation(world, step_size_s=1.0)

    assert world.running is False


@pytest.mark.asyncio
async def test_run_with_simulation_suggested_aid():
    a = SimpleAgent()
    async with run_with_simulation((a, {"aid": "my_agent"})) as world:
        assert a.aid == "my_agent"
        assert world["my_agent"] is a


@pytest.mark.asyncio
async def test_run_with_simulation_custom_comm_sim():
    comm = SimpleCommunicationSimulation(default_delay_s=3.0)
    a = SimpleAgent()
    async with run_with_simulation(a, communication_sim=comm) as world:
        assert world.communication_sim is comm


@pytest.mark.asyncio
async def test_run_with_simulation_custom_environment():
    env = DefaultEnvironment(space=Area2D(width=999.0, height=999.0))
    a = SimpleAgent()
    async with run_with_simulation(a, environment=env) as world:
        assert world.environment is env


@pytest.mark.asyncio
async def test_run_with_simulation_start_time():
    a = SimpleAgent()
    async with run_with_simulation(a, start_time=50.0) as world:
        assert world.clock.time == 50.0


@pytest.mark.asyncio
async def test_discrete_step_until_via_run_with_simulation():
    class CountingAgent(Agent):
        def __init__(self):
            super().__init__()
            self.ticks = 0

        def handle_message(self, content, meta):
            pass

        def on_ready(self):
            self.schedule_periodic_task(self._tick, 1.0)

        async def _tick(self):
            self.ticks += 1

    agent = CountingAgent()
    async with run_with_simulation(agent) as world:
        results = await discrete_step_until(world, max_advance_time_s=3.0)

    assert world.clock.time <= 3.0
    assert len(results) >= 1
    assert agent.ticks >= 1
