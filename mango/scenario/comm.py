"""Communication settings of a scenario: :class:`CommConfig`, :func:`build_communication`
and :class:`LayeredCommunication` (per-link delay and loss, partitions).

Per-message delay and loss come from three places, applied in this order to
every message an agent sends:

1. the run's communication simulation, built from the effective
   :class:`CommConfig` (``params.comm`` <- phase ``comm`` <- active events)
   or by the scenario's communication factory;
2. ``comm.partition``: messages between entities in different blocks are
   lost;
3. the ``delay``/``loss`` of the link the sender and receiver share
   (``scenario.link(..., delay=..., loss=...)``): the link delay is added,
   the link loss is an independent extra loss, drawn from the link's own
   stream per directed edge.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import (
    BaseModel,
    Field,
    TypeAdapter,
    field_validator,
    model_serializer,
    model_validator,
)

from mango.simulation import (
    CommunicationSimulation,
    CommunicationSimulationResult,
    DelayProviderCommunicationSimulation,
    MessagePackage,
    PackageResult,
    SimpleCommunicationSimulation,
)

from ._util import call_flexible, import_ref
from .distributions import Constant, Dist, Distribution
from .timecfg import parse_duration

__all__ = [
    "CommConfig",
    "build_communication",
    "LayeredCommunication",
    "LinkComm",
]


def _seconds(value: Any) -> Any:
    """Duration strings (``"2h"``) to seconds, also inside distribution shorthands."""
    if isinstance(value, str):
        try:
            return parse_duration(value)
        except ValueError:
            return value
    if isinstance(value, Mapping):
        return {
            key: item if key == "kind" else _seconds(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_seconds(item) for item in value]
    return value


def _check_delay_value(value: Any, what: str) -> Any:
    if isinstance(value, Constant):
        if isinstance(value.value, bool) or not isinstance(value.value, int | float):
            raise ValueError(
                f"{what} must be seconds, a duration or a distribution, got "
                f"{value.value!r}"
            )
        value = float(value.value)
    if isinstance(value, float) and value < 0:
        raise ValueError(f"{what} must be >= 0, got {value}")
    return value


def _delay_provider(delay: Any) -> Callable[[Any], float] | None:
    """``fn(rng) -> seconds`` for a distribution, ``None`` for a constant."""
    if isinstance(delay, Distribution):

        def draw(generator: Any) -> float:
            return max(0.0, float(delay.draw(generator)))

        return draw
    return None


#: CommConfig fields left out of dumps while unset, so that adding them kept
#: the run ids (content hashes of the dumped params) of existing scenarios.
_OMIT_WHEN_NONE = ("partition",)


class CommConfig(BaseModel, extra="forbid"):
    """Communication settings of a scenario (see :func:`build_communication`).

    ``kind`` selects a mode of this one model: switching it in a preset, an
    override or an event keeps the other settings (``comm.kind=custom``
    keeps ``loss_percent``, ``delay_s`` and ``options``, which the factory
    receives in *cfg*; ``ideal`` ignores loss and delay).

    :param kind: ``"simple"`` (loss and delay), ``"ideal"`` (no loss, no
        delay) or ``"custom"`` (``factory`` builds the simulation)
    :param loss_percent: probability in ``[0, 1]`` that a message is lost
    :param delay_s: message delay: seconds, a duration (``"50ms"`` is not a
        unit, use ``0.05``; ``"2h"``), or a distribution drawn per message
        from the ``"comm"`` stream (e.g. ``{normal: [0.04, 0.008]}``,
        ``{uniform: ["1h", "2h"]}``)
    :param factory: import reference ``"module:function"`` of
        ``fn(cfg, rng)`` or ``fn(cfg, rng, ctx) -> CommunicationSimulation``
        (required for ``custom``; *ctx* is the run's
        :class:`~mango.scenario.BuildContext`, giving access to agents,
        entities, the space and named streams)
    :param options: free-form settings for custom factories
    :param partition: drop messages between parts of the scenario:
        ``[[group or entity, ...], [...], ...]`` lists the blocks (entities
        in no block communicate freely), ``{by_attr: name}`` separates
        entities whose install attribute *name* differs; typically set by
        an event (``comm: {partition: [[left], [right]]}``)

    Example::

        CommConfig(loss_percent=0.1, delay_s={"normal": [0.04, 0.008]})
        CommConfig(kind="custom", factory="my_study.comm:make_comm", options={"mbps": 10})
        CommConfig(partition=[["north"], ["south"]])
    """

    kind: Literal["simple", "ideal", "custom"] = "simple"
    loss_percent: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="probability that a message is lost, in [0, 1] (10 % is 0.1)",
    )
    delay_s: float | Dist = 0.0
    factory: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    partition: list[list[str]] | dict[str, str] | None = None

    @model_serializer(mode="wrap")
    def _omit_unset(self, handler: Any) -> Any:
        data = handler(self)
        if isinstance(data, dict):
            for key in _OMIT_WHEN_NONE:
                if data.get(key) is None:
                    data.pop(key, None)
        return data

    def __repr_args__(self) -> Any:
        for key, value in super().__repr_args__():
            if not (key in _OMIT_WHEN_NONE and value is None):
                yield key, value

    @field_validator("delay_s", mode="before")
    @classmethod
    def _delay_units(cls, value: Any) -> Any:
        return _seconds(value)

    @field_validator("delay_s")
    @classmethod
    def _check_delay(cls, value: Any) -> Any:
        return _check_delay_value(value, "comm.delay_s")

    @field_validator("partition")
    @classmethod
    def _check_partition(cls, value: Any) -> Any:
        if isinstance(value, dict):
            if set(value) != {"by_attr"} or not str(value["by_attr"]).strip():
                raise ValueError(
                    "partition as a mapping must be {by_attr: <install attribute>}, "
                    f"got {value!r}"
                )
            return value
        if isinstance(value, list):
            if len(value) < 2:
                raise ValueError(
                    "partition needs at least two blocks, e.g. [[north], [south]]"
                )
            seen: dict[str, int] = {}
            for index, block in enumerate(value):
                if not block:
                    raise ValueError(f"partition block {index} is empty")
                for name in block:
                    if name in seen:
                        raise ValueError(
                            f"{name!r} is in partition blocks {seen[name]} and {index}"
                        )
                    seen[name] = index
        return value

    @model_validator(mode="after")
    def _check_factory(self) -> CommConfig:
        if self.kind == "custom" and not self.factory:
            raise ValueError(
                "comm.kind 'custom' needs comm.factory: an import reference "
                "'module:function' to fn(cfg, rng) or fn(cfg, rng, ctx) -> "
                "CommunicationSimulation."
            )
        return self


def build_communication(
    cfg: CommConfig | Mapping, rng: Any, ctx: Any = None
) -> CommunicationSimulation:
    """Build the communication simulation described by *cfg*.

    - ``ideal``: ``SimpleCommunicationSimulation(0.0, 0.0, rng=rng)``
    - ``simple`` with a constant delay: ``SimpleCommunicationSimulation(
      loss_percent, delay_s, rng=rng)``
    - ``simple`` with a distribution: ``DelayProviderCommunicationSimulation``
      drawing ``max(0, delay_s.draw(rng))`` per message
    - ``custom``: ``import_ref(cfg.factory)(cfg, rng)``, or
      ``(cfg, rng, ctx)`` when the factory takes three arguments

    ``partition`` and link settings are not applied here; a scenario run
    wraps the result in a :class:`LayeredCommunication` for them.

    :param cfg: the configuration (a dict is validated first)
    :param rng: the generator for loss and delay draws, normally
        ``seeds.rng("comm")``
    :param ctx: the run's :class:`~mango.scenario.BuildContext`, passed to
        custom factories that take it (``None`` outside a run)
    :return: the communication simulation

    Example::

        sim = build_communication(CommConfig(loss_percent=0.1), SeedTree(7).rng("comm"))
    """
    if not isinstance(cfg, CommConfig):
        cfg = CommConfig.model_validate(cfg)
    if cfg.kind == "ideal":
        return SimpleCommunicationSimulation(0.0, 0.0, rng=rng)
    if cfg.kind == "custom":
        factory = import_ref(cfg.factory)
        simulation = call_flexible(factory, cfg, rng, ctx)
        if not isinstance(simulation, CommunicationSimulation):
            raise TypeError(
                f"comm.factory {cfg.factory!r} must return a CommunicationSimulation, "
                f"got {type(simulation).__name__}"
            )
        return simulation
    provider = _delay_provider(cfg.delay_s)
    if provider is not None:
        return DelayProviderCommunicationSimulation(
            default_delay_s_provider=provider,
            rng=rng,
            loss_percent=cfg.loss_percent,
        )
    return SimpleCommunicationSimulation(cfg.loss_percent, float(cfg.delay_s), rng=rng)


# --------------------------------------------------------------------------- links


class LinkComm(BaseModel, extra="forbid"):
    """Delay and loss of the messages along one link (``scenario.link(delay=, loss=)``).

    :param delay: extra delay of every message between two entities the link
        connects: seconds, a duration (``"1w"``) or a distribution
        (``{choice: [0, "1w", "2w"]}``: with weekly steps a lead time of
        1, 2 or 3 weeks, as delay ``(L - 1) * step`` gives a lead time of
        ``L`` steps), drawn per message from the directed edge's own
        stream; ``uniform``/``int_uniform`` over durations draw seconds
    :param loss: extra, independent probability in ``[0, 1]`` that such a
        message is lost

    Example::

        LinkComm(delay={"uniform": ["1h", "2h"]}, loss=0.05)
    """

    delay: float | Dist = 0.0
    loss: float = Field(0.0, ge=0.0, le=1.0)

    @field_validator("delay", mode="before")
    @classmethod
    def _delay_units(cls, value: Any) -> Any:
        return _seconds(value)

    @field_validator("delay")
    @classmethod
    def _check_delay(cls, value: Any) -> Any:
        return _check_delay_value(value, "link delay")

    @property
    def active(self) -> bool:
        """True when the link changes anything (a delay or a loss)."""
        return self.loss > 0 or isinstance(self.delay, Distribution) or self.delay > 0


_LINK_COMM = TypeAdapter(LinkComm)


def link_comm(delay: Any, loss: Any) -> LinkComm | None:
    """Validate ``scenario.link(delay=, loss=)``; ``None`` when both are unset."""
    if delay is None and loss is None:
        return None
    data = {}
    if delay is not None:
        data["delay"] = delay
    if loss is not None:
        data["loss"] = loss
    return _LINK_COMM.validate_python(data)


@dataclass
class EdgeRule:
    """The link settings of one directed edge, with its own random stream."""

    link: str
    loss: float
    delay: float
    provider: Callable[[Any], float] | None
    rng: Any


class LayeredCommunication(CommunicationSimulation):
    """A run's communication simulation plus partitions and per-link delay and loss.

    Every message is first evaluated by ``inner`` (the simulation built
    from ``comm`` or by the communication factory).  Then a message between
    two entities in different partition blocks is lost, and a message along
    a link with ``delay``/``loss`` gets the link delay added and is lost
    with the link's probability (draws from the edge's stream, only when the
    message still reached).

    The scenario run creates it only when a partition or link settings
    exist; read your own simulation as ``world.communication_sim.inner``.

    :param inner: the base communication simulation
    :param block_of: entity name -> partition block (``None``: no partition)
    :param edges: ``(sender, receiver)`` -> ``EdgeRule`` (delay and loss of one edge)
    """

    def __init__(
        self,
        inner: CommunicationSimulation,
        *,
        block_of: Mapping[str, Any] | None = None,
        edges: Mapping[tuple[str, str], EdgeRule] | None = None,
    ):
        self.inner = inner
        self.block_of = dict(block_of or {})
        self.edges = edges if edges is not None else {}

    def calculate_communication(
        self, current_time: float, messages: list[MessagePackage]
    ) -> CommunicationSimulationResult:
        base = self.inner.calculate_communication(current_time, messages)
        results: list[PackageResult] = []
        block_of = self.block_of
        for message, result in zip(messages, base.package_results):
            if not result.reached:
                results.append(result)
                continue
            sender, receiver = message.sender_id, message.receiver_id
            if block_of:
                a, b = block_of.get(sender), block_of.get(receiver)
                if a is not None and b is not None and a != b:
                    results.append(PackageResult(reached=False, delay_s=result.delay_s))
                    continue
            rule = self.edges.get((sender, receiver))
            if rule is None:
                results.append(result)
                continue
            if rule.loss > 0 and rule.rng.random() < rule.loss:
                results.append(PackageResult(reached=False, delay_s=result.delay_s))
                continue
            extra = rule.delay if rule.provider is None else rule.provider(rule.rng)
            results.append(
                PackageResult(reached=True, delay_s=result.delay_s + float(extra))
            )
        return CommunicationSimulationResult(package_results=results)


def partition_blocks(
    partition: Sequence[Sequence[str]] | Mapping[str, str] | None,
    entities: Mapping[str, Any],
    groups: Mapping[str, Sequence[str]],
    attrs: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Entity name -> block of ``comm.partition`` (entities in no block are left out).

    :raises ValueError: for names that are neither entities nor groups
    """
    if not partition:
        return {}
    if isinstance(partition, Mapping):
        key = partition["by_attr"]
        blocks: dict[str, Any] = {}
        for name, values in (attrs or {}).items():
            if key in values:
                value = values[key]
                blocks[name] = repr(value) if not isinstance(value, str) else value
        return blocks
    result: dict[str, Any] = {}
    for index, block in enumerate(partition):
        for name in block:
            if name in entities:
                members = [name]
            elif name in groups:
                members = list(groups[name])
            else:
                from ._util import did_you_mean

                hint = did_you_mean(name, [*groups, *entities])
                raise ValueError(
                    f"comm.partition: {name!r} is neither a group nor an entity of "
                    f"the scenario.{hint} Groups: {', '.join(groups) or '(none)'}"
                )
            for member in members:
                result[member] = index
    return result
