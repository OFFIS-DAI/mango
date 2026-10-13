"""What a run of a scenario would build: :class:`ScenarioPlan` and its parts."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mango.util.seeding import name_key

from ._util import did_you_mean, to_plain
from .events import EventPlan, PhasePlan, plan_lines
from .reducers import describe
from .timecfg import TimeConfig, format_duration

if TYPE_CHECKING:
    from pydantic import BaseModel

    from mango.util.seeding import SeedTree

    from .specs import Entity, LinkSpec

__all__ = [
    "ScenarioPlan",
    "KitPlan",
    "GroupPlan",
    "LinkPlan",
    "RecordPlan",
    "count_edges",
]


# --------------------------------------------------------------------------- plan


@dataclass
class GroupPlan:
    """A planned group: qualified name, resolved count and entity names."""

    name: str
    count: int
    names: list[str]


@dataclass
class LinkPlan:
    """A planned link (all declarations of one name).

    ``edges`` is the exact edge count when the edges follow from the group
    sizes alone, else ``None``: the pattern or a ``where`` filter draws from
    the link's random stream or reads install attributes (``random``,
    ``by_attr``, ...), see :func:`count_edges`.
    """

    name: str
    directed: bool
    declarations: list[dict]
    edges: int | None
    #: ``{"delay": ..., "loss": ...}`` of a link with delay/loss, else ``None``
    comm: dict | None = None


@dataclass
class RecordPlan:
    """A planned recorder: name, kind (``"agent"``/``"world"``), group or owner, ``every``.

    ``aggregate`` (across the group), ``window`` (over each ``every``
    window), ``value_kind`` (``"state"``/``"flow"``, the recorder's
    ``kind=``) and ``categories`` (of ``aggregate="count"``) are ``None``
    unless set (see :mod:`mango.scenario.reducers`).
    """

    name: str
    kind: str
    group: str | None
    entity: str | None
    every: float | None
    aggregate: str | None = None
    window: str | None = None
    value_kind: str | None = None
    categories: list[str] | None = None


@dataclass
class KitPlan:
    """A kit used by the scenario: prefix, class, reference and resolved parameters."""

    prefix: str
    component: str
    ref: str
    params: dict


@dataclass
class ScenarioPlan:
    """What a run of a scenario would build, computed without a world or agents.

    Example::

        plan = scenario.plan(overrides=["ev.n=20"])
        print(plan)                  # compact summary
        plan.total_agents            # 21
        plan.to_dict()               # JSON-friendly
    """

    scenario: str
    params: BaseModel
    time: TimeConfig
    groups: list[GroupPlan]
    links: list[LinkPlan]
    recorders: list[RecordPlan]
    kits: list[KitPlan] = field(default_factory=list)
    #: resource name -> providing scope (kit prefix or ``"scenario"``)
    resources: dict[str, str] = field(default_factory=dict)
    #: environment behaviors, e.g. ``["FeederBehavior (grid)"]``
    behaviors: list[str] = field(default_factory=list)
    #: phases in order, incl. the implicit ``"main"`` phase (offsets as applied)
    phases: list[PhasePlan] = field(default_factory=list)
    #: events in ``at`` order (offsets as applied; snapped to the step grid)
    events: list[EventPlan] = field(default_factory=list)
    #: time points that are not on the step grid (also issued as warnings)
    warnings: list[str] = field(default_factory=list)
    #: how messages are simulated, e.g. ``"simple, loss 0.1, delay 0s"`` or
    #: ``"factory radio (overrides comm.kind; ...)"`` (run manifests name the
    #: factory by reference, see :meth:`Scenario.factory_refs`)
    communication: str = ""
    #: ``"DefaultEnvironment"`` or ``"factory <qualified name>"``
    environment: str = ""
    #: ``stop_when`` conditions (function names, with ``every``)
    stop_when: list[str] = field(default_factory=list)

    @property
    def total_agents(self) -> int:
        """Number of agents over all groups."""
        return sum(group.count for group in self.groups)

    @property
    def profiles(self) -> dict[str, dict]:
        """The profiles of the parameters: name -> plain description.

        Keys: ``source``, ``path`` or ``values`` (the number of inline
        samples), ``column``, ``time_column``, ``resolution`` (seconds),
        ``start`` (ISO text, or ``"time.start"`` when the profile follows
        the scenario start), and ``scale``, ``noise``, ``interpolation``
        and ``wrap`` when they differ from the defaults.

        Example::

            scenario.plan().profiles["demand"]["resolution"]     # 900.0
        """
        return {
            str(name): _profile_text(spec)
            for name, spec in (getattr(self.params, "profiles", None) or {}).items()
        }

    def group(self, name: str) -> GroupPlan:
        """Return the plan of group *name*.

        Example::

            plan.group("ev").count
        """
        for group in self.groups:
            if group.name == name:
                return group
        known = [group.name for group in self.groups]
        raise KeyError(
            f"No group {name!r} in the plan.{did_you_mean(name, known)} Groups: {known}"
        )

    def link(self, name: str) -> LinkPlan:
        """Return the plan of link *name*.

        Example::

            plan.link("dispatch").edges
        """
        for link in self.links:
            if link.name == name:
                return link
        known = [link.name for link in self.links]
        raise KeyError(
            f"No link {name!r} in the plan.{did_you_mean(name, known)} Links: {known}"
        )

    def to_dict(self) -> dict:
        """Return the plan as JSON-friendly data.

        Example::

            json.dumps(plan.to_dict())
        """
        return {
            "scenario": self.scenario,
            "time": self.time.model_dump(mode="json"),
            "agents": self.total_agents,
            "groups": [dataclasses.asdict(group) for group in self.groups],
            "links": [dataclasses.asdict(link) for link in self.links],
            "recorders": [dataclasses.asdict(rec) for rec in self.recorders],
            "kits": [dataclasses.asdict(kit) for kit in self.kits],
            "resources": dict(self.resources),
            "behaviors": list(self.behaviors),
            "phases": to_plain(
                [dataclasses.asdict(phase) for phase in self.phases], default=str
            ),
            "events": to_plain(
                [dataclasses.asdict(event) for event in self.events], default=repr
            ),
            "warnings": list(self.warnings),
            "communication": self.communication,
            "environment": self.environment,
            "stop_when": list(self.stop_when),
            "profiles": self.profiles,
        }

    def __str__(self) -> str:
        def plural(n: int, word: str) -> str:
            return f"{n} {word}{'' if n == 1 else 's'}"

        time = self.time
        if time.is_event_driven:
            timing = f"horizon {format_duration(time.horizon_s)}, event-driven"
        else:
            timing = (
                f"horizon {format_duration(time.horizon_s)}, step "
                f"{format_duration(time.step_s)} ({plural(time.n_steps, 'step')})"
            )
        if time.start is not None:
            timing += f", start {time.start.isoformat()}"
        items = (*self.groups, *self.links, *self.recorders, *self.phases)
        width = max(
            [
                16,
                *(len(item.name) for item in items),
                *(len(event.name) for event in self.events if event.name),
            ]
        )
        lines = [
            f"Scenario {self.scenario!r}: {plural(self.total_agents, 'agent')} in "
            f"{plural(len(self.groups), 'group')}, {plural(len(self.links), 'link')}, "
            f"{plural(len(self.recorders), 'recorder')}",
            f"  time: {timing}",
        ]
        if self.communication and self.communication != _DEFAULT_COMMUNICATION:
            lines.append(f"  communication: {self.communication}")
        if self.environment and self.environment != "DefaultEnvironment":
            lines.append(f"  environment: {self.environment}")
        if self.stop_when:
            lines.append(f"  stop when: {', '.join(self.stop_when)}")
        if self.kits:
            lines.append("  kits:")
            for kit in self.kits:
                lines.append(f"    {kit.prefix:<{width}} {kit.component} ({kit.ref})")
        if self.behaviors:
            lines.append(f"  behaviors: {', '.join(self.behaviors)}")
        if self.resources:
            lines.append(
                "  resources: "
                + ", ".join(
                    f"{name} ({owner})" for name, owner in self.resources.items()
                )
            )
        if self.groups:
            lines.append("  groups:")
            for group in self.groups:
                if not group.names:
                    names = "(empty)"
                elif len(group.names) == 1:
                    names = group.names[0]
                else:
                    names = f"{group.names[0]} .. {group.names[-1]}"
                lines.append(f"    {group.name:<{width}} {group.count:>6}  {names}")
        if self.links:
            lines.append("  links:")
            for link in self.links:
                parts = ", ".join(
                    f"{d['source']} -> {d['target']} ({d['pattern']}"
                    + _where_text(d.get("where"))
                    + ")"
                    for d in link.declarations
                )
                edges = "? edges" if link.edges is None else plural(link.edges, "edge")
                kind = "directed" if link.directed else "undirected"
                comm = ""
                if link.comm:
                    comm = ", " + ", ".join(
                        f"{key} {value}" for key, value in link.comm.items()
                    )
                lines.append(f"    {link.name:<{width}} {parts}; {edges}, {kind}{comm}")
        if self.recorders:
            lines.append("  recorders:")
            for rec in self.recorders:
                if rec.kind == "agent":
                    owner = rec.group
                elif rec.entity == "world":
                    owner = "world"
                else:
                    owner = f"{rec.entity} (world)"
                every = (
                    "" if rec.every is None else f" every {format_duration(rec.every)}"
                )
                reduce = describe(
                    rec.aggregate, rec.window, getattr(rec, "value_kind", None)
                )
                lines.append(f"    {rec.name:<{width}} {owner}{every}{reduce}")
        profiles = self.profiles
        if profiles:
            lines.append("  profiles:")
            for name, info in profiles.items():
                lines.append(f"    {name:<{width}} {_profile_line(info)}")
        lines += plan_lines(self.time, self.phases, self.events, width)
        if self.warnings:
            lines.append("  warnings:")
            lines += [f"    {warning}" for warning in self.warnings]
        return "\n".join(lines)


#: ``ScenarioPlan.communication`` of the default settings (not printed)
_DEFAULT_COMMUNICATION = "simple, loss 0, delay 0s"


def _where_text(where: str | None) -> str:
    if where is None:
        return ""
    if where.startswith("<"):
        return ", filtered by where="
    return f", where={where}"


def _profile_text(spec: Any) -> dict:
    """Plain description of a :class:`~mango.scenario.profiles.ProfileSpec` (see ``ScenarioPlan.profiles``)."""
    info: dict = {"source": spec.source}
    if spec.values is not None:
        info["values"] = len(spec.values)
    if spec.path is not None:
        info["path"] = spec.path
    for key in ("column", "time_column"):
        value = getattr(spec, key)
        if value is not None:
            info[key] = to_plain(value)
    if spec.resolution is not None:
        info["resolution"] = float(spec.resolution)
    info["start"] = spec.start.isoformat() if spec.start is not None else "time.start"
    if spec.scale != 1.0:
        info["scale"] = spec.scale
    if spec.noise:
        info["noise"] = spec.noise
    if spec.interpolation != "step":
        info["interpolation"] = spec.interpolation
    if spec.wrap is not None:
        info["wrap"] = spec.wrap
    return info


def _profile_line(info: Mapping) -> str:
    """``csv data/load.csv [h0], every 15min, from time.start, scale 2``."""
    source = info["source"]
    if "values" in info:
        head = f"{source}, {info['values']} value{'s' * (info['values'] != 1)}"
    else:
        head = f"{source} {info.get('path', '?')}"
    if "column" in info:
        column = info["column"]
        head += f" [{', '.join(column) if isinstance(column, list) else column}]"
    parts = [head]
    if "time_column" in info:
        parts.append(f"times from column {info['time_column']!r}")
    if "resolution" in info:
        parts.append(f"every {format_duration(info['resolution'])}")
    if "time_column" not in info or info["start"] != "time.start":
        parts.append(f"from {info['start']}")
    for key in ("scale", "noise"):
        if key in info:
            parts.append(f"{key} {info[key]:g}")
    if "interpolation" in info:
        parts.append(f"{info['interpolation']} interpolation")
    if "wrap" in info:
        parts.append("wraps" if info["wrap"] else "no wrap")
    return ", ".join(parts)


# --------------------------------------------------------------------------- edges


class _Use:
    """Set when a plan probe touched a random stream or install attributes."""

    used = False


class _WatchedAttrs(dict):
    """Empty install attributes that note every read."""

    def __init__(self, use: _Use):
        super().__init__()
        self._use = use

    def _touch(self) -> None:
        self._use.used = True

    def __getitem__(self, key):
        self._touch()
        return super().__getitem__(key)

    def get(self, key, default=None):
        self._touch()
        return super().get(key, default)

    def __contains__(self, key):
        self._touch()
        return super().__contains__(key)

    def __iter__(self):
        self._touch()
        return super().__iter__()

    def __len__(self):
        self._touch()
        return super().__len__()

    def keys(self):
        self._touch()
        return super().keys()

    def values(self):
        self._touch()
        return super().values()

    def items(self):
        self._touch()
        return super().items()


class _WatchedRng:
    """A random generator proxy that notes every use."""

    def __init__(self, rng: Any, use: _Use):
        self._rng = rng
        self._use = use

    def __getattr__(self, name: str) -> Any:
        self._use.used = True
        return getattr(self._rng, name)


def count_edges(
    specs: Sequence[LinkSpec],
    by_group: Mapping[str, Sequence[Entity]],
    seeds: SeedTree,
) -> int | None:
    """Edge count of one link, when its edges follow from the group sizes alone.

    Evaluates the patterns (and ``where`` filters) of *specs* on
    placeholder entities with empty install attributes and a watched
    random stream.  When a pattern or filter draws from the stream, reads
    install attributes or raises, the count depends on things only a run
    knows and the result is ``None`` (``? edges`` in the plan); else it is
    the exact number of edges every run gets.  This needs no
    ``deterministic`` flag on custom patterns, as long as they draw from the
    ``rng`` they receive (not from a global generator).

    :param specs: the declarations of one link (one name)
    :param by_group: placeholder entities per qualified group
    :param seeds: the scenario's seed tree
    :return: the edge count, or ``None``

    Example::

        count_edges(specs, placeholders, SeedTree(0))   # 3, or None for random(...)
    """
    import networkx as nx

    from .specs import Entity, link_pairs

    use = _Use()
    watched = {
        group: [
            Entity(e.name, e.group, e.index, attrs=_WatchedAttrs(use)) for e in ents
        ]
        for group, ents in by_group.items()
    }
    rng = _WatchedRng(seeds.child("link", name_key(specs[0].name)).generator(), use)
    graph = nx.DiGraph() if specs[0].directed else nx.Graph()
    try:
        for spec in specs:
            src = watched[spec.source]
            dst = src if spec.target == spec.source else watched[spec.target]
            for a, b in link_pairs(spec, src, dst, rng):
                graph.add_edge(a.name, b.name)
    except Exception:
        return None
    return None if use.used else graph.number_of_edges()
