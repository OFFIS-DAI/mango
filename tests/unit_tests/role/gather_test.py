"""End-to-end tests for :meth:`RoleContext.gather`.

``gather`` is the simplification target for the most common scare /
mes pattern: send a request to N agents, collect their replies under
one id, return either when everyone answered or after a timeout.
These tests run against real TCP containers so the message-passing
infrastructure (tracking_id threading, reply matching) is exercised
end-to-end, not stubbed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from mango import (
    Role,
    RoleAgent,
    activate,
    create_tcp_container,
    on_message,
)


@dataclass
class _Ask:
    """Request payload — carried by the gather caller."""

    topic: str


@dataclass
class _Reply:
    """Per-responder reply payload."""

    value: float


class _Responder(Role):
    """Replies to every :class:`_Ask` it receives with its configured value.

    Uses :meth:`AgentDelegates.reply_to` so the ``tracking_id`` threads
    back to the caller automatically — that is the contract the gather
    machinery relies on.
    """

    def __init__(self, value: float):
        super().__init__()
        self.value = value
        self.received: int = 0

    @on_message(_Ask)
    async def on_ask(self, content: _Ask, meta: dict) -> None:
        self.received += 1
        await self.context.reply_to(_Reply(value=self.value), received_meta=meta)


class _SilentResponder(Role):
    """Never replies — used to verify the timeout branch."""

    @on_message(_Ask)
    async def on_ask(self, content: _Ask, meta: dict) -> None:  # noqa: ARG002
        return None


class _Caller(Role):
    """Owns the ``gather`` call.  Stored result is asserted by the test."""

    def __init__(self, receivers, *, timeout: float, min_fraction: float = 1.0):
        super().__init__()
        self.receivers = receivers
        self.timeout = timeout
        self.min_fraction = min_fraction
        self.responses: dict[Any, _Reply] | None = None
        self.elapsed: float = 0.0

    async def run(self) -> None:
        start = asyncio.get_event_loop().time()
        self.responses = await self.context.gather(
            _Ask(topic="x"),
            receivers=self.receivers,
            reply_type=_Reply,
            timeout=self.timeout,
            min_fraction=self.min_fraction,
        )
        self.elapsed = asyncio.get_event_loop().time() - start


@pytest.mark.asyncio
async def test_gather_collects_all_replies():
    container = create_tcp_container(addr=("127.0.0.1", 5556))
    responder_agents = []
    responder_addrs = []
    for v in (1.0, 2.0, 3.0):
        a = container.register(RoleAgent())
        a.add_role(_Responder(value=v))
        responder_agents.append(a)
        responder_addrs.append(a.addr)

    caller = container.register(RoleAgent())
    caller_role = _Caller(responder_addrs, timeout=2.0)
    caller.add_role(caller_role)

    async with activate([container]):
        await caller_role.run()

    assert caller_role.responses is not None
    assert len(caller_role.responses) == 3
    values = sorted(r.value for r in caller_role.responses.values())
    assert values == [1.0, 2.0, 3.0]
    # All three responded, so we never hit the timeout — gather should
    # have returned essentially immediately.
    assert caller_role.elapsed < 2.0


@pytest.mark.asyncio
async def test_gather_returns_on_timeout_with_partial_results():
    """With one silent responder and ``min_fraction=1.0``, gather waits
    until the timeout and returns whatever did arrive."""
    container = create_tcp_container(addr=("127.0.0.1", 5557))
    addrs = []
    for v in (1.0, 2.0):
        a = container.register(RoleAgent())
        a.add_role(_Responder(value=v))
        addrs.append(a.addr)
    silent = container.register(RoleAgent())
    silent.add_role(_SilentResponder())
    addrs.append(silent.addr)

    caller = container.register(RoleAgent())
    caller_role = _Caller(addrs, timeout=0.5)
    caller.add_role(caller_role)

    async with activate([container]):
        await caller_role.run()

    assert caller_role.responses is not None
    # Two responders answered; the silent one did not.
    assert len(caller_role.responses) == 2
    # The timeout should have been hit (we waited ≥ timeout-but-less than 2x).
    assert 0.4 <= caller_role.elapsed < 1.5


@pytest.mark.asyncio
async def test_gather_returns_early_on_quorum():
    """``min_fraction=0.5`` lets gather return as soon as half have replied —
    no need to wait for the silent member."""
    container = create_tcp_container(addr=("127.0.0.1", 5558))
    addrs = []
    for v in (1.0, 2.0):
        a = container.register(RoleAgent())
        a.add_role(_Responder(value=v))
        addrs.append(a.addr)
    silent = container.register(RoleAgent())
    silent.add_role(_SilentResponder())
    addrs.append(silent.addr)

    caller = container.register(RoleAgent())
    caller_role = _Caller(addrs, timeout=5.0, min_fraction=0.5)
    caller.add_role(caller_role)

    async with activate([container]):
        await caller_role.run()

    # Quorum is 2 (round(0.5 * 3) = 2).  Should return as soon as the
    # two real responders answered — well under the 5 s timeout.
    assert caller_role.responses is not None
    assert len(caller_role.responses) >= 2
    assert caller_role.elapsed < 1.0


@pytest.mark.asyncio
async def test_gather_empty_receivers_returns_empty_dict():
    """A degenerate ``gather`` with no receivers must not block."""
    container = create_tcp_container(addr=("127.0.0.1", 5559))
    caller = container.register(RoleAgent())
    caller_role = _Caller([], timeout=2.0)
    caller.add_role(caller_role)

    async with activate([container]):
        await caller_role.run()

    assert caller_role.responses == {}
    assert caller_role.elapsed < 0.5


@pytest.mark.asyncio
async def test_gather_timeout_uses_simulation_clock():
    """Under :class:`ExternalClock` the timeout must advance with the
    simulation, not with wall time.  Strategy: open a gather to a
    silent receiver with ``timeout=10`` simulated seconds, advance the
    world clock by one 10-second step in a background task, and
    confirm the call returns when the *clock* crosses the deadline."""
    from mango import RoleAgent
    from mango.express.api import run_with_simulation
    from mango.simulation.world import step_simulation

    class SilentInSim(Role):
        @on_message(_Ask)
        async def on_ask(self, content: _Ask, meta: dict) -> None:
            return None

    class SimCaller(Role):
        def __init__(self, peer):
            super().__init__()
            self.peer = peer
            self.elapsed_sim_time = None

        async def run(self, world):
            start = world.clock.time
            advancer = asyncio.create_task(
                _advance_after_sleep(world, step_size_s=10.0)
            )
            responses = await self.context.gather(
                _Ask(topic="x"),
                receivers=[self.peer],
                reply_type=_Reply,
                timeout=10.0,
                min_fraction=1.0,
            )
            await advancer
            self.elapsed_sim_time = world.clock.time - start
            assert responses == {}

    async def _advance_after_sleep(world, *, step_size_s):
        # Yield once so the gather() call has time to register its
        # ``clock.sleep`` future before we tick.
        await asyncio.sleep(0)
        await step_simulation(world, step_size_s=step_size_s)

    silent_agent = RoleAgent()
    silent_agent.add_role(SilentInSim())
    caller_agent = RoleAgent()

    async with run_with_simulation(silent_agent, caller_agent) as world:
        caller = SimCaller(silent_agent.addr)
        caller_agent.add_role(caller)
        await caller.run(world)

    # 10 simulated seconds elapsed — confirms gather used clock.sleep,
    # not wall-clock asyncio.wait_for.
    assert caller.elapsed_sim_time == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_gather_filters_by_reply_type():
    """A reply of an unrelated type with the same tracking_id is dropped."""

    class _NoiseResponder(Role):
        """Sends back a string instead of a ``_Reply`` — ``reply_type``
        filter must reject it without raising."""

        @on_message(_Ask)
        async def on_ask(self, content: _Ask, meta: dict) -> None:  # noqa: ARG002
            await self.context.reply_to("not-a-Reply", received_meta=meta)

    container = create_tcp_container(addr=("127.0.0.1", 5560))
    addrs = []
    a = container.register(RoleAgent())
    a.add_role(_Responder(value=1.0))
    addrs.append(a.addr)
    noisy = container.register(RoleAgent())
    noisy.add_role(_NoiseResponder())
    addrs.append(noisy.addr)

    caller = container.register(RoleAgent())
    caller_role = _Caller(addrs, timeout=0.6)
    caller.add_role(caller_role)

    async with activate([container]):
        await caller_role.run()

    assert caller_role.responses is not None
    # Only the well-typed reply survives.
    assert len(caller_role.responses) == 1
    (reply,) = caller_role.responses.values()
    assert isinstance(reply, _Reply)
    assert reply.value == 1.0
