"""What declarations, factories and hooks of a run see: :class:`BuildContext`,
:class:`EntityContext` and the run state they share.

``__all__`` is the public part.  The other names without an underscore
(``RunState``, the ``check_*`` validators, ``resolve_links``, ...) are the
package-internal interface of the builder, the runtime and the kits.
"""

from __future__ import annotations

import dataclasses
import inspect
import numbers
import operator
import re
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from mango.agent.core import Agent
from mango.util.seeding import SeedTree, name_key

from ._util import did_you_mean
from .configfiles import get_path
from .errors import ParamsError, RunWarning, ScenarioError
from .params import check_path
from .patterns import (
    depends_on_params,
    is_within_group,
    parameter_paths,
    pattern_name,
    resolve_pattern,
)
from .reducers import (
    aggregate_text,
    check_aggregate,
    check_categories,
    check_kind,
    check_window,
)
from .specs import (
    Entity,
    GroupRef,
    LinkSpec,
    RecordSpec,
    links_by_name,
)
from .timecfg import TimeConfig, _tolerance, format_duration, parse_duration

if TYPE_CHECKING:
    import numpy

    from mango.simulation.world import SimulationWorld

    from .builder import Scenario
    from .runtime import ScenarioRun

__all__ = ["BuildContext", "EntityContext"]


#: Passes of the declaration stage, in order.
STAGES: tuple[str, ...] = ("build", "wire", "record")

#: Install-attribute keys that clash with ``world.register`` / ``install`` arguments.
RESERVED_ATTRS = frozenset({"agent", "suggested_aid", "agent_id"})

# mango's containers generate aids "agent<N>" and refuse them as suggested aids
_GENERATED_AID_PREFIX = "agent"

_UNSET = object()

_TABLE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


def first_error_message(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        found = errors()
        if found:
            return str(found[0]["msg"]).removeprefix("Value error, ")
    return str(exc)


def check_name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioError(f"{what} must be a non-empty string, got {value!r}.")
    return value


def _check_local_name(value: Any, what: str) -> str:
    check_name(value, what)
    for char in "./":
        if char in value:
            raise ScenarioError(
                f"{what} {value!r} must not contain {char!r} ('.' separates kit "
                "prefixes from names, '/' separates random stream names)."
            )
    return value


def check_callable(value: Any, what: str, *, optional: bool = True) -> None:
    if value is None and optional:
        return
    if not callable(value):
        raise TypeError(f"{what} must be callable, got {type(value).__name__}.")


def _check_group_arg(value: Any, what: str) -> None:
    if isinstance(value, GroupRef | str):
        return
    hint = ""
    if callable(value):
        hint = (
            " (it is a function: scenario.group(name, count) without a factory "
            "returns a decorator; pass the factory as third argument)"
        )
    raise TypeError(
        f"{what} must be a GroupRef or a group name, got {type(value).__name__}{hint}."
    )


def parse_every(every: Any) -> float | None:
    if every is None:
        return None
    seconds = parse_duration(every)
    if seconds <= 0:
        raise ScenarioError(f"every must be a positive duration, got {every!r}.")
    return seconds


def check_every(every: Any) -> Any:
    """Validate ``every=`` when it is declared.

    Returns seconds (or ``None``), or the *deferred* spec when it reads the
    run's parameters: a string that is no duration (a parameter path such as
    ``"market.ticks"``) or a function ``fn(p)``; :meth:`BuildContext._every`
    resolves it per run.
    """
    if every is None:
        return None
    if callable(every) and not isinstance(every, str):
        return every
    try:
        return parse_every(every)
    except ValueError:
        if isinstance(every, str) and every.strip() and not every[0].isdigit():
            return every.strip()
        raise


def every_text(every: Any) -> Any:
    """Process-independent description of an ``every`` spec (fingerprints)."""
    if callable(every) and not isinstance(every, str):
        return getattr(every, "__qualname__", None) or type(every).__qualname__
    return every


def _is_reference(value: Any) -> bool:
    """A ``"$path"`` string, or a mapping/list holding one (``"$$"`` escapes)."""
    if isinstance(value, str):
        return value.startswith("$") and not value.startswith("$$")
    if isinstance(value, dict):
        return any(_is_reference(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_is_reference(item) for item in value)
    return False


def _is_bare_path(value: Any, parse: Callable[[Any], Any]) -> bool:
    """A string *parse* rejects that may name a parameter (``"lead.weeks"``)."""
    if not isinstance(value, str) or not value.strip() or value[0] in "$+-.0123456789":
        return False
    try:
        parse(value)
    except (ValueError, TypeError):
        return True
    return False


def is_param_spec(value: Any, parse: Callable[[Any], Any] = parse_duration) -> bool:
    """True when *value* reads the run's parameters and is resolved per run.

    That is a function ``fn(p)``, a ``"$path"`` string (also inside a
    distribution shorthand such as ``{"uniform": ["$a", 60]}``) or a bare
    parameter path, i.e. a string that *parse* (a duration or time-point
    parser) rejects.

    Example::

        is_param_spec("$lead.weeks"), is_param_spec("lead.weeks")   # True, True
        is_param_spec("1w"), is_param_spec({"uniform": [1, 2]})     # False, False
    """
    if callable(value) and not isinstance(value, str | type | BaseModel):
        return True
    return _is_reference(value) or _is_bare_path(value, parse)


def param_spec_paths(value: Any, parse: Callable[[Any], Any] = parse_duration) -> list:
    """The parameter paths a spec of :func:`is_param_spec` reads (not those of ``fn(p)``)."""
    if _is_bare_path(value, parse):
        return [value.strip()]
    found: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, str):
            if item.startswith("$") and not item.startswith("$$"):
                found.append(item[1:])
        elif isinstance(item, dict):
            for inner in item.values():
                visit(inner)
        elif isinstance(item, list | tuple):
            for inner in item:
                visit(inner)

    visit(value)
    return found


def param_spec_text(value: Any) -> Any:
    """Process-independent description of a parameter spec (fingerprints)."""
    if callable(value) and not isinstance(value, str | type | BaseModel):
        return getattr(value, "__qualname__", None) or type(value).__qualname__
    return value


def resolve_param_spec(
    value: Any,
    params: Any,
    what: str,
    parse: Callable[[Any], Any] = parse_duration,
) -> tuple[Any, str]:
    """Resolve a spec of :func:`is_param_spec` against *params*.

    :return: ``(value, source)``; *source* describes where the value came
        from for error messages (``"'$lead.weeks' (= 3)"``)
    :raises ScenarioError: for unknown parameter paths or failing functions
    """

    def read(path: str) -> Any:
        try:
            return get_path(params, path)
        except (KeyError, AttributeError, IndexError, TypeError):
            reason = ""
            if isinstance(params, BaseModel):
                try:
                    check_path(type(params), path)
                except ParamsError as exc:
                    reason = f" ({exc})"
            raise ScenarioError(
                f"{what}: {path!r} is not a parameter path{reason}."
            ) from None

    def substitute(item: Any) -> Any:
        if isinstance(item, str):
            if item.startswith("$$"):
                return item[1:]
            return read(item[1:]) if item.startswith("$") else item
        if isinstance(item, dict):
            return {key: substitute(inner) for key, inner in item.items()}
        if isinstance(item, list | tuple):
            return type(item)(substitute(inner) for inner in item)
        return item

    if callable(value) and not isinstance(value, str | type | BaseModel):
        name = getattr(value, "__name__", value)
        try:
            result = value(params)
        except Exception as exc:
            add_note = getattr(exc, "add_note", None)
            if callable(add_note):
                add_note(f"while computing {what} with {name!r}(p)")
            raise
        return result, f"function {name!r} (returned {result!r})"
    if _is_bare_path(value, parse):
        result = read(value.strip())
        return result, f"{value!r} (= {result!r})"
    result = substitute(value)
    if isinstance(value, str):
        return result, f"{value!r} (= {result!r})"
    return result, f"{value!r} (= {result!r})"


def check_param_paths(model: Any, paths: list[str], what: str) -> None:
    """Fail early when a structural spec names a parameter path the model lacks."""
    for path in paths:
        try:
            check_path(model, path)
        except ParamsError as exc:
            raise ScenarioError(
                f"{what}: {path!r} is not a parameter path ({exc})."
            ) from None


def _group_args(value: Any, what: str) -> list:
    """One group or a list of groups (``record(name, [a, b], ...)``)."""
    if isinstance(value, list | tuple):
        if not value:
            raise ScenarioError(f"{what} needs at least one group, got an empty list.")
        for item in value:
            _check_group_arg(item, what)
        return list(value)
    _check_group_arg(value, what)
    return [value]


@dataclass
class _GroupSpec:
    name: str
    count: int
    factory: Callable
    install: Callable | None
    position: Callable | None
    names: list[str]
    ctx: BuildContext
    naming: Any = None


@dataclass
class _PendingLink:
    name: str
    source: GroupRef | str
    target: GroupRef | str | None
    pattern: Callable
    where: Callable | None
    directed: bool
    ctx: BuildContext
    comm: Any = None


@dataclass
class _StopCondition:
    fn: Callable
    every: float | None
    ctx: BuildContext

    @property
    def label(self) -> str:
        return getattr(self.fn, "__qualname__", None) or type(self.fn).__qualname__


@dataclass
class _PendingRecord:
    name: str
    kind: str
    group: GroupRef | str | None
    getter: Callable
    every: float | None
    entity: str | None
    ctx: BuildContext
    aggregate: Any = None
    window: str | None = None
    value_kind: str | None = None
    categories: tuple[str, ...] | None = None


@dataclass
class _MetricSpec:
    """A metric computed when the result is collected (``scenario.metric``)."""

    name: str
    fn: Callable
    ctx: BuildContext


class RunState:
    """Everything one run (or one plan) knows; shared by all its contexts."""

    def __init__(self, scenario: Scenario, params: BaseModel, seeds: SeedTree):
        self.scenario = scenario
        self.params = params
        self.time: TimeConfig = params.time
        self.comm = params.comm
        self.seeds = seeds
        self.world: SimulationWorld | None = None
        self.run: ScenarioRun | None = None
        self.stage = "idle"
        self.groups: dict[str, _GroupSpec] = {}
        self.entity_groups: dict[str, str] = {}
        self.pending_links: list[_PendingLink] = []
        self.pending_records: list[_PendingRecord] = []
        self.links: list[LinkSpec] = []
        self.records: list[RecordSpec] = []
        self.build_hooks: list[tuple[Callable, BuildContext]] = []
        self.undecorated: dict[str, Any] = {}
        self.entities: dict[str, Entity] = {}
        self.by_group: dict[str, list[Entity]] = {}
        #: shared resources: name -> (object, providing scope)
        self.resources: dict[str, tuple[Any, str]] = {}
        #: environment behaviors in declaration order: (declaring prefix, behavior)
        self.behaviors: list[tuple[str, Any]] = []
        self.space: Any = None
        self.space_owner: str | None = None
        self.composed_behavior: Any = _UNSET
        self.kit_contexts: dict[str, BuildContext] = {}
        self.profiles: dict[tuple, Any] = {}
        #: events declared with ``scenario.event`` (in declaration order)
        self.events: list = []
        #: phases and events resolved to offsets (set after the declaration passes)
        self.schedule: Any = None
        #: ``stop_when`` conditions in declaration order
        self.stop_conditions: list[_StopCondition] = []
        #: metrics computed when the result is collected (``scenario.metric``)
        self.metric_specs: list[_MetricSpec] = []
        #: run warnings (``ctx.warn`` and the run's own), distinct, in order
        self.warnings: list[str] = []
        #: keys of warnings issued once per run (``warn_once``)
        self.warned_keys: set = set()
        #: ``ctx.state`` dicts by declaring prefix
        self.scratch: dict[str, dict] = {}
        #: sample-mode table rows: (entity, table path, size) -> row(s)
        self.table_rows: dict[tuple, Any] = {}
        self.root = BuildContext(self)

    def warn(self, message: str, *, stacklevel: int | None = 2) -> None:
        """Keep *message* as a run warning (once) and issue it as a :class:`RunWarning`.

        ``stacklevel=None`` attributes it to the first caller outside mango.
        """
        if message in self.warnings:
            return
        self.warnings.append(message)
        if stacklevel is None:
            from ._util import warn_user

            warn_user(message, RunWarning)
        else:
            warnings.warn(message, RunWarning, stacklevel=stacklevel + 1)

    def warn_once(self, key: Any, message: str) -> None:
        """Keep and issue *message* the first time *key* warns in this run.

        For checks that word one problem differently each time (e.g. naming
        the entity), so the run reports it once; attributed to user code.
        """
        if key in self.warned_keys:
            return
        self.warned_keys.add(key)
        self.warn(message, stacklevel=None)

    @property
    def declaring(self) -> bool:
        return self.stage in STAGES

    def scope_params(self, prefix: str) -> Any:
        return get_path(self.params, prefix) if prefix else self.params


# --------------------------------------------------------------------------- contexts


class BuildContext:
    """What factories and hooks of a run can see, scoped to a declaring prefix.

    The scenario passes a context as ``ctx`` to the environment and
    communication factories, ``on_build`` hooks and world recorders taking an
    argument; entity contexts expose it as ``e.ctx``.  During the declaration
    stage it also declares groups, links and recorders (the scenario's own
    ``group``/``link``/``record`` methods delegate to it, kits use it
    directly).

    Attributes: ``p`` (params of the declaring scope: the full params at
    scenario level), ``params`` (full params), ``time`` (:class:`TimeConfig`),
    ``comm`` (the effective :class:`~mango.scenario.CommConfig`; custom
    communication factories must read ``ctx.comm``), ``seeds`` (the root
    :class:`~mango.util.seeding.SeedTree`), ``world`` (``None`` during the
    declaration stage), ``prefix`` (``""`` at scenario level) and, while
    the run runs, ``phase`` (the current phase name) and ``recording``
    (whether a sample now is recorded).  :meth:`warn` keeps a warning with
    the run's result.

    Example::

        @scenario.environment
        def environment(ctx):
            return DefaultEnvironment(space=Area2D(ctx.p.area.width, ctx.p.area.height))

        @scenario.on_build
        def tag_leader(ctx):
            ctx.agents("coordinator")[0].is_leader = True
    """

    def __init__(self, state: RunState, prefix: str = ""):
        self._state = state
        self._prefix = prefix

    def __repr__(self) -> str:
        scope = f" prefix={self._prefix!r}" if self._prefix else ""
        return f"<BuildContext{scope} scenario={self._state.scenario.name!r}>"

    # -- read-only views -------------------------------------------------------

    @property
    def prefix(self) -> str:
        """The declaring scope (``""`` at scenario level, the kit prefix inside a kit)."""
        return self._prefix

    @property
    def p(self) -> Any:
        """Params of the declaring scope (kit params inside a kit, full params otherwise)."""
        return self._state.scope_params(self._prefix)

    @property
    def params(self) -> BaseModel:
        """The full, resolved params of the run."""
        return self._state.params

    @property
    def time(self) -> TimeConfig:
        """The run's :class:`~mango.scenario.TimeConfig`."""
        return self._state.time

    @property
    def comm(self) -> Any:
        """The effective communication config (``params.comm`` unless events override it)."""
        return self._state.comm

    @property
    def seeds(self) -> SeedTree:
        """The root :class:`~mango.util.seeding.SeedTree` of the run (shared with the world)."""
        return self._state.seeds

    @property
    def world(self) -> SimulationWorld | None:
        """The run's world, or ``None`` during the declaration stage."""
        return self._state.world

    @property
    def phase(self) -> str:
        """The name of the phase active now (``"main"`` without phases).

        Agents, behaviors, recorders and event actions use it to act per
        phase, e.g. to reset counters when the measured phase begins.

        :raises ScenarioError: outside a running run (while the scenario is
            declared or planned)

        Example::

            class Lane(Agent):
                def on_step(self, env, clock, step_size_s):
                    if self.ctx.phase == "warmup":
                        return
        """
        return self._running_run("ctx.phase").phase

    @property
    def recording(self) -> bool:
        """Whether a sample at the current time is recorded (``False`` in ``record: false`` phases).

        :raises ScenarioError: outside a running run

        Example::

            if ctx.recording:
                ctx.log("trades", price=price)
        """
        return self._running_run("ctx.recording").recording_enabled

    def warn(self, message: str) -> None:
        """Issue a warning about this run and keep it with the result.

        The message is issued as a :class:`~mango.scenario.RunWarning` (a
        ``UserWarning``) and stored once in :attr:`RunResult.warnings
        <mango.scenario.RunResult.warnings>` (repeated messages are kept
        once), so a study keeps it even when the run executes in a worker
        process.  Usable while the scenario is declared (kits checking their
        parameters; the warning then also appears in ``plan()``) and while
        the run runs.

        :param message: the warning text (a non-empty str)

        Example::

            if ctx.p.market.n_sellers > ctx.p.market.n_buyers:
                ctx.warn("more sellers than buyers: prices collapse to 0")
        """
        if not isinstance(message, str) or not message.strip():
            raise ScenarioError(
                f"ctx.warn(message) needs a non-empty str, got {message!r}."
            )
        self._state.warn(message, stacklevel=None)

    def rng(self, *names: str | int) -> numpy.random.Generator:
        """Return a named world-level random stream (prefixed by the scope inside a kit).

        :param names: stream name segments, e.g. ``"weather"``
        :return: the cached ``numpy.random.Generator`` (``seeds.rng(*names)``,
            or ``seeds.rng(prefix, *names)`` inside a kit)

        Example::

            noise = ctx.rng("weather").normal(0.0, 1.0, size=24)
        """
        if not names:
            raise TypeError("ctx.rng() needs a stream name, e.g. ctx.rng('weather').")
        if self._prefix:
            return self._state.seeds.rng(self._prefix, *names)
        return self._state.seeds.rng(*names)

    # -- entity lookups ----------------------------------------------------------

    def _resolve_group(self, group: GroupRef | str, what: str) -> str:
        _check_group_arg(group, what)
        name = group.name if isinstance(group, GroupRef) else group
        groups = self._state.groups
        # a kit's own group wins over a scenario group of the same local name
        local = f"{self._prefix}.{name}" if self._prefix else None
        if local is not None and not isinstance(group, GroupRef) and local in groups:
            return local
        if name in groups:
            return name
        known = list(groups)
        listing = ", ".join(repr(g) for g in known) if known else "none"
        scope = f" of kit {self._prefix!r}" if self._prefix else ""
        qualified = [g for g in known if g.rpartition(".")[2] == name]
        hint = (
            f" Did you mean {qualified[0]!r}?"
            if len(qualified) == 1
            else did_you_mean(name, known)
        )
        raise ScenarioError(
            f"Unknown group {name!r} in {what}{scope}.{hint} Known groups: {listing}."
        )

    def entities(self, group: GroupRef | str) -> list[Entity]:
        """Return the entities of *group* in index order (once the group is built).

        Inside a factory, groups declared earlier are complete and the group
        being built holds the entities created so far.

        :param group: a :class:`GroupRef` or a (qualified) group name
        :return: the entities
        :raises ScenarioError: for unknown groups (listing the known ones) and
            for groups that are not built yet

        Example::

            buses = [entity.attrs["bus"] for entity in ctx.entities("ev")]
        """
        name = self._resolve_group(group, "ctx.entities()")
        built = self._state.by_group.get(name)
        if built is None:
            raise ScenarioError(
                f"Group {name!r} is not built yet: groups are instantiated in "
                "declaration order once the world exists. Use the entities in "
                "factories of later groups, on_build hooks or recorders, or use a "
                "link instead."
            )
        return list(built)

    def agents(self, group: GroupRef | str) -> list[Agent]:
        """Return the agents of *group* in index order (see :meth:`entities`).

        Example::

            coordinator = ctx.agents("coordinator")[0]
        """
        return [entity.agent for entity in self.entities(group)]

    def entity(self, name: str) -> Entity:
        """Return the built entity called *name*.

        :raises ScenarioError: for unknown names (with a suggestion)

        Example::

            ctx.entity("ev_0").agent.soc_kwh
        """
        found = self._state.entities.get(name)
        if found is None:
            known = list(self._state.entities)
            raise ScenarioError(
                f"Unknown entity {name!r}.{did_you_mean(name, known)} "
                f"{len(known)} entities are built."
            )
        return found

    # -- declarations ------------------------------------------------------------

    def _require_declaring(self, call: str) -> None:
        if not self._state.declaring:
            raise ScenarioError(
                f"{call} can only be called while the scenario is declared (on the "
                "Scenario object, or in a kit's build/wire/record hook), not after "
                "the declaration stage."
            )

    def _qualify(self, name: str) -> str:
        return f"{self._prefix}.{name}" if self._prefix else name

    def group(
        self,
        name: str,
        count: int | str | Callable = 1,
        factory: Callable | None = None,
        *,
        install: Callable | None = None,
        position: Callable | None = None,
        naming: str | Callable | None = None,
    ) -> GroupRef | Callable[[Callable], GroupRef]:
        """Declare a group of entities (see :meth:`Scenario.group`).

        Inside a kit the group is qualified by the prefix (``ctx.group("hh",
        ...)`` declares ``"grid.hh"``).  Usable as a decorator when *factory*
        is omitted.

        Example::

            homes = ctx.group("hh", ctx.p.n_households, make_household,
                              install=lambda e: {"bus": e.index % ctx.p.n_buses})
        """
        self._require_declaring("ctx.group()")
        if factory is None:
            check_group_options(name, count, no_factory, install, position, naming)
            pending = self._state.undecorated
            pending[self._qualify(name)] = count

            def decorate(fn: Callable) -> GroupRef:
                pending.pop(self._qualify(name), None)
                return self.group(
                    name, count, fn, install=install, position=position, naming=naming
                )

            return decorate
        check_group_options(name, count, factory, install, position, naming)
        qualified = self._qualify(name)
        state = self._state
        if qualified in state.groups:
            raise ScenarioError(
                f"Group {qualified!r} is declared twice; group names must be unique "
                "(kit groups are qualified by their prefix, e.g. 'grid.hh')."
            )
        resolved = self._resolve_count(qualified, count)
        names = self._entity_names(qualified, resolved, count, naming)
        spec = _GroupSpec(
            name=qualified,
            count=resolved,
            factory=factory,
            install=install,
            position=position,
            names=names,
            ctx=self,
            naming=naming,
        )
        for entity_name in names:
            owner = state.entity_groups.get(entity_name)
            if owner is not None:
                raise ScenarioError(
                    f"Entity name {entity_name!r} of group {qualified!r} is already "
                    f"used by group {owner!r}; entity names must be unique. Pass "
                    "naming=... to one of the groups."
                )
            state.entity_groups[entity_name] = qualified
        state.groups[qualified] = spec
        return GroupRef(qualified)

    def _resolve_count(self, group: str, count: Any) -> int:
        if isinstance(count, str):
            scope = self.p
            try:
                value = get_path(scope, count)
            except (KeyError, AttributeError, IndexError, TypeError):
                reason = ""
                if isinstance(scope, BaseModel):
                    try:
                        check_path(type(scope), count)
                    except ParamsError as exc:
                        reason = f" {exc}"
                raise ScenarioError(
                    f"Group {group!r}: count {count!r} is not a parameter path.{reason}"
                ) from None
            source = f"count {count!r} (= {value!r})"
        elif callable(count):
            value = count(self.p)
            source = f"count function {getattr(count, '__name__', count)!r} (returned {value!r})"
        else:
            value = count
            source = f"count {count!r}"
        if isinstance(value, bool) or not isinstance(value, numbers.Integral):
            raise ScenarioError(f"Group {group!r}: {source} must be an int.")
        if value < 0:
            raise ScenarioError(f"Group {group!r}: {source} must be >= 0.")
        return int(value)

    def _entity_names(
        self, group: str, count: int, raw_count: Any, naming: Any
    ) -> list[str]:
        if naming is None:
            if (
                isinstance(raw_count, numbers.Integral)
                and not isinstance(raw_count, bool)
                and raw_count == 1
            ):
                names = [group]
            else:
                names = [f"{group}_{index}" for index in range(count)]
        elif isinstance(naming, str):
            names = []
            for index in range(count):
                try:
                    names.append(naming.format(group=group, index=index))
                except (KeyError, IndexError, ValueError) as exc:
                    raise ScenarioError(
                        f"Group {group!r}: invalid naming format {naming!r} ({exc}); "
                        "use the fields {group} and {index}, e.g. '{group}_{index:03d}'."
                    ) from None
        else:
            names = []
            for index in range(count):
                e = EntityContext(None, group, index, count, self)
                names.append(naming(e))
        seen: set[str] = set()
        for name in names:
            _check_entity_name(group, name)
            if name in seen:
                raise ScenarioError(
                    f"Group {group!r}: naming produced the name {name!r} twice; entity "
                    "names must be unique (include {index})."
                )
            seen.add(name)
        return names

    def _spawn_name(self, spec: _GroupSpec, index: int) -> str:
        """The name of entity *index* of a group (a spawned entity)."""
        naming, group = spec.naming, spec.name
        if naming is None:
            name = f"{group}_{index}"
        elif isinstance(naming, str):
            name = naming.format(group=group, index=index)
        else:
            name = naming(EntityContext(None, group, index, index + 1, self))
        _check_entity_name(group, name)
        owner = self._state.entity_groups.get(name)
        if owner is not None:
            raise ScenarioError(
                f"ctx.spawn({group!r}): the next entity name {name!r} is already used "
                f"by group {owner!r}; pass naming=... to the group so its names stay "
                "unique."
            )
        return name

    def link(
        self,
        name: str,
        source: GroupRef | str,
        target: GroupRef | str | None = None,
        *,
        pattern: Any = "all_to_all",
        where: Callable | None = None,
        directed: bool = False,
        delay: Any = None,
        loss: Any = None,
    ) -> None:
        """Declare a link (see :meth:`Scenario.link`); names resolve in this scope.

        *delay* and *loss* may read parameters of this scope (``"$path"``,
        a parameter path or ``fn(p)``), like *pattern*.

        Example::

            ctx.link("feeder", "hh", pattern="line")   # inside kit 'grid': grid.hh
            ctx.link("shipping", "retailer", "wholesaler", delay="$lead_s")
        """
        self._require_declaring("ctx.link()")
        resolved = check_link_options(
            name, source, target, pattern, where, directed, params=self.p
        )
        comm = check_link_comm(name, delay, loss, params=self.p)
        self._state.pending_links.append(
            _PendingLink(name, source, target, resolved, where, directed, self, comm)
        )

    def relink(self, name: str, pairs: Any = None) -> int:
        """Replace the edges of link *name* while the run is running.

        The link's topology (``agent.neighbors(tid=name)``) and its
        ``delay``/``loss`` settings follow the new edges at once; use it from
        event actions, ``on_build`` hooks, behaviors or agents that hold the
        context, e.g. to recompute range-based neighbourhoods every step.
        Entities that leave the link keep their node and get no neighbours.
        Without *pairs* the link's declared patterns are applied again to the
        current members of its groups (e.g. after :meth:`spawn`); draws then
        come from the stream ``link/<name>/relink/<k>`` (``k``: 1 for the
        first recomputation of the link, 2 for the next, ...).

        :param name: the link name (as declared)
        :param pairs: ``(source, target)`` pairs of entity names, entities or
            agents; undirected links connect both ways, directed ones let only
            the source see the target; ``None`` recomputes the declared
            patterns
        :return: the number of edges now in the link
        :raises ScenarioError: outside a running run, for unknown links or
            entities

        Example::

            def reconnect(ctx):   # an event action: a ring becomes a line
                nodes = [e.name for e in ctx.entities("node")]
                ctx.relink("overlay", zip(nodes, nodes[1:]))
        """
        run = self._state.run
        if run is None or self._state.world is None:
            raise ScenarioError(
                "ctx.relink() changes a link of a running run; call it from an event "
                "action, a behavior or an agent while the run steps."
            )
        return run._relink(name, pairs)

    def link_pairs(self, name: str) -> list[tuple[str, str]]:
        """The current edges of link *name* as ``(source, target)`` entity names.

        Undirected edges appear once.  Only available while the run is running.

        Example::

            ctx.link_pairs("overlay")     # [("node_0", "node_1"), ...]
        """
        run = self._state.run
        if run is None or self._state.world is None:
            raise ScenarioError("ctx.link_pairs() needs a running run.")
        return run._link_pairs(name)

    def spawn(self, group: GroupRef | str, *, attrs: dict | None = None) -> Entity:
        """Add a new entity to *group* while the run is running (a birth).

        The entity is built like the group's others: its name continues the
        group's numbering (``prey_3`` after ``prey_0..prey_2``, never reused
        after a :meth:`retire`; the group's ``naming`` applies), the
        group's ``install`` runs first (*attrs* are merged over its result),
        then its factory with an :class:`EntityContext` whose ``rng`` is the
        stream of the new name (so births are reproducible per seed and do not
        shift other streams).  The agent is registered (``on_ready`` runs) and
        positioned with the group's ``position``; it is stepped from the next
        step on and recorded from the next sample on (its rows start there;
        ``result.wide()`` shows NaN before).  Links do not change: add it with
        :meth:`relink` (``ctx.relink(name)`` recomputes the declared pattern).

        :param group: the group (a :class:`GroupRef` or name)
        :param attrs: install attributes added to (and overriding) the
            group's ``install`` result
        :return: the new :class:`Entity`
        :raises ScenarioError: outside a running run or for unknown groups

        Example::

            class Prey(Agent):
                def __init__(self, ctx):
                    super().__init__()
                    self.ctx, self.energy = ctx, 1.0

                def on_step(self, env, clock, step_size_s):
                    if self.energy > 2.0:          # reproduce
                        self.energy -= 1.0
                        self.ctx.spawn("prey", attrs={"energy": 1.0})

            scenario.group("prey", 10, lambda e: Prey(e.ctx))
        """
        run = self._running("ctx.spawn()")
        return run._spawn(self._resolve_group(group, "ctx.spawn()"), attrs)

    def retire(self, entity: Entity | Agent | str) -> None:
        """Remove an entity from the running run (a death).

        It leaves its group at once (``ctx.entities``/``ctx.agents``), every
        link (its edges are dropped) and the recorders (its time series end
        with the last sample before; ``result.wide()`` shows NaN after), and
        is deregistered from the world: it is not stepped in later steps, no
        message reaches it, its tasks are cancelled and ``on_stop`` runs.  The
        name stays taken (:meth:`entity` still finds it, with ``alive ==
        False``).

        :param entity: the entity, its agent or its name
        :raises ScenarioError: outside a running run, for unknown or already
            retired entities

        Example::

            class Fox(Agent):
                def on_step(self, env, clock, step_size_s):
                    prey = self.ctx.agents("prey")
                    if prey:
                        self.ctx.retire(prey[0])
        """
        run = self._running("ctx.retire()")
        run._retire(entity)

    def _running(self, call: str) -> ScenarioRun:
        run = self._state.run
        if run is None or self._state.world is None:
            raise ScenarioError(
                f"{call} changes the population of a running run; call it from an "
                "agent, a behavior, an event action or an on_build hook (not while "
                "the scenario is declared or planned)."
            )
        return run

    def stop_when(self, fn: Callable, *, every: Any = None) -> Callable:
        """Stop the run once ``fn(ctx)`` returns something true (see :meth:`Scenario.stop_when`).

        Example::

            ctx.stop_when(lambda ctx: converged(ctx.agents("node")))
        """
        self._require_declaring("ctx.stop_when()")
        seconds = self._every("stop_when", check_stop_when(fn, every))
        self._state.stop_conditions.append(_StopCondition(fn, seconds, self))
        return fn

    def stop(self, reason: str = "stopped") -> None:
        """Stop the run after the current step (an early end; see :meth:`ScenarioRun.stop`).

        Example::

            @scenario.event("12h")
            def give_up(ctx):
                ctx.stop("no convergence after 12h")
        """
        run = self._state.run
        if run is None or self._state.world is None:
            raise ScenarioError("ctx.stop() needs a running run.")
        run.stop(reason)

    def record(
        self,
        name: str,
        group: GroupRef | str | list[GroupRef | str],
        fn: Callable | None = None,
        *,
        attr: str | None = None,
        every: Any = None,
        aggregate: str | Callable | None = None,
        window: str | None = None,
        kind: str | None = None,
        categories: Sequence[Any] | None = None,
    ) -> None:
        """Declare a per-agent recorder (see :meth:`Scenario.record`).

        *group* may be a list of groups (one variable over all of them);
        *every* may be a parameter path or ``fn(p)`` (read from ``ctx.p``);
        *aggregate* reduces across each group, *window* over the samples
        between two recorded times; *kind* (``"state"`` or ``"flow"``)
        says how the values integrate and *categories* fixes the
        categories of ``aggregate="count"``.

        Example::

            ctx.record("pv_kw", homes, lambda a: a.pv_kw, kind="flow")
            ctx.record("stock", ["retailer", "factory"], attr="stock", kind="state")
            ctx.record("charging", homes, lambda a: a.power_kw > 0, aggregate="sum")
        """
        self._require_declaring("ctx.record()")
        getter, spec = check_record_options(
            name, group, fn, attr, every, aggregate, window
        )
        kind = check_kind(kind, window, f"record({name!r})")
        labels = check_categories(categories, aggregate, f"record({name!r})")
        if fn is not None and recorder_takes_ctx(fn):
            getter = _AgentCtxGetter(fn, self)
        seconds = self._every(f"record({name!r})", spec)
        for item in _group_args(group, f"record({name!r}) group"):
            self._state.pending_records.append(
                _PendingRecord(
                    name,
                    "agent",
                    item,
                    getter,
                    seconds,
                    None,
                    self,
                    aggregate,
                    window,
                    kind,
                    labels,
                )
            )

    def record_world(
        self,
        name: str,
        fn: Callable,
        *,
        every: Any = None,
        entity: str | None = None,
        window: str | None = None,
        kind: str | None = None,
    ) -> None:
        """Declare a world-level recorder (see :meth:`Scenario.record_world`).

        Inside a kit *entity* defaults to the kit prefix; *every* may be a
        parameter path or ``fn(p)`` (read from ``ctx.p``); *window* reduces
        the samples between two recorded times; *kind* (``"state"`` or
        ``"flow"``) says how the values integrate.

        Example::

            ctx.record_world("import_kw", lambda: feeder.import_kw)
            ctx.record_world("peak_kw", lambda: feeder.import_kw, every="1h", window="max")
        """
        self._require_declaring("ctx.record_world()")
        owner = entity if entity is not None else (self._prefix or "world")
        seconds = self._every(
            f"record_world({name!r})",
            check_record_world_options(name, fn, every, owner, window),
        )
        self._state.pending_records.append(
            _PendingRecord(
                name,
                "world",
                None,
                fn,
                seconds,
                owner,
                self,
                None,
                window,
                check_kind(kind, window, f"record_world({name!r})"),
            )
        )

    # -- event logs and metrics ----------------------------------------------------

    def log(self, name: str, /, **fields: Any) -> None:
        """Append one row to the event log *name* of the running run.

        Event logs hold what happens rather than sampled state: one row per
        trade, infection or delivery, any number per step.  Each row gets
        ``time`` (the clock now, seconds since the start), ``phase`` (the
        phase active now) and the given fields; a field missing in some rows
        is empty there.  Rows logged during a phase with ``record: false``
        are dropped.  Inside a kit the name is qualified by the prefix
        (``"market.trades"``).  The tables are saved next to the time series
        (``logs/<name>.parquet``) and read with :meth:`RunResult.log
        <mango.scenario.RunResult.log>` and ``StudyFrame.log(name)``.

        :param name: the log name (letters, digits, ``_``, ``-``, ``.``)
        :param fields: the row's values: numbers, bools, strings or ``None``
            (other values are stored as JSON text); not ``run_id``, ``time``,
            ``datetime`` or ``phase``
        :raises ScenarioError: outside a running run, for invalid names or
            reserved field names

        Example::

            class Market(Agent):
                def __init__(self, ctx):
                    super().__init__()
                    self.ctx = ctx

                def settle(self, buyer, seller, price):
                    self.ctx.log("trades", buyer=buyer, seller=seller, price=price)

            scenario.group("market", 1, lambda e: Market(e.ctx))
            result = scenario.run(seed=1)
            result.log("trades").groupby("buyer")["price"].mean()
        """
        run = self._running_run("ctx.log()")
        run._log(self._qualify(check_table_name(name, "ctx.log()")), fields)

    def set_metric(self, name: str, value: Any) -> None:
        """Set the run-level metric *name* (one value per run; the last call wins).

        Metrics are scalars known inside the run, e.g. the time a protocol
        converged.  They are stored with the run (``metrics.json``), read as
        ``result.metrics[name]`` (also by outcome functions, so they can be
        recomputed from disk) and become columns of
        :func:`~mango.scenario.load_study`.  Inside a kit the name is
        qualified by the prefix.  Metrics computed at the end of the run are
        declared with :meth:`Scenario.metric <mango.scenario.Scenario.metric>`.

        :param name: the metric name
        :param value: a number, bool, string, ``None`` or plain list/dict
        :raises ScenarioError: outside a run, for values without a JSON form
            and for names of declared metrics

        Example::

            class Node(Agent):
                def on_step(self, env, clock, step_size_s):
                    if not self.done and self.spread() < 1e-3:
                        self.done = True
                        self.ctx.set_metric("t_converged", clock.time)

            scenario.run(seed=1).metrics["t_converged"]
        """
        run = self._running_run("ctx.set_metric()")
        check_name(name, "A metric name")
        qualified = self._qualify(name)
        if any(spec.name == qualified for spec in self._state.metric_specs):
            raise ScenarioError(
                f"Metric {qualified!r} is computed by scenario.metric() at the end of "
                "the run; set_metric() cannot set it. Use another name."
            )
        run._set_metric(qualified, value)

    def get_metric(self, name: str, default: Any = None) -> Any:
        """The value :meth:`set_metric` stored for *name* in this run, or *default*.

        Inside a kit the name is qualified by the prefix, as for
        :meth:`set_metric`.  Values are stored in their plain form (a tuple
        reads back as a list).

        :param name: the metric name
        :param default: returned when the metric was not set (yet)
        :raises ScenarioError: outside a run

        Example::

            @scenario.stop_when(every="1s")
            def converged(ctx):
                if ctx.get_metric("t_first") is None and spread(ctx) < 0.1:
                    ctx.set_metric("t_first", ctx.world.clock.time)
                return spread(ctx) < 1e-3
        """
        run = self._running_run("ctx.get_metric()")
        return run._metrics.get(self._qualify(name), default)

    @property
    def state(self) -> dict:
        """A dict for this run's own bookkeeping (empty at the start of every run).

        Use it for values that hooks, event actions, recorders and
        ``stop_when`` conditions keep between calls, e.g. the last time a
        condition held; it is not stored with the result (use
        :meth:`set_metric` or :meth:`log` for that).  Inside a kit the kit
        has its own dict.

        :raises ScenarioError: outside a run

        Example::

            @scenario.stop_when(every="1min")
            def stable(ctx):
                level = ctx.agents("tank")[0].level
                calm = abs(level - ctx.state.get("last", level)) < 0.01
                ctx.state["last"] = level
                ctx.state["calm"] = ctx.state.get("calm", 0) + 1 if calm else 0
                return ctx.state["calm"] >= 10
        """
        self._running_run("ctx.state")
        return self._state.scratch.setdefault(self._prefix, {})

    def metric(self, name: str, fn: Callable) -> Callable:
        """Declare a metric computed when the result is collected (see :meth:`Scenario.metric`).

        Example::

            ctx.metric("final_soc", lambda ctx: mean(a.soc for a in ctx.agents("ev")))
        """
        self._require_declaring("ctx.metric()")
        check_name(name, "A metric name")
        check_callable(fn, f"metric({name!r}) fn", optional=False)
        qualified = self._qualify(name)
        if any(spec.name == qualified for spec in self._state.metric_specs):
            raise ScenarioError(
                f"Metric {qualified!r} is declared twice; metric names must be unique."
            )
        self._state.metric_specs.append(_MetricSpec(qualified, fn, self))
        return fn

    def _running_run(self, call: str) -> ScenarioRun:
        run = self._state.run
        if run is None or self._state.world is None:
            raise ScenarioError(
                f"{call} needs a running run: call it from an agent, a behavior, an "
                "event action, a recorder or an on_build hook (not while the scenario "
                "is declared or planned)."
            )
        return run

    def _every(self, what: str, every: Any) -> float | None:
        """Resolve a (deferred) ``every`` spec against ``ctx.p`` (see :func:`check_every`)."""
        if every is None or isinstance(every, float):
            return every
        if isinstance(every, str):
            scope = self.p
            try:
                value = get_path(scope, every)
            except (KeyError, AttributeError, IndexError, TypeError):
                reason = ""
                if isinstance(scope, BaseModel):
                    try:
                        check_path(type(scope), every)
                    except ParamsError as exc:
                        reason = f" {exc}"
                raise ScenarioError(
                    f"{what}: every {every!r} is neither a duration (e.g. '1h' or "
                    f"900) nor a parameter path.{reason}"
                ) from None
            source = f"every {every!r} (= {value!r})"
        else:
            value = every(self.p)
            name = getattr(every, "__name__", every)
            source = f"every function {name!r} (returned {value!r})"
        try:
            return parse_every(value)
        except (ValueError, ScenarioError) as exc:
            raise ScenarioError(
                f"{what}: {source} is not a positive duration ({exc})"
            ) from None

    def on_build(self, fn: Callable) -> Callable:
        """Register ``fn(ctx)`` to run after agents, links and recorders exist, before start.

        Usable as a decorator; returns *fn*.

        Example::

            @ctx.on_build
            def connect(ctx):
                ...
        """
        self._require_declaring("ctx.on_build()")
        check_callable(fn, "on_build hook", optional=False)
        self._state.build_hooks.append((fn, self))
        return fn

    # -- resources, behaviors, space, profiles -------------------------------------

    def _owner(self) -> str:
        return self._prefix or "scenario"

    def provide(self, name: str, obj: Any, *, scope: str = "kit") -> Any:
        """Share *obj* under *name* with every kit and the scenario (e.g. a feeder model).

        Resource names are scoped like group names: inside kit ``agg`` the
        resource ``"tariff"`` is stored as ``"agg.tariff"``, so a kit used
        twice (``agg_n``, ``agg_s``) provides two resources instead of
        colliding; at scenario level, or with ``scope="global"``, the name is
        kept as given.  :meth:`resource` finds it from everywhere (see there).
        Resources provided in a ``build`` hook are visible to all
        ``wire``/``record`` hooks and to every factory and recorder; within
        ``build`` only resources of kits declared earlier exist.

        :param name: the resource name
        :param obj: the object
        :param scope: ``"kit"`` (default: qualified by the kit prefix) or
            ``"global"`` (the bare name, shared by all kits)
        :return: *obj*
        :raises ScenarioError: when the (qualified) name is provided twice

        Example::

            feeder = ctx.provide("feeder", FeederBehavior(ctx.p.n_buses))   # "grid.feeder"
            ctx.provide("market", market, scope="global")                  # "market"
        """
        self._require_declaring("ctx.provide()")
        check_name(name, "A resource name")
        if scope not in ("kit", "global"):
            raise ScenarioError(
                f"ctx.provide({name!r}): scope must be 'kit' or 'global', got {scope!r}."
            )
        qualified = scope == "kit" and not name.startswith(f"{self._prefix}.")
        key = self._qualify(name) if qualified else name
        resources = self._state.resources
        if key in resources:
            raise ScenarioError(
                f"Resource {key!r} is provided twice (by {resources[key][1]!r} and "
                f"{self._owner()!r}); give one of them another name (resources "
                "provided with scope='global' are shared by all kits)."
            )
        resources[key] = (obj, self._owner())
        return obj

    def _resource_key(self, name: str) -> str | None:
        """The key *name* resolves to: own kit's, exact, then a unique kit's; else ``None``."""
        resources = self._state.resources
        if not isinstance(name, str):
            raise TypeError(
                f"A resource name must be a str, got {type(name).__name__}."
            )
        if self._prefix:
            local = f"{self._prefix}.{name}"
            if local in resources:
                return local
        if name in resources:
            return name
        matches = [key for key in resources if key.endswith(f".{name}")]
        if len(matches) > 1:
            listing = ", ".join(repr(key) for key in matches)
            raise ScenarioError(
                f"Resource {name!r} is provided by several kits ({listing}); name the "
                f"one you mean, e.g. ctx.resource({matches[0]!r}) (pass the provider's "
                "prefix to the consuming kit as a parameter)."
            )
        return matches[0] if matches else None

    def resource(self, name: str) -> Any:
        """Return the object provided under *name* (see :meth:`provide`).

        Resolution follows group names: inside a kit its own resource
        (``"<prefix>.<name>"``) wins, then the exact name (a qualified
        ``"agg_n.tariff"`` or a scenario-level/global ``"tariff"``), then the
        one resource of any kit called *name* (so a consumer need not know a
        single provider's prefix; several matches are an error that lists
        them).

        :raises ScenarioError: for unknown names (listing the provided ones)
            and for names several kits provide

        Example::

            feeder = ctx.resource("feeder")          # the one kit providing "feeder"
            north = ctx.resource("agg_n.tariff")     # one of several
        """
        state = self._state
        key = self._resource_key(name)
        if key is not None:
            return state.resources[key][0]
        known = list(state.resources)
        listing = ", ".join(repr(k) for k in known) if known else "none"
        hint = ""
        if state.stage == "build":
            hint = (
                " Inside a build hook only resources of kits declared earlier exist; "
                "read it in wire/record hooks, factories or recorders instead."
            )
        local_names = [key.rpartition(".")[2] for key in known]
        raise ScenarioError(
            f"Unknown resource {name!r}.{did_you_mean(name, [*known, *local_names])} "
            f"Provided resources: {listing}.{hint}"
        )

    def add_behavior(self, behavior: Any) -> Any:
        """Add an environment behavior (``on_step`` runs every step); returns it.

        The scenario's default environment runs all added behaviors in
        declaration order (one behavior directly, several as a
        :class:`~mango.scenario.CompositeBehavior`).  A behavior with ``rng =
        None`` gets the stream ``behavior/<prefix>/<i>`` (i-th behavior of the
        kit).  :meth:`set_behavior` is an alias.

        Example::

            feeder = ctx.add_behavior(FeederBehavior(ctx.p.n_buses, ctx.p.r_pu_per_kw))
            ctx.record_world("import_kw", lambda: feeder.import_kw)
        """
        self._require_declaring("ctx.add_behavior()")
        from .components import _check_behavior

        _check_behavior(behavior, "ctx.add_behavior() argument")
        if any(existing is behavior for _, existing in self._state.behaviors):
            raise ScenarioError(
                f"Behavior {behavior!r} is added twice; add each behavior once."
            )
        self._state.behaviors.append((self._prefix, behavior))
        return behavior

    set_behavior = add_behavior

    def set_space(self, space: Any) -> Any:
        """Set the environment's space (one per scenario); returns it.

        Example::

            ctx.set_space(Area2D(ctx.p.width, ctx.p.height))
        """
        self._require_declaring("ctx.set_space()")
        from mango.simulation import Space

        if not isinstance(space, Space):
            raise TypeError(
                f"ctx.set_space() needs a mango Space, got {type(space).__name__}."
            )
        state = self._state
        if state.space is not None:
            raise ScenarioError(
                f"The space is set twice (by {state.space_owner!r} and "
                f"{self._owner()!r}); a scenario has one space."
            )
        state.space, state.space_owner = space, self._owner()
        return space

    @property
    def behavior(self) -> Any:
        """The environment behavior composed from every :meth:`add_behavior`.

        ``None`` without behaviors, the behavior itself for one, else a
        :class:`~mango.scenario.CompositeBehavior`.  A custom environment
        factory must install it (the run checks this).  Available after the
        declaration stage, e.g. in the environment factory.

        Example::

            scenario.environment(lambda ctx: DefaultEnvironment(
                space=ctx.space, behavior=ctx.behavior))
        """
        state = self._state
        if state.declaring:
            raise ScenarioError(
                "ctx.behavior is composed after the declaration stage (when every "
                "kit's build hook ran); read it in the environment factory."
            )
        if state.composed_behavior is _UNSET:
            from .components import compose_behavior

            state.composed_behavior = compose_behavior(state.behaviors, state.seeds)
        return state.composed_behavior

    @property
    def space(self) -> Any:
        """The run's space: the one set with :meth:`set_space`, else the space of
        the world's environment (``scenario.environment(...)``), else ``None``.

        Example::

            scenario.record("x", birds, lambda a, ctx: ctx.space.location(a).x)
        """
        state = self._state
        if state.space is not None:
            return state.space
        environment = getattr(state.world, "environment", None)
        return getattr(environment, "space", None)

    def profile(self, name: str, key: Any = None) -> Any:
        """Return the profile *name* from ``params.profiles`` (see :mod:`mango.scenario.profiles`).

        The profile is noise-free (``e.profile`` adds the entity's noise) and
        read once per process.

        :param name: the profile name
        :param key: the column (a name, or an int modulo the number of
            columns; default: the first column)
        :return: a :class:`~mango.scenario.Profile`
        :raises ScenarioError: for unknown profiles (listing the declared ones)

        Example::

            HouseholdAgent(load_profile=ctx.profile("h0"))
        """
        return self._profile(name, key, None)

    def _profile(self, name: str, key: Any, entity: str | None) -> Any:
        from .profiles import _profile_spec, _resolve_key, load_profile

        state = self._state
        spec = _profile_spec(state.params, name)
        column = _resolve_key(spec, name, key)
        noisy = entity is not None and spec.noise > 0
        cache_key = (name, column, entity if noisy else None)
        found = state.profiles.get(cache_key)
        if found is None:
            if not any(key[0] == name for key in state.profiles):
                from .profiles import profile_warnings

                for message in profile_warnings(
                    state.params, names=[name], coverage=False
                ):
                    warnings.warn(message, UserWarning, stacklevel=4)
            rng = None
            if noisy:
                rng = state.seeds.child(
                    "agent", name_key(entity), "profile", name
                ).generator()
            found = load_profile(
                spec, name=name, key=column, scenario_start=state.time.start, rng=rng
            )
            state.profiles[cache_key] = found
        return found


def _check_entity_name(group: str, name: Any) -> None:
    if not isinstance(name, str) or not name:
        raise ScenarioError(
            f"Group {group!r}: naming produced {name!r}; entity names must be "
            "non-empty strings."
        )
    if "/" in name:
        raise ScenarioError(
            f"Entity name {name!r} of group {group!r} contains '/'; entity "
            "names key random streams and must not contain '/'. Pass "
            "naming=... to choose other names."
        )
    if (
        name.startswith(_GENERATED_AID_PREFIX)
        and name[len(_GENERATED_AID_PREFIX) :].isnumeric()
    ):
        raise ScenarioError(
            f"Entity name {name!r} of group {group!r} is not allowed: mango "
            f"generates aids '{_GENERATED_AID_PREFIX}<N>' and rejects them as "
            "suggested aids. Rename the group or pass naming=..., e.g. "
            "naming='{group}_{index}'."
        )


def no_factory(
    e: EntityContext,
) -> Agent:  # placeholder while checking decorator calls
    raise AssertionError


def check_group_options(name, count, factory, install, position, naming) -> None:
    _check_local_name(name, "A group name")
    if isinstance(count, bool) or not (
        isinstance(count, numbers.Integral | str) or callable(count)
    ):
        raise TypeError(
            f"Group {name!r}: count must be an int, a parameter path such as 'ev.n' "
            f"or a function of the params, got {count!r}."
        )
    if isinstance(count, numbers.Integral) and count < 0:
        raise ScenarioError(f"Group {name!r}: count must be >= 0, got {count}.")
    check_callable(factory, f"Group {name!r} factory", optional=False)
    if isinstance(factory, type) and issubclass(factory, Agent):
        raise TypeError(
            f"Group {name!r}: pass a factory function that builds the agent, e.g. "
            f"lambda e: {factory.__name__}(...), not the class {factory.__name__} "
            "itself (the factory is called with the EntityContext e)."
        )
    check_callable(install, f"Group {name!r} install")
    check_callable(position, f"Group {name!r} position")
    if naming is not None and not isinstance(naming, str) and not callable(naming):
        raise TypeError(
            f"Group {name!r}: naming must be a format string such as "
            f"'{{group}}_{{index}}' or a function e -> str, got {type(naming).__name__}."
        )
    for role, fn, example in (
        ("factory", factory, "lambda e: MyAgent()"),
        ("install", install, "lambda e: {'bus': e.index % 4}"),
        ("position", position, "lambda e: Position2D(e.index, 0)"),
        (
            "naming",
            None if isinstance(naming, str) else naming,
            "lambda e: f'n{e.index}'",
        ),
    ):
        if fn is not None and not takes_entity(fn):
            raise TypeError(
                f"Group {name!r}: the {role} must take one argument e (the "
                f"EntityContext), e.g. {example}; {_callable_label(fn)} cannot be "
                "called as fn(e)."
            )


def takes_entity(fn: Callable) -> bool:
    """Whether ``fn(e)`` binds (``True`` when the signature cannot be read)."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    try:
        signature.bind(None)
    except TypeError:
        return False
    return True


def _callable_label(fn: Callable) -> str:
    name = getattr(fn, "__qualname__", None) or type(fn).__qualname__
    try:
        return f"{name}{inspect.signature(fn)}"
    except (TypeError, ValueError):
        return name


@dataclass(frozen=True)
class DeferredLinkComm:
    """``link(delay=, loss=)`` that read the run's parameters (resolved per run)."""

    delay: Any
    loss: Any

    @property
    def fingerprint(self) -> dict:
        return {
            "delay": param_spec_text(self.delay),
            "loss": param_spec_text(self.loss),
        }


def _number(value: Any) -> Any:
    """Loss values parse as numbers: anything else may be a parameter path."""
    if isinstance(value, str):
        return float(value)
    return value


def check_link_comm(
    name: str, delay: Any, loss: Any, *, params: Any = None, model: Any = None
) -> Any:
    """Validate ``link(delay=, loss=)``: a :class:`~mango.scenario.LinkComm` or ``None``.

    *delay* and *loss* may read the run's parameters (``"$path"``, a bare
    parameter path, ``$path`` values inside a distribution or ``fn(p)``).
    With *params* (per run) they are resolved and validated; without, their
    paths are checked against *model* and a :class:`DeferredLinkComm` is
    returned.
    """
    from pydantic import ValidationError

    from .comm import link_comm

    deferred = (delay is not None and is_param_spec(delay)) or (
        loss is not None and is_param_spec(loss, _number)
    )
    sources: dict[str, str] = {}
    if deferred:
        if params is None:
            for key, value, parse in (
                ("delay", delay, parse_duration),
                ("loss", loss, _number),
            ):
                if value is not None and model is not None:
                    paths = param_spec_paths(value, parse)
                    for path in paths:
                        try:
                            check_path(model, path)
                        except ParamsError as exc:
                            raise ScenarioError(
                                f"link({name!r}): {key} {value!r} is neither "
                                + (
                                    "seconds, a duration or a distribution"
                                    if key == "delay"
                                    else "a probability"
                                )
                                + f" nor a parameter path ({exc})."
                            ) from None
            return DeferredLinkComm(delay, loss)
        if delay is not None and is_param_spec(delay):
            delay, sources["delay"] = resolve_param_spec(
                delay, params, f"link({name!r}) delay"
            )
        if loss is not None and is_param_spec(loss, _number):
            loss, sources["loss"] = resolve_param_spec(
                loss, params, f"link({name!r}) loss", _number
            )
    try:
        return link_comm(delay, loss)
    except ValidationError as exc:
        message = first_error_message(exc)
        field = str(exc.errors()[0]["loc"][0]) if exc.errors() else ""
        if field in sources:
            message = f"{field} {sources[field]}: {message}"
        else:
            message += _bare_path_hint((delay, loss), params, model)
        raise ScenarioError(f"link({name!r}): {message}") from None


def _bare_path_hint(values: Any, params: Any, model: Any = None) -> str:
    """A hint when a value inside a distribution names a parameter without ``$``."""
    found: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, str) and not item.startswith("$"):
            try:
                if params is not None:
                    get_path(params, item)
                elif model is not None:
                    check_path(model, item)
                else:
                    return
            except (KeyError, AttributeError, IndexError, TypeError, ParamsError):
                return
            found.append(item)
        elif isinstance(item, dict):
            for inner in item.values():
                visit(inner)
        elif isinstance(item, list | tuple):
            for inner in item:
                visit(inner)

    visit(values)
    if not found:
        return ""
    return f" ({found[0]!r} is a parameter path; write '${found[0]}' to use its value)"


def check_link_options(
    name, source, target, pattern, where, directed, *, params: Any = None, model=None
) -> Any:
    """Validate a link declaration and resolve its pattern.

    With *params* (per run) parameter references are resolved; without them
    a pattern that reads parameters is returned unchanged after checking its
    paths against *model* (the params model), so it is resolved per run.
    """
    check_name(name, "A link name")
    _check_group_arg(source, f"link({name!r}) source")
    if target is not None:
        _check_group_arg(target, f"link({name!r}) target")
    check_callable(where, f"link({name!r}) where")
    if not isinstance(directed, bool):
        raise TypeError(f"link({name!r}) directed must be a bool, got {directed!r}.")
    if params is None and depends_on_params(pattern):
        if model is not None:
            paths = parameter_paths(pattern)
            if isinstance(pattern, str):
                try:
                    check_path(model, pattern)
                except ParamsError as reason:
                    try:
                        resolve_pattern(pattern)  # the unknown-name message
                    except ValueError as exc:
                        raise ScenarioError(
                            f"link({name!r}): {exc} As a parameter path: {reason}"
                        ) from None
                paths = [p for p in paths if p != pattern]
            check_param_paths(model, paths, f"link({name!r}) pattern")
        return pattern
    try:
        return resolve_pattern(pattern, params)
    except (ValueError, TypeError) as exc:
        hint = _model_path_hint(pattern, model) if params is None else ""
        raise ScenarioError(f"link({name!r}): {exc}{hint}") from exc


def _model_path_hint(pattern: Any, model: Any) -> str:
    """A hint for a factory argument that names a parameter but lacks the ``$``."""
    if model is None or not isinstance(pattern, dict) or len(pattern) != 1:
        return ""
    ((_, args),) = pattern.items()
    values = args.values() if isinstance(args, dict) else [args]
    for value in values:
        if isinstance(value, str) and "." in value and not value.startswith("$"):
            try:
                check_path(model, value)
            except ParamsError:
                continue
            return (
                f" ({value!r} is a parameter path; write '${value}' to use its value)"
            )
    return ""


def check_stop_when(fn: Any, every: Any) -> Any:
    check_callable(fn, "The stop_when condition", optional=False)
    return check_every(every)


def check_table_name(name: Any, what: str) -> str:
    """A log name: also a file name (``logs/<name>.parquet``)."""
    check_name(name, f"{what}: the log name")
    if not _TABLE_NAME.fullmatch(name):
        raise ScenarioError(
            f"{what}: the log name {name!r} names a file; use letters, digits, '_', "
            "'-' and '.' (not starting with '.' or '-')."
        )
    return name


def check_record_options(
    name, group, fn, attr, every, aggregate=None, window=None
) -> tuple[Callable, Any]:
    check_name(name, "A recorder name")
    _group_args(group, f"record({name!r}) group")
    check_aggregate(aggregate, f"record({name!r})")
    check_window(window, every, f"record({name!r})")
    if (fn is None) == (attr is None):
        raise ScenarioError(
            f"record({name!r}) needs exactly one of fn (e.g. lambda a: a.soc_kwh) "
            "or attr (e.g. attr='soc_kwh')."
        )
    if attr is not None:
        check_name(attr, f"record({name!r}) attr")
        getter = operator.attrgetter(attr)
    else:
        check_callable(fn, f"record({name!r}) fn", optional=False)
        getter = fn
    return getter, check_every(every)


def recorder_takes_ctx(fn: Callable) -> bool:
    """True when a recorder ``fn`` takes ``(agent, ctx)`` rather than ``(agent)``.

    Only real signatures count: two or more positional parameters or
    ``*args``.  Callables without an inspectable signature (``attrgetter``,
    builtins) are called with the agent alone.
    """
    try:
        parameters = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    positional = 0
    for parameter in parameters:
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional += 1
    return positional >= 2


class _AgentCtxGetter:
    """A recorder getter calling ``fn(agent, ctx)`` with the declaring context."""

    __slots__ = ("fn", "ctx")

    def __init__(self, fn: Callable, ctx: BuildContext):
        self.fn = fn
        self.ctx = ctx

    def __call__(self, agent: Any) -> Any:
        return self.fn(agent, self.ctx)

    def __repr__(self) -> str:
        return f"<recorder {getattr(self.fn, '__qualname__', self.fn)!r} (agent, ctx)>"


def check_record_world_options(name, fn, every, entity, window=None) -> Any:
    check_name(name, "A recorder name")
    check_callable(fn, f"record_world({name!r}) fn", optional=False)
    check_window(window, every, f"record_world({name!r})")
    spec = check_every(every)
    check_name(entity, f"record_world({name!r}) entity")
    return spec


class EntityContext:
    """The ``e`` passed to a group's ``install``, ``factory``, ``position`` and ``naming``.

    Attributes: ``name`` (the entity name and later aid; ``None`` inside
    ``naming``), ``group`` (qualified group name), ``index``, ``count`` (group
    size), ``attrs`` (the install attributes; empty inside ``install``),
    ``agent`` (``None`` until the factory returned), ``ctx`` (the declaring
    :class:`BuildContext`), plus ``p``, ``params``, ``time`` and ``world``
    from that context.

    ``e.rng`` is the entity's own stream ``seeds.rng("agent", name_key(name))``,
    the very Generator the agent later gets as ``agent.rng``: construction
    draws and runtime draws continue one per-entity stream, and adding other
    entities never changes it.

    Example::

        ev = scenario.group("ev", "ev.n", lambda e: EVAgent(
            soc=e.p.ev.soc.draw(e.rng),
            arrival_h=e.p.ev.arrival_h.draw(e.rng),
            bus=e.attrs["bus"]),
            install=lambda e: {"bus": e.index % e.p.grid.n_buses})
    """

    __slots__ = ("name", "group", "index", "count", "attrs", "agent", "ctx")

    def __init__(
        self,
        name: str | None,
        group: str,
        index: int,
        count: int,
        ctx: BuildContext,
        attrs: dict | None = None,
    ):
        self.name = name
        self.group = group
        self.index = index
        self.count = count
        self.ctx = ctx
        self.attrs: dict = {} if attrs is None else attrs
        self.agent: Agent | None = None

    def __repr__(self) -> str:
        return f"<EntityContext {self.name!r} group={self.group!r} index={self.index}>"

    @property
    def p(self) -> Any:
        """Params of the declaring scope (kit params inside a kit)."""
        return self.ctx.p

    @property
    def params(self) -> BaseModel:
        """The full params of the run."""
        return self.ctx.params

    @property
    def time(self) -> TimeConfig:
        """The run's :class:`~mango.scenario.TimeConfig`."""
        return self.ctx.time

    @property
    def world(self) -> SimulationWorld | None:
        """The run's world (``None`` in a plan)."""
        return self.ctx.world

    def _require_name(self, what: str) -> str:
        if self.name is None:
            raise ScenarioError(
                f"e.{what} is not available inside naming: the entity stream is keyed "
                "by the entity name, which naming is computing."
            )
        return self.name

    @property
    def rng(self) -> numpy.random.Generator:
        """The entity's stream, identical to the registered agent's ``agent.rng``.

        Example::

            soc = e.p.ev.soc.draw(e.rng)
        """
        return self.ctx.seeds.rng("agent", name_key(self._require_name("rng")))

    def rng_for(self, name: str) -> numpy.random.Generator:
        """Return a named sub-stream of the entity stream (``agent.rng_for(name)``).

        Example::

            demand = e.rng_for("demand").uniform(5.0, 40.0)
        """
        return self.ctx.seeds.rng(
            "agent", name_key(self._require_name("rng_for")), name
        )

    def resource(self, name: str) -> Any:
        """Return a shared resource (see :meth:`BuildContext.provide`).

        Example::

            feeder = e.resource("feeder")
        """
        return self.ctx.resource(name)

    def profile(self, name: str, key: Any = None) -> Any:
        """Return the entity's variant of profile *name*.

        The column is chosen by *key* (default: the entity index, modulo the
        number of columns) and multiplicative noise (``noise`` of the spec) is
        drawn from the entity's sub-stream ``agent/<name>/profile/<profile>``,
        so it is reproducible per entity, independent across entities and
        does not consume ``e.rng``.

        Example::

            HouseholdAgent(load_profile=e.profile("h0"))
        """
        entity = self._require_name("profile")
        return self.ctx._profile(name, self.index if key is None else key, entity)

    def draw(self, dist: Any, size: Any = None) -> Any:
        """Draw from *dist* with the entity's stream (and index for per-entity tables).

        ``e.draw(d)`` is ``d.draw(e.rng)``, or ``d.draw(e.rng, index=e.index)``
        for distributions that take an entity index such as
        :class:`~mango.scenario.FromColumn`.  A ``FromColumn`` in sample mode
        reads the entity's row of the table (:meth:`row`), so every column of
        one table comes from the same row.

        Example::

            annual_kwh = e.draw(e.p.annual_kwh)
            opinion, age = e.draw(e.p.opinion), e.draw(e.p.age)   # one respondent
        """
        if _is_sample_table(dist):
            return dist.values_at(self._sample_rows(dist, size))
        rng = self.rng
        try:
            parameters = inspect.signature(dist.draw).parameters
        except (TypeError, ValueError, AttributeError):
            parameters = {}
        if "index" in parameters:
            return dist.draw(rng, size, index=self.index)
        return dist.draw(rng, size)

    def row(self, dist: Any) -> int:
        """The row of a per-entity table this entity reads (``FromColumn``).

        In index mode it is the entity index (modulo the table length, with
        the table's ``wrap`` rule); in sample mode the row drawn once per
        entity and table from the sub-stream ``agent/<name>/table/<path>``
        (the row :meth:`draw` reads every column of that table from).  Use
        it to keep the link between an agent and its survey respondent.

        :param dist: a :class:`~mango.scenario.FromColumn`
        :return: the 0-based row number
        :raises TypeError: for other distributions

        Example::

            scenario.group(
                "person", "n",
                lambda e: Person(opinion=e.draw(e.p.opinion), age=e.draw(e.p.age)),
                install=lambda e: {"respondent_row": e.row(e.p.opinion)},
            )
        """
        from .distributions import FromColumn

        if not isinstance(dist, FromColumn):
            raise TypeError(
                f"e.row() needs a FromColumn (a per-entity table), got "
                f"{type(dist).__name__}."
            )
        if dist.mode == "sample":
            return int(self._sample_rows(dist, None))
        n = len(dist.column_values())
        dist._check_rows(self.index, n)
        return self.index % n

    def _sample_rows(self, dist: Any, size: Any) -> Any:
        """The sample-mode row(s) of this entity for *dist*'s table (drawn once)."""
        import os

        name = self._require_name("draw")
        table = os.path.normpath(dist.path)
        shape = size if size is None or isinstance(size, tuple) else (size,)
        key = (name, table, shape)
        rows = self.ctx._state.table_rows
        if key not in rows:
            n = len(dist.column_values())
            if n == 0:
                raise ValueError(
                    f"FromColumn({dist.path!r}, {dist.column!r}) has no rows to sample."
                )
            stream = self.ctx.seeds.rng(
                "agent", name_key(name), "table", name_key(table)
            )
            rows[key] = (
                int(stream.integers(n))
                if shape is None
                else stream.integers(0, n, shape)
            )
        return rows[key]


def _is_sample_table(dist: Any) -> bool:
    from .distributions import FromColumn

    return isinstance(dist, FromColumn) and dist.mode == "sample"


def resolve_links(state: RunState) -> None:
    for pending in state.pending_links:
        ctx = pending.ctx
        what = f"link({pending.name!r})"
        source = ctx._resolve_group(pending.source, what)
        target = (
            source
            if pending.target is None
            else ctx._resolve_group(pending.target, what)
        )
        if is_within_group(pending.pattern) and target != source:
            raise ScenarioError(
                f"Link {pending.name!r}: pattern {pattern_name(pending.pattern)!r} "
                f"connects entities within one group; omit the target (got source "
                f"{source!r}, target {target!r})."
            )
        state.links.append(
            LinkSpec(
                pending.name,
                source,
                target,
                pending.pattern,
                pending.where,
                pending.directed,
                pending.comm,
            )
        )
    for name, specs in links_by_name(state.links).items():
        if len({spec.directed for spec in specs}) > 1:
            raise ScenarioError(
                f"Link {name!r} is declared both directed and undirected; all "
                "declarations of one link must agree on directed=."
            )
        settings = {repr(spec.comm) for spec in specs if spec.comm is not None}
        if len(settings) > 1:
            raise ScenarioError(
                f"Link {name!r} is declared with different delay/loss settings; "
                "give them in one declaration (they apply to the whole link)."
            )


def _declarer(ctx: BuildContext | None) -> str:
    prefix = getattr(ctx, "_prefix", "")
    return f"kit {prefix!r}" if prefix else "the scenario"


def resolve_records(state: RunState) -> None:
    agent_names: dict[str, set[str]] = {}
    aggregates: dict[str, Any] = {}
    kinds: dict[str, str | None] = {}
    world_owners: dict[str, dict[Any, Any]] = {}
    for pending in state.pending_records:
        if pending.kind == "world":
            if pending.name in agent_names:
                raise ScenarioError(
                    f"Recorder {pending.name!r} is both a world recorder and a "
                    "per-agent recorder; use distinct names."
                )
            owners = world_owners.setdefault(pending.name, {})
            if pending.entity in owners:
                first = _declarer(owners[pending.entity])
                second = _declarer(pending.ctx)
                by = first if first == second else f"{first} and {second}"
                raise ScenarioError(
                    f"World recorder {pending.name!r} of entity {pending.entity!r} "
                    f"is declared twice (by {by}); give one of them another name "
                    "or another entity=."
                )
            owners[pending.entity] = pending.ctx
            group = None
        else:
            group = pending.ctx._resolve_group(
                pending.group, f"record({pending.name!r})"
            )
            if pending.name in world_owners:
                raise ScenarioError(
                    f"Recorder {pending.name!r} is both a world recorder and a "
                    "per-agent recorder; use distinct names."
                )
            groups = agent_names.setdefault(pending.name, set())
            if group in groups:
                raise ScenarioError(
                    f"Recorder {pending.name!r} is declared twice on group {group!r}; "
                    "the same name may only be recorded on different groups."
                )
            groups.add(group)
            first = aggregates.setdefault(pending.name, pending.aggregate)
            if first is not pending.aggregate and first != pending.aggregate:
                raise ScenarioError(
                    f"Recorder {pending.name!r} is declared with different aggregate= "
                    f"on its groups ({aggregate_text(first)!r} and "
                    f"{aggregate_text(pending.aggregate)!r}); one variable holds either "
                    "per-entity or per-group rows. Use distinct names."
                )
        first_kind = kinds.setdefault(pending.name, pending.value_kind)
        if first_kind != pending.value_kind:
            raise ScenarioError(
                f"Recorder {pending.name!r} is declared with different kind= "
                f"({first_kind!r} and {pending.value_kind!r}); one variable is either "
                "a state or a flow. Use distinct names."
            )
        _check_every(pending.name, pending.every, state.time)
        state.records.append(
            RecordSpec(
                pending.name,
                pending.kind,
                group,
                pending.getter,
                pending.every,
                pending.entity,
                pending.ctx,
                aggregate=pending.aggregate,
                window=pending.window,
                value_kind=pending.value_kind,
                categories=pending.categories,
            )
        )
    state.records[:] = [  # e.g. one kit used under two prefixes
        dataclasses.replace(spec, key=f"{spec.entity}/{spec.name}")
        if spec.kind == "world" and len(world_owners[spec.name]) > 1
        else spec
        for spec in state.records
    ]


def _check_every(name: str, every: float | None, time: TimeConfig) -> None:
    """With a fixed step, ``every`` must be a multiple of it (samples happen at steps)."""
    step = time.step_s
    if every is None or step is None:
        return
    ratio = every / step
    whole = round(ratio)
    if whole >= 1 and abs(ratio - whole) <= _tolerance(ratio):
        return
    lower = max(1, int(ratio)) * step
    options = sorted({lower, lower + step} if ratio > 1 else {step})
    raise ScenarioError(
        f"Recorder {name!r}: every {format_duration(every)} is not a multiple of the "
        f"step {format_duration(step)}, and samples are only taken at steps; use "
        f"every={' or '.join(repr(format_duration(o)) for o in options)}, or omit "
        "every to record at every step."
    )
