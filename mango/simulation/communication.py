"""
Communication simulation for mango's SimulationWorld.

Provides deterministic simulation of message delays and packet loss between agents.
"""

import random
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import networkx as nx


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
    ``(sender_id, receiver_id)`` tuples as keys.  Message loss is drawn
    independently per package from ``loss_percent``; each package is
    evaluated exactly once by the world, so no result cache is kept.

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
        rng: random.Random | None = None,
    ):
        self.loss_percent = loss_percent
        self.default_delay_s = default_delay_s
        self.delay_s_directed_edge_dict: dict[tuple[str | None, str], float] = (
            delay_s_directed_edge_dict or {}
        )
        self._rng = rng or random

    def calculate_communication(
        self,
        current_time: float,
        messages: list[MessagePackage],
    ) -> CommunicationSimulationResult:
        results: list[PackageResult] = []
        for msg in messages:
            key = (msg.sender_id, msg.receiver_id)
            delay_s = self.delay_s_directed_edge_dict.get(key, self.default_delay_s)
            reached = self._rng.random() >= self.loss_percent
            results.append(PackageResult(reached=reached, delay_s=delay_s))

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

    def calculate_communication(
        self,
        current_time: float,
        messages: list[MessagePackage],
    ) -> CommunicationSimulationResult:
        results: list[PackageResult] = []
        for msg in messages:
            key = (msg.sender_id, msg.receiver_id)
            if key in self.delay_s_directed_edge_dict:
                delay_s = self.delay_s_directed_edge_dict[key]()
            else:
                delay_s = self.default_delay_s_provider()
            delay_s = max(0.0, delay_s)
            results.append(PackageResult(reached=True, delay_s=delay_s))

        return CommunicationSimulationResult(package_results=results)


def create_distribution_based_com_sim(
    aid_graph: "nx.Graph",
    default_delay_per_edge_ms: float = 10.0,
    base_delay_per_message_ms: float = 0.0,
    max_edge_delay_ms: float | None = None,
    distribution_provider: Callable[[float], Callable[[], float]] | None = None,
) -> DelayProviderCommunicationSimulation:
    """Create a topology-aware communication simulation.

    Delays are derived from shortest-path distances in *aid_graph*.  Each
    node in the graph must be an agent AID (string).  The delay for a
    ``(sender, receiver)`` pair is::

        mean_delay_ms = base_delay_per_message_ms
                        + shortest_path_length * default_delay_per_edge_ms

    capped at *max_edge_delay_ms* when provided.  A *distribution_provider*
    maps a mean delay (in ms) to a zero-argument callable that samples the
    actual delay (in seconds).  The default provider returns the mean value
    deterministically.

    :param aid_graph: a ``networkx.Graph`` (or DiGraph) whose nodes are
        agent AID strings
    :param default_delay_per_edge_ms: additional delay per hop (ms)
    :param base_delay_per_message_ms: fixed base delay for every message (ms)
    :param max_edge_delay_ms: optional cap on the total mean delay (ms)
    :param distribution_provider: ``(mean_ms: float) -> () -> float``
        factory; each call returns a provider that samples a delay in **seconds**.
        Defaults to a constant provider (no randomness).
    :return: a configured :class:`DelayProviderCommunicationSimulation`

    Example::

        import networkx as nx
        import random

        g = nx.path_graph(["a0", "a1", "a2"])
        # Gaussian delays, mean grows with graph distance
        sim = create_distribution_based_com_sim(
            g,
            default_delay_per_edge_ms=20.0,
            distribution_provider=lambda mean_ms: (
                lambda: max(0.0, random.gauss(mean_ms, mean_ms * 0.1)) / 1000.0
            ),
        )
    """
    try:
        import networkx as nx
    except ImportError as exc:
        raise ImportError(
            "networkx is required for create_distribution_based_com_sim"
        ) from exc

    if distribution_provider is None:

        def distribution_provider(mean_ms: float) -> Callable[[], float]:  # type: ignore[misc]
            delay_s = mean_ms / 1000.0

            def _constant() -> float:
                return delay_s

            return _constant

    edge_providers: dict[tuple[str | None, str], Callable[[], float]] = {}

    for source, lengths in nx.all_pairs_shortest_path_length(aid_graph):
        for target, hops in lengths.items():
            if source == target:
                continue
            mean_ms = base_delay_per_message_ms + hops * default_delay_per_edge_ms
            if max_edge_delay_ms is not None:
                mean_ms = min(mean_ms, max_edge_delay_ms)
            edge_providers[(str(source), str(target))] = distribution_provider(mean_ms)

    return DelayProviderCommunicationSimulation(
        delay_s_directed_edge_dict=edge_providers,
    )
