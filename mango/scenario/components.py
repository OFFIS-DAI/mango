"""
Scenario components ("kits"): reusable bundles of parameters, groups, links,
behaviors and recorders.

A kit subclasses :class:`ScenarioComponent`, declares its typed ``Params``
and implements any of the hooks ``build`` (groups, behaviors, space,
resources), ``wire`` (links) and ``record`` (recorders).  A scenario uses it
under a prefix; the kit's parameters then live at ``params.<prefix>`` (so
presets and overrides reach them as ``grid.pv_share=0.8``) and its groups are
qualified by the prefix (``grid.hh``, entities ``grid.hh_0`` ...).

Every hook gets a :class:`ComponentContext` (a
:class:`~mango.scenario.BuildContext` scoped to the prefix).  All ``build``
hooks run first (scenario-level declarations take part in declaration
order), then all ``wire`` hooks, then all ``record`` hooks; group factories
run later still, once the world exists.  So resources a kit provides in
``build`` are visible to every ``wire``/``record`` hook and every factory.

Example::

    from pydantic import BaseModel
    from mango.scenario import Scenario, ScenarioComponent, Uniform

    class Neighbourhood(ScenarioComponent):
        class Params(BaseModel):
            n_buses: int = 12
            n_households: int = 30
            pv_peak_kw: Uniform = Uniform(3.0, 8.0)

        def build(self, ctx):
            p = ctx.p
            feeder = ctx.set_behavior(FeederBehavior(p.n_buses))
            homes = ctx.group("hh", p.n_households, lambda e: HouseholdAgent(
                pv_peak_kw=p.pv_peak_kw.draw(e.rng), load_profile=ctx.profile("h0")),
                install=lambda e: {"bus": e.index % p.n_buses})
            ctx.record("pv_kw", homes, lambda a: a.pv_kw)
            ctx.record_world("import_kw", lambda: feeder.import_kw)

    scenario = Scenario("ev_feeder", params=Params)
    scenario.use(Neighbourhood(n_households=40), prefix="grid")
    scenario.link("reports", "grid.hh", coordinator, pattern="many_to_one")
"""

from __future__ import annotations

import importlib.metadata
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, Field, ValidationError, create_model

from mango.simulation import Behavior, Space

from ._util import (
    add_note,
    canonical_json,
    did_you_mean,
    import_ref_or_raise,
    to_plain,
)
from .configfiles import get_path
from .context import BuildContext
from .errors import ParamsError, ScenarioError
from .params import _normalize, _unknown_paths, _unknown_reason

if TYPE_CHECKING:
    from mango.util.seeding import SeedTree

    from .context import RunState

__all__ = [
    "ScenarioComponent",
    "ComponentContext",
    "CompositeBehavior",
    "register_component",
    "resolve_component",
    "component_kinds",
    "ENTRY_POINT_GROUP",
]

#: Entry point group under which packages publish components by kind.
ENTRY_POINT_GROUP = "mango.scenario.components"

_MISSING = object()

# the marker the world sets on components whose rng it bound (see
# mango.simulation.world): a rebinding replaces only generators it set itself
_BOUND_RNG_ATTR = "_mango_world_bound_rng"


def _snake_case(name: str) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name)
    return text.lower()


#: Prefixes a kit may not use: roots of the run's random streams with
#: sub-streams (a kit's ``ctx.rng(name)`` is ``<prefix>/<name>``, so a kit
#: ``link`` would draw the stream of link ``name``) and the owner names of
#: scenario-level behaviors and world rows.
RESERVED_PREFIXES = frozenset(
    {"agent", "behavior", "design", "link", "scenario", "world"}
)


PREFIX_HINT = (
    "Use another prefix (prefix=... in Python, another key under 'components:' "
    "in a scenario file)"
)


def _check_prefix(prefix: Any, what: str) -> str:
    if (
        not isinstance(prefix, str)
        or not prefix.isidentifier()
        or prefix.startswith("_")
    ):
        raise ScenarioError(
            f"{what} {prefix!r} is not a valid kit prefix: use a Python identifier "
            "that does not start with '_' (it becomes a params field and qualifies "
            "group names), e.g. 'grid'."
        )
    if prefix in RESERVED_PREFIXES:
        raise ScenarioError(
            f"{what} {prefix!r} is reserved: the kit's random streams "
            f"'{prefix}/<name>' would be those the run uses for {prefix!r}. "
            f"{PREFIX_HINT}, e.g. '{prefix}s' or 'my_{prefix}'."
        )
    return prefix


def _class_ref(cls: type) -> str:
    return f"{cls.__module__}:{cls.__qualname__}"


# --------------------------------------------------------------------------- component


def _validate_kit_params(cls: type[ScenarioComponent], values: dict) -> BaseModel:
    model = cls.Params
    label = f"{cls.__name__}()"
    data = _normalize([model], values)
    unknown = [path for path, _ in _unknown_paths([model], data)]
    if unknown:
        lines = [
            f"  {path}: {_unknown_reason(model, path) or 'not a parameter'}"
            for path in unknown
        ]
        known = ", ".join(model.model_fields) or "none"
        raise ParamsError(
            f"{label} got unknown parameters:\n"
            + "\n".join(lines)
            + f"\nParameters of {cls.__name__}.Params: {known}.",
            path=unknown[0],
            source=label,
        )
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        errors = [
            {
                "path": ".".join(str(part) for part in error["loc"]) or "<root>",
                "message": str(error["msg"]).removeprefix("Value error, "),
                "source": label,
            }
            for error in exc.errors()
        ]
        lines = [f"  {item['path']}: {item['message']}" for item in errors]
        raise ParamsError(
            f"Invalid parameters for {label}:\n" + "\n".join(lines),
            path=errors[0]["path"] if errors else None,
            source=label,
            errors=errors,
        ) from None


class ScenarioComponent:
    """Base class of kits: reusable parameters, groups, links, behaviors and recorders.

    "Kit" is the user-facing name of a scenario component: scenarios use
    them with :meth:`Scenario.use <mango.scenario.Scenario.use>` and list
    them in :attr:`Scenario.kits <mango.scenario.Scenario.kits>`, scenario
    files under ``components:``.  The registry *kind* (YAML ``kind:``) and
    the default prefix (``name``) are separate names; the built-in
    ``AgentPopulation`` is kind ``agent_population`` with prefix
    ``population``.

    Subclasses set the class attributes ``name`` (the default prefix;
    default: the class name in snake case, ``EVFleet`` → ``"ev_fleet"``) and
    ``Params`` (a pydantic model; default: no parameters) and override any
    of :meth:`build`, :meth:`wire` and :meth:`record`.

    Keyword arguments of the constructor set kit parameters; they are
    validated against ``Params`` immediately (unknown names fail with a
    did-you-mean suggestion) and become the defaults of ``params.<prefix>``,
    which presets and overrides still change.  Hooks therefore read the
    run's parameters from ``ctx.p``, never from the instance.

    :param params: kit parameters, e.g. ``n_households=40``
    :raises ParamsError: for unknown or invalid parameters

    Example::

        class EVFleet(ScenarioComponent):
            class Params(BaseModel, extra="forbid"):
                n: int = 10
                capacity_kwh: float = 60.0

            def build(self, ctx):
                ctx.group("ev", ctx.p.n, lambda e: EVAgent(e.p.capacity_kwh))

            def wire(self, ctx):
                ctx.link("dispatch", "ev", "coordinator", pattern="many_to_one")

            def record(self, ctx):
                ctx.record("soc_kwh", "ev", attr="soc_kwh")

        scenario.use(EVFleet(n=20))          # prefix "ev_fleet"; override ev_fleet.n=40
    """

    name: ClassVar[str] = "scenario_component"

    class Params(BaseModel, extra="forbid"):
        """Kit parameters (none by default)."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "name" not in cls.__dict__:
            cls.name = _snake_case(cls.__name__)
        else:
            _check_prefix(cls.name, f"{cls.__name__}.name")
        params = cls.__dict__.get("Params", cls.Params)
        if not (isinstance(params, type) and issubclass(params, BaseModel)):
            raise TypeError(
                f"{cls.__name__}.Params must be a pydantic BaseModel subclass, got "
                f"{params!r}."
            )
        if params.__qualname__ == f"{cls.__qualname__}.Params":
            # repr 'SensorField.Params(n=5)', not 'Params(n=5)' next to the
            # scenario's own Params (the class name and schema stay as they are)
            label = f"{cls.__name__}.Params"
            params.__repr_name__ = lambda self: label  # type: ignore[method-assign]

    def __init__(self, **params: Any):
        self._init_kwargs: dict = dict(params)
        self._params = _validate_kit_params(type(self), params)

    def __repr__(self) -> str:
        args = ", ".join(f"{key}={value!r}" for key, value in self._init_kwargs.items())
        return f"{type(self).__name__}({args})"

    @property
    def params(self) -> BaseModel:
        """The kit's default parameters: ``Params`` updated by the constructor arguments.

        A run reads its (possibly overridden) parameters from ``ctx.p``.

        Example::

            Neighbourhood(n_households=40).params.n_households   # 40
        """
        try:
            return self.__dict__["_params"]
        except KeyError:
            raise TypeError(
                f"{type(self).__name__}.__init__ must call super().__init__(**params) "
                "so the kit's parameters are validated."
            ) from None

    @property
    def init_kwargs(self) -> dict:
        """The constructor arguments (part of :meth:`Scenario.fingerprint`)."""
        self.params  # noqa: B018  (fails clearly for a missing super().__init__)
        return dict(self._init_kwargs)

    @property
    def has_params(self) -> bool:
        """True when ``Params`` has fields (only then ``params.<prefix>`` exists)."""
        return bool(type(self).Params.model_fields)

    @property
    def p(self) -> Any:
        raise AttributeError(
            f"{type(self).__name__} has no attribute 'p': read the run's kit "
            "parameters from ctx.p inside build/wire/record (presets and overrides "
            "change them per run); self.params holds only the defaults."
        )

    def param_defaults(self) -> Mapping[str, Any] | None:
        """Scenario-level parameter defaults this kit contributes (default: none).

        Called once by :meth:`Scenario.use`; the result is applied like
        :meth:`Scenario.defaults` (dotted keys or a nested dict), so presets
        and overrides still win.  :class:`~mango.scenario.kits.CommunicationPreset`
        uses it to set ``comm`` defaults.

        Example::

            def param_defaults(self):
                return {"comm.delay_s": 0.001}
        """
        return None

    def build(self, ctx: ComponentContext) -> None:
        """Declare groups, behaviors, the space, resources and kit-local recorders."""

    def wire(self, ctx: ComponentContext) -> None:
        """Declare links (runs after every component's ``build``)."""

    def record(self, ctx: ComponentContext) -> None:
        """Declare recorders (runs after every component's ``wire``)."""


class ComponentContext(BuildContext):
    """The ``ctx`` of a kit's hooks: a :class:`~mango.scenario.BuildContext` scoped to its prefix.

    Within kit ``grid``: ``ctx.p`` is ``params.grid``; ``ctx.group("hh",
    ...)`` declares ``grid.hh`` (entities ``grid.hh_0`` ...); group names in
    ``link``/``record``/``entities`` resolve to the kit's own group
    ``grid.<name>`` when it exists, else as given (``"coordinator"``); ``ctx.rng("x")`` is the stream ``grid/x``;
    ``ctx.record_world`` rows belong to entity ``grid``.  Behaviors
    (:meth:`~mango.scenario.BuildContext.add_behavior`), the space
    (:meth:`~mango.scenario.BuildContext.set_space`), shared resources
    (:meth:`~mango.scenario.BuildContext.provide`/
    :meth:`~mango.scenario.BuildContext.resource`) and profiles
    (:meth:`~mango.scenario.BuildContext.profile`) work as
    on every build context.

    Example::

        def build(self, ctx):
            feeder = ctx.add_behavior(FeederBehavior(ctx.p.n_buses))
            ctx.provide("feeder", feeder)
            ctx.group("hh", ctx.p.n_households, make_household)

        def wire(self, ctx):
            ctx.link("feeder", "hh", pattern="line")       # grid.hh
    """

    def __init__(
        self,
        state: RunState,
        prefix: str = "",
        component: ScenarioComponent | None = None,
    ):
        super().__init__(state, prefix)
        self._component = component

    def __repr__(self) -> str:
        kit = type(self._component).__name__ if self._component is not None else None
        return (
            f"<ComponentContext prefix={self.prefix!r} kit={kit} "
            f"scenario={self._state.scenario.name!r}>"
        )

    @property
    def component(self) -> ScenarioComponent | None:
        """The kit whose hooks receive this context."""
        return self._component

    @property
    def p(self) -> Any:
        """The kit's parameters of this run (``params.<prefix>``)."""
        component = self._component
        if component is not None and not component.has_params:
            return component.params
        return super().p


def _stable_text(value: Any) -> str:
    """A process-independent description (qualified names for callables)."""
    if callable(value):
        return getattr(value, "__qualname__", None) or type(value).__qualname__
    return repr(value)


class _KitDeclaration:
    """A kit used by a scenario: runs the kit's hook of each declaration pass."""

    __slots__ = ("component", "prefix", "fingerprint")

    def __init__(self, component: ScenarioComponent, prefix: str):
        self.component = component
        self.prefix = prefix
        self.fingerprint = (
            "kit",
            type(component).__qualname__,  # no module: see Scenario.fingerprint
            to_plain(component.init_kwargs, default=_stable_text),
            prefix,
        )

    def context(self, state: RunState) -> ComponentContext:
        ctx = state.kit_contexts.get(self.prefix)
        if ctx is None:
            ctx = ComponentContext(state, self.prefix, self.component)
            state.kit_contexts[self.prefix] = ctx
        return ctx

    def apply(self, stage: str, root: BuildContext) -> None:
        hook = getattr(self.component, stage, None)
        if hook is None:
            return
        try:
            hook(self.context(root._state))
        except Exception as exc:
            add_note(
                exc,
                f"in {type(self.component).__name__}.{stage}() of kit {self.prefix!r}",
            )
            hint = kit_params_hint(exc, root._state.params, self.prefix, "ctx")
            if hint:
                add_note(exc, hint)
            raise


def kit_params_hint(
    exc: BaseException, params: Any, prefix: str, accessor: str = "ctx"
) -> str | None:
    """Explain the kit scope of ``ctx.p``/``e.p`` for an AttributeError, or ``None``.

    Inside kit *prefix*, ``ctx.p`` and ``e.p`` are ``params.<prefix>``; a
    scenario-level field (or the prefix itself, as in ``e.p.chain.holding``)
    is not there.

    :param exc: the error raised by a kit stage or a group function of the kit
    :param params: the run's full parameters
    :param prefix: the kit's prefix (``""`` at scenario level: no hint)
    :param accessor: ``"ctx"`` or ``"e"``, as the user wrote it
    :return: the note text, or ``None`` when the error is not about this

    Example::

        kit_params_hint(exc, state.params, "chain", "e")
        # "inside kit 'chain', e.p already is params.chain (the kit's parameters):
        #  write e.p.holding, not e.p.chain.holding"
    """
    if not prefix or not isinstance(exc, AttributeError):
        return None
    name = getattr(exc, "name", None)
    if not name:
        match = re.search(r"has no attribute '(\w+)'", str(exc))
        name = match.group(1) if match else None
    if not name or name not in getattr(type(params), "model_fields", {}):
        return None
    try:
        scoped = get_path(params, prefix)
    except Exception:
        return None
    if name in getattr(type(scoped), "model_fields", {}):
        return None  # a field of the kit too: not a scoping mistake
    owner = getattr(exc, "obj", None)
    if owner is not None and owner is not scoped:
        return None
    if name == prefix.split(".")[0]:
        fields = list(getattr(type(scoped), "model_fields", {}))
        field = fields[0] if fields else "<field>"
        return (
            f"inside kit {prefix!r}, {accessor}.p already is params.{prefix} (the "
            f"kit's parameters): write {accessor}.p.{field}, not "
            f"{accessor}.p.{prefix}.{field}"
        )
    return (
        f"inside kit {prefix!r}, {accessor}.p is params.{prefix} (the kit's "
        f"parameters); read the scenario-level field with {accessor}.params.{name}"
    )


def nested_params_model(
    base: type[BaseModel], kits: Sequence[tuple[str, ScenarioComponent]]
) -> type[BaseModel]:
    """Return *base* extended by one field per kit with parameters.

    Field ``<prefix>`` has the kit's ``Params`` type and the kit's
    :attr:`~ScenarioComponent.params` as default.  Without such kits *base*
    itself is returned.  Equal inputs give the same class within a process,
    and instances pickle by value (they are rebuilt from *base* and the kits'
    ``Params`` classes, which must be importable).

    :param base: the scenario's params model
    :param kits: ``(prefix, component)`` pairs in use order
    :return: the effective params model (same class name and module as *base*)
    """
    specs = tuple(
        (prefix, type(component).__name__, type(component).Params, component.params)
        for prefix, component in kits
        if component.has_params
    )
    if not specs:
        return base
    return _effective_model(base, specs)


_EFFECTIVE_MODELS: dict[tuple, type[BaseModel]] = {}


def _effective_model(base: type[BaseModel], specs: tuple) -> type[BaseModel]:
    key = (
        base,
        tuple(
            (prefix, kit, params_cls, canonical_json(default.model_dump(mode="json")))
            for prefix, kit, params_cls, default in specs
        ),
    )
    model = _EFFECTIVE_MODELS.get(key)
    if model is not None:
        return model
    fields = {
        prefix: (
            params_cls,
            Field(default=default, description=f"Parameters of the {kit} kit"),
        )
        for prefix, kit, params_cls, default in specs
    }
    model = create_model(
        base.__name__,
        __base__=base,
        __module__=base.__module__,
        __doc__=base.__doc__,
        **fields,
    )
    # pickle finds *base* under the shared name, so instances reduce to a rebuild
    model._mango_effective_spec = (base, specs)
    model.__reduce__ = _reduce_effective
    _EFFECTIVE_MODELS[key] = model
    return model


def _reduce_effective(self: BaseModel) -> tuple:
    return _restore_effective, (type(self)._mango_effective_spec, self.__getstate__())


def _restore_effective(spec: tuple, state: dict) -> BaseModel:
    model = _effective_model(*spec)
    obj = model.__new__(model)
    obj.__setstate__(state)
    return obj


# --------------------------------------------------------------------------- behaviors


def _check_behavior(behavior: Any, what: str) -> None:
    missing = [
        name
        for name in ("initialize", "on_step", "install")
        if not callable(getattr(behavior, name, None))
    ]
    if missing:
        raise TypeError(
            f"{what} must be a mango Behavior (with initialize, on_step and install), "
            f"got {type(behavior).__name__} without {', '.join(missing)}."
        )


def _key_segments(key: Any) -> tuple:
    return tuple(key) if isinstance(key, tuple | list) else (key,)


def _child_generator(generator: Any, key: tuple) -> Any:
    import numpy as np

    from mango.util.seeding import _segment_keys

    seq = getattr(getattr(generator, "bit_generator", None), "seed_seq", None)
    if isinstance(seq, np.random.SeedSequence):
        child = np.random.SeedSequence(
            seq.entropy, spawn_key=tuple(seq.spawn_key) + _segment_keys(key)
        )
    else:  # e.g. random.Random: derive the child seed from the stream itself
        child = np.random.SeedSequence(int(generator.getrandbits(64)))
    return np.random.Generator(np.random.PCG64(child))


def _bind(component: Any, generator: Any) -> None:
    current = getattr(component, "rng", _MISSING)
    if current is _MISSING:
        return
    if current is None or getattr(component, _BOUND_RNG_ATTR, None) is current:
        component.rng = generator
        try:
            setattr(component, _BOUND_RNG_ATTR, generator)
        except AttributeError:
            pass


class CompositeBehavior(Behavior):
    """Run several environment behaviors as one, in order.

    ``initialize``, ``on_step`` and ``install`` are forwarded to every child
    in the given order.  When a world binds its ``"behavior"`` stream to the
    composite (as to any behavior with ``rng = None``), each child that
    exposes ``rng = None`` gets its own sub-stream ``behavior/<key>`` (key:
    the child's position, or the given *keys*), so children never share draws.

    In scenarios the default environment combines the behaviors that kits
    add with ``ctx.add_behavior`` this way; there the i-th behavior of kit
    ``grid`` uses the stream ``behavior/grid/<i>`` (``behavior/scenario/<i>``
    outside kits), so adding a kit never changes another kit's draws.

    :param behaviors: the child behaviors
    :param keys: optional stream keys per child (str, int or a tuple of them)

    Example::

        env = DefaultEnvironment(behavior=CompositeBehavior([Weather(), Feeder(12)]))
        world = create_world(environment=env, seed=7)   # Weather gets "behavior/0"
    """

    def __init__(self, behaviors: Iterable[Behavior], *, keys: Sequence | None = None):
        children = tuple(behaviors)
        for index, child in enumerate(children):
            _check_behavior(child, f"CompositeBehavior child {index}")
        if keys is None:
            keys = list(range(len(children)))
        if len(keys) != len(children):
            raise ValueError(
                f"CompositeBehavior got {len(keys)} keys for {len(children)} behaviors."
            )
        self._behaviors = children
        self._keys = tuple(_key_segments(key) for key in keys)
        self._rng: Any = None

    def __repr__(self) -> str:
        names = ", ".join(type(child).__name__ for child in self._behaviors)
        return f"CompositeBehavior([{names}])"

    def __len__(self) -> int:
        return len(self._behaviors)

    def __iter__(self) -> Iterator[Behavior]:
        return iter(self._behaviors)

    def __getitem__(self, index: int) -> Behavior:
        return self._behaviors[index]

    @property
    def behaviors(self) -> tuple[Behavior, ...]:
        """The children in call order."""
        return self._behaviors

    @property
    def rng(self) -> Any:
        """The stream the world bound; setting it binds the children's sub-streams."""
        return self._rng

    @rng.setter
    def rng(self, generator: Any) -> None:
        self._rng = generator
        if generator is None:
            return
        for key, child in zip(self._keys, self._behaviors):
            current = getattr(child, "rng", _MISSING)
            if current is _MISSING:
                continue
            if current is None or getattr(child, _BOUND_RNG_ATTR, None) is current:
                _bind(child, _child_generator(generator, key))

    def initialize(self, environment: Any, clock: Any) -> None:
        for child in self._behaviors:
            child.initialize(environment, clock)

    def on_step(self, environment: Any, clock: Any, step_size_s: float) -> None:
        for child in self._behaviors:
            child.on_step(environment, clock, step_size_s)

    def install(self, agent: Any, **kwargs: Any) -> None:
        for child in self._behaviors:
            child.install(agent, **kwargs)


def _behavior_keys(entries: Sequence[tuple[str, Any]]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    keys = []
    for prefix, _ in entries:
        owner = prefix or "scenario"
        keys.append((owner, counts.get(owner, 0)))
        counts[owner] = counts.get(owner, 0) + 1
    return keys


def compose_behavior(entries: Sequence[tuple[str, Any]], seeds: SeedTree) -> Any:
    """Combine ``(prefix, behavior)`` entries into the environment's behavior.

    Binds ``behavior/<prefix>/<i>`` to every behavior with ``rng = None``
    first.  Returns ``None`` without entries, the behavior itself for one
    entry and a :class:`CompositeBehavior` otherwise.
    """
    keys = _behavior_keys(entries)
    for key, (_, behavior) in zip(keys, entries):
        if getattr(behavior, "rng", _MISSING) is None:
            behavior.rng = seeds.rng("behavior", *key)
    if not entries:
        return None
    if len(entries) == 1:
        return entries[0][1]
    return CompositeBehavior([behavior for _, behavior in entries], keys=keys)


def describe_behaviors(entries: Sequence[tuple[str, Any]]) -> list[str]:
    """``["FeederBehavior (grid)", ...]`` for plans."""
    return [
        f"{type(behavior).__name__} ({prefix or 'scenario'})"
        for prefix, behavior in entries
    ]


def _installs(candidate: Any, behavior: Any) -> bool:
    if candidate is behavior:
        return True
    if isinstance(candidate, CompositeBehavior):
        return any(_installs(child, behavior) for child in candidate.behaviors)
    return False


def check_environment(
    environment: Any,
    behavior: Any,
    space: Space | None,
    entries: Sequence[tuple[str, Any]],
    scenario: str,
) -> None:
    """Fail when a custom environment ignores the kits' behaviors or space."""
    if behavior is not None and not _installs(
        getattr(environment, "behavior", None), behavior
    ):
        added = ", ".join(describe_behaviors(entries))
        raise ScenarioError(
            f"The environment factory of scenario {scenario!r} returned an "
            f"environment without the behaviors kits added ({added}); install "
            "ctx.behavior, e.g. DefaultEnvironment(space=..., behavior=ctx.behavior) "
            "(or a CompositeBehavior containing it)."
        )
    if space is not None and getattr(environment, "space", None) is not space:
        raise ScenarioError(
            f"The environment factory of scenario {scenario!r} returned an "
            f"environment without the space a kit set ({type(space).__name__}); use "
            "DefaultEnvironment(space=ctx.space, behavior=ctx.behavior)."
        )


# --------------------------------------------------------------------------- registry


_REGISTRY: dict[str, type[ScenarioComponent]] = {}


def _check_component_class(obj: Any, origin: str) -> type[ScenarioComponent]:
    if not (isinstance(obj, type) and issubclass(obj, ScenarioComponent)):
        raise ScenarioError(f"{origin} is {obj!r}, not a ScenarioComponent subclass.")
    return obj


def register_component(kind: Any = None) -> Any:
    """Register a component class under *kind* (for YAML ``components:`` and lookups).

    Use as ``@register_component("neighbourhood")``, as bare
    ``@register_component`` (kind = the class's ``name``) or call
    ``register_component("kind")(cls)``.  Registering another class under a
    taken kind raises; re-registering the same class (e.g. after a module
    reload) replaces it.  Packages can also publish components through the
    entry point group ``"mango.scenario.components"`` (entry point name =
    kind, value = ``"module:Class"``).

    :param kind: the registry name
    :return: the decorator (or the class, for bare use)
    :raises ScenarioError: for a kind taken by another class

    Example::

        @register_component("neighbourhood")
        class Neighbourhood(ScenarioComponent):
            ...

        resolve_component("neighbourhood") is Neighbourhood   # True
    """
    if isinstance(kind, type):
        cls = _check_component_class(kind, "register_component() target")
        return register_component(cls.name)(cls)
    if kind is not None and (not isinstance(kind, str) or not kind.strip()):
        raise TypeError(f"A component kind must be a non-empty str, got {kind!r}.")
    if isinstance(kind, str) and ":" in kind:
        raise ScenarioError(
            f"Component kind {kind!r} must not contain ':' (that marks 'module:Class' "
            "references)."
        )

    def decorate(cls: type[ScenarioComponent]) -> type[ScenarioComponent]:
        _check_component_class(cls, f"register_component({kind!r}) target")
        name = kind if kind is not None else cls.name
        existing = _REGISTRY.get(name)
        if existing is not None and _class_ref(existing) != _class_ref(cls):
            raise ScenarioError(
                f"Component kind {name!r} is already registered for "
                f"{_class_ref(existing)}; choose another kind for {_class_ref(cls)}."
            )
        _REGISTRY[name] = cls
        return cls

    return decorate


def _entry_points() -> list:
    try:
        return list(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP))
    except Exception:  # broken metadata must not break lookups of registered kinds
        return []


def component_kinds() -> dict[str, str]:
    """Return the known component kinds: ``{kind: "module:Class"}``.

    Registered classes and entry points of the group
    ``"mango.scenario.components"`` (not imported by this call).

    Example::

        component_kinds()["agent_population"]   # "mango.scenario.kits:AgentPopulation"
    """
    kinds = {kind: _class_ref(cls) for kind, cls in _REGISTRY.items()}
    for entry in _entry_points():
        kinds.setdefault(entry.name, entry.value)
    return dict(sorted(kinds.items()))


def resolve_component(kind: str | type) -> type[ScenarioComponent]:
    """Return the component class for a kind, an entry point or a ``"module:Class"`` ref.

    Lookup order: registered kinds, entry points of the group
    ``"mango.scenario.components"``, import references.

    :param kind: a kind such as ``"agent_population"``, a reference such as
        ``"ev_study.kits:Neighbourhood"``, or a component class
    :return: the class
    :raises ScenarioError: for unknown kinds (listing the known ones) and for
        objects that are not components

    Example::

        cls = resolve_component("communication_preset")
        kit = cls(preset="lte")
    """
    if isinstance(kind, type):
        return _check_component_class(kind, "The component")
    if not isinstance(kind, str) or not kind.strip():
        raise TypeError(f"A component kind must be a non-empty str, got {kind!r}.")
    kind = kind.strip()
    found = _REGISTRY.get(kind)
    if found is not None:
        return found
    entries = _entry_points()
    for entry in entries:
        if entry.name == kind:
            try:
                loaded = entry.load()
            except Exception as exc:
                raise ScenarioError(
                    f"Component kind {kind!r}: loading entry point {entry.value!r} "
                    f"(group {ENTRY_POINT_GROUP!r}) failed: {exc}"
                ) from exc
            return _check_component_class(
                loaded, f"Entry point {kind!r} ({entry.value})"
            )
    if ":" in kind or "." in kind:
        obj = import_ref_or_raise(kind, f"Component {kind!r}")
        return _check_component_class(obj, f"Component reference {kind!r}")
    known = sorted({*_REGISTRY, *(entry.name for entry in entries)})
    hint = did_you_mean(kind, known)
    listing = ", ".join(known) if known else "none"
    raise ScenarioError(
        f"Unknown component kind {kind!r}.{hint} Known kinds: {listing}. Use "
        "'module:Class' for an unregistered component."
    )
