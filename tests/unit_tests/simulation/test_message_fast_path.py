"""Messages in a simulation world: the instant-message fast path and message retention.

``schedule_instant_message`` in a :class:`~mango.SimulationWorld` queues one
event-loop callback instead of an asyncio task. These tests pin that the fast
path changes nothing observable (order, send times, delays, loss draws, role
send subscriptions, overrides, error reporting), and that
``create_world(record_messages=False)`` only counts delivered messages.
"""

import asyncio

import pytest

from mango import (
    Agent,
    AgentTaskError,
    Role,
    RoleAgent,
    create_world,
    record_world,
)
from mango.simulation import SimulationContainer
from mango.simulation.communication import (
    CommunicationSimulation,
    DelayProviderCommunicationSimulation,
)


class Chatter(Agent):
    """Sends from on_step, from a periodic task and in reply to messages."""

    def __init__(self):
        super().__init__()
        self.peers = []
        self.received = []

    def on_ready(self):
        async def tick():
            if self.peers:
                await self.send_message(("tick", self.rng.random()), self.peers[0])

        self.schedule_periodic_task(tick, 1.5)

    def on_step(self, env, clock, step_size_s):
        for peer in self.peers:
            if self.rng.random() < 0.6:
                self.schedule_instant_message(("step", clock.time), peer)

    def handle_message(self, content, meta):
        self.received.append((content, meta["sender_id"]))
        if content[0] == "step" and self.rng.random() < 0.5:
            self.schedule_instant_message(
                ("reply", content[1]), self.peers[-1], note="r"
            )


class Gossip(Role):
    def __init__(self):
        super().__init__()
        self.peers = []
        self.sent_seen = []

    def setup(self):
        self.context.subscribe_send(self, self.on_send)

    def on_send(self, content, receiver_addr, **kwargs):
        self.sent_seen.append((content, receiver_addr.aid))

    def on_step(self, env, clock, step_size_s):
        for peer in self.peers:
            self.context.schedule_instant_message(("gossip", clock.time), peer)


async def _run_mixed_world(discrete: bool):
    world = create_world(
        seed=11,
        communication_sim=DelayProviderCommunicationSimulation(
            default_delay_s_provider=lambda rng: float(rng.integers(0, 3)),
            loss_percent=0.1,
        ),
    )
    chatters = [world.register(Chatter(), f"c{i}") for i in range(4)]
    for i, chatter in enumerate(chatters):
        chatter.peers = [c.addr for c in chatters if c is not chatter][: 1 + i % 3]
    host = world.register(RoleAgent(), "host")
    gossip = Gossip()
    host.add_role(gossip)
    gossip.peers = [chatters[0].addr, chatters[2].addr]
    async with world:
        if discrete:
            await world.step(1.0)
            await world.step_until(25.0)
        else:
            for _ in range(25):
                await world.step(1.0)
    messages = [
        (m.sender_id, m.receiver_id, m.sent_time, m.arriving_time, m.content)
        for m in world.recorded_messages
    ]
    return messages, [c.received for c in chatters], gossip.sent_seen


@pytest.mark.parametrize("discrete", [False, True])
@pytest.mark.asyncio
async def test_the_fast_path_delivers_exactly_what_the_task_path_delivers(
    monkeypatch, discrete
):
    fast = await _run_mixed_world(discrete)
    monkeypatch.setattr(SimulationContainer, "_send_soon", None)  # the task path
    slow = await _run_mixed_world(discrete)
    assert len(fast[0]) > 100
    assert {m[4][0] for m in fast[0]} == {"step", "tick", "reply", "gossip"}
    assert fast == slow


class Hub(Agent):
    def __init__(self):
        super().__init__()
        self.leaves = []
        self.handles = []

    def on_step(self, env, clock, step_size_s):
        for leaf in self.leaves:
            self.handles.append(self.schedule_instant_message("x", leaf))


class Leaf(Agent):
    def __init__(self):
        super().__init__()
        self.got = []

    def handle_message(self, content, meta):
        self.got.append((content, meta["sent_at"] if "sent_at" in meta else None))


@pytest.mark.asyncio
async def test_instant_messages_create_no_tasks_and_resolve_to_the_send_result():
    world = create_world(seed=1)
    hub = world.register(Hub(), "hub")
    leaves = [world.register(Leaf(), f"l{i}") for i in range(3)]
    hub.leaves = [leaf.addr for leaf in leaves]
    scheduled = []
    original = hub.scheduler.schedule_task
    hub.scheduler.schedule_task = lambda task, src=None: (
        scheduled.append(task) or original(task, src=src)
    )
    async with world:
        result = await world.step(1.0)
    assert result.messages_delivered == 3
    assert scheduled == []  # no asyncio task per message
    assert [await handle for handle in hub.handles] == [True, True, True]
    assert all(not isinstance(h, asyncio.Task) for h in hub.handles)
    # sent from on_step: the send time is the end of the step, as before
    assert [m.sent_time for m in world.recorded_messages] == [1.0, 1.0, 1.0]


class LoggingHub(Hub):
    def __init__(self):
        super().__init__()
        self.logged = []

    async def send_message(self, content, receiver_addr, **kwargs):
        self.logged.append(content)
        return await super().send_message(content, receiver_addr, sent_at=1, **kwargs)


@pytest.mark.asyncio
async def test_an_overridden_send_message_is_still_called():
    world = create_world(seed=1)
    hub = world.register(LoggingHub(), "hub")
    leaf = world.register(Leaf(), "leaf")
    hub.leaves = [leaf.addr]
    async with world:
        await world.step(1.0)
    assert hub.logged == ["x"]
    assert leaf.got == [("x", 1)]
    assert isinstance(hub.handles[0], asyncio.Task)  # the regular path


@pytest.mark.asyncio
async def test_role_send_subscriptions_see_fast_path_messages():
    world = create_world(seed=1)
    host = world.register(RoleAgent(), "host")
    leaf = world.register(Leaf(), "leaf")
    gossip = Gossip()
    host.add_role(gossip)
    gossip.peers = [leaf.addr]
    async with world:
        await world.step(1.0)
    assert gossip.sent_seen == [(("gossip", 0.0), "leaf")]  # content: on_step time
    assert leaf.got == [(("gossip", 0.0), None)]


class Exploding(CommunicationSimulation):
    def calculate_communication(self, current_time, messages):
        raise ValueError("no route")


@pytest.mark.asyncio
async def test_a_failing_send_fails_the_step_like_a_failing_task():
    world = create_world(seed=1, communication_sim=Exploding())
    hub = world.register(Hub(), "hub")
    leaf = world.register(Leaf(), "leaf")
    hub.leaves = [leaf.addr]
    async with world:
        with pytest.raises(AgentTaskError, match="no route") as info:
            await world.step(1.0)
    assert info.value.errors[0].aid == "hub"
    assert info.value.errors[0].task == "AgentDelegates.send_message"
    with pytest.raises(ValueError, match="no route"):
        await hub.handles[0]


@pytest.mark.asyncio
async def test_tracing_keeps_the_task_path(tmp_path):
    world = create_world(seed=1, trace=tmp_path / "t.jsonl")
    hub = world.register(Hub(), "hub")
    leaf = world.register(Leaf(), "leaf")
    hub.leaves = [leaf.addr]
    async with world:
        await world.step(1.0)
    assert isinstance(hub.handles[0], asyncio.Task)  # traced like any task
    assert leaf.got == [("x", None)]


# ------------------------------------------------------------------ record_messages


@pytest.mark.asyncio
async def test_plain_worlds_keep_delivered_messages_by_default():
    world = create_world(seed=1)
    hub = world.register(Hub(), "hub")
    hub.leaves = [world.register(Leaf(), "leaf").addr]
    async with world:
        await world.step(1.0)
        await world.step(1.0)
    assert world.record_messages is True
    assert len(world.recorded_messages) == world.messages_delivered == 2


@pytest.mark.asyncio
async def test_record_messages_false_only_counts_and_frees_the_contents():
    world = create_world(seed=1, record_messages=False)
    hub = world.register(Hub(), "hub")
    leaf = world.register(Leaf(), "leaf")
    hub.leaves = [leaf.addr]
    record_world(world, "n_msgs", lambda: world.messages_delivered)
    async with world:
        for _ in range(3):
            await world.step(1.0)
        assert world.messages_delivered == 3 and len(leaf.got) == 3
        assert world.container.recorded_messages == []
        assert world.data_collections["n_msgs"].timeseries == [0, 1, 2, 3]
        with pytest.raises(RuntimeError, match="record_messages=False"):
            world.recorded_messages  # noqa: B018
        world.record_messages = True  # switchable at any time
        await world.step(1.0)
    assert len(world.recorded_messages) == 1 and world.messages_delivered == 4
