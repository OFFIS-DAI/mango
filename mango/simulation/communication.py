"""
Communication simulation for mango's SimulationWorld.

Mirrors the communication simulation capabilities from Mango.jl, allowing
deterministic simulation of message delays and packet loss between agents.
"""

import random
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class MessagePackage:
    """Describes a message between two agents in the simulation."""

    sender_id: str | None
    receiver_id: str
    sent_time: float
    content: Any  # tuple of (content, meta)


@dataclass(frozen=True)
class PackageResult:
    """Result for a single message package."""

    reached: bool
    delay_s: float


@dataclass
class CommunicationSimulationResult:
    """Aggregated result for a set of message packages."""

    package_results: list[PackageResult]


class CommunicationSimulation(ABC):
    """Abstract base class for communication simulations.

    Implement this to define custom message delay and loss behaviour.
    The same ``MessagePackage`` may be passed multiple times within a
    simulation step; implementations must return **identical** results for
    the same package (determinism requirement).
    """

    @abstractmethod
    def calculate_communication(
        self,
        current_time: float,
        messages: list[MessagePackage],
    ) -> CommunicationSimulationResult:
        """Calculate delivery results for the given messages.

        :param current_time: current simulation time in seconds
        :param messages: messages to evaluate
        :return: one :class:`PackageResult` per message, in the same order
        """


class SimpleCommunicationSimulation(CommunicationSimulation):
    """Default communication simulation with configurable loss and delay.

    Per-link delays can be set via ``delay_s_directed_edge_dict`` using
    ``(sender_id, receiver_id)`` tuples as keys.  A message cache ensures
    deterministic results when the same package is evaluated more than once
    within a step.

    Example::

        sim = SimpleCommunicationSimulation(
            default_delay_s=0.1,
            loss_percent=0.05,
            delay_s_directed_edge_dict={("a1", "a2"): 0.5},
        )
    """

    def __init__(
        self,
        loss_percent: float = 0.0,
        default_delay_s: float = 0.0,
        delay_s_directed_edge_dict: dict[tuple[str | None, str], float] | None = None,
    ):
        self.loss_percent = loss_percent
        self.default_delay_s = default_delay_s
        self.delay_s_directed_edge_dict: dict[tuple[str | None, str], float] = (
            delay_s_directed_edge_dict or {}
        )
        # Cache keyed by object id to ensure determinism within a step
        self._message_cache: dict[int, PackageResult] = {}

    def calculate_communication(
        self,
        current_time: float,
        messages: list[MessagePackage],
    ) -> CommunicationSimulationResult:
        results: list[PackageResult] = []
        for msg in messages:
            msg_id = id(msg)
            if msg_id in self._message_cache:
                results.append(self._message_cache[msg_id])
                continue

            key = (msg.sender_id, msg.receiver_id)
            delay_s = self.delay_s_directed_edge_dict.get(key, self.default_delay_s)
            reached = random.random() >= self.loss_percent
            result = PackageResult(reached=reached, delay_s=delay_s)
            self._message_cache[msg_id] = result
            results.append(result)

        return CommunicationSimulationResult(package_results=results)


class DelayProviderCommunicationSimulation(CommunicationSimulation):
    """Communication simulation where delays come from callable providers.

    Use this when delays should be drawn from a distribution.  Each provider
    is a zero-argument callable that returns a delay in seconds.

    Example::

        import random
        sim = DelayProviderCommunicationSimulation(
            default_delay_s_provider=lambda: random.gauss(0.1, 0.01),
        )
    """

    def __init__(
        self,
        default_delay_s_provider: Callable[[], float] = lambda: 0.0,
        delay_s_directed_edge_dict: (
            dict[tuple[str | None, str], Callable[[], float]] | None
        ) = None,
    ):
        self.default_delay_s_provider = default_delay_s_provider
        self.delay_s_directed_edge_dict: dict[
            tuple[str | None, str], Callable[[], float]
        ] = delay_s_directed_edge_dict or {}
        # Cache keyed by object id to ensure determinism within a step
        self._message_cache: dict[int, PackageResult] = {}

    def calculate_communication(
        self,
        current_time: float,
        messages: list[MessagePackage],
    ) -> CommunicationSimulationResult:
        results: list[PackageResult] = []
        for msg in messages:
            msg_id = id(msg)
            if msg_id in self._message_cache:
                results.append(self._message_cache[msg_id])
                continue

            key = (msg.sender_id, msg.receiver_id)
            if key in self.delay_s_directed_edge_dict:
                delay_s = self.delay_s_directed_edge_dict[key]()
            else:
                delay_s = self.default_delay_s_provider()
            delay_s = max(0.0, delay_s)
            result = PackageResult(reached=True, delay_s=delay_s)
            self._message_cache[msg_id] = result
            results.append(result)

        return CommunicationSimulationResult(package_results=results)
