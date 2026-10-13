"""
Scenario events and phases: time-bounded changes declared as data.

*Phases* split a run into consecutive named periods (``params.phases``).  A
phase is active from the previous phase's ``until`` (the start for the first
one) up to its own ``until``; after the last ``until`` an implicit phase
``"main"`` runs to the horizon.  ``record: false`` drops the rows of the
phase (only of that phase: every phase records unless it says ``record:
false``), ``comm`` overrides communication settings (cascading: a phase's
``comm`` merges over the previous phase's), and the tidy
table's ``phase`` column holds the phase of every sample.  A row at ``t > 0``
is the state after the step that ended at ``t``, so it belongs to the phase
of that step (the row at a phase's start closes the previous phase).

*Events* (``params.events`` or :meth:`Scenario.event
<mango.scenario.Scenario.event>`) happen at a time point ``at``:

- ``comm``: communication settings that apply from ``at`` to ``until`` (the
  effective settings are ``params.comm`` <- phase ``comm`` <- the ``comm`` of
  every active event, in ``at`` order);
- ``emit``: a payload broadcast to all agents as a :class:`ScenarioEvent`
  (always through ``on_global_event``), or to the entities ``emit_to``
  names (an entity, a group, a list of these or a selector ``{group,
  by_attr}``; always through ``on_agent_event``); with ``until`` it is sent
  again at ``until`` (``ScenarioEvent.stage`` ``"start"``/``"end"``);
- ``action``: a function ``fn(ctx)`` (an import reference in params, any
  callable with :meth:`Scenario.event <mango.scenario.Scenario.event>`).

``every`` (with an optional ``count``) repeats an event.  Overrides and study
factors address events and phases by name (``events.comm_outage.at``) or
index (``events.0.at``).

With a fixed step, time points between step boundaries apply at the first
boundary at or after them (:meth:`Scenario.plan
<mango.scenario.Scenario.plan>` warns about them); event-driven runs stop
exactly at every time point.

Example (preset)::

    time: {start: 2024-06-01T00:00, horizon: 2d, step: 15min}
    phases:
      - {name: warmup, until: 2024-06-02T00:00, record: false}
    events:
      - {name: comm_outage, at: 2024-06-02T12:00, until: 2024-06-02T14:00,
         comm: {loss_percent: 1.0}}
      - {at: 2024-06-02T18:00, emit: {kind: grid_fault, bus: 7}}
"""

from __future__ import annotations

import bisect
import copy
import dataclasses
import datetime as _dt
import math
import numbers
from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    Field,
    PlainSerializer,
    ValidationError,
    WithJsonSchema,
    WrapValidator,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError

from ._util import (
    canonical_json,
    did_you_mean,
    import_ref,
    import_ref_or_raise,
    object_ref,
    to_plain,
)
from .comm import CommConfig
from .configfiles import deep_merge, get_path
from .errors import ScenarioError
from .timecfg import (
    _EPS,
    TimeConfig,
    TimePoint,
    _coerce_time_point,
    _datetime_at,
    _format_time_point,
    _tolerance,
    format_duration,
    parse_duration,
    parse_time_point,
)

__all__ = [
    "Phase",
    "Event",
    "ScenarioEvent",
    "PhasePlan",
    "EventPlan",
    "MAIN_PHASE",
]

#: Name of the implicit phase after the last declared ``until`` (and of the
#: only phase of a run without phases).
MAIN_PHASE = "main"

# --------------------------------------------------------------------------- models


def _is_param_ref(value: Any) -> bool:
    """A ``"$path"`` string: the value of a parameter, read per run."""
    return isinstance(value, str) and value.startswith("$") and len(value) > 1


def _ref_or(parse: Callable[[Any], Any]) -> Callable[[Any, Any], Any]:
    def validate(value: Any, handler: Any) -> Any:
        if _is_param_ref(value):
            return value.strip()
        return parse(value)

    return validate


def _ref_or_text(fmt: Callable[[Any], str]) -> Callable[[Any], str]:
    def serialize(value: Any) -> str:
        return value if isinstance(value, str) else fmt(value)

    return serialize


#: An event time point, or ``"$path"`` (a parameter holding it, read per run).
TimePointOrRef = Annotated[
    float | _dt.datetime | str,
    WrapValidator(_ref_or(_coerce_time_point)),
    PlainSerializer(_ref_or_text(_format_time_point), return_type=str),
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "number", "minimum": 0},
                {
                    "type": "string",
                    "examples": ["12h", "2024-06-01T12:00", "$market.open"],
                },
            ],
            "description": "offset from the start (duration), an ISO datetime or "
            "'$path' (a parameter holding one)",
        }
    ),
]

#: An event period: a duration, or ``"$path"`` (a parameter holding it).
DurationOrRef = Annotated[
    float | str,
    WrapValidator(_ref_or(parse_duration)),
    PlainSerializer(_ref_or_text(format_duration), return_type=str),
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "number", "minimum": 0},
                {"type": "string", "examples": ["1d", "$market.period_s"]},
            ],
            "description": "seconds, a duration like '1d' or '$path' (a parameter "
            "holding one)",
        }
    ),
]

#: Event fields that may read a parameter, with their parser.
_TIME_FIELDS = (
    ("at", _coerce_time_point),
    ("until", _coerce_time_point),
    ("every", parse_duration),
)


def _check_comm_override(value: Any) -> dict | None:
    """Validate a partial comm config: known keys, valid values (normalised)."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(
            f"comm must be a mapping of communication settings, e.g. "
            f"{{loss_percent: 1.0}}, got {type(value).__name__}"
        )
    fields = list(CommConfig.model_fields)
    problems = []
    for key in value:
        if key not in fields:
            hint = did_you_mean(str(key), fields)
            problems.append(
                InitErrorDetails(
                    type=PydanticCustomError(
                        "value_error",
                        "{message}",
                        {
                            "message": f"unknown communication setting {key!r}.{hint} "
                            f"Settings: {', '.join(fields)}"
                        },
                    ),
                    loc=(str(key),),
                    input=value[key],
                )
            )
    if problems:
        raise ValidationError.from_exception_data("comm", problems)
    probe = dict(value)
    if probe.get("kind") == "custom" and "factory" not in probe:
        probe["factory"] = (
            "placeholder:factory"  # the factory may come from params.comm
        )
    dumped = CommConfig.model_validate(probe).model_dump(mode="json")
    return {key: dumped.get(key) for key in value}


def _check_plain(value: Any, path: str = "emit") -> None:
    if value is None or isinstance(value, str | bool | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite, got {value!r}")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings, got {key!r}")
            _check_plain(item, f"{path}.{key}")
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _check_plain(item, f"{path}.{index}")
        return
    raise ValueError(
        f"{path} must be plain data (mappings, lists, strings, numbers), got "
        f"{type(value).__name__}; pass Python objects with scenario.event(emit=...)"
    )


#: Keys of an ``emit_to`` selector mapping.
_SELECTOR_KEYS = ("group", "by_attr")


def check_selector(value: Mapping) -> dict:
    """Validate an ``emit_to`` selector ``{group: ..., by_attr: {attr: value}}``."""
    unknown = [key for key in value if key not in _SELECTOR_KEYS]
    if unknown:
        hint = did_you_mean(str(unknown[0]), list(_SELECTOR_KEYS))
        raise ValueError(
            f"emit_to selector key {unknown[0]!r} is unknown.{hint} A selector is "
            "{group: person, by_attr: {age_group: young}} (group optional)"
        )
    by_attr = value.get("by_attr")
    if not isinstance(by_attr, Mapping) or not by_attr:
        raise ValueError(
            "an emit_to mapping selects entities by install attributes and needs "
            "by_attr, e.g. {group: person, by_attr: {age_group: young}}; name entities "
            "or groups with a string or a list"
        )
    for key in by_attr:
        if not isinstance(key, str):
            raise ValueError(f"emit_to by_attr keys must be strings, got {key!r}")
    _check_plain(dict(by_attr), "emit_to.by_attr")
    group = value.get("group")
    if group is not None:
        groups = [group] if isinstance(group, str) else group
        if (
            not isinstance(groups, list)
            or not groups
            or not all(isinstance(item, str) for item in groups)
        ):
            raise ValueError(
                f"emit_to group must be a group name or a list of them, got {group!r}"
            )
    return dict(value)


def selector_text(selector: Mapping) -> str:
    """``person with age_group=young`` (plan output)."""
    group = selector.get("group")
    where = ", ".join(
        f"{key}={'|'.join(map(str, val)) if isinstance(val, list) else val}"
        for key, val in selector["by_attr"].items()
    )
    if group is None:
        scope = "entities"
    else:
        scope = group if isinstance(group, str) else ", ".join(group)
    return f"{scope} with {where}"


def selector_matches(selector: Mapping, attrs: Mapping) -> bool:
    """True when *attrs* match every ``by_attr`` entry (a list accepts any item)."""
    missing = object()
    for key, wanted in selector["by_attr"].items():
        value = attrs.get(key, missing)
        if value is missing:
            return False
        accepted = wanted if isinstance(wanted, list) else [wanted]
        if value not in accepted:
            return False
    return True


class Phase(BaseModel, extra="forbid"):
    """A named period of a run (an item of ``params.phases``).

    Phases follow each other from the start; a phase is active from the
    previous phase's ``until`` to its own.  ``comm`` merges over the
    previous phase's ``comm`` (later phases state only what changes), while
    ``record`` is per phase: a phase records unless it says ``record:
    false``, so a warm-up with ``record: false`` does not silence the
    phases after it.  After the last ``until`` the implicit phase
    ``"main"`` (recorded, no comm override) runs to the horizon.

    :param name: the phase name (the tidy table's ``phase`` column)
    :param until: end of the phase: an offset (``"1d"``) or an absolute time
        (``"2024-06-02T00:00"``); only the last phase may omit it (it then runs
        to the horizon)
    :param record: ``False`` records no rows of the phase's steps (and
        drops its ``ctx.log`` rows); default ``True`` (not inherited from the
        previous phase)
    :param comm: communication settings for the phase, e.g.
        ``{"loss_percent": 0.1}`` (keys of :class:`~mango.scenario.CommConfig`)

    Example::

        Phase(name="warmup", until="2024-06-02T00:00", record=False)
    """

    name: str = Field(min_length=1)
    until: TimePoint | None = None
    record: bool | None = None
    comm: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a phase name must not be blank")
        return value

    @field_validator("comm", mode="before")
    @classmethod
    def _check_comm(cls, value: Any) -> Any:
        return _check_comm_override(value)


#: Event fields left out of dumps while unset, so adding them kept the run ids
#: (content hashes of the dumped params) of existing scenarios.
_OMIT_WHEN_NONE = ("every", "count")


class Event(BaseModel, extra="forbid"):
    """A scheduled change of a run (an item of ``params.events``).

    Address an event in overrides and study factors by its name
    (``events.comm_outage.at``) or its index (``events.0.at``).

    :param name: optional name (shown by ``plan()``, logs and the emitted
        :class:`ScenarioEvent`; makes ``events.<name>.<field>`` paths work)
    :param at: when the event happens: an offset (``"36h"``) or an absolute
        time (``"2024-06-02T12:00"``); it must lie before the horizon
    :param until: end of the event: ``comm`` applies from ``at`` to ``until``
        (default: the end of the run), and ``emit`` is sent twice, at ``at``
        (``ScenarioEvent.stage == "start"``) and at ``until`` (``"end"``;
        not sent when ``until`` is at or after the horizon); needs ``comm``
        or ``emit``
    :param comm: communication settings while the event is active, e.g.
        ``{"loss_percent": 1.0}`` for an outage (keys of
        :class:`~mango.scenario.CommConfig`)
    :param emit: payload (plain data) sent to the agents as a
        :class:`ScenarioEvent` at ``at``
    :param emit_to: who receives ``emit``; targeted events always go to
        the agents' (and roles') ``on_agent_event`` hook, broadcasts to
        ``on_global_event``: an entity name, a group name (every entity of
        the group) or a list of these, or a selector ``{group: person,
        by_attr: {age_group: young}}`` (entities whose install attributes
        match; ``group`` is optional, a list value accepts any of its
        items); default: all agents (``on_global_event``).  A run warns once
        per event when none of its receivers implements the hook
    :param action: import reference ``"module:function"`` of ``fn(ctx)``
        called at ``at`` with the run's :class:`~mango.scenario.BuildContext`
    :param every: repeat the event with this period (a duration, e.g.
        ``"1d"``): occurrences at ``at``, ``at + every``, ... before the
        horizon, each with the same ``until - at`` window; ``plan()`` lists
        every occurrence
    :param count: number of occurrences with ``every`` (default: as many as
        fit before the horizon)

    ``at``, ``until`` and ``every`` may also be ``"$path"``: the value of
    the parameter at that path, read per run (``at: $market.open``,
    ``every: $market.period_s``), so a study factor or an override of the
    parameter moves the event.

    Example::

        Event(name="comm_outage", at="2024-06-02T12:00", until="2024-06-02T14:00",
              comm={"loss_percent": 1.0})
        Event(at="2024-06-02T18:00", emit={"kind": "grid_fault", "bus": 7})
        Event(name="lane_closure", at="8h", until="8h30min",
              emit={"kind": "close", "lane": 2}, emit_to="I2")
        Event(name="news", at="0s", every="7d", emit={"kind": "news"})
        Event(name="campaign", at="1d", emit={"kind": "campaign"},
              emit_to={"group": "person", "by_attr": {"age_group": "young"}})
    """

    name: str | None = None
    at: TimePointOrRef
    until: TimePointOrRef | None = None
    comm: dict[str, Any] | None = None
    emit: Any = None
    emit_to: str | list[str] | dict[str, Any] | None = None
    action: str | None = None
    every: DurationOrRef | None = None
    count: int | None = Field(None, ge=1)

    @model_serializer(mode="wrap")
    def _omit_unset(self, handler: Any) -> Any:
        data = handler(self)
        if isinstance(data, dict):
            for key in _OMIT_WHEN_NONE:
                if data.get(key) is None:
                    data.pop(key, None)
        return data

    @field_validator("emit_to")
    @classmethod
    def _check_emit_to(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return check_selector(value)
        if isinstance(value, list):
            if not value:
                raise ValueError(
                    "emit_to must name at least one entity or group (omit it to send "
                    "to all agents)"
                )
            if len(set(value)) != len(value):
                raise ValueError(f"emit_to names an entity twice: {value!r}")
        return value

    @field_validator("every")
    @classmethod
    def _check_every(cls, value: Any) -> Any:
        if value is not None and not isinstance(value, str) and value <= 0:
            raise ValueError(f"every must be a positive duration, got {value!r}")
        return value

    @field_validator("comm", mode="before")
    @classmethod
    def _check_comm(cls, value: Any) -> Any:
        return _check_comm_override(value)

    @field_validator("emit")
    @classmethod
    def _check_emit(cls, value: Any) -> Any:
        _check_plain(value)
        return value

    @field_validator("action", mode="before")
    @classmethod
    def _check_action(cls, value: Any) -> Any:
        if value is None or isinstance(value, str):
            if isinstance(value, str) and not value.strip():
                raise ValueError("action must be an import reference 'module:function'")
            return value
        if callable(value):
            ref = object_ref(value)
            if ref is None:
                raise ValueError(
                    "action must be an import reference 'module:function'; a lambda "
                    "or local function cannot be stored in params (use "
                    "scenario.event(..., action=fn) for those)"
                )
            return ref
        raise ValueError(
            f"action must be an import reference 'module:function', got "
            f"{type(value).__name__}"
        )

    @model_validator(mode="after")
    def _check_effect(self) -> Event:
        if self.comm is None and self.emit is None and self.action is None:
            raise ValueError("an event needs comm, emit or action (it does nothing)")
        if self.emit_to is not None and self.emit is None:
            raise ValueError("emit_to needs emit (the payload to send)")
        if self.until is not None and self.comm is None and self.emit is None:
            raise ValueError(
                "until ends a comm override or an emit window, but the event has "
                "neither comm nor emit; drop until"
            )
        if self.count is not None and self.every is None:
            raise ValueError("count needs every (the period of the repetitions)")
        return self


@dataclasses.dataclass(frozen=True)
class ScenarioEvent:
    """What agents receive from an event with ``emit``.

    Delivered through ``on_global_event(event)`` (all agents) or
    ``on_agent_event(event)`` (the entities named by ``emit_to``).  An event
    with ``until`` is delivered twice: at ``at`` with ``stage == "start"``
    and at ``until`` with ``stage == "end"``.

    :param name: the event's name (``None`` if unnamed)
    :param payload: the ``emit`` value (a copy for params events)
    :param time: simulation seconds since the start when it was emitted
    :param datetime: calendar time of the emission (``None`` without
        ``time.start``)
    :param stage: ``None`` for an event without ``until``; ``"start"`` or
        ``"end"`` for the two deliveries of an event with ``until``
    :param until: the end of the event's window in seconds (``None``
        without ``until``)

    Example::

        class Feeder(Agent):
            def on_global_event(self, event):
                if isinstance(event, ScenarioEvent) and event.kind == "grid_fault":
                    self.fault_bus = event["bus"]

        class Intersection(Agent):
            def on_agent_event(self, event):
                if isinstance(event, ScenarioEvent) and event.kind == "close":
                    self.closed = event.stage != "end"
    """

    name: str | None
    payload: Any
    time: float
    datetime: _dt.datetime | None = None
    stage: str | None = None
    until: float | None = None

    @property
    def kind(self) -> Any:
        """``payload["kind"]`` for mapping payloads (``None`` otherwise)."""
        payload = self.payload
        return payload.get("kind") if isinstance(payload, Mapping) else None

    def __getitem__(self, key: Any) -> Any:
        payload = self.payload
        if not isinstance(payload, Mapping):
            raise TypeError(
                f"ScenarioEvent {self.name!r}: event[{key!r}] needs a mapping payload, "
                f"got {type(payload).__name__}; read event.payload."
            )
        return payload[key]

    def get(self, key: Any, default: Any = None) -> Any:
        """``payload.get(key, default)`` for mapping payloads (*default* otherwise)."""
        payload = self.payload
        return payload.get(key, default) if isinstance(payload, Mapping) else default


# --------------------------------------------------------------------------- structure events


@dataclasses.dataclass
class _EventSpec:
    """One event of a run (params or structure level), resolved to offsets."""

    label: str
    name: str | None
    at: float
    until: float | None
    comm: dict | None
    emit: Any
    emit_to: str | list[str] | None
    action: Callable | None
    action_text: str | None
    source: str  # "params" or "scenario"
    copy_payload: bool
    #: the entities ``emit`` goes to (``None``: all agents; ``()`` with a
    #: selector, resolved when the event fires)
    targets: tuple[str, ...] | None = None
    #: an ``emit_to`` selector ``{group, by_attr}`` (resolved per firing)
    selector: dict | None = None


@dataclasses.dataclass(frozen=True)
class Firing:
    """One delivery of an event: its start (``at``) or the end of an emit window."""

    time: float
    spec: _EventSpec
    #: ``None`` (no window), ``"start"`` or ``"end"``
    stage: str | None

    @property
    def emits(self) -> bool:
        return self.spec.emit is not None

    @property
    def acts(self) -> bool:
        return self.spec.action is not None and self.stage != "end"


@dataclasses.dataclass
class _StructureEvent:
    """An event declared with ``scenario.event`` (time points resolved per run)."""

    at: float | _dt.datetime
    until: float | _dt.datetime | None
    name: str | None
    comm: dict | None
    emit: Any
    emit_to: str | list[str] | None
    action: Callable | str | None
    every: float | None = None
    count: int | None = None

    #: fields that read the run's parameters (resolved in the build pass)
    deferred: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> tuple:
        from .context import param_spec_text

        action = self.action
        if callable(action):
            action = getattr(action, "__qualname__", None) or type(action).__qualname__
        parts = (
            "event",
            self.name,
            to_plain(param_spec_text(self.at), default=str),
            to_plain(param_spec_text(self.until), default=str),
            to_plain(self.comm, default=repr),
            to_plain(self.emit, default=repr),
            to_plain(self.emit_to),
            action,
        )
        if self.every is not None:
            parts += (param_spec_text(self.every), self.count)
        return parts

    def apply(self, stage: str, ctx: Any) -> None:
        if stage == "build":
            event = self._resolved(ctx.p) if self.deferred else self
            ctx._state.events.append(event)

    def _label(self) -> str:
        return f"scenario.event(at={self.at!r}" + (
            f", name={self.name!r})" if self.name is not None else ")"
        )

    def _resolved(self, params: Any) -> _StructureEvent:
        """This event with its parameter-driven times read from *params*."""
        from .context import resolve_param_spec

        values: dict[str, Any] = {}
        sources: dict[str, str] = {}
        parsers = dict(_TIME_FIELDS)
        for field in self.deferred:
            values[field], sources[field] = resolve_param_spec(
                getattr(self, field),
                params,
                f"{self._label()} {field}",
                parsers[field],
            )
        probe = {
            "at": values.get("at", self.at),
            "until": values.get("until", self.until),
            "every": values.get("every", self.every),
            "count": self.count,
            "comm": self.comm,
            "emit": None if self.emit is None else 0,
            "action": None if self.action is None else "placeholder:action",
        }
        try:
            checked = Event.model_validate(probe)
        except ValidationError as exc:
            field = str(exc.errors()[0]["loc"][0]) if exc.errors() else ""
            source = f" {sources[field]}" if field in sources else ""
            raise ScenarioError(
                f"Invalid {self._label()}: {field}{source}: "
                f"{_error_text(exc).split(': ', 1)[-1]}"
            ) from None
        for field in values:
            if _is_param_ref(getattr(checked, field)):
                raise ScenarioError(
                    f"Invalid {self._label()}: {field} {sources[field]} is itself a "
                    "parameter reference."
                )
        return dataclasses.replace(
            self,
            at=checked.at,
            until=checked.until,
            every=checked.every,
            deferred=(),
        )

    def check_paths(self, model: Any) -> None:
        """Fail early when a deferred time names a parameter path *model* lacks."""
        from .context import param_spec_paths
        from .errors import ParamsError
        from .params import check_path

        parsers = dict(_TIME_FIELDS)
        for field in self.deferred:
            value = getattr(self, field)
            for path in param_spec_paths(value, parsers[field]):
                try:
                    check_path(model, path)
                except ParamsError as exc:
                    try:
                        parsers[field](value)
                        reason = ""
                    except (ValueError, TypeError) as parse_error:
                        reason = f"{str(parse_error).rstrip('.')}; "
                    raise ScenarioError(
                        f"Invalid {self._label()}: {field}: {reason}{value!r} is no "
                        f"parameter path either ({exc})."
                    ) from None


def structure_event(
    at: Any,
    *,
    until: Any = None,
    name: str | None = None,
    comm: Mapping | None = None,
    emit: Any = None,
    emit_to: str | Sequence[str] | None = None,
    action: Callable | str | None = None,
    every: Any = None,
    count: int | None = None,
) -> _StructureEvent:
    """Validate the arguments of ``scenario.event`` (time points stay unresolved).

    *at*, *until* and *every* may read the run's parameters (``"$path"``, a
    parameter path or ``fn(p)``); they are resolved in the build pass.
    """
    from .context import is_param_spec

    raw = {"at": at, "until": until, "every": every}
    parsers = dict(_TIME_FIELDS)
    deferred = tuple(
        field
        for field, value in raw.items()
        if value is not None and is_param_spec(value, parsers[field])
    )
    placeholders = {"at": 0.0, "until": 1.0, "every": 1.0}
    if action is not None and not (callable(action) or isinstance(action, str)):
        raise TypeError(
            f"scenario.event(action=...) must be callable or an import reference, got "
            f"{type(action).__name__}."
        )
    if isinstance(action, str):
        action = import_ref_or_raise(
            action, f"Invalid scenario.event(action={action!r})"
        )
    probe = {
        "name": name,
        "at": placeholders["at"] if "at" in deferred else at,
        "until": placeholders["until"] if "until" in deferred else until,
        "comm": comm,
        "emit_to": list(emit_to) if isinstance(emit_to, list | tuple) else emit_to,
        # any object may be emitted from Python; only params payloads are plain data
        "emit": None if emit is None else 0,
        "action": None if action is None else "placeholder:action",
        "every": placeholders["every"] if "every" in deferred else every,
        "count": count,
    }
    try:
        checked = Event.model_validate(probe)
    except ValidationError as exc:
        raise ScenarioError(
            f"Invalid scenario.event(at={at!r}"
            + (f", name={name!r}" if name is not None else "")
            + f"): {_error_text(exc)}"
        ) from None
    return _StructureEvent(
        at=at if "at" in deferred else checked.at,
        until=until if "until" in deferred else checked.until,
        name=name,
        comm=checked.comm,
        emit=emit,
        emit_to=checked.emit_to,
        action=action,
        every=every if "every" in deferred else checked.every,
        count=checked.count,
        deferred=deferred,
    )


# --------------------------------------------------------------------------- schedule


@dataclasses.dataclass
class PhasePlan:
    """A phase of a planned run: name, span in seconds (and datetimes), record, comm."""

    name: str
    start: float
    end: float
    record: bool
    comm: dict
    start_datetime: _dt.datetime | None = None
    end_datetime: _dt.datetime | None = None


@dataclasses.dataclass
class EventPlan:
    """An event of a planned run (``at``/``until`` as applied, in seconds)."""

    name: str | None
    at: float
    until: float | None
    comm: dict | None
    emit: Any
    emit_to: str | list[str] | None
    action: str | None
    source: str
    at_datetime: _dt.datetime | None = None
    until_datetime: _dt.datetime | None = None


def _time_text(time: TimeConfig, offset: float) -> str:
    moment = _datetime_at(time, offset)
    if moment is None:
        return format_duration(offset)
    if moment.second == 0 and moment.microsecond == 0:
        text = moment.isoformat(timespec="minutes")
    else:
        text = moment.isoformat()
    return text


def _effective_comm(base: Mapping, override: Mapping) -> CommConfig:
    """``params.comm`` (dumped) <- *override*, validated."""
    merged = deep_merge(base, override)
    config = CommConfig.model_validate(merged)
    if config.kind == "ideal":
        changed = [
            key
            for key in ("loss_percent", "delay_s")
            if key in override and override[key] not in (0, 0.0)
        ]
        if changed:
            raise ValueError(
                f"comm.kind is 'ideal' (no loss, no delay), so {changed[0]}="
                f"{override[changed[0]]!r} has no effect; use kind: simple for "
                "loss and delay"
            )
    return config


def _error_text(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        found = errors(include_url=False)
        if found:
            first = found[0]
            where = ".".join(str(part) for part in first["loc"])
            message = str(first["msg"]).removeprefix("Value error, ")
            return f"{where}: {message}" if where else message
    return str(exc)


def _offset(time: TimeConfig, value: Any) -> float:
    return parse_time_point(value, time.start)


def _resolve_event_refs(event: Any, params: Any) -> tuple[Any, list]:
    """*event* with its ``"$path"`` times read from *params*, and the problems.

    :return: ``(event, [(loc, message), ...])``; the event is a copy when a
        reference was resolved
    """
    changes: dict[str, Any] = {}
    problems: list[tuple[tuple, str]] = []
    for field, parse in _TIME_FIELDS:
        value = getattr(event, field, None)
        if not _is_param_ref(value):
            continue
        path = value[1:]
        try:
            raw = get_path(params, path)
        except (KeyError, AttributeError, IndexError, TypeError) as exc:
            problems.append(((field,), f"{value!r} is not a parameter path ({exc})"))
            continue
        try:
            parsed = parse(raw)
        except (ValueError, TypeError) as exc:
            problems.append(((field,), f"{value!r} (= {raw!r}): {exc}"))
            continue
        if field == "every" and parsed <= 0:
            problems.append(
                ((field,), f"{value!r} (= {raw!r}) must be a positive duration")
            )
            continue
        changes[field] = parsed
    if changes and not problems:
        event = event.model_copy(update=changes)
    return event, problems


def params_schedule_problems(params: Any) -> list[tuple[tuple, str]]:
    """Problems of ``params.phases``/``params.events`` as ``[(loc, message)]``.

    Checks what a single field cannot: time points against ``time`` (start,
    horizon, order) and every comm override merged over ``params.comm``.
    """
    time = params.time
    base = params.comm.model_dump(mode="json")
    problems: list[tuple[tuple, str]] = []
    problems += _phase_problems(time, base, params.phases)
    for index, declared in enumerate(params.events):
        event, found = _resolve_event_refs(declared, params)
        if not found:
            found = _event_problems(time, base, event)
        problems += [(("events", index, *loc), message) for loc, message in found]
    return problems


def _phase_problems(time: TimeConfig, base: dict, phases: Sequence[Phase]):
    horizon = time.horizon_s
    problems: list[tuple[tuple, str]] = []
    names: dict[str, int] = {}
    previous: float = 0.0
    previous_name: str | None = None
    override: dict = {}

    def add(field: str, message: str) -> None:
        problems.append((("phases", index, field), message))

    for index, phase in enumerate(phases):
        if phase.name in names:
            add(
                "name",
                f"phase name {phase.name!r} is used twice (phases "
                f"{names[phase.name]} and {index}); phase names must be unique",
            )
        names.setdefault(phase.name, index)
        if phase.comm is not None:
            override = deep_merge(override, phase.comm)
            try:
                _effective_comm(base, override)
            except ValueError as exc:
                add("comm", _error_text(exc))
        if phase.until is None:
            if index != len(phases) - 1:
                add(
                    "until",
                    f"phase {phase.name!r} needs until (only the last phase may "
                    "omit it and run to the horizon)",
                )
            continue
        if phase.name == MAIN_PHASE:
            add(
                "name",
                f"the name {MAIN_PHASE!r} is reserved for the implicit phase after "
                "the last until; rename the phase or drop its until",
            )
        try:
            until = _offset(time, phase.until)
        except ValueError as exc:
            add("until", str(exc))
            continue
        if until <= previous + _tolerance(previous):
            after = (
                "the start"
                if previous_name is None
                else f"the until of phase {previous_name!r} "
                f"({_time_text(time, previous)})"
            )
            add(
                "until",
                f"phase {phase.name!r} ends at {_time_text(time, until)}, which "
                f"must lie after {after}; phases are consecutive, so their until "
                "times must increase",
            )
            continue
        if until > horizon + _tolerance(horizon):
            add(
                "until",
                f"phase {phase.name!r} ends at {_time_text(time, until)}, after "
                f"the horizon ({_time_text(time, horizon)}){_UNTIL_HINT}",
            )
            continue
        previous, previous_name = until, phase.name
    return problems


#: Appended to "after the horizon" problems: a shorter horizon is the wrong
#: tool for a quick test run.
_UNTIL_HINT = (
    "; for a quick, shorter run keep time.horizon and pass until= "
    "(Scenario.run) or --until (CLI) instead"
)


def _event_problems(time: TimeConfig, base: dict, event: Any):
    """Problems of one event (an :class:`Event` or a structure event)."""
    horizon = time.horizon_s
    problems: list[tuple[tuple, str]] = []

    def add(field: str, message: str) -> None:
        problems.append(((field,), message))

    try:
        at = _offset(time, event.at)
    except ValueError as exc:
        add("at", str(exc))
        at = None
    if at is not None and at >= horizon - _tolerance(horizon):
        add(
            "at",
            f"the event happens at {_time_text(time, at)}, at or after the end "
            f"of the run ({_time_text(time, horizon)}){_UNTIL_HINT}",
        )
    if event.until is not None:
        try:
            until = _offset(time, event.until)
        except ValueError as exc:
            add("until", str(exc))
        else:
            if at is not None and until <= at + _tolerance(at):
                add(
                    "until",
                    f"until ({_time_text(time, until)}) must lie after at "
                    f"({_time_text(time, at)})",
                )
    if event.comm is not None:
        try:
            _effective_comm(base, event.comm)
        except ValueError as exc:
            add("comm", _error_text(exc))
    return problems


def raise_params_problems(model_name: str, problems: list[tuple[tuple, str]]) -> None:
    """Raise one pydantic ``ValidationError`` locating every problem."""
    if not problems:
        return
    raise ValidationError.from_exception_data(
        model_name,
        [
            InitErrorDetails(
                type=PydanticCustomError("value_error", "{message}", {"message": msg}),
                loc=loc,
                input=None,
            )
            for loc, msg in problems
        ],
    )


class Schedule:
    """Phases and events of one run, resolved to offsets (snapped in fixed-step mode).

    Built in the declaration stage (so ``plan()`` and ``build()`` validate the
    same things); the runtime asks it which phase and comm config apply at a
    time and which events are due.
    """

    def __init__(
        self,
        params: Any,
        structure_events: Sequence[_StructureEvent] = (),
        *,
        entities: Mapping[str, str] | None = None,
        groups: Mapping[str, Sequence[str]] | None = None,
    ):
        self.time: TimeConfig = params.time
        self._base = params.comm.model_dump(mode="json")
        self.warnings: list[str] = []
        self.phases: list[PhasePlan] = self._build_phases(params.phases)
        self._starts = [span.start for span in self.phases]
        self.events: list[_EventSpec] = self._build_events(
            [
                self._resolved_event(event, index, params)
                for index, event in enumerate(params.events)
            ],
            structure_events,
            entities or {},
            groups or {},
        )
        self.has_comm_changes = any(span.comm for span in self.phases) or any(
            spec.comm for spec in self.events
        )
        horizon = self.time.horizon_s
        points = {span.start for span in self.phases[1:]}
        for spec in self.events:
            points.add(spec.at)
            if spec.until is not None:
                points.add(spec.until)
        self.boundaries: list[float] = sorted(
            t for t in points if 0.0 < t < horizon - _tolerance(horizon)
        )
        self._configs: dict[str, CommConfig] = {}
        self._check_comm_intervals()
        self._check_partitions(entities or {}, groups or {})

    # -- construction ------------------------------------------------------------

    @staticmethod
    def _resolved_event(event: Event, index: int, params: Any) -> Event:
        """A params event with its ``"$path"`` times read from *params*."""
        resolved, problems = _resolve_event_refs(event, params)
        if problems:
            (field,), message = problems[0]
            label = (
                f"event {event.name!r}" if event.name is not None else f"events.{index}"
            )
            raise ScenarioError(f"Invalid {label}: {field}: {message}.")
        return resolved

    def _snap(self, t: float) -> float:
        """The first step boundary at or after *t* (fixed step); *t* in event mode."""
        time = self.time
        if time.is_event_driven:
            return t
        k = math.ceil(t / time.step_s - _EPS)
        return time.horizon_s if k >= time.n_steps else k * time.step_s

    def _snapped(self, t: float, what: str, *, start: bool = False) -> float:
        """Snap *t* to the step grid, recording a warning when it moves."""
        horizon = self.time.horizon_s
        if t >= horizon - _tolerance(horizon):
            return t  # an until at or after the end of the run
        snapped = self._snap(t)
        if abs(snapped - t) > _tolerance(t):
            time = self.time
            note = ""
            if start and snapped >= time.horizon_s - _tolerance(time.horizon_s):
                note = " (the end of the run, so it never applies)"
            self.warnings.append(
                f"{what} {_time_text(time, t)} is not on the "
                f"{format_duration(time.step_s)} step grid; it applies at "
                f"{_time_text(time, snapped)}{note}, the first step boundary at or "
                "after it. Use a time on the grid or step='event'."
            )
        return snapped

    def _build_phases(self, phases: Sequence[Phase]) -> list[PhasePlan]:
        time = self.time
        horizon = time.horizon_s
        spans: list[PhasePlan] = []

        def span(name: str, start: float, end: float, record: bool, comm: dict):
            return PhasePlan(
                name,
                start,
                end,
                record,
                comm,
                _datetime_at(time, start),
                _datetime_at(time, end),
            )

        start = 0.0
        override: dict = {}
        for phase in phases:
            # record is per phase (a warm-up's record: false must not silence
            # the phases after it); comm cascades
            record = phase.record is not False
            if phase.comm is not None:
                override = deep_merge(override, phase.comm)
            if phase.until is None:
                end = horizon
            else:
                end = self._snapped(
                    _offset(self.time, phase.until), f"phase {phase.name!r} until"
                )
            spans.append(span(phase.name, start, end, record, dict(override)))
            start = end
        if start < horizon - _tolerance(horizon) or not spans:
            spans.append(span(MAIN_PHASE, start, horizon, True, {}))
        return spans

    def _build_events(
        self,
        params_events: Sequence[Event],
        structure_events: Sequence[_StructureEvent],
        entities: Mapping[str, str],
        groups: Mapping[str, Sequence[str]],
    ) -> list[_EventSpec]:
        time = self.time
        horizon = time.horizon_s
        specs: list[tuple[float, int, _EventSpec]] = []
        order = 0
        sources = [("scenario", event, i) for i, event in enumerate(structure_events)]
        sources += [("params", event, i) for i, event in enumerate(params_events)]
        for source, event, index in sources:
            label = (
                f"event {event.name!r}"
                if event.name is not None
                else (
                    f"events.{index}"
                    if source == "params"
                    else f"scenario.event #{index}"
                )
            )
            if source == "scenario":
                problems = _event_problems(time, self._base, event)
                if problems:
                    loc, message = problems[0]
                    raise ScenarioError(
                        f"Invalid {label} (declared with scenario.event): "
                        f"{'.'.join(map(str, loc))}: {message}."
                    )
            raw_at = _offset(time, event.at)
            raw_until = None if event.until is None else _offset(time, event.until)
            action, action_text = self._resolve_action(event.action, label)
            targets = selector = None
            if isinstance(event.emit_to, Mapping):
                selector = _resolve_selector(event.emit_to, label, groups)
                targets = ()
            elif event.emit_to is not None:
                targets = _resolve_targets(event.emit_to, label, entities, groups)
            every = getattr(event, "every", None)
            count = getattr(event, "count", None)
            occurrences = 0
            while True:
                shift = 0.0 if every is None else occurrences * every
                start = raw_at + shift
                if occurrences and start >= horizon - _tolerance(horizon):
                    break
                what = (
                    label
                    if not occurrences
                    else f"{label} (occurrence {occurrences + 1})"
                )
                warned = len(self.warnings)
                at = self._snapped(start, f"{what} at", start=True)
                until = (
                    None
                    if raw_until is None
                    else self._snapped(raw_until + shift, f"{what} until")
                )
                if occurrences and len(self.warnings) > warned:
                    del self.warnings[warned:]  # one grid warning per event
                spec = _EventSpec(
                    label=what,
                    name=event.name,
                    at=at,
                    until=until,
                    comm=event.comm,
                    emit=event.emit,
                    emit_to=event.emit_to,
                    action=action,
                    action_text=action_text,
                    source=source,
                    copy_payload=source == "params",
                    targets=targets,
                    selector=selector,
                )
                specs.append((at, order, spec))
                order += 1
                occurrences += 1
                if every is None or (count is not None and occurrences >= count):
                    break
            if count is not None and occurrences < count:
                self.warnings.append(
                    f"{label} repeats every {format_duration(every)} with count "
                    f"{count}, but only {occurrences} occurrence"
                    f"{'s' * (occurrences != 1)} start before the end of the run "
                    f"({_time_text(time, horizon)})."
                )
        specs.sort(key=lambda item: (item[0], item[1]))
        return [spec for _, _, spec in specs]

    @property
    def firings(self) -> list[Firing]:
        """Every delivery of the events, in the order the run applies them.

        An event with ``emit`` or ``action`` fires at ``at``; one with
        ``emit`` and an ``until`` before the horizon fires again at
        ``until`` (stage ``"end"``).  At the same time point ends come
        first, then starts, each in ``at`` order.
        """
        horizon = self.time.horizon_s
        items: list[tuple[float, int, int, Firing]] = []
        for order, spec in enumerate(self.events):
            window = spec.emit is not None and spec.until is not None
            if spec.emit is not None or spec.action is not None:
                stage = "start" if window else None
                items.append((spec.at, 1, order, Firing(spec.at, spec, stage)))
            if window and spec.until < horizon - _tolerance(horizon):
                items.append((spec.until, 0, order, Firing(spec.until, spec, "end")))
        items.sort(key=lambda item: item[:3])
        return [item[3] for item in items]

    @staticmethod
    def _resolve_action(action: Any, label: str):
        if action is None:
            return None, None
        if callable(action):
            return action, (
                object_ref(action)
                or getattr(action, "__qualname__", None)
                or type(action).__qualname__
            )
        try:
            fn = import_ref(action)
        except (ImportError, AttributeError, ValueError) as exc:
            raise ScenarioError(
                f"The action of {label} cannot be imported: {exc}"
            ) from None
        if not callable(fn):
            raise ScenarioError(
                f"The action {action!r} of {label} is a {type(fn).__name__}, not a "
                "function fn(ctx)."
            )
        return fn, action

    def _check_comm_intervals(self) -> None:
        if not self.has_comm_changes:
            return
        for t in [0.0, *self.boundaries]:
            try:
                self.comm_at(t)
            except ValueError as exc:
                active = [spec.label for spec in self._active_events(t)]
                raise ScenarioError(
                    f"The communication settings at {_time_text(self.time, t)} (phase "
                    f"{self.phase_at(t).name!r}"
                    + (f", active: {', '.join(active)}" if active else "")
                    + f") are invalid: {_error_text(exc)}."
                ) from None

    def _check_partitions(
        self, entities: Mapping[str, str], groups: Mapping[str, Sequence[str]]
    ) -> None:
        """Every ``comm.partition`` names groups or entities of the scenario."""
        from .comm import partition_blocks

        times = [0.0, *self.boundaries] if self.has_comm_changes else [0.0]
        for t in times:
            config = self.comm_at(t)[0] if self.has_comm_changes else None
            partition = (
                config.partition if config is not None else self._base.get("partition")
            )
            if not isinstance(partition, list):
                continue
            try:
                partition_blocks(partition, entities, groups)
            except ValueError as exc:
                raise ScenarioError(
                    f"The communication settings at {_time_text(self.time, t)} are "
                    f"invalid: {exc}."
                ) from None

    # -- queries -----------------------------------------------------------------

    def phase_at(self, t: float) -> PhasePlan:
        """The phase active at offset *t* (the last one starting at or before it)."""
        index = bisect.bisect_right(self._starts, t + _tolerance(t)) - 1
        return self.phases[max(index, 0)]

    def sample_phase(self, t: float) -> PhasePlan:
        """The phase a sample at offset *t* belongs to (see :func:`sample_phase_index`)."""
        return self.phases[sample_phase_index(self._starts, t)]

    @property
    def phase_log(self) -> list[tuple[float, str]]:
        """``[(start, name)]`` of all phases (the tidy ``phase`` column's source)."""
        return [(span.start, span.name) for span in self.phases]

    def _active_events(self, t: float) -> list[_EventSpec]:
        tol = _tolerance(t)
        return [
            spec
            for spec in self.events
            if spec.comm
            and spec.at <= t + tol
            and (spec.until is None or t + tol < spec.until)
        ]

    def comm_override_at(self, t: float) -> dict:
        """Phase comm <- comm of the events active at *t* (in ``at`` order)."""
        override = self.phase_at(t).comm
        for spec in self._active_events(t):
            override = deep_merge(override, spec.comm)
        return override

    def comm_at(self, t: float) -> tuple[CommConfig, str]:
        """The effective comm config at *t* and a key identifying it."""
        override = self.comm_override_at(t)
        key = canonical_json(override)
        config = self._configs.get(key)
        if config is None:
            config = _effective_comm(self._base, override)
            self._configs[key] = config
        return config, key

    # -- plan --------------------------------------------------------------------

    def phase_plans(self) -> list[PhasePlan]:
        return [dataclasses.replace(span, comm=dict(span.comm)) for span in self.phases]

    def event_plans(self) -> list[EventPlan]:
        time = self.time
        return [
            EventPlan(
                spec.name,
                spec.at,
                spec.until,
                None if spec.comm is None else dict(spec.comm),
                spec.emit,
                spec.emit_to,
                spec.action_text,
                spec.source,
                _datetime_at(time, spec.at),
                None if spec.until is None else _datetime_at(time, spec.until),
            )
            for spec in self.events
        ]


def phase_starts(time: TimeConfig, phases: Sequence[Any]) -> dict[str, float]:
    """Phase name -> start offset in seconds, as a run applies it (snapped to the grid).

    :param time: the run's time settings
    :param phases: :class:`Phase` models or their dumps
    """
    grid = Schedule.__new__(Schedule)  # only the phase part of a schedule
    grid.time = time
    grid.warnings = []
    parsed = [Phase.model_validate(phase) for phase in phases]
    return {span.name: span.start for span in grid._build_phases(parsed)}


def sample_phase_index(starts: Sequence[float], t: float) -> int:
    """Index of the phase a sample at offset *t* belongs to.

    A sample at ``t > 0`` is the state after the step that ended at ``t``,
    so it belongs to the phase active during that step: the last phase that
    starts *before* ``t``.  The sample at ``t = 0`` (the initial state)
    belongs to the first phase.

    :param starts: the sorted start offsets of the phases (the first is 0)
    :param t: the sample time in seconds
    """
    index = bisect.bisect_left(starts, t - _tolerance(t)) - 1
    return max(index, 0)


def _resolve_targets(
    emit_to: str | Sequence[str],
    label: str,
    entities: Mapping[str, str],
    groups: Mapping[str, Sequence[str]],
) -> tuple[str, ...]:
    """The entity names *emit_to* stands for (entities and groups, in order)."""
    names = [emit_to] if isinstance(emit_to, str) else list(emit_to)
    targets: list[str] = []
    for name in names:
        if name in entities:
            members = [name]
        elif name in groups:
            members = list(groups[name])
        else:
            hint = did_you_mean(name, [*entities, *groups])
            known = list(entities)
            shown = ", ".join(known[:8]) + (", ..." if len(known) > 8 else "")
            raise ScenarioError(
                f"{label}: emit_to={name!r} is neither an entity nor a group of the "
                f"scenario.{hint} Groups: {', '.join(groups) or '(none)'}; entities: "
                f"{shown or '(none)'}."
            )
        targets += [member for member in members if member not in targets]
    return tuple(targets)


def _resolve_selector(
    selector: Mapping, label: str, groups: Mapping[str, Sequence[str]]
) -> dict:
    """Check the groups an ``emit_to`` selector names; return it unchanged."""
    try:
        checked = check_selector(selector)
    except ValueError as exc:
        raise ScenarioError(f"{label}: {exc}.") from None
    group = checked.get("group")
    for name in [] if group is None else [group] if isinstance(group, str) else group:
        if name not in groups:
            hint = did_you_mean(name, list(groups))
            raise ScenarioError(
                f"{label}: emit_to group {name!r} is not a group of the scenario.{hint} "
                f"Groups: {', '.join(groups) or '(none)'}."
            )
    return checked


def emitted_event(
    spec: _EventSpec,
    time: float,
    moment: _dt.datetime | None,
    stage: str | None = None,
) -> ScenarioEvent:
    """The :class:`ScenarioEvent` an event with ``emit`` sends."""
    payload = copy.deepcopy(spec.emit) if spec.copy_payload else spec.emit
    until = spec.until if stage is not None else None
    return ScenarioEvent(spec.name, payload, time, moment, stage, until)


def describe_comm(comm: Mapping | None) -> str:
    """``loss_percent=1.0, delay_s=0.04`` for plan output."""
    if not comm:
        return ""
    parts = []
    for key, value in comm.items():
        if isinstance(value, numbers.Real) and not isinstance(value, bool):
            parts.append(f"{key}={value:g}")
        else:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


def plan_lines(
    time: TimeConfig,
    phases: Sequence[PhasePlan],
    events: Sequence[EventPlan],
    width: int,
) -> list[str]:
    """The ``phases:``/``events:`` lines of ``ScenarioPlan.__str__``."""
    lines: list[str] = []
    declared = len(phases) > 1 or any(p.name != MAIN_PHASE for p in phases)
    if declared:
        lines.append("  phases:")
        for phase in phases:
            details = []
            if not phase.record:
                details.append("not recorded")
            if phase.comm:
                details.append(f"comm {describe_comm(phase.comm)}")
            span = f"{_time_text(time, phase.start)} .. {_time_text(time, phase.end)}"
            lines.append(
                f"    {phase.name:<{width}} {span}"
                + (f"  ({'; '.join(details)})" if details else "")
            )
    if events:
        lines.append("  events:")
        for index, event in enumerate(events):
            label = event.name if event.name is not None else f"#{index}"
            span = _time_text(time, event.at)
            if event.until is not None:
                span += f" .. {_time_text(time, event.until)}"
            details = []
            if event.comm:
                details.append(f"comm {describe_comm(event.comm)}")
            if event.emit is not None:
                target = event.emit_to
                hook = "on_agent_event"
                if target is None:
                    target, hook = "all agents", "on_global_event"
                elif isinstance(target, Mapping):
                    target = selector_text(target)
                elif not isinstance(target, str):
                    target = ", ".join(target)
                ending = " and at until" if event.until is not None else ""
                details.append(f"emit {event.emit!r} to {target} ({hook}{ending})")
            if event.action is not None:
                details.append(f"action {event.action}")
            lines.append(f"    {label:<{width}} {span}  ({'; '.join(details)})")
    return lines


# --------------------------------------------------------------------------- run summaries


def _stop_point_text(time: TimeConfig, t: float) -> str:
    """``54.6 s`` or ``2024-01-05T00:00 (432000 s)`` with a calendar start."""
    if time.start is None:
        return f"{t:g} s"
    return f"{_time_text(time, t)} ({t:g} s)"


def describe_stop(config: Mapping, stats: Mapping) -> str | None:
    """Why and when a run ended before its horizon, or ``None`` if it did not.

    Shared by the CLI run summary and the run warnings, e.g. ``"stopped
    early at 54.6 s (converged)"``.

    :param config: the run's resolved params as plain data (``result.config``)
    :param stats: the run's statistics (``result.stats``)

    Example::

        describe_stop(result.config, result.stats)
    """
    if not stats.get("stopped_early"):
        return None
    t = stats.get("stop_time_s")
    reason = stats.get("stop_reason") or "stopped"
    try:
        time = TimeConfig.model_validate(config["time"])
    except Exception:
        time = None
    if not isinstance(t, int | float):
        return f"stopped early ({reason})"
    where = f"{t:g} s" if time is None else _stop_point_text(time, float(t))
    return f"stopped early at {where} ({reason})"


def empty_run_reason(
    config: Mapping, stats: Mapping, *, recorders: int | None = None
) -> str:
    """Why a run recorded no values, as one clause.

    Used by the run warning, the CLI summary and the ``KeyError`` of an
    empty :class:`~mango.scenario.RunResult`, so all three say the same:

    - ``"the scenario declares no recorders"`` (with *recorders* ``0``);
    - ``"no phase records (every phase has record: false)"``;
    - ``"the run stopped at <time> (<reason>) inside phase 'warmup'
      (record: false); the first recorded phase 'measure' starts at <time>"``;
    - otherwise a hint at empty groups or a long ``every``.

    :param config: the run's resolved params as plain data (``result.config``)
    :param stats: the run's statistics (``result.stats``)
    :param recorders: the number of declared recorders (default:
        ``stats["recorders"]`` when present)

    Example::

        empty_run_reason(result.config, result.stats)
    """
    if recorders is None:
        recorders = stats.get("recorders")
    if recorders == 0:
        return "the scenario declares no recorders"
    try:
        time = TimeConfig.model_validate(config["time"])
        phases = [Phase.model_validate(phase) for phase in config.get("phases") or []]
        grid = Schedule.__new__(Schedule)  # only the phase part of a schedule
        grid.time = time
        grid.warnings = []
        spans = grid._build_phases(phases)
    except Exception:
        spans = []
    generic = (
        "the recorders produced no rows (empty groups, every= longer than the run, "
        "or a window that never closed?)"
    )
    if not spans:
        return generic
    if not any(span.record for span in spans):
        return "no phase records (every phase has record: false)"
    horizon = time.horizon_s
    stopped = bool(stats.get("stopped_early")) and isinstance(
        stats.get("stop_time_s"), int | float
    )
    end = float(stats["stop_time_s"]) if stopped else horizon
    # the t=0 sample belongs to the first phase, a sample at t > 0 to the
    # phase of the step that ended at t
    owners = [
        span
        for index, span in enumerate(spans)
        if index == 0 or span.start < end - _tolerance(end)
    ]
    if any(span.record for span in owners):
        return generic
    inside = owners[-1]
    first = next(span for span in spans if span.record)
    if stopped:
        reason = stats.get("stop_reason") or "stopped"
        when = f"the run stopped at {_stop_point_text(time, end)} ({reason})"
    else:
        when = "the run ended"
    return (
        f"{when} inside phase {inside.name!r} (record: false); the first recorded "
        f"phase {first.name!r} starts at {_time_text(time, first.start)}"
    )
