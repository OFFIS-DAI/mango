"""Resolved declarations of a run (groups, entities, links, recorders) and
the compilation of link declarations into topology graphs.

``__all__`` is the public part; ``compile_link``, ``links_by_name`` and
``link_pairs`` are package-internal (builder, runtime, script export).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mango.agent.core import Agent
from mango.util.seeding import SeedTree, name_key

from .errors import ScenarioError
from .patterns import is_deterministic, pattern_name
from .timecfg import _tolerance

if TYPE_CHECKING:
    import networkx

    from .context import BuildContext

__all__ = ["GroupRef", "Entity", "LinkSpec", "RecordSpec"]


# --------------------------------------------------------------------------- data


@dataclass(frozen=True)
class GroupRef:
    """Reference to a declared group, returned by :meth:`Scenario.group`.

    Use it wherever a group is expected (links, records, ``ctx.entities``);
    a plain qualified name such as ``"grid.hh"`` works as well.

    :param name: the qualified group name (``"ev"``, or ``"grid.hh"`` inside a kit)

    Example::

        ev = scenario.group("ev", "ev.n", make_ev)
        str(ev)        # "ev"
    """

    name: str

    def __str__(self) -> str:
        return self.name


@dataclass
class Entity:
    """One instantiated member of a group.

    :param name: the entity name, which is also the agent's aid (``"ev_0"``)
    :param group: the qualified group name
    :param index: position within the group (``0 .. count-1``)
    :param agent: the registered agent (``None`` in a :class:`ScenarioPlan`)
    :param attrs: the install attributes (the dict returned by ``install``);
        a custom link pattern may add keys (e.g. a household id, see
        :mod:`mango.scenario.patterns`)
    :param alive: ``False`` once the entity was retired
        (:meth:`~mango.scenario.BuildContext.retire`)

    Example::

        for entity in ctx.entities("ev"):
            print(entity.name, entity.attrs.get("bus"), entity.agent.aid)
    """

    name: str
    group: str
    index: int
    agent: Agent | None = None
    attrs: dict = field(default_factory=dict)
    alive: bool = True


@dataclass(frozen=True)
class LinkSpec:
    """One resolved link declaration (several with the same name form one topology).

    :param name: the link name, used as topology id (``agent.neighbors(tid=name)``)
    :param source: qualified source group
    :param target: qualified target group (equal to *source* within one group)
    :param pattern: the pattern callable (see :mod:`mango.scenario.patterns`)
    :param where: optional pair filter ``(src_entity, dst_entity) -> bool``
    :param directed: only the source end sees the target end
    :param comm: extra delay and loss of the messages along the link
        (:class:`~mango.scenario.LinkComm`), or ``None``

    Example::

        plan = scenario.plan()
        [(link.name, link.edges) for link in plan.links]
    """

    name: str
    source: str
    target: str
    pattern: Callable
    where: Callable | None = None
    directed: bool = False
    comm: Any = None

    @property
    def pattern_name(self) -> str:
        """Display name of the pattern, e.g. ``"many_to_one"``."""
        return pattern_name(self.pattern)

    @property
    def deterministic(self) -> bool:
        """True when the edges depend only on group sizes (no draws, attributes or filter)."""
        return self.where is None and is_deterministic(self.pattern)


@dataclass(frozen=True)
class RecordSpec:
    """One resolved recorder.

    :param name: the variable name in the results
    :param kind: ``"agent"`` (one value per entity) or ``"world"``
    :param group: qualified group (agent recorders) or ``None``
    :param getter: ``fn(agent)`` for agent recorders, ``fn()``/``fn(ctx)`` for
        world recorders
    :param every: record only at offsets that are multiples of this many
        seconds (``None``: every step)
    :param entity: owner name of world rows (``"world"`` by default)
    :param ctx: the declaring context (passed to world recorders taking ``ctx``)
    :param key: the world's recording key (``name``, or ``"<entity>/<name>"``
        when several entities record a world variable of that name)
    :param aggregate: reduction across the group at each sample (``"count"``,
        ``"sum"``, ``"mean"``, ``"min"``, ``"max"`` or ``fn(values)``), or
        ``None`` for one row per entity (see :mod:`mango.scenario.reducers`)
    :param window: reduction of the samples in ``(t - every, t]`` (``"last"``,
        ``"sum"``, ``"mean"``, ``"min"``, ``"max"``), or ``None`` (snapshots)
    :param value_kind: what a value is (``record(..., kind=)``): ``"flow"``
        (describes the interval that ended at the row), ``"state"`` (holds
        from the row on) or ``None`` (not declared)
    :param categories: the declared category labels of ``aggregate="count"``
        (counted 0 when absent), or ``None``

    Example::

        [(rec.name, rec.kind, rec.group) for rec in scenario.plan().recorders]
    """

    name: str
    kind: str
    group: str | None
    getter: Callable
    every: float | None = None
    entity: str | None = None
    ctx: BuildContext | None = dataclasses.field(
        default=None, repr=False, compare=False
    )
    key: str | None = None
    aggregate: Any = None
    window: str | None = None
    value_kind: str | None = None
    categories: tuple[str, ...] | None = None

    @property
    def holds(self) -> bool:
        """True for a state recorder whose window weights each sample by the time after it."""
        return self.value_kind == "state" and self.window in ("mean", "min", "max")

    @property
    def row_kind(self) -> str | None:
        """How the written rows integrate: ``"state"``, ``"flow"`` or ``None`` (undeclared).

        Window statistics of a state (``"mean"``, ``"min"``, ``"max"``)
        describe the window before their row, so they integrate as flows.
        """
        return "flow" if self.holds else self.value_kind

    @property
    def recording_key(self) -> str:
        """The key of the world's recording (``key``, default ``name``)."""
        return self.key or self.name

    def due(self, t: float) -> bool:
        """True when a sample at offset *t* seconds is recorded (honours ``every``).

        Example::

            spec.due(3600.0)
        """
        if self.every is None:
            return True
        multiple = round(t / self.every)
        return abs(t - multiple * self.every) <= _tolerance(t)


# --------------------------------------------------------------------------- links


def link_pairs(spec: LinkSpec, src: list[Entity], dst: list[Entity], rng) -> list:
    try:
        pairs = spec.pattern(src, dst, rng)
    except ValueError as exc:
        raise ScenarioError(
            f"Link {spec.name!r} from {spec.source!r} ({len(src)} entities) to "
            f"{spec.target!r} ({len(dst)} entities), pattern {spec.pattern_name!r}: "
            f"{exc}."
        ) from exc
    result = []
    for pair in pairs:
        if (
            not isinstance(pair, tuple | list)
            or len(pair) != 2
            or not all(isinstance(item, Entity) for item in pair)
        ):
            raise ScenarioError(
                f"Link {spec.name!r}: pattern {spec.pattern_name!r} must return "
                f"(source_entity, target_entity) pairs, got {pair!r}."
            )
        a, b = pair
        if a.name == b.name:
            continue
        if spec.where is not None and not spec.where(a, b):
            continue
        result.append((a, b))
    return result


def compile_link(
    specs: list[LinkSpec], by_group: Mapping[str, list[Entity]], seeds: SeedTree
) -> tuple[networkx.Graph, list[Entity]]:
    """Return the link graph (int nodes ``0..n-1``) and its participants in node order."""
    import networkx as nx

    name = specs[0].name
    graph = nx.DiGraph() if specs[0].directed else nx.Graph()
    participants: list[Entity] = []
    node_of: dict[str, int] = {}
    rng = seeds.rng("link", name_key(name))
    for spec in specs:
        src = by_group[spec.source]
        dst = src if spec.target == spec.source else by_group[spec.target]
        for entity in (*src, *dst):
            if entity.name not in node_of:
                node_of[entity.name] = len(participants)
                participants.append(entity)
                graph.add_node(node_of[entity.name])
        for a, b in link_pairs(spec, src, dst, rng):
            graph.add_edge(node_of[a.name], node_of[b.name])
    return graph, participants


def links_by_name(links: list[LinkSpec]) -> dict[str, list[LinkSpec]]:
    grouped: dict[str, list[LinkSpec]] = {}
    for link in links:
        grouped.setdefault(link.name, []).append(link)
    return grouped
