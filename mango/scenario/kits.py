"""
Built-in kits: :class:`AgentPopulation` and :class:`CommunicationPreset`.

Both are registered component kinds (``agent_population``,
``communication_preset``), so scenario files can use them by kind.  Domain
kits (EV fleets, households, grids) belong to domain packages; see
:class:`~mango.scenario.ScenarioComponent` for writing one.

Example::

    from mango.scenario import AgentPopulation, CommunicationPreset, Scenario

    scenario = Scenario("swarm")
    scenario.use(AgentPopulation(factory="swarm.agents:make_bee", count=50,
                                 link="peers", pattern={"random": {"p": 0.1}}),
                 prefix="bees")
    scenario.use(CommunicationPreset("lte"))
    scenario.run(overrides=["bees.count=100"])
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from mango.agent.core import Agent

from ._util import did_you_mean, import_ref_or_raise, object_ref
from .comm import CommConfig
from .components import ScenarioComponent, register_component
from .errors import ParamsError, ScenarioError
from .patterns import resolve_pattern

__all__ = ["AgentPopulation", "CommunicationPreset", "COMM_PRESETS"]


@register_component("agent_population")
class AgentPopulation(ScenarioComponent):
    """A group of agents made by a factory reference, optionally linked.

    The group is ``<prefix>.<group>`` (default ``population.agent``,
    entities ``population.agent_0`` ...).  ``factory`` is a ``"module:attr"``
    reference (or an importable function, stored as its reference) to either
    a function ``fn(e) -> Agent`` (``e`` is the
    :class:`~mango.scenario.EntityContext`; ``e.p.kwargs`` holds the extra
    arguments) or an ``Agent`` subclass, which is called as
    ``Cls(**kwargs)``.

    Parameters (``params.<prefix>``): ``factory``, ``count`` (>= 0),
    ``group`` (local group name), ``kwargs``, ``install`` (optional
    ``"module:fn"`` returning install attributes), ``link`` (link name;
    ``None``: no link), ``pattern`` (pattern name or one-key mapping such as
    ``{"random": {"p": 0.1}}``, default ``"complete"``) and ``target``
    (target group of the link; default: within the population).

    Example::

        scenario.use(AgentPopulation(factory="model.agents:Sensor", count=20,
                                     kwargs={"period_s": 60}, link="mesh",
                                     pattern="ring"), prefix="sensors")
    """

    name = "population"

    class Params(BaseModel, extra="forbid"):
        factory: str
        count: int = Field(1, ge=0)
        group: str = "agent"
        kwargs: dict[str, Any] = Field(default_factory=dict)
        install: str | None = None
        link: str | None = None
        pattern: str | dict[str, Any] = "complete"
        target: str | None = None

        @field_validator("factory", "install", mode="before")
        @classmethod
        def _as_ref(cls, value: Any) -> Any:
            if value is None or isinstance(value, str):
                return value
            if callable(value):
                ref = object_ref(value)
                if ref is None:
                    raise ValueError(
                        f"{value!r} cannot be referenced by import; pass a module-level "
                        "function or class, or a 'module:attr' string"
                    )
                return ref
            raise ValueError(f"expected a 'module:attr' reference, got {value!r}")

        @field_validator("group")
        @classmethod
        def _local_name(cls, value: str) -> str:
            if not value or "." in value or "/" in value:
                raise ValueError(
                    f"group must be a non-empty name without '.' or '/', got {value!r}"
                )
            return value

        @field_validator("pattern")
        @classmethod
        def _known_pattern(cls, value: Any) -> Any:
            try:
                resolve_pattern(value)
            except (ValueError, TypeError) as exc:
                raise ValueError(str(exc)) from None
            return value

    def build(self, ctx) -> None:
        p = ctx.p
        what = f"AgentPopulation {ctx.prefix!r}"
        target = import_ref_or_raise(p.factory, f"{what} factory")
        install = (
            None
            if p.install is None
            else import_ref_or_raise(p.install, f"{what} install")
        )
        if isinstance(target, type) and issubclass(target, Agent):
            kwargs = dict(p.kwargs)

            def factory(e):
                return target(**kwargs)

        elif callable(target):
            factory = target
        else:
            raise ScenarioError(
                f"{what}: factory {p.factory!r} is {type(target).__name__}, not an Agent "
                "class or a function e -> Agent."
            )
        ctx.group(p.group, p.count, factory, install=install)

    def wire(self, ctx) -> None:
        p = ctx.p
        if p.link is None:
            if p.target is not None:
                raise ScenarioError(
                    f"AgentPopulation {ctx.prefix!r}: target={p.target!r} needs a link "
                    "name (link=...)."
                )
            return
        ctx.link(p.link, p.group, p.target, pattern=resolve_pattern(p.pattern))


#: The communication presets of :class:`CommunicationPreset` (``comm`` settings).
COMM_PRESETS: dict[str, dict[str, Any]] = {
    "ideal": {"kind": "ideal", "loss_percent": 0.0, "delay_s": 0.0},
    "lan": {"kind": "simple", "loss_percent": 0.0, "delay_s": 0.001},
    "lte": {
        "kind": "simple",
        "loss_percent": 0.0,
        "delay_s": {"kind": "normal", "mean": 0.04, "std": 0.008, "low": 0.0},
    },
    "lossy": {"kind": "simple", "loss_percent": 0.1, "delay_s": 0.0},
}


@register_component("communication_preset")
class CommunicationPreset(ScenarioComponent):
    """Set the scenario's communication defaults from a named preset.

    Presets (see :data:`~mango.scenario.kits.COMM_PRESETS`): ``"ideal"`` (no delay, no loss),
    ``"lan"`` (1 ms), ``"lte"`` (delay ``Normal(40 ms, 8 ms)``, never
    negative) and ``"lossy"`` (10 % loss).  Keyword arguments adjust the
    preset (fields of :class:`~mango.scenario.CommConfig`).

    The preset becomes a scenario-level default of ``params.comm``, so
    presets and overrides (``comm.loss_percent=0.2``) still win.  The kit has
    no parameters of its own.

    :param preset: the preset name
    :param settings: ``CommConfig`` fields over the preset, e.g.
        ``loss_percent=0.05``
    :raises ParamsError: for unknown presets or invalid settings

    Example::

        scenario.use(CommunicationPreset("lte"))
        scenario.use(CommunicationPreset("lossy", loss_percent=0.2))
    """

    name = "communication"

    def __init__(self, preset: str = "ideal", **settings: Any):
        super().__init__()
        if preset not in COMM_PRESETS:
            hint = did_you_mean(str(preset), list(COMM_PRESETS))
            raise ParamsError(
                f"CommunicationPreset(): unknown preset {preset!r}.{hint} Presets: "
                f"{', '.join(COMM_PRESETS)}.",
                path="preset",
                source="CommunicationPreset()",
            )
        unknown = [key for key in settings if key not in CommConfig.model_fields]
        if unknown:
            hint = did_you_mean(unknown[0], list(CommConfig.model_fields))
            raise ParamsError(
                f"CommunicationPreset(): unknown setting {unknown[0]!r}.{hint} "
                f"Settings: {', '.join(CommConfig.model_fields)}.",
                path=unknown[0],
                source="CommunicationPreset()",
            )
        comm = {**COMM_PRESETS[preset], **settings}
        try:
            self._comm = CommConfig.model_validate(comm)
        except ValidationError as exc:
            first = exc.errors()[0]
            path = ".".join(str(part) for part in first["loc"])
            raise ParamsError(
                f"CommunicationPreset({preset!r}): {path}: {first['msg']}",
                path=path,
                source="CommunicationPreset()",
            ) from None
        self.preset = preset
        self._init_kwargs = {"preset": preset, **settings}

    @property
    def comm(self) -> Any:
        """The resulting :class:`~mango.scenario.CommConfig`."""
        return self._comm

    def param_defaults(self) -> Mapping[str, Any]:
        """``{"comm": <the preset settings>}``."""
        return {"comm": self._comm.model_dump(mode="json")}
