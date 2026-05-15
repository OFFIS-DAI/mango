"""Per-topology link-health tracking.

Multi-agent gossip protocols typically need a notion of "is this
neighbour still alive?" so a sender can route around silent peers.
Without framework support every role re-invents the same machinery —
a per-neighbour decay timer, a multiplicative recovery rule on every
received message, and a filter that drops neighbours whose score is
below some threshold.

This module makes that machinery first-class on a :class:`Topology`
via the :class:`EdgeHealth` config and the :class:`TopologyHealth`
runtime.  When a topology is built with ``edge_health=EdgeHealth(...)``,
every agent in the topology gets an auto-installed receive hook that
multiplicatively recovers the corresponding edge score on every
incoming message.  Roles can then ask the topology for the
``live_neighbours()`` set — neighbours whose current score is at or
above the configured threshold — without touching the bookkeeping.

The decay follows the clock attached to the agent's scheduler, so
behaviour is identical under real-time (:class:`AsyncioClock`) and
simulation (:class:`ExternalClock`).

Example::

    with create_topology(tid="groups", edge_health=EdgeHealth(
        decay_per_s=0.125, recovery_rate=0.6, liveness_threshold=0.5,
    )) as topo:
        ...

    # In a role:
    live = self.context.live_neighbours(tid="groups")          # filtered list
    weights = self.context.neighbour_scores(tid="groups")       # K per neighbour
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mango.agent.core import AgentAddress


@dataclass(frozen=True)
class EdgeHealth:
    """Configuration for continuous link-health tracking on a topology.

    The score for a directed (owner → neighbour) edge starts at
    ``initial`` and:

    * decays linearly with elapsed clock time at ``decay_per_s``;
    * recovers multiplicatively toward 1.0 by ``score += (1-score) *
      recovery_rate`` on every incoming message from the neighbour.

    Defaults match the scare baseline: a poll period of ~8 s on a
    sensitive sector implies one missed beat per second of silence is
    ``≈ 1/8`` of the full score, while every received message rebuilds
    60 % of the missing fraction.

    :param decay_per_s: score decay per second of silence.
    :param recovery_rate: fraction of (1-score) recovered per received
        message.  Must be in ``(0, 1]``.
    :param initial: starting score for a never-contacted neighbour
        (default 1.0 — optimistic bootstrap).
    :param liveness_threshold: default cutoff for ``live_neighbours``.
        Callers can override per-call.
    """

    decay_per_s: float = 0.125
    recovery_rate: float = 0.6
    initial: float = 1.0
    liveness_threshold: float = 0.5

    def __post_init__(self) -> None:
        if not (0.0 < self.recovery_rate <= 1.0):
            raise ValueError("recovery_rate must be in (0, 1]")
        if self.decay_per_s < 0.0:
            raise ValueError("decay_per_s must be non-negative")


class TopologyHealth:
    """Per-topology runtime state — one instance per :class:`Topology`.

    Stores a score per directed (owner, neighbour) pair keyed by their
    string addresses.  The owner side is included because the same
    agent can be a member of multiple topologies, each with its own
    health view of the same neighbour.

    All clock reads go through the ``clock_fn`` callable supplied at
    construction time — ``lambda: scheduler.clock.time``.  This keeps
    decay rate-aware of simulation time without coupling the runtime
    to a specific clock class.
    """

    def __init__(self, params: EdgeHealth) -> None:
        self.params = params
        # (owner_addr_str, neighbour_addr_str) -> (score, last_t)
        self._state: dict[tuple[str, str], tuple[float, float]] = {}

    def _key(
        self, owner_addr: AgentAddress, neighbour_addr: AgentAddress
    ) -> tuple[str, str]:
        return (str(owner_addr), str(neighbour_addr))

    def nudge(
        self,
        owner_addr: AgentAddress,
        neighbour_addr: AgentAddress,
        now: float,
    ) -> None:
        """Apply the multiplicative recovery on (owner → neighbour).

        Called by the message-receive hook on every inbound message
        from *neighbour_addr*.  The decay is first applied up to
        *now*, then the recovery rule lifts the score.
        """
        key = self._key(owner_addr, neighbour_addr)
        score = self._decayed_score(key, now)
        score = min(1.0, score + (1.0 - score) * self.params.recovery_rate)
        self._state[key] = (score, now)

    def score(
        self,
        owner_addr: AgentAddress,
        neighbour_addr: AgentAddress,
        now: float,
    ) -> float:
        """Return the current decayed score for (owner → neighbour)."""
        key = self._key(owner_addr, neighbour_addr)
        decayed = self._decayed_score(key, now)
        # Memoise the decay snapshot so a stale entry doesn't compound
        # over many reads.
        self._state[key] = (decayed, now)
        return decayed

    def is_live(
        self,
        owner_addr: AgentAddress,
        neighbour_addr: AgentAddress,
        now: float,
        *,
        threshold: float | None = None,
    ) -> bool:
        cutoff = threshold if threshold is not None else self.params.liveness_threshold
        return self.score(owner_addr, neighbour_addr, now) >= cutoff

    def _decayed_score(self, key: tuple[str, str], now: float) -> float:
        score, last_t = self._state.get(key, (self.params.initial, now))
        elapsed = max(0.0, now - last_t)
        return max(0.0, score - elapsed * self.params.decay_per_s)
