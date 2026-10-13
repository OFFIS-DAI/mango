"""
The scenario builder: declare groups, links and recorders once, run them with
any parameters and seed.

A :class:`Scenario` holds the *structure* of a simulation (which agents exist,
how they are wired, what is recorded); the numbers live in a parameter model
(:class:`~mango.scenario.ScenarioParams`).  Each run resolves the parameters,
derives every random stream from one seed, and builds a fresh
:class:`~mango.simulation.world.SimulationWorld` in a fixed order.

Declarations are replayed per run in a *declaration stage* with three passes,
``build`` (groups) → ``wire`` (links) → ``record`` (recorders); every
declaration takes part in declaration order.  Group and link names are
resolved after all passes, so a link may name a group declared later.

Example::

    from mango import Agent
    from mango.scenario import Scenario

    class Node(Agent):
        def handle_message(self, content, meta):
            self.received = getattr(self, "received", 0) + 1

    scenario = Scenario("toy")
    nodes = scenario.group("node", 10, lambda e: Node())
    scenario.link("ring", nodes, pattern="ring")
    scenario.record("received", nodes, lambda a: getattr(a, "received", 0))
    result = scenario.run(seed=1)
    result.wide("received")          # time x entity table
"""

from __future__ import annotations

import copy
import sys
import warnings
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, create_model, model_validator

from mango.util.seeding import SeedTree

from ._util import content_hash, object_ref, to_plain
from .components import (
    PREFIX_HINT,
    ScenarioComponent,
    _check_prefix,
    _KitDeclaration,
    nested_params_model,
)
from .configfiles import get_path, set_path
from .context import (
    STAGES,
    BuildContext,
    DeferredLinkComm,
    EntityContext,
    RunState,
    check_callable,
    check_group_options,
    check_link_comm,
    check_link_options,
    check_name,
    check_record_options,
    check_record_world_options,
    check_stop_when,
    every_text,
    first_error_message,
    no_factory,
    resolve_links,
    resolve_records,
)
from .errors import ParamsError, ScenarioError
from .events import Schedule, structure_event
from .params import (
    ScenarioParams,
    _merge_layers,
    check_path,
    load_params,
)
from .patterns import (
    depends_on_params,
    pattern_name,
)
from .plan import (
    GroupPlan,
    KitPlan,
    LinkPlan,
    RecordPlan,
    ScenarioPlan,
    count_edges,
)
from .reducers import aggregate_text, check_categories, check_kind
from .specs import (
    Entity,
    GroupRef,
    LinkSpec,
    RecordSpec,
    compile_link,
    links_by_name,
)
from .timecfg import TimeConfig, format_duration, parse_duration

if TYPE_CHECKING:
    from .result import RunResult
    from .runtime import ScenarioRun
    from .script import GeneratedScript

__all__ = [
    "Scenario",
    "GroupRef",
    "Entity",
    "EntityContext",
    "BuildContext",
    "LinkSpec",
    "RecordSpec",
    "ScenarioPlan",
    "KitPlan",
    "GroupPlan",
    "LinkPlan",
    "RecordPlan",
]


# --------------------------------------------------------------------------- scenario


def _callable_name(fn: Any) -> str | None:
    """A process-independent name of a callable (its qualified name)."""
    if fn is None:
        return None
    return getattr(fn, "__qualname__", None) or type(fn).__qualname__


def _factory_ref(fn: Any) -> str | None:
    """``"module:qualname"`` of an importable factory, else its qualified name."""
    if fn is None:
        return None
    return object_ref(fn) or _callable_name(fn)


def _value_text(value: Any) -> Any:
    """A process-independent description of a declaration argument."""
    if callable(value):
        return _callable_name(value)
    if isinstance(value, GroupRef):
        return value.name
    return value


class _StageHook:
    """A scenario-level declaration: ``fn(root_ctx)`` run in one declaration pass.

    ``fingerprint`` describes the declaration without object identities
    (kind, names and the qualified names of its callables).
    """

    __slots__ = ("stage", "fn", "fingerprint")

    def __init__(
        self, stage: str, fn: Callable[[BuildContext], Any], fingerprint: tuple = ()
    ):
        self.stage = stage
        self.fn = fn
        self.fingerprint = fingerprint

    def apply(self, stage: str, ctx: BuildContext) -> None:
        if stage == self.stage:
            self.fn(ctx)


def _spec_text(spec: Any) -> Any:
    """A process-independent description of a pattern spec (fingerprints)."""
    if callable(spec):
        return _callable_name(spec)
    return to_plain(spec, default=repr)


def _derived_label(source: Any) -> str:
    if callable(source):
        return f"function {_callable_name(source)!r}"
    return f"parameter {source!r}"


def _derived_time_model(
    base: type[ScenarioParams], derived: Mapping[str, Any]
) -> type[ScenarioParams]:
    """*base* with an after-validator that computes ``time.<key>`` from the params.

    A value a preset, override or default sets explicitly must agree with the
    derived one (a resolved config dump reloads unchanged).
    """

    def derive_time(self: Any) -> Any:
        time = self.time
        updates: dict[str, Any] = {}
        for key, source in derived.items():
            label = _derived_label(source)
            raw = source(self) if callable(source) else get_path(self, source)
            if key == "step" and isinstance(raw, str) and raw.strip() == "event":
                value: Any = "event"
            else:
                try:
                    value = parse_duration(raw)
                except ValueError as exc:
                    raise ValueError(
                        f"time.{key} derived from {label}: {raw!r} is not a duration "
                        f"({exc})"
                    ) from None
            current = getattr(time, key)
            if key in time.model_fields_set and not _same_time_value(current, value):
                raise ValueError(
                    f"time.{key} is derived from {label} (scenario.time({key}=...)), "
                    f"which gives {_time_text(value)}, but it is also set explicitly to "
                    f"{_time_text(current)} by a preset, override or default; drop the "
                    f"explicit time.{key} or change the parameters it is derived from"
                )
            updates[key] = value
        updated = time.model_copy(update=updates)
        type(time).model_validate(updated.model_dump())  # horizon/step agreement
        self.__dict__["time"] = updated
        return self

    validator = model_validator(mode="after")(derive_time)
    return create_model(
        base.__name__,
        __base__=base,
        __module__=base.__module__,
        __validators__={"_derive_time": validator},
    )


def _same_time_value(a: Any, b: Any) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(b)))


def _time_text(value: Any) -> str:
    return value if isinstance(value, str) else format_duration(float(value))


def _resolve_seed(seed: Any, params: BaseModel) -> SeedTree:
    if seed is not None:
        return SeedTree(seed)
    if getattr(params, "seed", None) is not None:
        return SeedTree(params.seed)
    return SeedTree()


class Scenario:
    """A reusable simulation structure: groups, links, recorders and factories.

    :param name: the scenario name (used in results and as fallback run reference)
    :param params: the params model, a subclass of
        :class:`~mango.scenario.ScenarioParams` (default: ``ScenarioParams``)
    :raises TypeError: if *params* is not a ``ScenarioParams`` subclass

    The scenario records the module it was created in, so a scenario assigned
    to a module-level name can be referenced as ``"module:name"`` (see
    :attr:`ref`) by process-based studies.

    Example::

        scenario = Scenario("ev_feeder", params=Params)
        scenario.time(start="2024-06-01T00:00", horizon="2d", step="15min")
        coordinator = scenario.group("coordinator", 1, lambda e: CoordinatorAgent(
            e.p.algorithm, e.time.step_s))
        ev = scenario.group("ev", "ev.n", lambda e: EVAgent(
            capacity_kwh=e.p.ev.capacity_kwh, soc=e.p.ev.soc.draw(e.rng)),
            install=lambda e: {"bus": e.index})
        scenario.link("dispatch", ev, coordinator, pattern="many_to_one")
        scenario.record("soc_kwh", ev, attr="soc_kwh")
        result = scenario.run("presets/base.yaml", seed=7)
    """

    def __init__(self, name: str, params: type[ScenarioParams] = ScenarioParams):
        check_name(name, "A scenario name")
        if not (isinstance(params, type) and issubclass(params, ScenarioParams)):
            raise TypeError(
                "Scenario params must be a subclass of mango.scenario.ScenarioParams "
                f"(class Params(ScenarioParams): ...), got {params!r}."
            )
        self.name = name
        self._params_model = params
        self._default_layers: list[dict] = []
        self._declarations: list = []
        self._group_names: set[str] = set()
        #: group name -> count of ``group(name, count)`` decorators not applied yet
        self._undecorated: dict[str, Any] = {}
        self._environment_factory: Callable | None = None
        self._communication_factory: Callable | None = None
        #: kits in use order: (prefix, component)
        self._kits: list[tuple[str, Any]] = []
        self._effective_model: type[ScenarioParams] | None = None
        #: ``time`` fields derived from the params: key -> fn(p) or parameter path
        self._derived_time: dict[str, Any] = {}
        self.__module__ = sys._getframe(1).f_globals.get("__name__", __name__)

    def __repr__(self) -> str:
        return f"Scenario({self.name!r}, params={self._params_model.__name__})"

    # -- parameters ----------------------------------------------------------------

    @property
    def params_model(self) -> type[ScenarioParams]:
        """The effective params model class.

        The model given to the constructor, extended by one field
        ``<prefix>`` per kit with parameters (see :meth:`use`) and, with
        ``scenario.time(horizon=fn)``, by the derivation of ``time``.
        """
        if not self._kits and not self._derived_time:
            return self._params_model
        if self._effective_model is None:
            model = self._params_model
            if self._kits:
                model = nested_params_model(model, self._kits)
            if self._derived_time:
                model = _derived_time_model(model, dict(self._derived_time))
            self._effective_model = model
        return self._effective_model

    @property
    def kits(self) -> dict[str, Any]:
        """The kits in use order, by prefix.

        Example::

            scenario.kits["grid"]      # the Neighbourhood instance
        """
        return dict(self._kits)

    def use(self, component: Any, prefix: str | None = None) -> Any:
        """Use a kit (a :class:`~mango.scenario.ScenarioComponent`) under *prefix*.

        The kit's ``Params`` become the params field ``<prefix>`` (defaults:
        the kit's constructor arguments; presets and overrides use
        ``"<prefix>.<field>"``), its groups are qualified by the prefix
        (``grid.hh``) and its ``build``/``wire``/``record`` hooks take part in
        the declaration passes in declaration order, together with the
        scenario's own declarations.

        :param component: the kit instance, e.g. ``Neighbourhood(n_households=40)``
        :param prefix: the prefix (default: the kit's ``name``)
        :return: *component*
        :raises ScenarioError: for an invalid or duplicate prefix, or one that
            clashes with a params field

        Example::

            scenario.use(Neighbourhood(), prefix="grid")
            scenario.link("reports", "grid.hh", coordinator, pattern="many_to_one")
            scenario.run(overrides=["grid.pv_share=0.8"])
        """
        if isinstance(component, type) and issubclass(component, ScenarioComponent):
            raise TypeError(
                f"scenario.use() needs a kit instance: scenario.use("
                f"{component.__name__}(...)), not the class."
            )
        if not isinstance(component, ScenarioComponent):
            raise TypeError(
                "scenario.use() needs a ScenarioComponent instance, got "
                f"{type(component).__name__}."
            )
        component.params  # noqa: B018  (a kit that skipped super().__init__ fails here)
        prefix = component.name if prefix is None else prefix
        _check_prefix(prefix, "The prefix")
        for used, other in self._kits:
            if used == prefix:
                raise ScenarioError(
                    f"Kit prefix {prefix!r} is used twice ({type(other).__name__} and "
                    f"{type(component).__name__}); {PREFIX_HINT[0].lower()}"
                    f"{PREFIX_HINT[1:]} to tell them apart."
                )
        # a kit without parameters adds no field, so only its groups use the prefix
        if component.has_params and prefix in self._params_model.model_fields:
            raise ScenarioError(
                f"Kit prefix {prefix!r} clashes with the field {prefix!r} of "
                f"{self._params_model.__name__} (the kit's parameters would live "
                f"there). {PREFIX_HINT}."
            )
        defaults = component.param_defaults()
        if defaults is not None and not isinstance(defaults, Mapping):
            raise TypeError(
                f"{type(component).__name__}.param_defaults() must return a mapping "
                f"or None, got {type(defaults).__name__}."
            )
        self._kits.append((prefix, component))
        self._effective_model = None
        if defaults:
            self.defaults(defaults)
        self._add_declaration(_KitDeclaration(component, prefix))
        return component

    @classmethod
    def compose(
        cls,
        *components: Any,
        name: str | None = None,
        params: type[ScenarioParams] = ScenarioParams,
    ) -> Scenario:
        """Create a scenario from kits, each under its default prefix.

        :param components: kit instances, used in order
        :param name: the scenario name (default: the kit names joined by ``_``)
        :param params: the base params model
        :return: the scenario (more declarations can follow)

        Example::

            scenario = Scenario.compose(GridKit(), EVFleet(n=10), name="ev",
                                        params=ScenarioParams)
        """
        for component in components:
            if not isinstance(component, ScenarioComponent):
                raise TypeError(
                    "Scenario.compose() takes ScenarioComponent instances, got "
                    f"{component!r}."
                )
        if name is None:
            name = "_".join(component.name for component in components) or "scenario"
        scenario = cls(name, params=params)
        scenario.__module__ = sys._getframe(1).f_globals.get("__name__", __name__)
        for component in components:
            scenario.use(component)
        return scenario

    @property
    def ref(self) -> str | None:
        """``"module:name"`` when the scenario is reachable from module globals, else ``None``.

        Example::

            # studies/ev.py:  scenario = Scenario("ev_feeder", params=Params)
            scenario.ref    # "studies.ev:scenario"
        """
        return object_ref(self)

    def param_defaults(self) -> dict:
        """Return the scenario-level parameter defaults as a nested dict.

        They sit between the model defaults and presets (see
        :func:`~mango.scenario.load_params`).  Every ``time()``/``defaults()``
        call is one layer, merged in call order like presets (later calls win,
        ``"+name"`` extends the model's default list).

        Example::

            scenario.time(horizon="2d")
            scenario.param_defaults()     # {"time": {"horizon": "2d"}}
        """
        label = self._defaults_label
        layers = [(label, layer, None) for layer in self._default_layers]
        return _merge_layers(self.params_model, layers)[0]

    #: names the layer of :meth:`defaults` in parameter errors
    _defaults_label = "scenario defaults"

    def time(self, start: Any = None, horizon: Any = None, step: Any = None) -> None:
        """Set scenario-level defaults for ``params.time`` (presets still override them).

        *horizon* and *step* may also be derived from the run's parameters:
        a function ``fn(p)`` or a parameter path (``"market.horizon_s"``)
        returning a duration (seconds or a string such as ``"2d"``).  A derived
        value is computed after presets and overrides are merged; setting the
        same field explicitly to a different value (``-s time.horizon=100``)
        is an error rather than silently ignored.

        :param start: calendar start (datetime or ISO string)
        :param horizon: run length, e.g. ``"2d"``, or ``fn(p)`` / a parameter path
        :param step: fixed step, e.g. ``"15min"``, or ``"event"``, or ``fn(p)``
            / a parameter path

        Example::

            scenario.time(start="2024-06-01T00:00", horizon="2d", step="15min")
            scenario.time(horizon=lambda p: p.market.periods * p.market.ticks, step=1)
        """
        values = {
            key: value
            for key, value in (("start", start), ("horizon", horizon), ("step", step))
            if value is not None
        }
        if not values:
            raise ScenarioError(
                "scenario.time() needs at least one of start, horizon, step."
            )
        derived = {}
        for key in ("horizon", "step"):
            value = values.get(key)
            if callable(value) and not isinstance(value, str):
                derived[key] = values.pop(key)
            elif isinstance(value, str) and not (
                key == "step" and value.strip() == "event"
            ):
                try:
                    parse_duration(value)
                except ValueError:
                    try:
                        check_path(self.params_model, value)
                    except ParamsError:
                        continue  # reported as an invalid duration below
                    derived[key] = values.pop(key)
        for key, value in values.items():
            # each field alone; horizon/step compatibility is checked at load time
            try:
                if key == "start":
                    TimeConfig.model_validate({"start": value})
                elif not (
                    key == "step"
                    and isinstance(value, str)
                    and value.strip() == "event"
                ):
                    if parse_duration(value) <= 0:
                        raise ValueError(f"time.{key} must be > 0")
            except ValueError as exc:
                raise ParamsError(
                    f"Invalid scenario.time({key}={value!r}): "
                    f"{first_error_message(exc)} (a parameter path or fn(p) derives "
                    "it from the parameters)",
                    path=f"time.{key}",
                    source="scenario defaults",
                ) from None
        if values:
            self._default_layers.append({"time": values})
            replaced = [self._derived_time.pop(key, None) for key in list(values)]
            if any(source is not None for source in replaced):
                self._effective_model = None  # a literal replaces a derivation
        for key in derived:
            # a derived field replaces an earlier literal default of the scenario
            for layer in self._default_layers:
                if isinstance(layer.get("time"), dict):
                    layer["time"].pop(key, None)
        if derived:
            self._derived_time.update(derived)
            self._effective_model = None

    def defaults(self, values: Mapping[str, Any]) -> None:
        """Set scenario-level parameter defaults (dotted keys or a nested dict).

        Unknown paths fail when parameters are loaded, naming the
        ``scenario defaults`` layer.

        :param values: e.g. ``{"ev.n": 5}`` or ``{"ev": {"n": 5}}``

        Example::

            scenario.defaults({"ev.n": 5, "comm.loss_percent": 0.0})
        """
        if not isinstance(values, Mapping):
            raise TypeError(
                f"scenario.defaults() needs a mapping, got {type(values).__name__}."
            )
        layers = []
        for key, value in values.items():
            if not isinstance(key, str) or not key:
                raise ScenarioError(
                    f"Invalid parameter path {key!r} in scenario.defaults()."
                )
            layer: dict = {}
            set_path(layer, key, copy.deepcopy(value))
            layers.append(layer)
        self._default_layers.extend(layers)

    def resolve_params(self, params: Any = None, *, overrides: Any = None) -> BaseModel:
        """Resolve *params* into a validated params instance.

        :param params: ``None``, a params instance, a dict, a preset path, or a
            list of these (merged in order)
        :param overrides: ``["a.b=value", ...]`` or ``{"a.b": value}``
        :return: the params model instance
        :raises ParamsError: for unknown keys or invalid values

        Example::

            params = scenario.resolve_params(["presets/base.yaml", {"ev": {"n": 5}}],
                                             overrides=["algorithm=proportional"])
        """
        sources = list(params) if isinstance(params, list | tuple) else [params]
        return load_params(self, *sources, overrides=overrides)

    # -- factories -------------------------------------------------------------------

    def environment(self, factory: Callable[[BuildContext], Any]) -> Callable:
        """Set the environment factory ``factory(ctx) -> Environment`` (usable as decorator).

        Without it every run uses a fresh ``DefaultEnvironment()``.  The last
        factory set wins.

        Example::

            scenario.environment(lambda ctx: DefaultEnvironment(space=Area2D(10, 10)))
        """
        check_callable(factory, "The environment factory", optional=False)
        self._environment_factory = factory
        return factory

    def communication(self, factory: Callable[[BuildContext], Any]) -> Callable:
        """Set the communication factory ``factory(ctx) -> CommunicationSimulation``.

        Without it every run builds ``build_communication(ctx.comm,
        ctx.seeds.rng("comm"), ctx)`` from ``params.comm``.  Read
        ``ctx.comm`` (the effective config), not ``ctx.p.comm``.  Usable as
        decorator.

        When phases or events change the effective settings, a simulation
        with a method ``on_comm_change(comm)`` is kept and told the new
        settings; otherwise the factory is called once per distinct setting
        and the run reuses that instance whenever the setting returns, so
        state kept on the simulation survives an outage.  Link ``delay``/
        ``loss`` and ``comm.partition`` are layered on top
        (:class:`~mango.scenario.LayeredCommunication`).  Run manifests name
        the factory (``manifest["factories"]``).

        Example::

            scenario.communication(lambda ctx: SimpleCommunicationSimulation(
                loss_percent=ctx.comm.loss_percent))
        """
        check_callable(factory, "The communication factory", optional=False)
        self._communication_factory = factory
        return factory

    # -- declarations ------------------------------------------------------------------

    def _declare(
        self, stage: str, fn: Callable[[BuildContext], Any], *description: Any
    ) -> None:
        fingerprint = tuple(_value_text(value) for value in description)
        self._add_declaration(_StageHook(stage, fn, fingerprint))

    def fingerprint(self) -> str:
        """Return a content hash of the scenario's structure.

        It covers the declarations in order (groups, links, recorders,
        hooks: their kinds, names, counts, patterns and the qualified names of
        their functions), the environment and communication factories, the
        params model and the scenario-level defaults.  Two processes that
        define the scenario the same way get the same fingerprint; a study
        uses it to detect a scenario that was changed after it was imported.

        :return: 12 hex characters

        Example::

            before = scenario.fingerprint()
            scenario.record("x_squared", ev, lambda a: a.x**2)
            scenario.fingerprint() != before   # True
        """
        declarations = [
            getattr(item, "fingerprint", None) or [type(item).__qualname__]
            for item in self._declarations
        ]
        content = {
            "name": self.name,
            # qualified names without the module, like the factories: the
            # script that plans a study is "__main__" there, "__mp_main__" in
            # spawn workers and "pkg.script" in Slurm tasks
            "params": self._params_model.__qualname__,
            "defaults": to_plain(self._default_layers, default=repr),
            "environment": _callable_name(self._environment_factory),
            "communication": _callable_name(self._communication_factory),
            "declarations": to_plain(declarations, default=repr),
        }
        if self._derived_time:
            content["derived_time"] = {
                key: _spec_text(source) for key, source in self._derived_time.items()
            }
        return content_hash(content)

    def _add_declaration(self, declaration: Any) -> None:
        """Append a declaration: an object with ``apply(stage, root_ctx)``.

        Every declaration takes part in each pass of :data:`STAGES` in
        declaration order; kits plug in here.
        """
        self._declarations.append(declaration)

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
        """Declare a group of *count* entities built by ``factory(e) -> Agent``.

        :param name: the group name (no ``.`` or ``/``)
        :param count: an int, a dotted params path (``"ev.n"``) or ``fn(p) ->
            int``; ``0`` gives an empty group
        :param factory: ``factory(e)`` returning a mango ``Agent`` (``e`` is an
            :class:`EntityContext`); omit it to use ``group`` as a decorator
        :param install: ``install(e) -> dict``, evaluated *before* the factory;
            the dict becomes ``e.attrs`` and is passed as install keyword
            arguments to ``world.register`` (environment ``install``)
        :param position: ``position(e) -> Position`` applied with
            ``environment.space.move`` after registration
        :param naming: entity names, a format string with ``{group}`` and
            ``{index}`` or ``naming(e) -> str``; default ``"{group}_{index}"``,
            and exactly the group name for a literal ``count=1``
        :return: a :class:`GroupRef` (or a decorator returning one)

        Example::

            coordinator = scenario.group("coordinator", 1, lambda e: Coordinator())

            @scenario.group("ev", "ev.n", install=lambda e: {"bus": e.index})
            def ev(e):
                return EVAgent(soc=e.p.ev.soc.draw(e.rng))
        """
        if factory is None:
            check_group_options(name, count, no_factory, install, position, naming)
            self._undecorated[name] = count

            def decorate(fn: Callable) -> GroupRef:
                self._undecorated.pop(name, None)
                return self.group(
                    name, count, fn, install=install, position=position, naming=naming
                )

            return decorate
        check_group_options(name, count, factory, install, position, naming)
        if name in self._group_names:
            raise ScenarioError(
                f"Group {name!r} is declared twice in scenario {self.name!r}; group "
                "names must be unique."
            )
        self._group_names.add(name)
        self._declare(
            "build",
            lambda ctx: ctx.group(
                name, count, factory, install=install, position=position, naming=naming
            ),
            "group",
            name,
            count,
            factory,
            install,
            position,
            naming,
        )
        return GroupRef(name)

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
        """Declare a link: a mango topology with ``tid=name`` between two groups.

        Agents query it with ``self.neighbors(tid=name)``.  Several
        declarations with the same name merge into one topology.  Undirected
        links let both ends see each other; with ``directed=True`` only the
        source sees the target.  ``ctx.relink(name, pairs)`` replaces the
        edges while the run is running (dynamic neighbourhoods).

        *delay* and *loss* model the communication along the link: every
        message between two entities the link connects (in the link's
        direction) gets *delay* added to the delay of ``params.comm`` and is
        lost with the extra, independent probability *loss*; draws come from
        the stream of the directed edge, so they do not shift other draws.

        :param name: the link name and topology id (never prefixed)
        :param source: source group (:class:`GroupRef` or name, e.g. ``"grid.hh"``)
        :param target: target group; omit it to link within *source*
        :param pattern: a built-in pattern name (``"one_to_one"``/``"zip"``,
            ``"many_to_one"``, ``"one_to_many"``, ``"all_to_all"``,
            ``"complete"``, ``"ring"``, ``"line"``), a factory such as
            ``patterns.random(per_target=(0, 2))``, ``patterns.by_attr("bus")``,
            ``patterns.small_world(k=4, p=0.1)`` or ``patterns.reverse("line")``,
            a callable ``(src, dst, rng) -> pairs`` (see
            :mod:`mango.scenario.patterns` for the argument types), or a spec
            resolved per run from the parameters: a parameter path
            (``"net.topology"``), a factory mapping with ``$path`` arguments
            (``{"small_world": {"k": "$net.k", "p": "$net.p"}}``) or a
            one-argument function ``fn(p) -> pattern``
        :param where: optional filter ``where(src_entity, dst_entity) -> bool``
        :param directed: directed edges: only the source of each pair sees the
            target (``line``/``ring`` pairs run in index order ``e_i ->
            e_(i+1)``)
        :param delay: extra message delay along the link: seconds, a duration
            (``"1w"``) or a distribution (``{"choice": [0, "1w", "2w"]}``;
            ``uniform``/``int_uniform`` over durations draw (whole) seconds,
            so they are no lead time in steps: a message sent in a step
            arrives ``1 + ceil(delay / step)`` steps later), or
            read per run from the parameters: ``"$corridor.travel_s"`` (or
            the bare path), ``$path`` values inside a distribution
            (``{"uniform": ["$lead.min_s", "$lead.max_s"]}``) or ``fn(p)``
        :param loss: extra probability in ``[0, 1]`` that a message along the
            link is lost (a number, ``"$path"``/a parameter path or ``fn(p)``)

        Example::

            scenario.link("dispatch", ev, coordinator, pattern="many_to_one")
            scenario.link("peers", ev, pattern=patterns.random(per_target=(0, 2)))
            # weekly steps, lead time L in {1, 2, 3} weeks: delay = (L - 1) * step
            scenario.link("shipping", retailer, wholesaler, pattern="one_to_one",
                          delay={"choice": [0, "1w", "2w"]})
            scenario.link("overlay", node, pattern="net.topology")   # per run
            scenario.link("platoon", lane, pattern="line", directed=True,
                          delay="$corridor.travel_s")                # per run
        """
        resolved = check_link_options(
            name, source, target, pattern, where, directed, model=self.params_model
        )
        comm = check_link_comm(name, delay, loss, model=self.params_model)
        description = [
            "link",
            name,
            source,
            target,
            _spec_text(resolved)
            if depends_on_params(resolved)
            else pattern_name(resolved),
            where,
            directed,
        ]
        if isinstance(comm, DeferredLinkComm):
            description.append(comm.fingerprint)
        elif comm is not None:
            description.append(comm.model_dump(mode="json"))
        self._declare(
            "wire",
            lambda ctx: ctx.link(
                name,
                source,
                target,
                pattern=resolved,
                where=where,
                directed=directed,
                delay=None if comm is None else comm.delay,
                loss=None if comm is None else comm.loss,
            ),
            *description,
        )

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
        """Record one value per agent of *group* after every step (and at t=0).

        The row at ``t > 0`` is the state after the step that ended at ``t``
        (agents computed it in ``on_step`` at the step start); the row at
        ``t = 0`` is the initial state.  Records with the same name on
        different groups form one variable (pass a list of groups to declare
        them at once); the same name twice on one group is an error.

        *aggregate* reduces the members' values at each sample to one row
        per group (``group`` and ``entity`` are the group name):
        ``"sum"``, ``"mean"``, ``"min"``, ``"max"`` (missing values are
        skipped) or ``fn(values)``; ``"count"`` writes one row per category
        instead, with the category as ``entity`` (categories that appear
        later count 0 before), so ``result.wide(name)`` is a time x category
        table.  A function may also return ``{label: value}`` (one row per
        label).

        *window* (needs *every*) evaluates the recorder at every step and
        writes, at each multiple of *every*, the ``"max"``, ``"min"``,
        ``"mean"`` (weighted by the step lengths), ``"sum"`` or ``"last"``
        of the samples in ``(t - every, t]``; the row at ``t = 0`` is the
        initial value.  Samples of ``record: false`` phases are left out; a
        run that ends between two multiples drops the incomplete window.
        Without *window*, *every* takes snapshots.

        *kind* says what a value is, so that :meth:`RunResult.integrate
        <mango.scenario.RunResult.integrate>`, :meth:`RunResult.time_average
        <mango.scenario.RunResult.time_average>` and windows weight it with
        the right interval:

        - ``"flow"``: the value describes the interval that just ended
          (computed in ``on_step`` for the step, e.g. a power); the row at
          ``t`` covers ``[t_prev, t)``.
        - ``"state"``: a level that holds until it changes (a queue length,
          an inventory, an opinion changed in message handlers, tasks or
          events); the row at ``t`` holds on ``[t, t_next)``.  A state
          recorder also writes a row at the start of every recorded phase
          (after a ``record: false`` warm-up), its rows belong to the phase
          that starts at their time, and ``window="mean"`` is the
          time-weighted mean of the levels held during the window
          (``"sum"`` is refused).

        Undeclared recorders behave like flows, but ``integrate`` and
        ``time_average`` ask for the kind instead of guessing.

        :param name: the variable name in the results
        :param group: the group, or a list of groups
        :param fn: ``fn(agent) -> value``, or ``fn(agent, ctx) -> value`` to
            reach the run (``ctx`` is the run's
            :class:`~mango.scenario.BuildContext`; ``ctx.space`` is the
            environment's space, so ``lambda a, ctx: ctx.space.location(a).x``
            records positions)
        :param attr: alternatively a (dotted) agent attribute, e.g. ``"soc_kwh"``
        :param every: record only at offsets that are multiples of this
            duration (e.g. ``"1h"``); with a fixed step it must be a multiple
            of the step, in event mode the run also stops at these offsets.
            A parameter path (``"market.period_s"``) or ``fn(p)`` reads it from
            the run's parameters.
        :param aggregate: reduce across each group: ``"count"``, ``"sum"``,
            ``"mean"``, ``"min"``, ``"max"`` or ``fn(values)``
        :param window: reduce over each ``every`` window: ``"last"``,
            ``"sum"``, ``"mean"``, ``"min"`` or ``"max"``
        :param kind: ``"flow"`` or ``"state"`` (see above), or ``None``
        :param categories: with ``aggregate="count"``, the categories that
            always get a row (0 while absent), in this column order; other
            values that occur are appended after them

        Example::

            scenario.record("soc_kwh", ev, attr="soc_kwh", kind="state")
            scenario.record("power_kw", ev, attr="power_kw", kind="flow")
            scenario.record("queue_len", station, attr="n_waiting", kind="state")
            scenario.record("power_kw", ev, lambda a: a.power_kw, every="1h")
            scenario.record("order_qty", [retailer, wholesaler, factory], attr="order_qty")
            scenario.record("price", market, attr="price", every="market.period_s")
            scenario.record("state", person, attr="state", aggregate="count",
                            categories=["S", "E", "I", "R"])
            scenario.record("infected", person, lambda a: a.state == "I", aggregate="sum")
            scenario.record("queue_max", lanes, attr="queue", every="1min", window="max")
            scenario.record("x", birds, lambda a, ctx: ctx.space.location(a).x)
        """
        getter, spec = check_record_options(
            name, group, fn, attr, every, aggregate, window
        )
        kind = check_kind(kind, window, f"record({name!r})")
        labels = check_categories(categories, aggregate, f"record({name!r})")
        self._check_every_path(f"record({name!r})", spec)
        groups = (
            [item.name if isinstance(item, GroupRef) else item for item in group]
            if isinstance(group, list | tuple)
            else group
        )
        reduce = (
            ()
            if aggregate is None and window is None
            else (
                aggregate_text(aggregate),
                window,
            )
        )  # only when used, so fingerprints of other recorders stay
        extra = (
            ()
            if kind is None and labels is None
            else (f"kind={kind}", f"categories={list(labels or ())}")
        )
        self._declare(
            "record",
            lambda ctx: ctx.record(
                name,
                group,
                getter,
                every=spec,
                aggregate=aggregate,
                window=window,
                kind=kind,
                categories=labels,
            ),
            "record",
            name,
            groups,
            attr if fn is None else fn,
            every_text(spec),
            *reduce,
            *extra,
        )

    def _check_every_path(self, what: str, spec: Any) -> None:
        if isinstance(spec, str):
            try:
                check_path(self.params_model, spec)
            except ParamsError as exc:
                raise ScenarioError(
                    f"{what}: every {spec!r} is neither a duration (e.g. '1h' or 900) "
                    f"nor a parameter path ({exc})."
                ) from None

    def record_world(
        self,
        name: str,
        fn: Callable,
        *,
        every: Any = None,
        entity: str = "world",
        window: str | None = None,
        kind: str | None = None,
    ) -> None:
        """Record a world-level value after every step; ``fn()`` or ``fn(ctx)``.

        :param name: the variable name in the results
        :param fn: zero-argument function, or one taking the :class:`BuildContext`
        :param every: record only at multiples of this duration, a
            parameter path or ``fn(p)`` (see :meth:`record`)
        :param entity: the owner name in the results (``group`` and ``entity``
            columns), default ``"world"``
        :param window: with *every*, the ``"max"``, ``"min"``, ``"mean"``,
            ``"sum"`` or ``"last"`` of the samples in ``(t - every, t]``
            (see :meth:`record`)
        :param kind: ``"flow"`` (describes the interval that ended at the
            row) or ``"state"`` (a level that holds from the row on), see
            :meth:`record`

        Example::

            scenario.record_world("n_msgs", lambda ctx: ctx.world.messages_delivered)
            scenario.record_world("peak_kw", lambda: feeder.import_kw, every="1h", window="max")
        """
        spec = check_record_world_options(name, fn, every, entity, window)
        kind = check_kind(kind, window, f"record_world({name!r})")
        self._check_every_path(f"record_world({name!r})", spec)
        self._declare(
            "record",
            lambda ctx: ctx.record_world(
                name, fn, every=spec, entity=entity, window=window, kind=kind
            ),
            "record_world",
            name,
            fn,
            every_text(spec),
            entity,
            *(() if window is None else (window,)),
            *(() if kind is None else (f"kind={kind}",)),
        )

    def metric(self, name: str, fn: Callable | None = None) -> Any:
        """Declare a run-level metric: ``fn(ctx)`` (or ``fn()``) evaluated once per run.

        It is computed when the result is collected (after the last step, or
        at the stop of a run ended early), with the agents still in place,
        and stored with the run like values set by :meth:`ctx.set_metric
        <mango.scenario.BuildContext.set_metric>`: ``result.metrics[name]``,
        ``metrics.json`` in the run folder and a column of
        :func:`~mango.scenario.load_study`.  Outcome functions can read
        ``run.metrics``, so they stay recomputable from disk, unlike
        outcomes that read ``run.agent(...)``.  Usable as decorator.

        :param name: the metric name
        :param fn: ``fn(ctx)`` returning a number, bool, string, ``None`` or
            plain list/dict
        :return: *fn*, or a decorator when *fn* is omitted

        Example::

            @scenario.metric("unserved_kwh")
            def unserved(ctx):
                return sum(ev.unserved_kwh for ev in ctx.agents("ev"))

            scenario.run(seed=1).metrics["unserved_kwh"]
        """
        if fn is None:
            return lambda function: self.metric(name, function)
        check_name(name, "A metric name")
        check_callable(fn, f"metric({name!r}) fn", optional=False)
        self._declare("build", lambda ctx: ctx.metric(name, fn), "metric", name, fn)
        return fn

    def on_build(self, fn: Callable[[BuildContext], Any]) -> Callable:
        """Run ``fn(ctx)`` after agents, links and recorders exist, before the world starts.

        An escape hatch for wiring the builder does not cover.  Usable as
        decorator; returns *fn*.

        Example::

            @scenario.on_build
            def mark_leader(ctx):
                ctx.agents("coordinator")[0].leader = True
        """
        check_callable(fn, "on_build hook", optional=False)
        self._declare("build", lambda ctx: ctx.on_build(fn), "on_build", fn)
        return fn

    def stop_when(self, fn: Callable | None = None, *, every: Any = None) -> Any:
        """End runs early once ``fn(ctx)`` (or ``fn()``) returns something true.

        The condition is checked after every step (after the recorders, so
        the last row is the state it saw); with *every* only at offsets that
        are multiples of it (event-driven runs then also stop there).  A
        string result is the stop reason, any other true value stops with the
        reason ``"stop_when <function name>"``.  The run ends at that step:
        the time series end there (no padding to the horizon), and
        ``result.stats`` records ``stopped_early``, ``stop_time_s`` and
        ``stop_reason`` (also in the manifest, so outcomes and studies can
        use them).  Usable as decorator; several conditions may be declared.

        :param fn: ``fn(ctx) -> bool | str`` with the run's
            :class:`~mango.scenario.BuildContext`
        :param every: check only at multiples of this duration (e.g. ``"1h"``)
        :return: *fn*, or a decorator when *fn* is omitted

        Example::

            @scenario.stop_when(every="10s")
            def converged(ctx):
                values = [a.value for a in ctx.agents("node")]
                return max(values) - min(values) < 1e-3 and "converged"

            result = scenario.run(seed=1)
            result.stats["stop_time_s"], result.stats["stop_reason"]
        """
        if fn is None:

            def decorate(function: Callable) -> Callable:
                return self.stop_when(function, every=every)

            return decorate
        spec = check_stop_when(fn, every)
        self._check_every_path("stop_when", spec)
        self._declare(
            "build",
            lambda ctx: ctx.stop_when(fn, every=spec),
            "stop_when",
            fn,
            every_text(spec),
        )
        return fn

    def event(
        self,
        at: Any,
        *,
        until: Any = None,
        name: str | None = None,
        comm: Mapping[str, Any] | None = None,
        emit: Any = None,
        emit_to: str | list[str] | dict | None = None,
        action: Callable | str | None = None,
        every: Any = None,
        count: int | None = None,
    ) -> Any:
        """Declare an event of every run (like an item of ``params.events``).

        At *at* the run applies *comm* (communication settings, until
        *until*), sends *emit* to all agents as a
        :class:`~mango.scenario.ScenarioEvent` (``on_global_event``) or to the
        entities *emit_to* (``on_agent_event``), and calls ``action(ctx)``.
        With *until*, *emit* is sent again at *until* (``event.stage ==
        "end"``).  With *every* the event repeats (*count* times, default:
        until the horizon).
        Unlike params events, *emit* may be any object and *action* any
        callable, lambdas included; the declaration is part of
        :meth:`fingerprint`.  Without *comm*, *emit* and *action* it returns a
        decorator that makes the decorated function the action.

        :param at: offset (``"36h"``) or absolute time (``"2024-06-02T12:00"``);
            like *until* and *every* it may also be read per run from the
            parameters: ``"$market.open"`` (or the bare parameter path) or
            ``fn(p)``
        :param until: end of the *comm* override (default: the end of the run)
        :param name: optional name (plan output, logs, ``ScenarioEvent.name``)
        :param comm: communication settings while active, e.g.
            ``{"loss_percent": 1.0}``
        :param emit: payload sent to the agents
        :param emit_to: entity name, group name, a list of these, or a
            selector ``{"group": "person", "by_attr": {"age_group": "young"}}``
            (entities whose install attributes match) to send *emit* to; a
            targeted *emit* always goes to ``on_agent_event`` (default: all
            agents, through ``on_global_event``)
        :param action: ``fn(ctx)`` (or ``fn()``) called at *at*, or an import
            reference
        :param every: repeat period, e.g. ``"1d"``
        :param count: number of occurrences with *every*
        :return: ``None``, or the decorator
        :raises ScenarioError: for invalid arguments (time points against the
            horizon and ``emit_to`` are checked when a run is planned or built)

        Example::

            scenario.event("2024-06-02T12:00", until="2024-06-02T14:00",
                           name="comm_outage", comm={"loss_percent": 1.0})
            scenario.event("36h", emit={"kind": "grid_fault", "bus": 7})

            @scenario.event("1d")
            def switch_tariff(ctx):
                for agent in ctx.agents("ev"):
                    agent.tariff = "night"

            scenario.event(0, every="market.period_s", name="clear",
                           emit={"kind": "clear"})            # period from params
        """
        if comm is None and emit is None and action is None:

            def decorate(fn: Callable) -> Callable:
                check_callable(fn, "The event action", optional=False)
                self.event(
                    at,
                    until=until,
                    name=name,
                    comm=comm,
                    emit=emit,
                    emit_to=emit_to,
                    action=fn,
                    every=every,
                    count=count,
                )
                return fn

            return decorate
        declaration = structure_event(
            at,
            until=until,
            name=name,
            comm=comm,
            emit=emit,
            emit_to=emit_to,
            action=action,
            every=every,
            count=count,
        )
        declaration.check_paths(self.params_model)
        self._add_declaration(declaration)
        return None

    # -- declaration stage --------------------------------------------------------------

    def _declaration_stage(self, params: BaseModel, seeds: SeedTree) -> RunState:
        if self._undecorated:
            name, count = next(iter(self._undecorated.items()))
            raise ScenarioError(
                f"Group {name!r} of scenario {self.name!r} was declared without a "
                f"factory and the returned decorator was never applied. Pass the "
                f"factory as third argument, e.g. scenario.group({name!r}, "
                f"{'1' if callable(count) else repr(count)}, factory), or decorate "
                "a function with @scenario.group(...)."
            )
        state = RunState(self, params, seeds)
        for stage in STAGES:
            state.stage = stage
            for declaration in self._declarations:
                declaration.apply(stage, state.root)
        if state.undecorated:
            name = next(iter(state.undecorated))
            raise ScenarioError(
                f"Group {name!r} was declared with ctx.group() without a factory and "
                "the returned decorator was never applied; pass the factory as third "
                "argument."
            )
        state.stage = "resolve"
        resolve_links(state)
        resolve_records(state)
        state.schedule = Schedule(
            params,
            state.events,
            entities=state.entity_groups,
            groups={name: spec.names for name, spec in state.groups.items()},
        )
        if state.records and not any(span.record for span in state.schedule.phases):
            state.schedule.warnings.append(
                "No phase records (every phase has record: false), so every recorder "
                "produces 0 rows; drop record: false from the phases to measure."
            )
        state.stage = "done"
        return state

    def plan(self, params: Any = None, *, overrides: Any = None) -> ScenarioPlan:
        """Resolve params and run the declaration stage only (no world, no agents).

        :param params: as for :meth:`resolve_params`
        :param overrides: as for :meth:`resolve_params`
        :return: the :class:`ScenarioPlan` (counts, entity names, links with
            exact edge counts where deterministic, recorders, phases and
            events with the times they apply at)
        :raises ParamsError: for invalid params
        :raises ScenarioError: for invalid declarations (unknown groups,
            duplicate names, pattern size mismatches, ``emit_to`` naming no
            entity, ...)

        With a fixed step, phase and event times between step boundaries
        apply at the next boundary; ``plan`` issues a ``UserWarning`` for each
        (also listed in ``plan.warnings``), and likewise for profiles whose
        dates do not cover the run or that a warm-up would shift (see
        :mod:`mango.scenario.profiles`).

        Example::

            print(scenario.plan(overrides=["ev.n=20"]))
        """
        resolved = self.resolve_params(params, overrides=overrides)
        return self._plan_resolved(resolved, stacklevel=3)

    def _plan_resolved(
        self, resolved: BaseModel, *, stacklevel: int = 2
    ) -> ScenarioPlan:
        """:meth:`plan` for already resolved params."""
        seed = getattr(resolved, "seed", None)
        state = self._declaration_stage(
            resolved, SeedTree(seed if seed is not None else 0)
        )
        schedule = state.schedule
        from .profiles import profile_warnings

        plan_warnings = [*schedule.warnings, *profile_warnings(resolved)]
        for message in plan_warnings:
            warnings.warn(message, UserWarning, stacklevel=stacklevel)
        # ctx.warn() during the declaration already issued its warnings
        plan_warnings += [text for text in state.warnings if text not in plan_warnings]
        placeholders = {
            spec.name: [Entity(name, spec.name, i) for i, name in enumerate(spec.names)]
            for spec in state.groups.values()
        }
        links = []
        for name, specs in links_by_name(state.links).items():
            if all(spec.deterministic for spec in specs):
                graph, _ = compile_link(specs, placeholders, state.seeds)
                edges = graph.number_of_edges()
            else:  # custom patterns and filters: exact when they use neither
                edges = count_edges(specs, placeholders, state.seeds)
            declarations = [
                {
                    "source": s.source,
                    "target": s.target,
                    "pattern": s.pattern_name,
                    "where": None
                    if s.where is None
                    else getattr(s.where, "__name__", type(s.where).__name__),
                }
                for s in specs
            ]
            comm = next((spec.comm for spec in specs if spec.comm is not None), None)
            links.append(
                LinkPlan(
                    name,
                    specs[0].directed,
                    declarations,
                    edges,
                    None if comm is None else comm.model_dump(mode="json"),
                )
            )
        return ScenarioPlan(
            scenario=self.name,
            params=resolved,
            time=resolved.time,
            groups=[
                GroupPlan(spec.name, spec.count, list(spec.names))
                for spec in state.groups.values()
            ],
            links=links,
            recorders=[
                RecordPlan(
                    rec.name,
                    rec.kind,
                    rec.group,
                    rec.entity,
                    rec.every,
                    aggregate_text(rec.aggregate),
                    rec.window,
                    rec.value_kind,
                    None if rec.categories is None else list(rec.categories),
                )
                for rec in state.records
            ],
            kits=[
                KitPlan(
                    prefix,
                    type(component).__name__,
                    f"{type(component).__module__}:{type(component).__qualname__}",
                    to_plain(
                        getattr(resolved, prefix).model_dump(mode="json")
                        if component.has_params
                        else {}
                    ),
                )
                for prefix, component in self._kits
            ],
            resources={name: owner for name, (_, owner) in state.resources.items()},
            behaviors=[
                f"{type(behavior).__name__} ({prefix or 'scenario'})"
                for prefix, behavior in state.behaviors
            ],
            phases=schedule.phase_plans(),
            events=schedule.event_plans(),
            warnings=plan_warnings,
            communication=self._communication_text(resolved),
            environment=self._environment_text(),
            stop_when=[
                condition.label
                + (
                    ""
                    if condition.every is None
                    else f" every {format_duration(condition.every)}"
                )
                for condition in state.stop_conditions
            ],
        )

    def factory_refs(self, params: Any = None) -> dict[str, str | None]:
        """The communication and environment factories as references (``None`` if unset).

        ``"module:qualname"`` for importable functions, else the qualified
        name; written into run manifests (``manifest["factories"]``).  With
        the run's *params*, a params-level ``comm: {kind: custom, factory:
        "module:fn"}`` is named when no :meth:`communication` factory
        replaces it (``"radio:make (comm.factory)"``).

        Example::

            scenario.factory_refs()   # {"communication": "swarm:radio", "environment": None}
        """
        communication = _factory_ref(self._communication_factory)
        comm = getattr(params, "comm", None)
        if (
            communication is None
            and getattr(comm, "kind", None) == "custom"
            and getattr(comm, "factory", None)
        ):
            communication = f"{comm.factory} (comm.factory)"
        return {
            "communication": communication,
            "environment": _factory_ref(self._environment_factory),
        }

    def _communication_text(self, params: BaseModel) -> str:
        name = _callable_name(self._communication_factory)
        if name is not None:
            return f"factory {name} (overrides comm.kind; ctx.comm is passed in)"
        comm = params.comm
        if comm.kind == "custom":
            return f"custom factory {comm.factory} (comm.kind)"
        if comm.kind == "ideal":
            return "ideal (no loss, no delay)"
        delay = comm.delay_s
        delay_text = (
            format_duration(delay) if isinstance(delay, int | float) else repr(delay)
        )
        return f"simple, loss {comm.loss_percent:g}, delay {delay_text}"

    def _environment_text(self) -> str:
        name = _callable_name(self._environment_factory)
        return "DefaultEnvironment" if name is None else f"factory {name}"

    # -- running ---------------------------------------------------------------------

    def build(
        self,
        params: Any = None,
        *,
        seed: Any = None,
        overrides: Any = None,
        trace: Any = None,
        record_messages: bool = False,
    ) -> ScenarioRun:
        """Resolve params and seed and return a :class:`~mango.scenario.ScenarioRun`.

        The world is built when the run is entered (``async with run:``).

        :param params: ``None``, a params instance, a dict, a preset path or a
            list of these
        :param seed: run seed (int, :class:`~mango.util.seeding.SeedTree` or a
            seed dict such as ``result.seed``); takes precedence over
            ``params.seed``; without both, fresh entropy is drawn and recorded
        :param overrides: ``["a.b=value", ...]`` or ``{"a.b": value}``
        :param trace: passed to :func:`~mango.simulation.world.create_world`
        :param record_messages: keep every delivered message with its content
            in ``run.world.recorded_messages`` (default: only count them in
            ``world.messages_delivered``, so memory does not grow with the
            number of messages)
        :return: the run

        Example::

            run = scenario.build("presets/base.yaml", seed=7)
            async with run:
                await run.run_until("12h")
                print(run.ctx.agents("ev")[0].soc_kwh)
                await run.run()
            result = run.result()
        """
        from .runtime import ScenarioRun

        resolved = self.resolve_params(params, overrides=overrides)
        return ScenarioRun(
            self,
            resolved,
            _resolve_seed(seed, resolved),
            trace=trace,
            record_messages=record_messages,
        )

    async def run_async(
        self,
        params: Any = None,
        *,
        seed: Any = None,
        overrides: Any = None,
        trace: Any = None,
        until: Any = None,
        record_messages: bool = False,
    ) -> RunResult:
        """Build, run to the horizon and return the :class:`~mango.scenario.RunResult`.

        Arguments as for :meth:`run`.

        Example::

            result = await scenario.run_async(seed=7)
        """
        run = self.build(
            params,
            seed=seed,
            overrides=overrides,
            trace=trace,
            record_messages=record_messages,
        )
        return await run.execute(until=until)

    def run(
        self,
        params: Any = None,
        *,
        seed: Any = None,
        overrides: Any = None,
        trace: Any = None,
        until: Any = None,
        record_messages: bool = False,
    ) -> RunResult:
        """Run the scenario synchronously to the horizon (see :meth:`build`).

        Works in scripts and inside a running event loop (e.g. Jupyter), where
        the run executes in a helper thread with its own loop.

        *until* stops the run early for a quick test without changing the
        scenario: the horizon, and so the validation of phases and events
        against it, stays as it is; events after *until* never fire.  The
        result is marked truncated (``stats["stop_reason"] == "until"``).

        :param params: as for :meth:`build`
        :param seed: as for :meth:`build`
        :param overrides: as for :meth:`build`
        :param trace: as for :meth:`build`
        :param until: stop at this offset (``"10min"``) or absolute time
            instead of the horizon (fixed step: the last step boundary at or
            before it)
        :param record_messages: as for :meth:`build` (the result keeps the
            world, so ``result.world.recorded_messages`` holds the messages)

        Example::

            result = scenario.run("presets/base.yaml", seed=7, overrides=["ev.n=20"])
            result.ts("soc_kwh", entity="ev_0")
            smoke = scenario.run("presets/base.yaml", seed=1, until="2h")
        """
        from .runtime import run_sync

        run = self.build(
            params,
            seed=seed,
            overrides=overrides,
            trace=trace,
            record_messages=record_messages,
        )
        return run_sync(lambda: run.execute(until=until))

    # -- tooling -----------------------------------------------------------------------

    def to_script(
        self,
        params: Any = None,
        *,
        seed: Any = None,
        overrides: Any = None,
        lean: bool | None = None,
    ) -> GeneratedScript:
        """Return a plain mango script that builds and runs one run of this scenario.

        The script writes out what the scenario layer does: ``create_world``
        with the seed and calendar, the communication simulation, one
        registration loop per group calling the group's factory with an
        ``e`` that offers what :class:`EntityContext` offers, every link as
        explicit topology edges, the recorders and the step loop.  It draws
        from the same random streams, so it records the same values as
        ``scenario.run(params, seed=seed)``.

        Two forms (see :mod:`mango.scenario.script`): the **lean** one
        (``create_world``, registration loops, topology, recorders and a
        plain step loop) is written when the scenario uses no kits, phases,
        events, communication changes, link delay/loss, partitions,
        ``stop_when``/``ctx.stop``, ``ctx.spawn``/``retire``/``relink``,
        behaviors or resources and steps with a fixed size; the **full** one
        also applies phases, events, communication switches, link delay/loss
        and partitions, kit scoping and what agents call while the run steps
        (``ctx.spawn``, ``ctx.retire``, ``ctx.relink``, ``ctx.link_pairs``,
        ``ctx.stop``, ``ctx.log``, ``ctx.set_metric``, ``ctx.phase``).  The
        ``ctx``/``e`` methods agents call are included when the scenario's
        modules call them; a left-out one raises an ``AttributeError`` naming
        it.  :meth:`GeneratedScript.write
        <mango.scenario.script.GeneratedScript.write>` saves the script so
        that it runs from any folder.

        Module-level functions are imported.  Lambdas and local functions
        are written out when their closure variables hold literals,
        parameter models (a kit's ``ctx.p`` becomes ``params.<prefix>``),
        the kit's ``ctx`` or importable objects.  Objects kits create
        (behaviors, the space, resources) are written out as constructor
        calls when their class is importable and their state is what the
        constructor makes of the stored arguments.  Everything else becomes
        a clearly marked ``Placeholder`` (listed in the script's docstring
        and in ``script.placeholders``) that raises when the script reaches
        it.  Links with ``where=`` or attribute-based patterns are evaluated
        by building the agents once (without starting them).

        :param params: as for :meth:`resolve_params`
        :param seed: the run seed (as for :meth:`build`): an int or a seed
            dict as a run spec stores it; without it ``params.seed`` is
            used, else fresh entropy is drawn and written into the script
        :param overrides: dotted overrides, as for :meth:`resolve_params`
        :param lean: ``None`` (default): the lean form when possible;
            ``True``: require it; ``False``: always the full form
        :return: the script source, a ``str`` with the attributes
            ``placeholders`` (tuple of descriptions) and ``lean``
        :raises ParamsError: for invalid params
        :raises ScenarioError: for invalid declarations, or ``lean=True``
            when the scenario needs the full form

        Example::

            script = scenario.to_script("presets/base.yaml", seed=7)
            assert not script.placeholders
            Path("ev_script.py").write_text(script)
            # python ev_script.py values.csv

            run = load_study("results/fleet").run(22)   # a study run
            Path("run22.py").write_text(scenario.to_script(run.config, seed=run.seed))
        """
        from .script import generate_script

        return generate_script(self, params, seed=seed, overrides=overrides, lean=lean)
