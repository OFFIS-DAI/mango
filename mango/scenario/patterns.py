"""
Link patterns: which entities of two groups a scenario link connects.

A pattern is any callable ``pattern(src, dst, rng) -> list[(Entity, Entity)]``:

- ``src`` and ``dst`` are lists of :class:`~mango.scenario.Entity` (name,
  group, index, agent, attrs) of the source and the target group in index
  order; for a link within one group (no target) ``dst`` *is* ``src`` (the
  same list object);
- ``rng`` is the link's own ``numpy.random.Generator`` (stream
  ``link/<name>``), so draws are reproducible per seed and independent of
  every other link and entity;
- it returns ``(source_entity, target_entity)`` pairs.  The builder drops
  self-loops, applies the link's ``where`` filter and compiles the pairs into
  a mango topology.  With ``directed=True`` only the source of a pair sees the
  target (``agent.neighbors(tid=name)``); undirected links connect both ways.

Built-in patterns are referenced by name in :meth:`Scenario.link
<mango.scenario.Scenario.link>` (``pattern="ring"``); factories take
arguments: :func:`random` (alias ``erdos_renyi``), :func:`by_attr`,
:func:`small_world` (``watts_strogatz``), :func:`scale_free`
(``barabasi_albert``), :func:`grid` (``lattice``), :func:`networkx`,
:func:`edges` and the combinator :func:`reverse`.  In scenario files and
parameters a factory is a one-key mapping (``{small_world: {k: 4, p: 0.1}}``).

Patterns may depend on the run's parameters (resolved per run, like a
group's ``count``): a parameter path (``pattern="net.topology"`` or
``{param: net.topology}``) whose value is a pattern spec, ``$path`` values
inside factory arguments (``{small_world: {k: $net.k, p: $net.rewire}}``) or
a one-argument function ``fn(p) -> pattern spec``.

A custom pattern may also *annotate* the entities it connects: adding keys
to ``entity.attrs`` (e.g. a household id for the cliques it builds) is a
supported contract.  Patterns run once per run, after every agent exists
and in link declaration order, so the attributes are visible to later
links (:func:`by_attr`), ``on_build`` hooks, ``ctx.entities(...)``, event
``emit_to`` selectors (``{by_attr: {household: 3}}``) and ``comm``
partitions.  Only add keys; install attributes and the attributes of
earlier links stay as they are.  (In ``plan()`` patterns run on placeholder
entities, so the annotations are not kept there.)

Example::

    from mango.scenario import Scenario, patterns

    scenario.link("dispatch", ev, coordinator, pattern="many_to_one")
    scenario.link("peers", ev, pattern=patterns.random(per_target=(0, 2)))
    scenario.link("feeder", "grid.hh", ev, pattern=patterns.by_attr("bus"))
    scenario.link("friends", person, pattern={"small_world": {"k": 4, "p": "$net.p"}})
    scenario.link("upstream", stage, pattern=patterns.reverse("line"), directed=True)

    def nearest_pairs(src, dst, rng):           # a custom pattern
        return [(s, d) for s in src for d in dst if abs(s.index - d.index) <= 1]

    def households(src, dst, rng):              # cliques of random size, tagged
        pairs, start, household = [], 0, 0
        while start < len(src):
            size = int(rng.integers(1, 5))
            members = src[start:start + size]
            for member in members:
                member.attrs["household"] = household
            pairs += [(a, b) for i, a in enumerate(members) for b in members[i + 1:]]
            start, household = start + size, household + 1
        return pairs

    scenario.link("household", person, pattern=households)
    scenario.record("household", person,
                    lambda a, ctx: ctx.entity(a.aid).attrs["household"])
"""

from __future__ import annotations

import functools
import inspect
import numbers
import os
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ._util import did_you_mean

if TYPE_CHECKING:
    import numpy

    from .specs import Entity

# ``random`` and ``by_attr`` are left out of ``__all__``: a star import would
# shadow the stdlib ``random`` module.  Use them as ``patterns.random(...)``.
__all__ = [
    "Pattern",
    "one_to_one",
    "many_to_one",
    "one_to_many",
    "all_to_all",
    "complete",
    "ring",
    "line",
    "small_world",
    "scale_free",
    "grid",
    "networkx",
    "edges",
    "reverse",
    "builtin_patterns",
    "pattern_factories",
    "resolve_pattern",
    "depends_on_params",
    "pattern_name",
    "is_deterministic",
    "is_within_group",
]

#: Type of a link pattern: ``(src, dst, rng) -> [(source_entity, target_entity), ...]``.
Pattern = Callable[
    [Sequence["Entity"], Sequence["Entity"], "numpy.random.Generator"],
    list[tuple["Entity", "Entity"]],
]

_REGISTRY: dict[str, Pattern] = {}


def _builtin(name: str, *, within: bool = False, aliases: Sequence[str] = ()):
    def decorate(fn):
        fn.pattern_name = name
        fn.deterministic = True
        fn.within_group = within
        for key in (name, *aliases):
            _REGISTRY[key] = fn
        return fn

    return decorate


def pattern_name(pattern: Any) -> str:
    """Return the display name of *pattern* (as ``plan()`` prints it).

    The ``pattern_name`` attribute when the callable has one (set it on a
    custom pattern for a readable name), else the qualified function name
    without ``<locals>`` (a closure ``pattern`` made by ``make_chain()``
    shows as ``make_chain.pattern``), ``fn(args)`` for a
    ``functools.partial`` and the class name for other callables.

    Example::

        pattern_name(ring)                            # "ring"
        pattern_name(random(per_target=(0, 2)))       # "random(per_target=(0, 2))"
        chain.pattern_name = "chain(i -> i+1)"        # a custom display name
    """
    name = getattr(pattern, "pattern_name", None)
    if isinstance(name, str):
        return name
    if isinstance(pattern, functools.partial):
        args = [repr(arg) for arg in pattern.args]
        args += [f"{key}={value!r}" for key, value in pattern.keywords.items()]
        return f"{pattern_name(pattern.func)}({', '.join(args)})"
    qualname = getattr(pattern, "__qualname__", None)
    if isinstance(qualname, str) and qualname:
        return qualname.replace(".<locals>", "")
    return getattr(pattern, "__name__", None) or type(pattern).__name__


def is_deterministic(pattern: Any) -> bool:
    """True when *pattern* declares that it depends only on group sizes.

    Built-in patterns declare it (``deterministic = True``); for other
    callables the plan of a scenario finds out by evaluating them on
    placeholder entities (see :func:`mango.scenario.plan.count_edges`), so
    custom patterns get exact edge counts in ``plan()`` without the flag.

    Example::

        is_deterministic(ring), is_deterministic(by_attr("bus"))   # True, False
    """
    return getattr(pattern, "deterministic", False) is True


def is_within_group(pattern: Any) -> bool:
    """True when *pattern* connects entities of a single group (``complete``, ``ring``, ``line``).

    Example::

        is_within_group(ring), is_within_group(all_to_all)   # True, False
    """
    return getattr(pattern, "within_group", False) is True


def _sizes(src: Sequence, dst: Sequence) -> str:
    return f"source has {len(src)} entities, target has {len(dst)}"


@_builtin("one_to_one", aliases=("zip",))
def one_to_one(src: Sequence[Entity], dst: Sequence[Entity], rng=None) -> list:
    """Connect the i-th source entity with the i-th target entity (alias ``"zip"``).

    :raises ValueError: if the groups differ in size

    Example::

        scenario.link("meter", household, meter, pattern="one_to_one")
    """
    if len(src) != len(dst):
        raise ValueError(
            f"pattern 'one_to_one' needs groups of equal size ({_sizes(src, dst)})"
        )
    return list(zip(src, dst))


@_builtin("many_to_one")
def many_to_one(src: Sequence[Entity], dst: Sequence[Entity], rng=None) -> list:
    """Connect every source entity with the single target entity.

    :raises ValueError: if the target group does not have exactly one entity

    Example::

        scenario.link("dispatch", ev, coordinator, pattern="many_to_one")
    """
    if len(dst) != 1:
        raise ValueError(
            f"pattern 'many_to_one' needs exactly one target entity ({_sizes(src, dst)})"
        )
    return [(entity, dst[0]) for entity in src]


@_builtin("one_to_many")
def one_to_many(src: Sequence[Entity], dst: Sequence[Entity], rng=None) -> list:
    """Connect the single source entity with every target entity.

    :raises ValueError: if the source group does not have exactly one entity

    Example::

        scenario.link("dispatch", coordinator, ev, pattern="one_to_many")
    """
    if len(src) != 1:
        raise ValueError(
            f"pattern 'one_to_many' needs exactly one source entity ({_sizes(src, dst)})"
        )
    return [(src[0], entity) for entity in dst]


@_builtin("all_to_all")
def all_to_all(src: Sequence[Entity], dst: Sequence[Entity], rng=None) -> list:
    """Connect every source entity with every target entity (complete bipartite).

    Example::

        scenario.link("market", seller, buyer, pattern="all_to_all")
    """
    return [(s, d) for s in src for d in dst]


@_builtin("complete", within=True)
def complete(src: Sequence[Entity], dst: Sequence[Entity] = (), rng=None) -> list:
    """Connect every pair of entities within one group.

    Returns both orders of every pair, so a directed link gets edges both ways
    (``e_i -> e_j`` for all ``i != j``).

    Example::

        scenario.link("gossip", node, pattern="complete")
    """
    return [(a, b) for a in src for b in src if a is not b]


@_builtin("ring", within=True)
def ring(src: Sequence[Entity], dst: Sequence[Entity] = (), rng=None) -> list:
    """Connect each entity with the next one, and the last with the first.

    Pairs run in index order, ``e_i -> e_(i+1)`` and ``e_(n-1) -> e_0``; with
    ``directed=True`` each entity sees only its successor (use
    ``reverse("ring")`` for the predecessor).  Two entities get both
    directions.

    Example::

        scenario.link("token", node, pattern="ring", directed=True)
        # node_0 -> node_1 -> ... -> node_(n-1) -> node_0
    """
    n = len(src)
    if n < 2:
        return []
    if n == 2:
        return [(src[0], src[1]), (src[1], src[0])]
    return [(src[i], src[(i + 1) % n]) for i in range(n)]


@_builtin("line", within=True)
def line(src: Sequence[Entity], dst: Sequence[Entity] = (), rng=None) -> list:
    """Connect each entity with the next one (a path, no wrap-around).

    Pairs run in index order, ``e_i -> e_(i+1)``: with ``directed=True``
    ``bus_0`` sees ``bus_1`` and the last entity sees nobody.  For the
    opposite direction (an upstream chain) use ``reverse("line")``.

    Example::

        scenario.link("feeder", bus, pattern="line")   # bus_0 - bus_1 - ... - bus_n
        scenario.link("orders", stage, pattern="line", directed=True)
        # stage_0 -> stage_1 -> ... -> stage_(n-1)
    """
    return [(src[i], src[i + 1]) for i in range(len(src) - 1)]


def _span(value: Any, what: str) -> tuple[int, int]:
    if isinstance(value, numbers.Integral) and not isinstance(value, bool):
        low = high = int(value)
    elif (
        isinstance(value, Sequence)
        and not isinstance(value, str)
        and len(value) == 2
        and all(
            isinstance(v, numbers.Integral) and not isinstance(v, bool) for v in value
        )
    ):
        low, high = (int(v) for v in value)
    else:
        raise ValueError(
            f"{what} must be an int k or a pair (low, high) of ints, got {value!r}"
        )
    if low < 0 or high < low:
        raise ValueError(f"{what} must satisfy 0 <= low <= high, got {value!r}")
    return low, high


def _check_probability(value: Any, what: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, numbers.Real)
        or not 0 <= value <= 1
    ):
        raise ValueError(f"{what} must be a probability in [0, 1], got {value!r}")
    return float(value)


def _check_int(value: Any, what: str, *, minimum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, numbers.Integral)
        or value < minimum
    ):
        raise ValueError(f"{what} must be an int >= {minimum}, got {value!r}")
    return int(value)


def _is_connected(pairs: list, members: Sequence[Entity]) -> bool:
    """True when *pairs* (taken undirected) connect every entity of *members*."""
    parent = {entity.name: entity.name for entity in members}
    if len(parent) <= 1:
        return True

    def find(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    components = len(parent)
    for a, b in pairs:
        root_a, root_b = find(a.name), find(b.name)
        if root_a != root_b:
            parent[root_a] = root_b
            components -= 1
    return components == 1


def _redraw_until_connected(
    draw: Callable, name: str, max_tries: int, hint: str = ""
) -> Callable:
    def connected_pattern(src, dst, rng) -> list:
        members = list(src) if src is dst else [*src, *dst]
        for _ in range(max_tries):
            pairs = draw(src, dst, rng)
            if _is_connected(pairs, members):
                return pairs
        raise ValueError(
            f"{name} drew no connected graph over {len(members)} entities in "
            f"{max_tries} attempts{hint}; raise the edge density or max_tries"
        )

    return connected_pattern


def random(
    p: float | None = None,
    *,
    per_target: int | tuple[int, int] | None = None,
    per_source: int | tuple[int, int] | None = None,
    connected: bool = False,
    max_tries: int = 100,
) -> Pattern:
    """Return a pattern that connects entities at random (from the link's stream).

    Exactly one of ``p``, ``per_target`` and ``per_source`` must be given:

    - ``p``: every pair is connected with probability ``p`` (an Erdős–Rényi
      graph, also available as ``erdos_renyi(p)``).  Within one group each
      unordered pair is drawn once and points from the lower to the higher
      index (so a directed link gets ``e_i -> e_j`` with ``i < j``); across
      groups every (source, target) pair is drawn.
    - ``per_target=(lo, hi)``: each target gets ``k ~ U{lo..hi}`` distinct
      sources (without replacement, never itself; ``k`` is capped at the
      number of candidates).  An int ``k`` means ``(k, k)``.
    - ``per_source=(lo, hi)``: the same from the source side.

    With ``connected=True`` the whole graph is redrawn (from the same
    stream) until it connects every participating entity, at most
    *max_tries* times; then a ``ValueError`` suggests a higher density.

    The draws come from the link's stream ``seeds.rng("link/<name>")``, so they
    are reproducible per seed and independent of every other link and entity.

    :param p: edge probability in ``[0, 1]``
    :param per_target: sources per target
    :param per_source: targets per source
    :param connected: redraw until the graph is connected
    :param max_tries: number of draws before giving up with ``connected``
    :return: the pattern

    Example::

        scenario.link("peers", ev, pattern=patterns.random(per_target=(0, 2)))
        scenario.link("wifi", node, pattern=patterns.random(0.1, connected=True))
    """
    given = [
        name
        for name, v in (
            ("p", p),
            ("per_target", per_target),
            ("per_source", per_source),
        )
        if v is not None
    ]
    if len(given) != 1:
        raise ValueError(
            "random() needs exactly one of p, per_target or per_source, got "
            f"{', '.join(given) if given else 'none'}"
        )
    if not isinstance(connected, bool):
        raise ValueError(f"random(connected=...) must be a bool, got {connected!r}")
    tries = _check_int(max_tries, "random(max_tries=...)", minimum=1)
    suffix = ", connected=True" if connected else ""
    if p is not None:
        probability = _check_probability(p, "random(p=...)")

        def random_p(src, dst, rng) -> list:
            if src is dst or _same_entities(src, dst):
                candidates = [
                    (src[i], src[j])
                    for i in range(len(src))
                    for j in range(i + 1, len(src))
                ]
            else:
                candidates = [(s, d) for s in src for d in dst]
            if not candidates:
                return []
            draws = rng.random(len(candidates))
            return [pair for pair, u in zip(candidates, draws) if u < probability]

        name = f"random(p={probability!r}{suffix})"
        result = random_p
        if connected:
            result = _redraw_until_connected(
                random_p,
                name,
                tries,
                f" (p={probability:g}; a random graph on n entities is connected "
                "with high probability once p > ln(n)/n)",
            )
        result.pattern_name = name
        return result

    per_target_side = per_target is not None
    low, high = _span(
        per_target if per_target_side else per_source,
        "per_target" if per_target_side else "per_source",
    )

    def random_k(src, dst, rng) -> list:
        anchors, pool = (dst, src) if per_target_side else (src, dst)
        pairs = []
        for anchor in anchors:
            candidates = [entity for entity in pool if entity is not anchor]
            k = min(int(rng.integers(low, high + 1)), len(candidates))
            if k == 0:
                continue
            picks = rng.choice(len(candidates), size=k, replace=False)
            for index in sorted(int(i) for i in picks):
                other = candidates[index]
                pairs.append((other, anchor) if per_target_side else (anchor, other))
        return pairs

    side = "per_target" if per_target_side else "per_source"
    name = f"random({side}=({low}, {high}){suffix})"
    result = random_k
    if connected:
        result = _redraw_until_connected(random_k, name, tries)
    result.pattern_name = name
    return result


def erdos_renyi(p: float, *, connected: bool = False, max_tries: int = 100) -> Pattern:
    """Return an Erdős–Rényi pattern: every pair is connected with probability *p*.

    The same as ``random(p, connected=..., max_tries=...)`` (see
    :func:`random`); the name exists for the network-science vocabulary.

    Example::

        scenario.link("contacts", person, pattern={"erdos_renyi": {"p": 0.05}})
    """
    pattern = random(p, connected=connected, max_tries=max_tries)
    pattern.pattern_name = pattern.pattern_name.replace("random(", "erdos_renyi(", 1)
    return pattern


def _same_entities(src: Sequence, dst: Sequence) -> bool:
    return len(src) == len(dst) and all(a is b for a, b in zip(src, dst))


_MISSING = object()


def _attr_value(entity: Entity, name: str) -> Any:
    value = entity.attrs.get(name, _MISSING) if entity.attrs else _MISSING
    if value is _MISSING:
        value = getattr(entity.agent, name, _MISSING)
    if value is _MISSING:
        raise ValueError(
            f"by_attr({name!r}): entity {entity.name!r} has no install attribute or "
            f"agent attribute {name!r}; return it from the group's install function, "
            f"e.g. install=lambda e: {{{name!r}: ...}}"
        )
    return value


def by_attr(name: str) -> Pattern:
    """Return a pattern that connects entities whose attribute *name* is equal.

    The value is read from the entity's install attributes (``e.attrs``, the
    dict returned by the group's ``install``), falling back to the agent
    attribute of that name.

    :param name: the attribute name, e.g. ``"bus"``
    :return: the pattern

    Example::

        hh = scenario.group("hh", 30, make_hh, install=lambda e: {"bus": e.index % 12})
        ev = scenario.group("ev", 10, make_ev, install=lambda e: {"bus": e.index})
        scenario.link("same_bus", hh, ev, pattern=patterns.by_attr("bus"))
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"by_attr() needs a non-empty attribute name, got {name!r}")

    def same_attr(src, dst, rng) -> list:
        index: dict[Any, list] = {}
        for entity in dst:
            index.setdefault(_hashable(_attr_value(entity, name)), []).append(entity)
        pairs = []
        for entity in src:
            for other in index.get(_hashable(_attr_value(entity, name)), ()):
                pairs.append((entity, other))
        return pairs

    same_attr.pattern_name = f"by_attr({name!r})"
    return same_attr


def _hashable(value: Any) -> Any:
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


# --------------------------------------------------------------------------- graphs


def _seed(rng: Any) -> int:
    """One draw of the link stream as the seed of a networkx generator."""
    return int(rng.integers(2**32))


def _node_positions(graph: Any) -> dict:
    nodes = list(graph.nodes)
    if all(
        isinstance(node, numbers.Integral) and not isinstance(node, bool)
        for node in nodes
    ) and set(nodes) == set(range(len(nodes))):
        return {node: int(node) for node in nodes}
    try:
        ordered = sorted(nodes)
    except TypeError:
        ordered = nodes
    return {node: position for position, node in enumerate(ordered)}


def _graph_pairs(graph: Any, members: Sequence[Entity], what: str) -> list:
    """Map the nodes of *graph* onto *members* (in node order) and return its pairs.

    Undirected edges point from the lower to the higher index.
    """
    if graph.number_of_nodes() != len(members):
        raise ValueError(
            f"{what} made a graph with {graph.number_of_nodes()} nodes for "
            f"{len(members)} entities; the node count must equal the group size"
        )
    position = _node_positions(graph)
    directed = graph.is_directed()
    seen: set[tuple[int, int]] = set()
    for u, v in graph.edges():
        a, b = position[u], position[v]
        if a == b:
            continue
        if not directed and a > b:
            a, b = b, a
        seen.add((a, b))
    return [(members[a], members[b]) for a, b in sorted(seen)]


def _networkx_error(exc: Exception, what: str) -> ValueError:
    return ValueError(f"{what}: {exc}")


def small_world(k: int, p: float = 0.1, *, connected: bool = False) -> Pattern:
    """Return a Watts–Strogatz small-world pattern within one group (alias ``watts_strogatz``).

    Entities sit on a ring in index order, each joined to its *k* nearest
    neighbours (*k*/2 on each side); every edge is then rewired with
    probability *p* (``networkx.watts_strogatz_graph``, seeded from the
    link's stream).  ``p=0`` gives the regular ring lattice.  Edges point
    from the lower to the higher index (relevant with ``directed=True``).

    :param k: even number of ring neighbours (``2 <= k < n``)
    :param p: rewiring probability in ``[0, 1]``
    :param connected: redraw until the graph is connected
        (``networkx.connected_watts_strogatz_graph``, 100 attempts)
    :return: the pattern

    Example::

        scenario.link("friends", person, pattern=patterns.small_world(k=4, p=0.1))
        # scenario file: pattern: {small_world: {k: 4, p: $net.rewire}}
    """
    k = _check_int(k, "small_world(k=...)", minimum=2)
    if k % 2:
        raise ValueError(
            f"small_world(k={k}): k must be even (each entity gets k/2 neighbours on "
            "each side of the ring)"
        )
    probability = _check_probability(p, "small_world(p=...)")
    name = f"small_world(k={k}, p={probability!r}{', connected=True' if connected else ''})"

    def watts_strogatz(src, dst=(), rng=None) -> list:
        n = len(src)
        if n < 2:
            return []
        if k >= n:
            raise ValueError(f"{name} needs k < the number of entities ({n})")
        import networkx as nx

        try:
            if connected:
                graph = nx.connected_watts_strogatz_graph(
                    n, k, probability, tries=100, seed=_seed(rng)
                )
            else:
                graph = nx.watts_strogatz_graph(n, k, probability, seed=_seed(rng))
        except nx.NetworkXError as exc:
            raise _networkx_error(exc, name) from exc
        return _graph_pairs(graph, src, name)

    watts_strogatz.pattern_name = name
    watts_strogatz.within_group = True
    watts_strogatz.deterministic = probability == 0
    return watts_strogatz


def scale_free(m: int) -> Pattern:
    """Return a Barabási–Albert scale-free pattern within one group (alias ``barabasi_albert``).

    Entities join in index order, each attaching to *m* earlier entities with
    probability proportional to their degree
    (``networkx.barabasi_albert_graph``, seeded from the link's stream).
    Edges point from the lower to the higher index.

    :param m: edges of each joining entity (``1 <= m < n``)
    :return: the pattern

    Example::

        scenario.link("follows", user, pattern=patterns.scale_free(m=2))
    """
    m = _check_int(m, "scale_free(m=...)", minimum=1)
    name = f"scale_free(m={m})"

    def barabasi_albert(src, dst=(), rng=None) -> list:
        n = len(src)
        if n < 2:
            return []
        if m >= n:
            raise ValueError(f"{name} needs m < the number of entities ({n})")
        import networkx as nx

        try:
            graph = nx.barabasi_albert_graph(n, m, seed=_seed(rng))
        except nx.NetworkXError as exc:
            raise _networkx_error(exc, name) from exc
        return _graph_pairs(graph, src, name)

    barabasi_albert.pattern_name = name
    barabasi_albert.within_group = True
    return barabasi_albert


def _grid_shape(n: int, rows: int | None, cols: int | None, name: str) -> tuple:
    if rows is not None and cols is not None:
        if rows * cols != n:
            raise ValueError(f"{name} has {rows * cols} cells for {n} entities")
        return rows, cols
    if rows is not None or cols is not None:
        given = rows if rows is not None else cols
        if n % given:
            raise ValueError(f"{name}: {n} entities do not fill whole lines of {given}")
        other = n // given
        return (given, other) if rows is not None else (other, given)
    side = int(round(n**0.5))
    if side * side != n:
        raise ValueError(
            f"{name}: {n} entities do not form a square; give rows= or cols="
        )
    return side, side


def grid(
    rows: int | None = None,
    cols: int | None = None,
    *,
    periodic: bool = False,
    diagonal: bool = False,
) -> Pattern:
    """Return a 2-d lattice pattern within one group (alias ``lattice``).

    Entity ``i`` sits at row ``i // cols``, column ``i % cols`` and is joined
    to its right and lower neighbour (von Neumann neighbourhood), with
    ``diagonal=True`` also to the diagonal ones (Moore neighbourhood).
    ``periodic=True`` wraps both axes (a torus).  Without *rows* and *cols*
    the group must be a square number; one of them is enough otherwise.
    Edges point from the lower to the higher index.  Deterministic.

    :param rows: number of rows
    :param cols: number of columns
    :param periodic: wrap around the edges
    :param diagonal: also connect diagonal neighbours
    :return: the pattern

    Example::

        scenario.link("neighbours", cell, pattern=patterns.grid(cols=10, diagonal=True))
        # scenario file: pattern: {grid: {cols: 10, periodic: true}}
    """
    for value, what in ((rows, "rows"), (cols, "cols")):
        if value is not None:
            _check_int(value, f"grid({what}=...)", minimum=1)
    options = [
        f"{key}={value!r}"
        for key, value in (
            ("rows", rows),
            ("cols", cols),
            ("periodic", periodic or None),
            ("diagonal", diagonal or None),
        )
        if value is not None
    ]
    name = f"grid({', '.join(options)})"
    steps = [(0, 1), (1, 0)] + ([(1, 1), (1, -1)] if diagonal else [])

    def lattice(src, dst=(), rng=None) -> list:
        n = len(src)
        if n == 0:
            return []
        n_rows, n_cols = _grid_shape(n, rows, cols, name)
        seen: set[tuple[int, int]] = set()
        for r in range(n_rows):
            for c in range(n_cols):
                for dr, dc in steps:
                    r2, c2 = r + dr, c + dc
                    if periodic:
                        r2, c2 = r2 % n_rows, c2 % n_cols
                    elif not (0 <= r2 < n_rows and 0 <= c2 < n_cols):
                        continue
                    a, b = r * n_cols + c, r2 * n_cols + c2
                    if a != b:
                        seen.add((min(a, b), max(a, b)))
        return [(src[a], src[b]) for a, b in sorted(seen)]

    lattice.pattern_name = name
    lattice.within_group = True
    lattice.deterministic = True
    return lattice


def _nx_generator(generator: Any) -> tuple[Callable, str]:
    if callable(generator):
        return generator, getattr(generator, "__name__", type(generator).__name__)
    if not isinstance(generator, str) or not generator:
        raise ValueError(
            f"networkx() needs a generator name such as 'watts_strogatz_graph' or a "
            f"callable, got {generator!r}"
        )
    import networkx as nx

    found = getattr(nx, generator, None)
    if not callable(found):
        known = [n for n in dir(nx) if n.endswith("_graph") and not n.startswith("_")]
        raise ValueError(
            f"networkx has no graph generator {generator!r}.{did_you_mean(generator, known)}"
        )
    return found, generator


def networkx(generator: str | Callable, **kwargs: Any) -> Pattern:
    """Return a pattern from any networkx graph generator, within one group.

    The generator is called with ``n`` (the group size, when it has an ``n``
    parameter and *kwargs* do not set it), *kwargs* and ``seed`` (one draw of
    the link's stream, when it takes a seed); node ``i`` (in sorted node
    order) becomes the ``i``-th entity, so the node count must equal the
    group size.  Undirected edges point from the lower to the higher index;
    directed generators keep their direction.

    :param generator: a function name in the ``networkx`` namespace
        (``"random_regular_graph"``) or a callable returning a graph
    :param kwargs: further generator arguments
    :return: the pattern

    Example::

        scenario.link("peers", node, pattern=patterns.networkx("random_regular_graph", d=3))
        # scenario file: pattern: {networkx: {generator: powerlaw_cluster_graph, m: 2, p: 0.3}}
    """
    function, label = _nx_generator(generator)
    try:
        parameters = inspect.signature(function).parameters
    except (TypeError, ValueError):
        parameters = {}
    takes_seed = "seed" in parameters and "seed" not in kwargs
    pass_n = "n" in parameters and "n" not in kwargs
    arguments = ", ".join(f"{key}={value!r}" for key, value in kwargs.items())
    name = f"networkx({label}{', ' if arguments else ''}{arguments})"

    def generated(src, dst=(), rng=None) -> list:
        call = dict(kwargs)
        if pass_n:
            call["n"] = len(src)
        if takes_seed:
            call["seed"] = _seed(rng)
        import networkx as nx

        try:
            graph = function(**call)
        except (nx.NetworkXError, TypeError, ValueError) as exc:
            raise ValueError(f"{name} with {len(src)} entities failed: {exc}") from exc
        return _graph_pairs(graph, src, name)

    generated.pattern_name = name
    generated.within_group = True
    generated.deterministic = not takes_seed
    return generated


def _read_edge_file(path: Any, source: str, target: str) -> list[tuple[str, str]]:
    import csv

    from .profiles import resolve_data_path

    resolved = resolve_data_path(path, what=f"edges({os.fspath(path)!r})")
    with open(resolved, newline="", encoding="utf-8") as handle:
        dialect = "excel-tab" if resolved.suffix.lower() == ".tsv" else "excel"
        reader = csv.DictReader(handle, dialect=dialect)
        columns = reader.fieldnames or []
        missing = [column for column in (source, target) if column not in columns]
        if missing:
            raise ValueError(
                f"edges({os.fspath(path)!r}) has no column {missing[0]!r}; columns: "
                f"{', '.join(columns) or '(none)'} (set source=/target= to name them)"
            )
        return [(row[source], row[target]) for row in reader]


def _edge_end(value: Any, members: Sequence[Entity], by_name: dict, what: str) -> Any:
    if isinstance(value, str):
        found = by_name.get(value.strip())
        if found is not None:
            return found
        if value.strip().isdigit():
            value = int(value.strip())
        else:
            raise ValueError(
                f"{what} {value!r} is not an entity of the group "
                f"({len(members)} entities).{did_you_mean(value, list(by_name))}"
            )
    if isinstance(value, numbers.Integral) and not isinstance(value, bool):
        if 0 <= value < len(members):
            return members[int(value)]
        raise ValueError(
            f"{what} index {value} is outside the group (0..{len(members) - 1})"
        )
    raise ValueError(f"{what} must be an entity name or index, got {value!r}")


def edges(
    file: str | os.PathLike | None = None,
    *,
    pairs: Sequence[Sequence[Any]] | None = None,
    source: str = "source",
    target: str = "target",
) -> Pattern:
    """Return a pattern with explicit edges: an edge-list file or a list of pairs.

    Each edge names a source and a target entity, by entity name
    (``"person_3"``) or 0-based index within its group (for a link between
    two groups the first end is looked up in the source group, the second in
    the target group).  An unknown name fails with a did-you-mean
    suggestion.  Pairs from a parameter (``{edges: {pairs: $net.edges}}``)
    make an empirical network a study factor.  Deterministic.

    :param file: a CSV (``.tsv``: tab separated) with a header; relative
        paths resolve like profile paths
    :param pairs: alternatively the pairs themselves, ``[[a, b], ...]``
    :param source: source column of the file
    :param target: target column of the file
    :return: the pattern

    Example::

        scenario.link("friends", person, pattern=patterns.edges("data/friends.csv"))
        scenario.link("chain", stage, pattern=patterns.edges(pairs=[(0, 1), (1, 2)]))
        # scenario file: pattern: {edges: {file: data/friends.csv, source: ego, target: alter}}
    """
    if (file is None) == (pairs is None):
        raise ValueError("edges() needs exactly one of file and pairs")
    if pairs is not None:
        if isinstance(pairs, str | bytes) or not isinstance(pairs, Sequence):
            raise ValueError(
                f"edges(pairs=...) needs a list of [source, target] pairs, got "
                f"{type(pairs).__name__}"
            )
        listed = []
        for index, pair in enumerate(pairs):
            if isinstance(pair, str | bytes) or not isinstance(pair, Sequence):
                raise ValueError(
                    f"edges(pairs=...) item {index} is not a pair: {pair!r}"
                )
            if len(pair) != 2:
                raise ValueError(
                    f"edges(pairs=...) item {index} has {len(pair)} values, not 2: "
                    f"{list(pair)!r}"
                )
            listed.append((pair[0], pair[1]))
        name = f"edges({len(listed)} pairs)"
    else:
        if not isinstance(file, str | os.PathLike) or not os.fspath(file):
            raise ValueError(f"edges(file=...) needs a path, got {file!r}")
        listed = None
        name = f"edges({os.fspath(file)!r})"

    def explicit(src, dst, rng=None) -> list:
        rows = listed if listed is not None else _read_edge_file(file, source, target)
        src_names = {entity.name: entity for entity in src}
        dst_names = src_names if dst is src else {entity.name: entity for entity in dst}
        result = []
        for index, (a, b) in enumerate(rows):
            where = f"{name} edge {index}"
            result.append(
                (
                    _edge_end(a, src, src_names, f"{where}: source"),
                    _edge_end(b, dst, dst_names, f"{where}: target"),
                )
            )
        return result

    explicit.pattern_name = name
    explicit.deterministic = True
    return explicit


def reverse(pattern: Any) -> Pattern:
    """Return *pattern* with every pair turned around (``a -> b`` becomes ``b -> a``).

    Direction matters with ``directed=True``: ``reverse("line")`` is the
    upstream chain ``e_(i+1) -> e_i``, ``reverse("many_to_one")`` lets the
    single target see every source.  Works with any pattern spec (names,
    factories, custom callables); the data form is ``{reverse: line}``.

    :param pattern: any pattern spec accepted by :func:`resolve_pattern`
    :return: the reversed pattern

    Example::

        scenario.link("upstream", stage, pattern=patterns.reverse("line"), directed=True)
        # scenario file: pattern: {reverse: line}
    """
    return _reversed(resolve_pattern(pattern))


def _reversed(inner: Pattern) -> Pattern:
    def reversed_pattern(src, dst, rng=None) -> list:
        return [(b, a) for a, b in inner(src, dst, rng)]

    reversed_pattern.pattern_name = f"reverse({pattern_name(inner)})"
    reversed_pattern.within_group = is_within_group(inner)
    reversed_pattern.deterministic = is_deterministic(inner)
    return reversed_pattern


_FACTORIES: dict[str, Callable[..., Pattern]] = {
    "random": random,
    "erdos_renyi": erdos_renyi,
    "by_attr": by_attr,
    "small_world": small_world,
    "watts_strogatz": small_world,
    "scale_free": scale_free,
    "barabasi_albert": scale_free,
    "grid": grid,
    "lattice": grid,
    "networkx": networkx,
    "edges": edges,
    "reverse": reverse,
}


def builtin_patterns() -> dict[str, Pattern]:
    """Return the built-in patterns by name (including the alias ``"zip"``).

    Example::

        sorted(builtin_patterns())   # ['all_to_all', 'complete', 'line', ...]
    """
    return dict(_REGISTRY)


def pattern_factories() -> dict[str, Callable[..., Pattern]]:
    """Return the pattern factories by the names scenario files use (aliases included).

    Example::

        sorted(pattern_factories())   # ['barabasi_albert', 'by_attr', 'edges', ...]
    """
    return dict(_FACTORIES)


def _known() -> list[str]:
    return sorted(_REGISTRY) + [f"{name}(...)" for name in sorted(_FACTORIES)]


# --------------------------------------------------------------------------- specs

_MAX_DEPTH = 8


def _is_params_function(fn: Any) -> bool:
    """True for a one-argument function ``fn(p)`` (a pattern takes src, dst, rng)."""
    if isinstance(fn, type) or getattr(fn, "pattern_name", None) is not None:
        return False
    try:
        parameters = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    positional = [
        p
        for p in parameters
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)
    ]
    return len(positional) == 1 and positional[0].kind != positional[0].VAR_POSITIONAL


def _has_reference(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("$") and not value.startswith("$$")
    if isinstance(value, Mapping):
        return any(_has_reference(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_has_reference(item) for item in value)
    return False


def depends_on_params(pattern: Any) -> bool:
    """True when the pattern spec reads the run's parameters (resolved per run).

    That is a parameter path (a string that is no pattern name), ``{param:
    path}``, ``$path`` values in factory arguments or a one-argument function
    ``fn(p)``.

    Example::

        depends_on_params("ring"), depends_on_params("net.topology")   # False, True
        depends_on_params({"small_world": {"k": 4, "p": "$net.p"}})    # True
    """
    if isinstance(pattern, str):
        return not (pattern in _REGISTRY or pattern in _FACTORIES or ":" in pattern)
    if isinstance(pattern, Mapping):
        if len(pattern) != 1:
            return False
        ((name, args),) = pattern.items()
        if name == "param":
            return True
        if name == "reverse":
            return depends_on_params(args) or _has_reference(args)
        return _has_reference(args)
    if callable(pattern):
        return _is_params_function(pattern)
    return False


def parameter_paths(pattern: Any) -> list[str]:
    """The parameter paths a pattern spec reads (``$path`` values, ``{param: ...}``, paths).

    Function specs ``fn(p)`` read arbitrary parameters and are not listed.
    """
    found: list[str] = []

    def visit(value: Any, top: bool) -> None:
        if isinstance(value, str):
            if value.startswith("$") and not value.startswith("$$"):
                found.append(value[1:])
            elif top and depends_on_params(value):
                found.append(value)
        elif isinstance(value, Mapping):
            if len(value) == 1 and top:
                ((name, args),) = value.items()
                if name == "param" and isinstance(args, str):
                    found.append(args)
                    return
                if name == "reverse":
                    visit(args, True)
                    return
                visit(args, False)
                return
            for item in value.values():
                visit(item, False)
        elif isinstance(value, list | tuple):
            for item in value:
                visit(item, False)

    visit(pattern, True)
    return found


def _read_param(params: Any, path: str, what: str) -> Any:
    from .configfiles import get_path

    if params is None:
        raise ValueError(
            f"{what} reads the parameter {path!r}, which is only known per run "
            "(declare the link on the scenario or in a kit hook)"
        )
    try:
        return get_path(params, path)
    except (KeyError, AttributeError, IndexError, TypeError) as exc:
        raise ValueError(f"{what}: {path!r} is not a parameter path ({exc})") from None


def _substitute(value: Any, params: Any, what: str) -> Any:
    if isinstance(value, str):
        if value.startswith("$$"):
            return value[1:]
        if value.startswith("$"):
            return _read_param(params, value[1:], what)
        return value
    if isinstance(value, Mapping):
        return {key: _substitute(item, params, what) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(_substitute(item, params, what) for item in value)
    return value


def _path_hint(args: Any, params: Any) -> str:
    """A hint when a factory argument is a bare parameter path (``$`` forgotten)."""
    if params is None:
        return ""
    from .configfiles import get_path

    values = (
        args.values()
        if isinstance(args, Mapping)
        else (args if isinstance(args, list | tuple) else [args])
    )
    for value in values:
        if isinstance(value, str) and "." in value:
            try:
                get_path(params, value)
            except (KeyError, AttributeError, IndexError, TypeError):
                continue
            return (
                f" ({value!r} is a parameter path; write '${value}' to use its value)"
            )
    return ""


def _call_factory(name: str, factory: Callable, args: Any, params: Any) -> Pattern:
    try:
        if isinstance(args, Mapping):
            converted = {
                key: tuple(value) if isinstance(value, list) else value
                for key, value in args.items()
            }
            result = factory(**converted)
        elif isinstance(args, list | tuple):
            result = factory(*args)
        elif args is None:
            result = factory()
        else:
            result = factory(args)
    except (ValueError, TypeError) as exc:
        signature = ""
        try:
            signature = str(inspect.signature(factory))
        except (TypeError, ValueError):
            pass
        message = str(exc)
        if isinstance(exc, TypeError) and signature:
            message = f"{message}; signature {name}{signature}"
        raise type(exc)(f"{message}{_path_hint(args, params)}") from exc
    if not callable(result):
        raise TypeError(
            f"Pattern factory {name!r} returned {type(result).__name__}, not a pattern "
            "(a callable (src, dst, rng) -> pairs)"
        )
    if getattr(result, "pattern_name", None) is None:
        shown = (
            ", ".join(f"{k}={v!r}" for k, v in args.items())
            if isinstance(args, Mapping)
            else repr(args)
        )
        try:
            result.pattern_name = f"{name.rpartition(':')[2]}({shown})"
        except (AttributeError, TypeError):
            pass
    return result


def _import_pattern(ref: str) -> Any:
    from ._util import import_ref

    try:
        return import_ref(ref)
    except (ImportError, AttributeError, ValueError) as exc:
        raise ValueError(f"Cannot import the pattern {ref!r}: {exc}") from exc


def resolve_pattern(pattern: Any, params: Any = None) -> Pattern:
    """Turn a pattern spec into a pattern callable.

    Accepted specs:

    - a built-in name (``"ring"``) or an import reference ``"module:fn"`` of
      a pattern;
    - a callable ``(src, dst, rng) -> pairs``;
    - a one-key mapping naming a factory with its arguments, the form of
      scenario files: ``{"random": {"per_target": [0, 2]}}``, ``{"random":
      0.1}``, ``{"by_attr": "bus"}``, ``{"small_world": {"k": 4, "p": 0.1}}``,
      ``{"reverse": "line"}`` or ``{"module:factory": {...}}`` (any function
      returning a pattern, called with the arguments);
    - with *params* (per run): a parameter path whose value is a spec
      (``"net.topology"``, ``{"param": "net.topology"}``), ``"$path"`` values
      inside factory arguments (``{"random": {"p": "$net.p"}}``; ``"$$"``
      escapes a literal ``$``) and a one-argument function ``fn(p) -> spec``.

    :param pattern: the spec
    :param params: the run's (scope) parameters for parameter references
    :return: the pattern callable
    :raises ValueError: for unknown names (with a did-you-mean suggestion),
        unknown parameter paths and malformed factory specs

    Example::

        resolve_pattern("zip") is one_to_one                 # True
        resolve_pattern({"random": {"per_target": [0, 2]}})
        resolve_pattern({"small_world": {"k": "$net.k", "p": 0.1}}, params)
    """
    return _resolve(pattern, params, 0)


def _unknown_name(pattern: str, reason: str = "") -> ValueError:
    hint = did_you_mean(pattern, [*_REGISTRY, *_FACTORIES])
    return ValueError(
        f"Unknown link pattern {pattern!r}.{hint} Known patterns: "
        f"{', '.join(_known())}, 'module:fn', a parameter path whose value is a "
        f"pattern, or a callable (src, dst, rng) -> pairs.{reason}"
    )


def _resolve(pattern: Any, params: Any, depth: int) -> Pattern:
    if depth > _MAX_DEPTH:
        raise ValueError(
            "pattern parameters refer to each other more than "
            f"{_MAX_DEPTH} levels deep (a loop?)"
        )
    if isinstance(pattern, str):
        found = _REGISTRY.get(pattern)
        if found is not None:
            return found
        if pattern in _FACTORIES:
            raise ValueError(
                f"Link pattern {pattern!r} needs arguments: use "
                f"patterns.{pattern}(...) or {{{pattern!r}: {{...}}}}."
            )
        if ":" in pattern:
            return _resolve(_import_pattern(pattern), params, depth + 1)
        if params is None:
            raise _unknown_name(pattern)
        from .configfiles import get_path

        try:
            value = get_path(params, pattern)
        except (KeyError, AttributeError, IndexError, TypeError):
            raise _unknown_name(
                pattern, f" {pattern!r} is no parameter path either."
            ) from None
        try:
            return _resolve(value, params, depth + 1)
        except (ValueError, TypeError) as exc:
            raise type(exc)(f"{pattern} = {value!r}: {exc}") from exc
    if isinstance(pattern, Mapping):
        if len(pattern) != 1:
            raise ValueError(
                "A pattern mapping needs exactly one key naming a factory, e.g. "
                f"{{'random': {{'per_target': [0, 2]}}}}; got keys {sorted(pattern)}."
            )
        ((name, args),) = pattern.items()
        if name == "param":
            if not isinstance(args, str) or not args:
                raise ValueError(
                    f"{{param: ...}} needs a parameter path such as 'net.topology', "
                    f"got {args!r}."
                )
            value = _read_param(params, args, "{param: ...}")
            try:
                return _resolve(value, params, depth + 1)
            except (ValueError, TypeError) as exc:
                raise type(exc)(f"{args} = {value!r}: {exc}") from exc
        args = _substitute(args, params, f"pattern {name!r}")
        if name == "reverse":
            return _reversed(_resolve(args, params, depth + 1))
        factory = _FACTORIES.get(name)
        if factory is None and isinstance(name, str) and ":" in name:
            factory = _import_pattern(name)
            if not callable(factory):
                raise TypeError(
                    f"Pattern factory {name!r} is {type(factory).__name__}, not callable."
                )
        if factory is None:
            if name in _REGISTRY and not args:
                return _REGISTRY[name]
            if name in _REGISTRY:
                raise ValueError(
                    f"Pattern {name!r} takes no arguments (got {args!r}); for the "
                    f"opposite direction use {{'reverse': {name!r}}} "
                    f"(patterns.reverse({name!r}))."
                )
            raise ValueError(
                f"Unknown pattern factory {name!r}.{did_you_mean(name, list(_FACTORIES))}"
                f" Known factories: {', '.join(sorted(_FACTORIES))}, or "
                "'module:factory' (a function returning a pattern)."
            )
        return _call_factory(name, factory, args, params)
    if callable(pattern):
        if _is_params_function(pattern):
            if params is None:
                raise ValueError(
                    f"The pattern function {getattr(pattern, '__name__', pattern)!r} "
                    "takes one argument, so it is read as fn(p) -> pattern spec, which "
                    "needs the run's parameters; a pattern takes (src, dst, rng)."
                )
            return _resolve(pattern(params), params, depth + 1)
        return pattern
    raise TypeError(
        f"A link pattern must be a name, a callable (src, dst, rng) -> pairs or a "
        f"one-key mapping, got {type(pattern).__name__}."
    )
