"""Tests for :class:`mango.agent.conversation.Conversation`.

Three flavours of coverage:

* Real-time (TCP) — initiator opens a conversation, joiner receives
  the message and joins it, both sides exchange multiple messages
  under one id.
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
    Role,
    RoleAgent,
    activate,
    create_tcp_container,
    on_message,
    sender_addr,
)
from mango.express.api import run_with_simulation
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
