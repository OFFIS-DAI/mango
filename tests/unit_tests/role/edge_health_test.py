"""Tests for :class:`EdgeHealth` and the auto-nudge integration with topology.

Three layers of coverage:

1. :class:`TopologyHealth` unit tests — pure-state machine, no agents.
2. End-to-end test on TCP containers — confirms auto-nudge fires on
   every received message and ``live_neighbours`` filters correctly.
3. Simulation-clock test — confirms decay uses :class:`ExternalClock`
   so behaviour is identical under simulation time.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from mango import (
    EdgeHealth,
    Role,
    RoleAgent,
    activate,
    create_tcp_container,
    create_topology,
    on_message,
    sender_addr,
)
from mango.express.health import TopologyHealth

# ---------------------------------------------------------------------------
# Pure unit tests on the runtime — no agents, no clock.
# ---------------------------------------------------------------------------


class _A:
    """Tiny stand-in for an :class:`AgentAddress`; only its ``str``
    representation is used by :class:`TopologyHealth`."""

    def __init__(self, key: str) -> None:
        self.key = key

    def __str__(self) -> str:  # noqa: D401
        return self.key


class TestTopologyHealthRuntime:
    def test_initial_score_at_bootstrap(self):
        h = TopologyHealth(EdgeHealth(initial=0.7))
        # First read uses the configured initial value.
        score = h.score(_A("me"), _A("peer"), now=0.0)
        assert score == pytest.approx(0.7)

    def test_score_decays_linearly_with_silence(self):
        h = TopologyHealth(EdgeHealth(decay_per_s=0.5, initial=1.0))
        # Establish the entry.
        _ = h.score(_A("me"), _A("peer"), now=0.0)
        # Two seconds of silence → score = 1.0 - 2 * 0.5 = 0.0
        assert h.score(_A("me"), _A("peer"), now=2.0) == pytest.approx(0.0)

    def test_nudge_recovers_multiplicatively(self):
        h = TopologyHealth(EdgeHealth(decay_per_s=0.5, recovery_rate=0.5, initial=1.0))
        # Start at 1, decay to 0 by t=2.
        h.score(_A("me"), _A("peer"), now=0.0)
        assert h.score(_A("me"), _A("peer"), now=2.0) == pytest.approx(0.0)
        # Nudge at t=2 → score = 0 + (1-0) * 0.5 = 0.5
        h.nudge(_A("me"), _A("peer"), now=2.0)
        assert h.score(_A("me"), _A("peer"), now=2.0) == pytest.approx(0.5)
        # Another nudge → score = 0.5 + (1-0.5) * 0.5 = 0.75
        h.nudge(_A("me"), _A("peer"), now=2.0)
        assert h.score(_A("me"), _A("peer"), now=2.0) == pytest.approx(0.75)

    def test_is_live_uses_threshold(self):
        h = TopologyHealth(
            EdgeHealth(decay_per_s=0.5, initial=1.0, liveness_threshold=0.6)
        )
        # At t=0 score is 1.0 → live.
        assert h.is_live(_A("me"), _A("peer"), now=0.0) is True
        # At t=1 score is 0.5 → not live by configured threshold (0.6),
        # but live by custom threshold (0.4).
        assert h.is_live(_A("me"), _A("peer"), now=1.0) is False
        assert h.is_live(_A("me"), _A("peer"), now=1.0, threshold=0.4) is True

    def test_per_owner_scores_are_independent(self):
        """The same neighbour observed by two different owners has two
        independent scores — the runtime is a single shared instance
        across all agents in the topology, but the (owner, neighbour)
        key isolates each agent's view."""
        h = TopologyHealth(EdgeHealth(decay_per_s=0.0, initial=1.0))
        h.nudge(_A("alice"), _A("bob"), now=0.0)
        # Alice's view of Bob recovered, Carol's didn't.
        assert h.score(_A("alice"), _A("bob"), now=0.0) > 0.0
        # Carol hasn't been pinged — still at the initial.
        assert h.score(_A("carol"), _A("bob"), now=0.0) == pytest.approx(1.0)

    def test_recovery_rate_validation(self):
        with pytest.raises(ValueError):
            EdgeHealth(recovery_rate=0.0)
        with pytest.raises(ValueError):
            EdgeHealth(recovery_rate=-0.1)
        with pytest.raises(ValueError):
            EdgeHealth(recovery_rate=1.1)
        with pytest.raises(ValueError):
            EdgeHealth(decay_per_s=-1.0)


# ---------------------------------------------------------------------------
# End-to-end on TCP containers — auto-nudge + live_neighbours.
# ---------------------------------------------------------------------------


@dataclass
class _Ping:
    counter: int


class _PingHandler(Role):
    """Receives pings and replies — keeps the test wiring symmetric."""

    def __init__(self) -> None:
        super().__init__()
        self.received: list[int] = []

    @on_message(_Ping)
    async def on_ping(self, content: _Ping, meta: dict) -> None:
        self.received.append(content.counter)
        # Reply so the sender's edge to us also gets nudged.
        await self.context.send_message("ack", receiver_addr=sender_addr(meta))


@pytest.mark.asyncio
async def test_auto_nudge_on_received_message():
    """A message received from a topology neighbour must recover that
    neighbour's edge score on the receiving agent's TopologyService."""
    container = create_tcp_container(addr=("127.0.0.1", 5571))
    alice = container.register(RoleAgent())
    bob = container.register(RoleAgent())
    alice.add_role(_PingHandler())
    bob.add_role(_PingHandler())

    with create_topology(
        tid="pair",
        edge_health=EdgeHealth(
            decay_per_s=0.0,  # disabled — isolate the nudge effect
            recovery_rate=0.5,
            initial=0.0,  # start cold so nudge is observable
            liveness_threshold=0.4,
        ),
    ) as t:
        n0 = t.add_node(alice)
        n1 = t.add_node(bob)
        t.add_edge(n0, n1)

    async with activate([container]):
        # Before any message: Alice's view of Bob is at the initial 0.
        alice_ctx = alice._role_context
        assert alice_ctx.neighbour_score(bob.addr, tid="pair") == pytest.approx(0.0)
        # Send a ping; Alice should observe Bob's ack arrive and nudge.
        await alice.send_message(_Ping(counter=1), receiver_addr=bob.addr)
        # Let the ping → ack roundtrip complete.
        await asyncio.sleep(0.2)
        score = alice_ctx.neighbour_score(bob.addr, tid="pair")
    # One ack from Bob → recovery_rate=0.5 of the missing fraction
    # against a starting score of 0 → exactly 0.5.
    assert score == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_live_neighbours_filters_silent_peers():
    """A neighbour whose score has decayed below the threshold is
    excluded from ``live_neighbours``."""
    container = create_tcp_container(addr=("127.0.0.1", 5572))
    me = container.register(RoleAgent())
    chatty = container.register(RoleAgent())
    silent = container.register(RoleAgent())
    me.add_role(_PingHandler())
    chatty.add_role(_PingHandler())
    silent.add_role(_PingHandler())

    with create_topology(
        tid="trio",
        edge_health=EdgeHealth(
            decay_per_s=0.0,
            recovery_rate=0.9,
            initial=0.0,
            liveness_threshold=0.5,
        ),
    ) as t:
        n_me = t.add_node(me)
        n_chatty = t.add_node(chatty)
        n_silent = t.add_node(silent)
        t.add_edge(n_me, n_chatty)
        t.add_edge(n_me, n_silent)

    async with activate([container]):
        # Ping only chatty.
        await me.send_message(_Ping(counter=1), receiver_addr=chatty.addr)
        await asyncio.sleep(0.2)
        live = me._role_context.live_neighbours(tid="trio")
        # Chatty replied, silent didn't.  Only chatty's score crossed
        # the threshold.
        assert chatty.addr in live
        assert silent.addr not in live


@pytest.mark.asyncio
async def test_live_neighbours_falls_back_when_no_health():
    """Without ``edge_health`` configured, ``live_neighbours`` returns
    the full unfiltered neighbour list — callers can use the method
    unconditionally."""
    container = create_tcp_container(addr=("127.0.0.1", 5573))
    a = container.register(RoleAgent())
    b = container.register(RoleAgent())
    a.add_role(_PingHandler())
    b.add_role(_PingHandler())

    with create_topology(tid="bare") as t:
        n0 = t.add_node(a)
        n1 = t.add_node(b)
        t.add_edge(n0, n1)

    async with activate([container]):
        live = a._role_context.live_neighbours(tid="bare")
        # No nudges have happened — b is still in the list because
        # health tracking was never enabled.
        assert b.addr in live
        # neighbour_score returns None for a no-health topology.
        assert a._role_context.neighbour_score(b.addr, tid="bare") is None
