"""Tests for :class:`mango.agent.conversation.Conversation`.

Coverage flavours:

* Handle-level — termination latch (multi-consumer wakeup, re-iteration),
  send tagging, broadcast results.
* Real-time (TCP) — initiator opens a conversation, joiner receives
  the message and joins it, both sides exchange multiple messages
  under one id; also across two containers and with plain (role-less)
  agents.
* Convergence / cancellation — the iterator terminates correctly when
  the caller signals end.
* Simulation-time timeout — exercises the conversation's clock-aware
  timeout under an :class:`ExternalClock` so we know the timeout
  advances with simulation time, not wall time.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from mango import (
    Agent,
    Conversation,
    Role,
    RoleAgent,
    activate,
    create_acl,
    create_tcp_container,
    json_serializable,
    on_message,
    sender_addr,
)
from mango.express.api import run_with_simulation
from mango.messages.codecs import JSON
from mango.simulation.world import step_simulation


@dataclass
class _Step:
    """Multi-hop payload — carries a counter the participants increment."""

    counter: int


# ---------------------------------------------------------------------------
# End-to-end multi-hop conversation on TCP.
# ---------------------------------------------------------------------------


class _Joiner(Role):
    """Joiner-side: reads incoming step, replies with the next counter.
    Stays in the conversation so the initiator can keep volleying."""

    def __init__(self) -> None:
        super().__init__()
        self.processed: list[int] = []

    @on_message(_Step)
    async def on_step(self, content: _Step, meta: dict) -> None:
        self.processed.append(content.counter)
        async with self.context.join_conversation(meta) as conv:
            await conv.send(sender_addr(meta), _Step(counter=content.counter + 1))


class _Initiator(Role):
    """Opens a conversation, sends N messages and reads N replies on
    the same conversation id."""

    def __init__(self, peer_addr, *, rounds: int, timeout: float = 5.0) -> None:
        super().__init__()
        self.peer_addr = peer_addr
        self.rounds = rounds
        self.timeout = timeout
        self.seen: list[int] = []

    async def run(self) -> None:
        async with self.context.open_conversation(timeout=self.timeout) as conv:
            await conv.send(self.peer_addr, _Step(counter=0))
            async for content, meta in conv:
                self.seen.append(content.counter)
                if len(self.seen) >= self.rounds:
                    conv.converge()
                    continue
                await conv.send(self.peer_addr, _Step(counter=content.counter + 1))


@pytest.mark.asyncio
async def test_conversation_round_trip():
    """Three back-and-forth volleys carry the same conversation id —
    every reply routes to the initiator's async iterator, the joiner's
    fresh ``join_conversation`` succeeds every time."""
    container = create_tcp_container(addr=("127.0.0.1", 5580))
    joiner_agent = container.register(RoleAgent())
    joiner = _Joiner()
    joiner_agent.add_role(joiner)

    initiator_agent = container.register(RoleAgent())
    initiator = _Initiator(joiner_agent.addr, rounds=3)
    initiator_agent.add_role(initiator)

    async with activate([container]):
        await initiator.run()

    # Initiator received exactly ``rounds`` replies with strictly
    # increasing counters.
    assert initiator.seen == [1, 3, 5]
    # Joiner processed every initiator-sent step.
    assert joiner.processed == [0, 2, 4]


# ---------------------------------------------------------------------------
# Convergence and cancellation
# ---------------------------------------------------------------------------


class _BroadcastRecv(Role):
    @on_message(_Step)
    async def on_step(self, content: _Step, meta: dict) -> None:
        async with self.context.join_conversation(meta) as conv:
            # Echo once, then converge.
            await conv.send(sender_addr(meta), _Step(counter=content.counter * 10))


@pytest.mark.asyncio
async def test_conversation_converge_delivers_final_message():
    """``converge()`` lets the iterator deliver the message in flight
    before terminating.  The next pull then exits cleanly."""

    class CollectingInitiator(Role):
        def __init__(self, peer):
            super().__init__()
            self.peer = peer
            self.seen = []
            self.iterations = 0

        async def run(self):
            async with self.context.open_conversation(timeout=2.0) as conv:
                await conv.send(self.peer, _Step(counter=5))
                async for content, _meta in conv:
                    self.iterations += 1
                    self.seen.append(content.counter)
                    conv.converge()  # end after this one
            # On exit `closed` should be set.
            assert conv.closed

    container = create_tcp_container(addr=("127.0.0.1", 5581))
    recv = container.register(RoleAgent())
    recv.add_role(_BroadcastRecv())
    init_agent = container.register(RoleAgent())
    init = CollectingInitiator(recv.addr)
    init_agent.add_role(init)

    async with activate([container]):
        await init.run()

    assert init.seen == [50]
    assert init.iterations == 1


@pytest.mark.asyncio
async def test_conversation_cancel_drops_remaining_messages():
    """``cancel()`` ends iteration on the next pull without delivering
    whatever was already in flight."""

    container = create_tcp_container(addr=("127.0.0.1", 5582))
    init_agent = container.register(RoleAgent())

    class Solo(Role):
        async def run(self):
            async with self.context.open_conversation(timeout=2.0) as conv:
                conv.cancel()
                pulls = 0
                async for _, _meta in conv:  # pragma: no cover - shouldn't loop
                    pulls += 1
                self.pulls = pulls

    solo = Solo()
    init_agent.add_role(solo)

    async with activate([container]):
        await solo.run()

    assert solo.pulls == 0


# ---------------------------------------------------------------------------
# Handle-level behaviour — no container needed.
# ---------------------------------------------------------------------------


class _RecordingOwner:
    """Stub owner capturing what ``Conversation.send`` forwards."""

    def __init__(self, results: dict | None = None):
        self.sent = []
        self._results = results or {}

    async def send_message(self, content, receiver_addr, **kwargs):
        self.sent.append((receiver_addr, content, kwargs))
        return self._results.get(receiver_addr, True)


def _handle(owner=None, **kwargs) -> Conversation:
    return Conversation(owner=owner, conversation_id="cid-1", **kwargs)


@pytest.mark.asyncio
async def test_converge_wakes_all_concurrent_iterators():
    """The end sentinel is a latch: every consumer blocked in ``__anext__``
    terminates, not just the one that happens to read it first."""
    conv = _handle()

    async def consume():
        async for _ in conv:  # pragma: no cover - stream stays empty
            pass
        return True

    consumers = [asyncio.create_task(consume()) for _ in range(3)]
    await asyncio.sleep(0)  # let all consumers block in queue.get()
    conv.converge()
    results = await asyncio.wait_for(asyncio.gather(*consumers), timeout=1.0)
    assert results == [True, True, True]


@pytest.mark.asyncio
async def test_iteration_after_converge_terminates_immediately():
    """A second drain loop on an already-converged handle must exit
    instead of awaiting an empty queue forever."""
    conv = _handle()
    conv._on_inbound("x", {})
    conv.converge()

    assert [c async for c, _ in conv] == ["x"]
    assert [c async for c, _ in conv] == []


@pytest.mark.asyncio
async def test_cancel_wakes_all_concurrent_iterators():
    conv = _handle()

    async def consume():
        return [c async for c, _ in conv]

    consumers = [asyncio.create_task(consume()) for _ in range(2)]
    await asyncio.sleep(0)  # both block in queue.get()
    conv.cancel()
    results = await asyncio.wait_for(asyncio.gather(*consumers), timeout=1.0)
    assert results == [[], []]


@pytest.mark.asyncio
async def test_send_id_wins_over_kwargs_and_stamps_content():
    """The conversation's own id beats a stray ``conversation_id`` kwarg,
    and content carrying a ``conversation_id`` attribute (ACL-style) is
    stamped so the id survives split-content transports."""

    @dataclass
    class _AclLike:
        conversation_id: str | None = None

    owner = _RecordingOwner()
    conv = _handle(owner)
    payload = _AclLike()

    assert await conv.send("addr-1", payload, conversation_id="spoofed")

    _, sent_content, kwargs = owner.sent[0]
    assert kwargs["conversation_id"] == "cid-1"
    assert sent_content.conversation_id == "cid-1"


@pytest.mark.asyncio
async def test_broadcast_returns_result_per_receiver():
    owner = _RecordingOwner(results={"bad": False})
    conv = _handle(owner)

    results = await conv.broadcast(["good", "bad"], _Step(counter=0))

    assert results == {"good": True, "bad": False}
    assert all(kw["conversation_id"] == "cid-1" for _, _, kw in owner.sent)


@pytest.mark.asyncio
async def test_handle_escaping_its_context_terminates():
    """Leaving the ``async with`` cancels the handle, so an iterator that
    escaped the block ends instead of hanging on a dead queue."""
    container = create_tcp_container(addr=("127.0.0.1", 5583))
    agent = container.register(RoleAgent())
    role = Role()
    agent.add_role(role)

    async with activate([container]):
        async with role.context.open_conversation() as conv:
            pass
        assert conv.closed
        with pytest.raises(StopAsyncIteration):
            await conv.__anext__()


# ---------------------------------------------------------------------------
# Fan-out: several handles on the same id all receive every message.
# ---------------------------------------------------------------------------


class _DoubleJoiner(Agent):
    """Joins the same conversation twice concurrently; both handles must
    independently receive the follow-up message."""

    def __init__(self):
        super().__init__()
        self.ready = asyncio.Event()
        self.got: tuple | None = None

    def handle_message(self, content, meta):
        if content == "start":
            self.schedule_instant_task(self._double_join(dict(meta)))

    async def _double_join(self, meta):
        async with self.join_conversation(meta) as first:
            async with self.join_conversation(meta) as second:
                self.ready.set()
                got_first = await first.__anext__()
                got_second = await second.__anext__()
                self.got = (got_first[0], got_second[0])


@pytest.mark.asyncio
async def test_overlapping_joins_each_receive_every_message():
    container = create_tcp_container(addr=("127.0.0.1", 5584))
    joiner = container.register(_DoubleJoiner())
    initiator = container.register(RoleAgent())
    role = Role()
    initiator.add_role(role)

    async with activate([container]):
        async with role.context.open_conversation(timeout=5.0) as conv:
            await conv.send(joiner.addr, "start")
            await asyncio.wait_for(joiner.ready.wait(), timeout=2.0)
            await conv.send(joiner.addr, "payload")
            while joiner.got is None:
                await asyncio.sleep(0.01)

    assert joiner.got == ("payload", "payload")


# ---------------------------------------------------------------------------
# Plain (role-less) agents: conversations, reply_to threading, gather.
# ---------------------------------------------------------------------------


class _PlainResponder(Agent):
    """Replies via ``reply_to`` — the conversation id must thread back so
    the initiator's iterator receives the reply."""

    def handle_message(self, content, meta):
        if isinstance(content, _Step):
            self.schedule_instant_task(
                self.reply_to(_Step(counter=content.counter + 1), meta)
            )


class _PlainInitiator(Agent):
    def __init__(self, rounds: int):
        super().__init__()
        self.rounds = rounds
        self.seen: list[int] = []

    def handle_message(self, content, meta):
        pass

    async def run(self, peer) -> None:
        async with self.open_conversation(timeout=5.0) as conv:
            await conv.send(peer, _Step(counter=0))
            async for content, _meta in conv:
                self.seen.append(content.counter)
                if len(self.seen) >= self.rounds:
                    conv.converge()
                    continue
                await conv.send(peer, _Step(counter=content.counter + 1))


@pytest.mark.asyncio
async def test_plain_agent_conversation_with_reply_to():
    container = create_tcp_container(addr=("127.0.0.1", 5585))
    responder = container.register(_PlainResponder())
    initiator = container.register(_PlainInitiator(rounds=2))

    async with activate([container]):
        await initiator.run(responder.addr)

    assert initiator.seen == [1, 3]


class _PongAgent(Agent):
    def handle_message(self, content, meta):
        if content == "ping":
            self.schedule_instant_task(self.reply_to("pong", meta))


class _QuietAgent(Agent):
    def handle_message(self, content, meta):
        pass


@pytest.mark.asyncio
async def test_plain_agent_gather():
    container = create_tcp_container(addr=("127.0.0.1", 5586))
    responders = [container.register(_PongAgent()) for _ in range(3)]
    caller = container.register(_QuietAgent())

    async with activate([container]):
        replies = await caller.gather("ping", [r.addr for r in responders], timeout=2.0)

    assert len(replies) == 3
    assert set(replies.values()) == {"pong"}
    assert set(replies.keys()) == {r.addr for r in responders}


# ---------------------------------------------------------------------------
# Cross-container: the id must survive the codec, for plain and ACL content.
# ---------------------------------------------------------------------------


@json_serializable
@dataclass
class _WireStep:
    counter: int


def _wire_codec() -> JSON:
    codec = JSON()
    codec.add_serializer(*_WireStep.__serializer__())
    return codec


class _WireJoiner(Role):
    def __init__(self) -> None:
        super().__init__()
        self.processed: list[int] = []

    @on_message(_WireStep)
    async def on_step(self, content: _WireStep, meta: dict) -> None:
        self.processed.append(content.counter)
        async with self.context.join_conversation(meta) as conv:
            await conv.send(sender_addr(meta), _WireStep(counter=content.counter + 1))


class _WireInitiator(Role):
    def __init__(self, peer_addr, *, rounds: int, acl: bool = False) -> None:
        super().__init__()
        self.peer_addr = peer_addr
        self.rounds = rounds
        self.acl = acl
        self.seen: list[int] = []

    def _payload(self, counter: int):
        step = _WireStep(counter=counter)
        if not self.acl:
            return step
        return create_acl(
            step, receiver_addr=self.peer_addr, sender_addr=self.context.addr
        )

    async def run(self) -> None:
        async with self.context.open_conversation(timeout=5.0) as conv:
            await conv.send(self.peer_addr, self._payload(0))
            async for content, _meta in conv:
                self.seen.append(content.counter)
                if len(self.seen) >= self.rounds:
                    conv.converge()
                    continue
                await conv.send(self.peer_addr, self._payload(content.counter + 1))


async def _run_two_container_conversation(port_a: int, port_b: int, *, acl: bool):
    container_a = create_tcp_container(addr=("127.0.0.1", port_a), codec=_wire_codec())
    container_b = create_tcp_container(addr=("127.0.0.1", port_b), codec=_wire_codec())

    joiner_agent = container_b.register(RoleAgent())
    joiner = _WireJoiner()
    joiner_agent.add_role(joiner)

    initiator_agent = container_a.register(RoleAgent())
    initiator = _WireInitiator(joiner_agent.addr, rounds=2, acl=acl)
    initiator_agent.add_role(initiator)

    async with activate([container_a, container_b]):
        await asyncio.wait_for(initiator.run(), timeout=5.0)

    assert initiator.seen == [1, 3]
    assert joiner.processed == [0, 2]


@pytest.mark.asyncio
async def test_conversation_across_two_containers():
    """The conversation id survives JSON-codec serialization over TCP."""
    await _run_two_container_conversation(5587, 5588, acl=False)


@pytest.mark.asyncio
async def test_conversation_across_two_containers_with_acl_content():
    """ACL content is sent without the kwargs meta, so the id must travel
    in the ACL message's own ``conversation_id`` field (stamped by
    ``conv.send``)."""
    await _run_two_container_conversation(5589, 5590, acl=True)


# ---------------------------------------------------------------------------
# Simulation-clock timeout — the load-bearing requirement.
# ---------------------------------------------------------------------------


class SimAgent(Agent):
    """A bare :class:`Agent` for testing — we only need the scheduler
    clock; the message inbox is unused."""

    def handle_message(self, content, meta):
        pass


@pytest.mark.asyncio
async def test_conversation_timeout_follows_simulation_clock():
    """An open conversation under ``run_with_simulation`` must time out
    when *simulation* time crosses the deadline — not wall time.

    Strategy: open a conversation with ``timeout=5.0`` simulated
    seconds and advance the clock by one 5-second step.  The
    timeout future on the conversation's context manager fires when
    ``clock.sleep(5.0)`` resolves, which under ``ExternalClock`` only
    happens after the world advances past that instant.
    """
    from mango import RoleAgent
    from mango.agent.role import Role

    class TimedRole(Role):
        def __init__(self):
            super().__init__()
            self.iteration_done_at = None
            self.before_advance = None

        async def watch(self, world):
            async with self.context.open_conversation(timeout=5.0) as conv:
                self.before_advance = world.clock.time

                # Schedule the world advance as a background task so
                # the async iterator can proceed.  ``step_simulation``
                # advances the clock by 5 s, which makes the
                # conversation's ``clock.sleep(5.0)`` future fire.
                async def _advance():
                    # Give the conversation context manager a moment
                    # to register its sleep future before we tick.
                    await asyncio.sleep(0)
                    await step_simulation(world, step_size_s=5.0)

                advancer = asyncio.create_task(_advance())
                # Drain the conversation — when the timeout fires the
                # iterator exits.
                async for _, _meta in conv:  # pragma: no cover — empty stream
                    pass
                await advancer
                self.iteration_done_at = world.clock.time

    agent = RoleAgent()
    role = TimedRole()
    agent.add_role(role)

    async with run_with_simulation(agent) as world:
        await role.watch(world)

    # The conversation exited after simulation time advanced 5 s, not
    # 5 wall seconds — confirms the timeout used clock.sleep.
    assert role.before_advance == pytest.approx(0.0)
    assert role.iteration_done_at == pytest.approx(5.0)
