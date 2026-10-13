"""
Communication simulation for mango's SimulationWorld.

Provides deterministic simulation of message delays and packet loss between agents.
"""

import inspect
import random
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import networkx as nx
    import numpy


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
    The world evaluates each ``MessagePackage`` exactly once, when it is
    sent (``sent_time`` is the clock at that moment); for reproducible runs
    draw randomness only from the ``rng`` the world binds or the one passed
    in, in the order the packages arrive.
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
    independently per package from ``loss_percent`` using ``rng``; each
    package is evaluated exactly once by the world, so no result cache is kept.

    A world binds its ``"comm"`` stream to ``rng`` when it is still
    ``None``; a standalone, unbound simulation falls back to the global
    :mod:`random` module.

    :param loss_percent: probability (0..1) that a message is lost
    :param default_delay_s: delay in seconds for links without an entry in
        *delay_s_directed_edge_dict*
    :param delay_s_directed_edge_dict: per-link delays in seconds
    :param rng: random generator for loss draws (any object with a
        ``random()`` method, e.g. ``numpy.random.Generator``); ``None`` lets
        the world bind its ``"comm"`` stream

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
        rng: "numpy.random.Generator | random.Random | None" = None,
    ):
        self.loss_percent = loss_percent
        self.default_delay_s = default_delay_s
        self.delay_s_directed_edge_dict: dict[tuple[str | None, str], float] = (
            delay_s_directed_edge_dict or {}
        )
        self.rng = rng

    @property
    def _rng(self) -> Any:
        """Effective generator (alias of ``rng`` kept for backwards compatibility).

        Falls back to the global :mod:`random` module while ``rng`` is unbound.
        Assigning forwards to ``rng`` (assigning the :mod:`random` module
        itself means "unbound").
        """
        rng = getattr(self, "rng", None)
        return rng if rng is not None else random

    @_rng.setter
    def _rng(self, value: Any) -> None:
        self.rng = None if value is random else value

    def calculate_communication(
        self,
        current_time: float,
        messages: list[MessagePackage],
    ) -> CommunicationSimulationResult:
        rng = self._rng
        results: list[PackageResult] = []
        for msg in messages:
            key = (msg.sender_id, msg.receiver_id)
            delay_s = self.delay_s_directed_edge_dict.get(key, self.default_delay_s)
            reached = rng.random() >= self.loss_percent
            results.append(PackageResult(reached=reached, delay_s=delay_s))

        return CommunicationSimulationResult(package_results=results)


#: A delay provider: ``() -> float`` (legacy) or ``(rng) -> float``, in seconds.
DelayProvider = Callable[[], float] | Callable[[Any], float]


#: Marker of providers that take the rng as their single positional argument.
_RNG_POSITIONAL = ""


def _provider_rng_parameter(provider: Callable) -> str | None:
    """How *provider* takes the rng.

    ``None`` for a legacy zero-argument provider, :data:`_RNG_POSITIONAL` for
    exactly one required positional parameter, or the parameter name when the
    single required parameter is keyword-only (``def p(*, rng)``).
    """
    try:
        signature = inspect.signature(provider)
    except (TypeError, ValueError):
        return None
    required = [
        p
        for p in signature.parameters.values()
        if p.default is p.empty and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
    ]
    if len(required) > 1:
        raise TypeError(
            f"A delay provider must take no argument or one argument (the rng), "
            f"but {provider!r} requires {len(required)}: "
            f"{', '.join(p.name for p in required)}."
        )
    if not required:
        return None
    if required[0].kind is inspect.Parameter.KEYWORD_ONLY:
        return required[0].name
    return _RNG_POSITIONAL


class DelayProviderCommunicationSimulation(CommunicationSimulation):
    """Communication simulation where delays come from callable providers.

    Use this when delays should be drawn from a distribution.  A provider
    returns a delay in seconds and either takes no argument (legacy) or one
    required argument, the generator ``rng``, to draw from (passed by keyword
    when that parameter is keyword-only).
    The arity of each provider is detected once and cached.  With
    ``loss_percent > 0`` each message is lost with that probability, drawn
    from ``rng``.

    A world binds its ``"comm"`` stream to ``rng`` when it is still
    ``None``.  Used standalone and unbound, the simulation creates (on first
    need) a ``numpy.random.Generator`` seeded from the global :mod:`random`
    module.

    :param default_delay_s_provider: provider for links without an entry in
        *delay_s_directed_edge_dict*
    :param delay_s_directed_edge_dict: per-link providers keyed by
        ``(sender_id, receiver_id)``
    :param rng: ``numpy.random.Generator`` passed to one-argument providers
        and used for loss draws; ``None`` lets the world bind its ``"comm"``
        stream
    :param loss_percent: probability (0..1) that a message is lost

    Example::

        sim = DelayProviderCommunicationSimulation(
            default_delay_s_provider=lambda rng: rng.normal(0.1, 0.01),
            loss_percent=0.02,
        )
    """

    def __init__(
        self,
        default_delay_s_provider: DelayProvider = lambda: 0.0,
        delay_s_directed_edge_dict: (
            dict[tuple[str | None, str], DelayProvider] | None
        ) = None,
        rng: "numpy.random.Generator | random.Random | None" = None,
        loss_percent: float = 0.0,
    ):
        self.default_delay_s_provider = default_delay_s_provider
        self.delay_s_directed_edge_dict: dict[tuple[str | None, str], DelayProvider] = (
            delay_s_directed_edge_dict or {}
        )
        self.rng = rng
        self.loss_percent = loss_percent
        self._fallback_rng: Any = None
        # id(provider) -> (provider, rng parameter); the provider is kept so
        # its id cannot be reused while the entry exists.
        self._arity_cache: dict[int, tuple[Callable, str | None]] = {}

    def _effective_rng(self) -> Any:
        rng = getattr(self, "rng", None)
        if rng is not None:
            return rng
        if getattr(self, "_fallback_rng", None) is None:
            import numpy as np

            self._fallback_rng = np.random.default_rng(random.getrandbits(128))
        return self._fallback_rng

    def _rng_parameter(self, provider: Callable) -> str | None:
        cache = self.__dict__.setdefault("_arity_cache", {})
        cached = cache.get(id(provider))
        if cached is None or cached[0] is not provider:
            cached = (provider, _provider_rng_parameter(provider))
            cache[id(provider)] = cached
        return cached[1]

    def _draw_delay(self, provider: Callable) -> float:
        parameter = self._rng_parameter(provider)
        if parameter is None:
            return provider()
        if parameter == _RNG_POSITIONAL:
            return provider(self._effective_rng())
        return provider(**{parameter: self._effective_rng()})

    def calculate_communication(
        self,
        current_time: float,
        messages: list[MessagePackage],
    ) -> CommunicationSimulationResult:
        results: list[PackageResult] = []
        for msg in messages:
            key = (msg.sender_id, msg.receiver_id)
            provider = self.delay_s_directed_edge_dict.get(
                key, self.default_delay_s_provider
            )
            delay_s = max(0.0, float(self._draw_delay(provider)))
            loss_percent = getattr(self, "loss_percent", 0.0)
            reached = (
                loss_percent <= 0 or self._effective_rng().random() >= loss_percent
            )
            results.append(PackageResult(reached=reached, delay_s=delay_s))

        return CommunicationSimulationResult(package_results=results)


def create_distribution_based_com_sim(
    aid_graph: "nx.Graph",
    default_delay_per_edge_ms: float = 10.0,
    base_delay_per_message_ms: float = 0.0,
    max_edge_delay_ms: float | None = None,
    distribution_provider: Callable[[float], DelayProvider] | None = None,
    rng: "numpy.random.Generator | random.Random | None" = None,
    loss_percent: float = 0.0,
) -> DelayProviderCommunicationSimulation:
    """Create a topology-aware communication simulation.

    Delays are derived from shortest-path distances in *aid_graph*.  Each
    node in the graph must be an agent AID (string).  The delay for a
    ``(sender, receiver)`` pair is::

        mean_delay_ms = base_delay_per_message_ms
                        + shortest_path_length * default_delay_per_edge_ms

    capped at *max_edge_delay_ms* when provided.  A *distribution_provider*
    maps a mean delay (in ms) to a provider that samples the actual delay (in
    seconds); the provider takes either no argument or the generator to draw
    from.  The default provider returns the mean value deterministically.

    :param aid_graph: a ``networkx.Graph`` (or DiGraph) whose nodes are
        agent AID strings
    :param default_delay_per_edge_ms: additional delay per hop (ms)
    :param base_delay_per_message_ms: fixed base delay for every message (ms)
    :param max_edge_delay_ms: optional cap on the total mean delay (ms)
    :param distribution_provider: ``(mean_ms: float) -> provider`` factory,
        where a provider is ``(rng) -> float`` or ``() -> float`` and samples
        a delay in **seconds**. Defaults to a constant provider (no randomness).
    :param rng: generator handed to one-argument providers and used for loss;
        ``None`` lets the world bind its ``"comm"`` stream
    :param loss_percent: probability (0..1) that a message is lost
    :return: a configured :class:`DelayProviderCommunicationSimulation`

    Example::

        import networkx as nx

        g = nx.path_graph(["a0", "a1", "a2"])
        # Gaussian delays, mean grows with graph distance
        sim = create_distribution_based_com_sim(
            g,
            default_delay_per_edge_ms=20.0,
            distribution_provider=lambda mean_ms: (
                lambda rng: max(0.0, rng.normal(mean_ms, mean_ms * 0.1)) / 1000.0
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

    edge_providers: dict[tuple[str | None, str], DelayProvider] = {}

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
        rng=rng,
        loss_percent=loss_percent,
    )
