"""
Typed scenario parameters with presets, overrides and validation.

All numbers of a scenario live in one pydantic model that subclasses
:class:`ScenarioParams`.  :func:`load_params` builds it from layers, lowest
precedence first: the model defaults, the scenario's own defaults, preset
files or dicts in the given order, and ``"a.b=value"`` overrides.  Unknown
keys fail before any run, with a did-you-mean suggestion and the file they
came from.

Example::

    from typing import Literal
    from pydantic import BaseModel
    from mango.scenario import Normal, ScenarioParams, Uniform, load_params

    class EVParams(BaseModel, extra="forbid"):
        n: int = 10
        capacity_kwh: float = 60.0
        soc: Uniform = Uniform(0.2, 0.8)
        arrival_h: Normal = Normal(16.0, 1.5)

    class Params(ScenarioParams):
        ev: EVParams = EVParams()
        algorithm: Literal["greedy", "proportional"] = "greedy"

    params = load_params(Params, "presets/base.yaml", overrides=["ev.n=20", "algorithm=proportional"])
    print(params_to_yaml(params))
"""

from __future__ import annotations

import collections.abc
import copy
import difflib
import os
import types
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, Union, get_args, get_origin

import yaml
from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    model_validator,
)
from pydantic_core import PydanticUndefined

from ._util import model_json_data
from .comm import CommConfig, build_communication
from .configfiles import (
    deep_merge,
    display_path,
    generic_kind_switch,
    get_path,
    is_item_patch,
    item_index,
    list_index,
    mark_origins,
    merge_labeled,
    override_items,
    parse_overrides,
    read_config_file,
    read_tracked,
    set_path,
    split_marker,
    split_path,
)
from .distributions import (
    Distribution,
    distribution_kinds,
    expand_shorthand,
)
from .errors import ParamsError
from .events import Event, Phase, params_schedule_problems, raise_params_problems
from .profiles import ProfileName, ProfileSpec, rebase_preset_paths
from .timecfg import TimeConfig

__all__ = [
    "CommConfig",
    "ScenarioParams",
    "build_communication",
    "deep_merge",
    "read_config_file",
    "parse_overrides",
    "set_path",
    "get_path",
    "check_path",
    "load_params",
    "params_schema",
    "dump_params",
    "params_to_yaml",
]


# --------------------------------------------------------------------------- models


class ScenarioParams(BaseModel, extra="forbid"):
    """Base class of scenario parameter models.

    Subclass it and add your own (nested) fields.  It forbids unknown keys, so
    a typo in a preset fails at :func:`load_params`; give nested models
    ``extra="forbid"`` as well to get the same protection inside them.

    :param seed: root seed of the run (``None``: the run seed decides)
    :param time: calendar start, horizon and step
    :param comm: communication settings
    :param profiles: named time series profiles (see
        :mod:`mango.scenario.profiles`), read with ``ctx.profile(name)`` and
        ``e.profile(name)``
    :param phases: consecutive named periods of the run (see
        :class:`~mango.scenario.Phase`), e.g. a warm-up that is not recorded
    :param events: scheduled changes (see :class:`~mango.scenario.Event`):
        communication overrides, events sent to agents, actions

    Example::

        class EVParams(BaseModel, extra="forbid"):
            n: int = 3
            soc: Uniform = Uniform(0.2, 0.8)

        class Params(ScenarioParams):
            ev: EVParams = EVParams()
            algorithm: Literal["greedy", "proportional"] = "greedy"
    """

    seed: int | None = Field(None, ge=0)
    time: TimeConfig = Field(default_factory=TimeConfig)
    comm: CommConfig = Field(default_factory=CommConfig)
    profiles: dict[ProfileName, ProfileSpec] = Field(default_factory=dict)
    phases: list[Phase] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_schedule(self) -> ScenarioParams:
        if self.phases or self.events:
            raise_params_problems(type(self).__name__, params_schedule_problems(self))
        return self


# --------------------------------------------------------------------------- types

_ANY = object()
_MAPPING_ORIGINS = (
    dict,
    collections.abc.Mapping,
    collections.abc.MutableMapping,
)
_SEQUENCE_ORIGINS = (
    list,
    tuple,
    collections.abc.Sequence,
    collections.abc.MutableSequence,
)


def _flatten(tp: Any) -> list:
    origin = get_origin(tp)
    if origin is Annotated:
        return _flatten(get_args(tp)[0])
    if origin is Union or origin is types.UnionType:
        members = []
        for arg in get_args(tp):
            if arg is not type(None):
                members.extend(_flatten(arg))
        return members
    return [tp]


def _is_model(tp: Any) -> bool:
    return isinstance(tp, type) and issubclass(tp, BaseModel)


def _expand_models(types_: Iterable) -> list:
    out = []
    for tp in types_:
        for member in _flatten(tp):
            if member is Distribution:
                out.extend(distribution_kinds().values())
            else:
                out.append(member)
    return out


def _field_annotation(model: type[BaseModel], name: str) -> Any:
    fields = model.model_fields
    if name in fields:
        return fields[name].annotation
    for field in fields.values():
        if field.alias == name:
            return field.annotation
    return None


def _children(tp: Any, segment: str) -> list | None:
    if tp is _ANY or tp is Any or tp is object:
        return [_ANY]
    if _is_model(tp):
        annotation = _field_annotation(tp, segment)
        if annotation is not None:
            return [annotation]
        return [_ANY] if tp.model_config.get("extra") == "allow" else None
    origin = get_origin(tp) or tp
    args = get_args(tp)
    if origin in _MAPPING_ORIGINS:
        return [args[1] if len(args) == 2 else _ANY]
    if origin in _SEQUENCE_ORIGINS:
        index = list_index(segment)
        if index is None:
            # items with a name field are addressed by name: events.outage.at
            item = args[0] if args and origin is not tuple else None
            named = [
                m for m in _flatten(item) if _is_model(m) and "name" in m.model_fields
            ]
            return [item] if named and not segment.startswith(("+", "-")) else None
        if not args:
            return [_ANY]
        if origin is tuple and not (len(args) == 2 and args[1] is Ellipsis):
            return [args[index]] if index < len(args) else None
        return [args[0]]
    return None


def _type_name(tp: Any) -> str:
    return getattr(tp, "__name__", None) or str(tp)


def _close_match(name: str, known: Iterable[str]) -> str | None:
    """The known name closest to *name*: same letters in another case first."""
    known = [str(k) for k in known]
    lowered = {k.lower(): k for k in known}
    if name.lower() in lowered:
        return lowered[name.lower()]
    close = difflib.get_close_matches(name.lower(), list(lowered), n=1, cutoff=0.6)
    return lowered[close[0]] if close else None


def _describe_unknown(segments: list[str], depth: int, candidates: list) -> str:
    segment = segments[depth]
    parent = ".".join(segments[:depth])
    models = [tp for tp in candidates if _is_model(tp)]
    if not models:
        if any(get_origin(tp) in _SEQUENCE_ORIGINS for tp in candidates):
            return (
                f"{parent!r} is a list; address an item by its index "
                f"('{parent}.0') or, for items with a name, by the name"
            )
        names = ", ".join(sorted({_type_name(tp) for tp in candidates}))
        return f"{parent!r} has type {names} and no sub-parameters"
    known = sorted({name for model in models for name in model.model_fields})
    owner = " | ".join(dict.fromkeys(model.__name__ for model in models))
    text = f"{owner} has no field {segment!r}"
    close = _close_match(segment, known)
    if close:
        suggestion = f"{parent}.{close}" if parent else close
        text += f"; did you mean {suggestion!r}?"
    elif len(known) <= 20:
        text += f" (known: {', '.join(known)})"
    if not close and depth == 0 and models == [ScenarioParams]:
        text += (
            "; the scenario declares no parameters of its own: declare them under "
            "'parameters:' in its scenario file, or subclass ScenarioParams "
            "(class Params(ScenarioParams): ...) and pass it as "
            "Scenario(..., params=Params) / 'params: module:Params'"
        )
    return text


def _unknown_reason(model: type[BaseModel], path: str) -> str | None:
    segments = split_path(path)
    segments[-1] = split_marker(segments[-1])[1]
    candidates: list = [model]
    for depth, segment in enumerate(segments):
        expanded = _expand_models(candidates)
        if any(tp is _ANY or tp is Any for tp in expanded):
            return None
        found: list = []
        for tp in expanded:
            found.extend(_children(tp, segment) or [])
        if not found:
            return _describe_unknown(segments, depth, expanded)
        candidates = found
    return None


def _sentence(text: str) -> str:
    return text if text.endswith((".", "?", "!")) else text + "."


def _params_model(target: Any) -> type[BaseModel]:
    if _is_model(target):
        return target
    model = getattr(target, "params_model", None)
    if _is_model(model) and callable(getattr(target, "param_defaults", None)):
        return model
    raise TypeError(
        "Expected a params model class or a scenario (an object with a "
        "'params_model' class and a 'param_defaults()' method), got "
        f"{type(target).__name__}."
    )


def check_path(target: Any, path: str) -> None:
    """Check that dotted *path* names a parameter of the params model.

    The walk follows pydantic fields through ``Optional``/``Union``/
    ``Annotated``, accepts a segment that any member of a (discriminated)
    union knows, any key of ``dict[str, X]`` fields, and integer indices of
    lists (``households.0.pv_kw`` updates one item of the list; the item
    must exist when the parameters are loaded).  A ``+``/``-`` prefix on the
    last segment is ignored.

    :param target: a params model class or a scenario
    :param path: the dotted path, e.g. ``"ev.capacity_kwh"``
    :raises ParamsError: for unknown segments, with a did-you-mean suggestion

    Example::

        check_path(Params, "ev.capactiy_kwh")
        # ParamsError: Unknown parameter 'ev.capactiy_kwh': EVParams has no field
        #   'capactiy_kwh'; did you mean 'ev.capacity_kwh'?
    """
    model = _params_model(target)
    reason = _unknown_reason(model, path)
    if reason is not None:
        raise ParamsError(f"Unknown parameter {path!r}: {_sentence(reason)}", path=path)


def admits_distribution(target: Any, path: str) -> bool | None:
    """Whether the parameter at dotted *path* accepts a distribution value.

    :param target: a params model class or a scenario
    :param path: the dotted path, e.g. ``"homes.pv_kwp"``
    :return: ``True`` when the field's type includes a
        :class:`~mango.scenario.Distribution` (also inside ``Optional``,
        unions and ``Annotated``), ``False`` when it does not, ``None`` when
        the path is unknown or the type is ``Any``

    Example::

        admits_distribution(Params, "homes.pv_kwp")    # True for a Dist field
        admits_distribution(Params, "homes.hp_share")  # False for a float
    """
    model = _params_model(target)
    segments = split_path(path)
    segments[-1] = split_marker(segments[-1])[1]
    candidates: list = [model]
    for segment in segments:
        expanded = _expand_models(candidates)
        if any(tp is _ANY or tp is Any for tp in expanded):
            return None
        found: list = []
        for tp in expanded:
            found.extend(_children(tp, segment) or [])
        if not found:
            return None
        candidates = found
    flat = [member for tp in candidates for member in _flatten(tp)]
    if any(member is _ANY or member is Any or member is object for member in flat):
        return None
    return any(
        isinstance(member, type) and issubclass(member, Distribution) for member in flat
    )


# --------------------------------------------------------------------------- loading


def _normalize(types_: list, value: Any) -> Any:
    """Expand distribution shorthands where the model expects a distribution.

    After this, layers merge correctly: ``{uniform: [..]}`` over
    ``{kind: normal, ...}`` is a kind switch, and a scalar for a ``Uniform``
    field overrides both bounds.
    """
    flat = [member for tp in types_ for member in _flatten(tp)]
    if any(tp is _ANY for tp in flat):
        return value
    distributions = [
        tp for tp in flat if isinstance(tp, type) and issubclass(tp, Distribution)
    ]
    if distributions:
        target = (
            distributions[0]
            if len(distributions) == 1 and distributions[0] is not Distribution
            else None
        )
        tagged = (
            isinstance(value, Mapping)
            and len(value) == 1
            and "kind" not in value
            and next(iter(value)) in distribution_kinds()
        )
        only_distributions = len(distributions) == len(flat)
        plain = not isinstance(value, Mapping | Distribution | list | tuple)
        if tagged or (
            only_distributions
            and (plain or (target is not None and isinstance(value, list | tuple)))
        ):
            try:
                return expand_shorthand(value, target)
            except ValueError:
                return value
    if isinstance(value, BaseModel):
        value = _instance_data(value)
    if not isinstance(value, Mapping):
        return value
    models = _expand_models(flat)
    result = {}
    for key, item in value.items():
        name = split_marker(key)[1]
        children: list = []
        for tp in models:
            children.extend(_children(tp, str(name)) or [])
        result[key] = _normalize(children, item) if children else item
    return result


def _instance_data(model: BaseModel) -> Any:
    if isinstance(model, Distribution):
        return model.model_dump()
    data = {}
    for name in type(model).model_fields:
        if name in model.model_fields_set or name == "kind":
            data[name] = _instance_value(getattr(model, name))
    return data


def _instance_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _instance_data(value)
    if isinstance(value, Mapping):
        return {key: _instance_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_instance_value(item) for item in value]
    return value


_MISSING = object()


def _model_default(model: type[BaseModel], segments: tuple[str, ...]) -> Any:
    """Return the model's default value at *segments* (``_MISSING`` if it has none)."""
    current: Any = _MISSING
    tp: Any = model
    for segment in segments:
        if current is _MISSING:
            field = tp.model_fields.get(segment) if _is_model(tp) else None
            if field is None:
                return _MISSING
            default = field.get_default(call_default_factory=True)
            if default is PydanticUndefined:
                members = [m for m in _flatten(field.annotation) if _is_model(m)]
                if len(members) != 1:
                    return _MISSING
                tp = members[0]
                continue
            current = default
        elif isinstance(current, BaseModel):
            fields = type(current).model_fields
            current = getattr(current, segment) if segment in fields else _MISSING
        elif isinstance(current, Mapping):
            current = current.get(segment, _MISSING)
        elif isinstance(current, list | tuple):
            try:
                index = item_index(current, segment)
            except ParamsError:
                index = None
            current = (
                current[index]
                if index is not None and index < len(current)
                else _MISSING
            )
        else:
            return _MISSING
        if current is _MISSING:
            return _MISSING
    return current


def _variant_models(model: type[BaseModel], path: tuple[str, ...]) -> list:
    """The model classes a value at *path* may validate as (union members)."""
    types_: list = [model]
    for segment in path:
        children: list = []
        for tp in _expand_models(types_):
            children.extend(_children(tp, segment) or [])
        if not children:
            return []
        types_ = children
    return [tp for tp in _expand_models(types_) if _is_model(tp)]


def _default_kind(model: type[BaseModel], path: tuple[str, ...]) -> str | None:
    """The ``kind`` of the default at *path* when *path* holds a tagged union of models."""
    members = [
        m
        for m in _variant_models(model, path)
        if "kind" in m.model_fields and not issubclass(m, Distribution)
    ]
    if len(members) < 2:
        return None
    default = _model_default(model, path)
    if isinstance(default, BaseModel):
        kind = getattr(default, "kind", None)
    elif isinstance(default, Mapping):
        kind = default.get("kind")
    else:
        kind = None
    return kind if isinstance(kind, str) else None


def _node_at(merged: dict, path: tuple[str, ...]) -> dict | list | None:
    """The dict or list at *path* in *merged*, dicts created on the way; ``None`` if blocked."""
    node: Any = merged
    for segment in path:
        if isinstance(node, list):
            try:
                index = item_index(node, segment)
            except ParamsError:
                index = None
            if index is None or index >= len(node):
                return None
            node = node[index]
        else:
            if segment not in node:
                node[segment] = {}
            node = node[segment]
        if not isinstance(node, dict | list):
            return None
    return node


def _seedable_default(model: type[BaseModel], path: tuple, update: Mapping) -> Any:
    """The model default at *path* that a partial *update* should patch, else ``_MISSING``.

    A plain model's default ``kind`` is left out unless it was set
    explicitly: it is no layer's choice, so a later ``comm.kind=custom`` must
    not count as a switch that drops ``comm.factory`` of a lower layer (tagged
    unions get their default kind from :func:`_default_kind`).
    """
    default = _model_default(model, path)
    if isinstance(default, BaseModel):
        data = _instance_data(default)
        if not isinstance(default, Distribution) and "kind" not in (
            default.model_fields_set
        ):
            data.pop("kind", None)
        return data
    if isinstance(default, Mapping):
        return _instance_value(copy.deepcopy(dict(default)))
    if isinstance(default, list | tuple) and is_item_patch(default, update):
        return _instance_value(copy.deepcopy(list(default)))
    return _MISSING


def _seed_defaults(
    model: type[BaseModel], merged: dict, update: Mapping, segments: tuple = ()
) -> None:
    """Copy the model defaults into *merged* that merging *update* depends on.

    Model defaults are not a merge layer, but a layer that changes part of a
    value must change the *default* value where no lower layer set it:

    - ``ev.soc.high=0.9`` keeps the default ``low`` of the distribution, and
      ``grid.pv_share=0.8`` the other fields of a default model instance;
    - ``households.0.pv_kw=5`` updates the first default household;
    - ``"+households": [...]`` extends the default list;
    - ``algorithm.threshold_kw`` tunes the default variant of a tagged union
      (its ``kind`` is kept), and ``algorithm.kind=market`` over such a layer
      is a switch of the variant.
    """
    for key, value in update.items():
        marker, name = split_marker(key)
        path = (*segments, str(name))
        if not marker and isinstance(value, Mapping):
            parent = _node_at(merged, path[:-1])
            # a list item that a lower layer's (shorter) list does not have is
            # left alone: the merge reports the index as out of range
            if isinstance(parent, dict) and path[-1] not in parent:
                default = _seedable_default(model, path, value)
                if default is not _MISSING:
                    parent[path[-1]] = default
            kind = _default_kind(model, path)
            if kind is not None:
                node = _node_at(merged, path)
                if isinstance(node, dict):
                    node.setdefault("kind", kind)
            _seed_defaults(model, merged, value, path)
            continue
        if marker != "+":
            continue
        default = _model_default(model, path)
        if not isinstance(default, list | tuple):
            continue
        node = _node_at(merged, path[:-1])
        if isinstance(node, dict) and path[-1] not in node:
            node[path[-1]] = _instance_value(copy.deepcopy(list(default)))


def _kind_literals(model: type[BaseModel]) -> tuple:
    field = model.model_fields.get("kind")
    if field is None or get_origin(field.annotation) is not Literal:
        return ()
    return get_args(field.annotation)


def _selected_variant(expanded: list, data: Mapping) -> type[BaseModel] | None:
    """The member of a tagged union of models that ``data["kind"]`` selects."""
    kind = data.get("kind")
    if not isinstance(kind, str):
        return None
    variants = [
        tp
        for tp in expanded
        if _is_model(tp) and not issubclass(tp, Distribution) and _kind_literals(tp)
    ]
    if len(variants) < 2:
        return None
    chosen = [tp for tp in variants if kind in _kind_literals(tp)]
    return chosen[0] if len(chosen) == 1 else None


def _unknown_paths(
    types_: list, data: Any, prefix: str = ""
) -> list[tuple[str, str | None]]:
    """``(dotted path, variant kind)`` of keys in *data* that no candidate model has.

    Walks nested mappings (and list items) like :func:`check_path`, so a typo
    is found even in nested models that do not forbid extra keys (pydantic's
    default silently drops them).  In a tagged union of models (members with
    a ``Literal`` ``kind``), a mapping whose ``kind`` selects a member is
    checked against that member alone, and the entry names the kind (a field
    of another variant would be dropped silently); without a ``kind`` any
    member's field is accepted.  Values of ``Any``, keys of ``dict[str, X]``,
    models with an explicit ``extra="ignore"`` or ``"allow"`` and
    distribution payloads are not checked here (pydantic validates
    distributions).
    """
    expanded = _expand_models(types_)
    if not expanded or any(tp is _ANY or tp is Any or tp is object for tp in expanded):
        return []
    if all(isinstance(tp, type) and issubclass(tp, Distribution) for tp in expanded):
        return []
    found: list[tuple[str, str | None]] = []
    variant = None
    if isinstance(data, Mapping):
        variant = _selected_variant(expanded, data)
        if variant is not None:
            expanded = [variant]
        items = [(split_marker(key)[1], value) for key, value in data.items()]
    elif isinstance(data, list | tuple):
        items = list(enumerate(data))
    else:
        return found
    knows_fields = any(
        _is_model(tp) and tp.model_config.get("extra") != "ignore" for tp in expanded
    )
    for name, value in items:
        segment = str(name)
        children: list = []
        for tp in expanded:
            children.extend(_children(tp, segment) or [])
        if not children:
            if knows_fields and isinstance(data, Mapping):
                found.append((f"{prefix}{segment}", data["kind"] if variant else None))
            continue
        found.extend(_unknown_paths(children, value, f"{prefix}{segment}."))
    return found


def _flatten_sources(sources: Iterable) -> list:
    flat = []
    for source in sources:
        if isinstance(source, list | tuple):
            flat.extend(_flatten_sources(source))
        elif source is not None:
            flat.append(source)
    return flat


def _data_path(loc: tuple, data: Any) -> str:
    parts: list[str] = []
    node = data
    for position, item in enumerate(loc):
        if isinstance(node, Mapping) and item in node:
            node = node[item]
        elif (
            isinstance(node, list | tuple)
            and isinstance(item, int)
            and -len(node) <= item < len(node)
        ):
            node = node[item]
        elif position == len(loc) - 1 and isinstance(node, Mapping):
            node = None
        else:
            continue
        parts.append(str(item))
    return ".".join(parts)


def _variant_reason(loc: tuple, path: str) -> str:
    """Explain an unknown key that is a field of another variant of a tagged union."""
    known = path.split(".")
    tags = [item for item in loc if isinstance(item, str) and item not in known]
    if tags:
        return (
            f"it is not a field of the {tags[-1]!r} variant (set the 'kind' that has "
            "it, or drop the key)"
        )
    return "not a known parameter"


def _origin(origins: Mapping[str, str], path: str) -> str | None:
    while path:
        if path in origins:
            return origins[path]
        path = path.rpartition(".")[0]
    return None


def _short(value: Any, limit: int = 80) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _variant_kinds(model: type[BaseModel], path: str) -> list:
    segments = tuple(path.split(".")) if path else ()
    kinds: list = []
    for member in _variant_models(model, segments):
        field = member.model_fields.get("kind")
        if field is not None and get_origin(field.annotation) is Literal:
            kinds.extend(get_args(field.annotation))
    return kinds


_BOUND_ERRORS = frozenset(
    {"greater_than", "greater_than_equal", "less_than", "less_than_equal"}
)


def _field_description(model: type[BaseModel], path: str) -> str | None:
    """The ``Field(description=...)`` of the parameter at *path*, if any."""
    candidates: list = [model]
    info = None
    for segment in path.split("."):
        info, following = None, []
        for tp in _expand_models(candidates):
            if _is_model(tp) and segment in tp.model_fields and info is None:
                info = tp.model_fields[segment]
            following += [c for c in _children(tp, segment) or () if c is not _ANY]
        candidates = following
    return info.description if info is not None else None


def _value_hint(
    model: type[BaseModel], path: str, error: Mapping, texts: Mapping[str, str]
) -> str:
    """A fix suggestion for common value errors (empty when there is none).

    *texts* maps override paths to the value text typed for them.  Values
    out of bounds get the field's description (``Field(description=...)``).
    """
    if error["type"] in _BOUND_ERRORS:
        description = _field_description(model, path)
        if description:
            return f"; {path.rsplit('.', 1)[-1]}: {description}"
        return ""
    value = error.get("input")
    if (
        isinstance(value, str)
        and error["type"] in ("model_attributes_type", "model_type", "dict_type")
        and value in _variant_kinds(model, path)
    ):
        return f"; to choose that variant set {path}.kind={value}"
    if error["type"] == "string_type":
        if isinstance(value, Mapping):
            # a pattern or factory spec for a plain str parameter
            return (
                f"; {path} is a str parameter, so a mapping such as a pattern spec "
                "({small_world: {k: 4}}) needs a parameter typed str | dict (Python: "
                f"{path.rsplit('.', 1)[-1]}: str | dict = 'ring'; scenario file "
                f"parameters: {path.rsplit('.', 1)[-1]}: {{default: ring, type: "
                "pattern})"
            )
        if path in texts:
            typed = texts[path].replace("'", "''")
            return f"; quote the value to keep it text, e.g. {path}='{typed}'"
        if any(path.startswith(prefix + ".") for prefix in texts):
            return "; quote the value to keep it text"
    return ""


def _params_error(
    model: type[BaseModel],
    exc: ValidationError | None,
    data: dict,
    origins: dict,
    *,
    unknown: Sequence[dict] = (),
    texts: Mapping[str, str] | None = None,
) -> ParamsError:
    """One :class:`ParamsError` listing validation errors and *unknown* keys.

    *texts* maps override paths to the ``"path=value"`` value text they were
    parsed from (they get a quoting hint for string errors).
    """
    entries: list[dict] = []
    seen: set[tuple] = set()
    errors = [] if exc is None else exc.errors(include_url=False)
    for error in errors:
        path = _data_path(tuple(error["loc"]), data)
        if error["type"] == "extra_forbidden":
            reason = _unknown_reason(model, path) or _variant_reason(
                tuple(error["loc"]), path
            )
            message = f"unknown parameter; {reason}"
        else:
            message = error["msg"].removeprefix("Value error, ")
            if error["type"] not in ("missing", "value_error") and not isinstance(
                error.get("input"), Mapping
            ):
                message += f" (got {_short(error.get('input'))})"
            message += _value_hint(model, path, error, texts or {})
        key = (path, message)
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            {"path": path, "message": message, "source": _origin(origins, path)}
        )
    reported = {entry["path"] for entry in entries}
    entries += [entry for entry in unknown if entry["path"] not in reported]
    lines = [
        f"Invalid parameters for {model.__name__} ({len(entries)} "
        f"error{'s' if len(entries) != 1 else ''}):"
    ]
    for entry in entries:
        where = f" [from {entry['source']}]" if entry["source"] else ""
        lines.append(f"  {entry['path'] or '(root)'}: {entry['message']}{where}")
    first = entries[0] if entries else {"path": None, "source": None}
    return ParamsError(
        "\n".join(lines), path=first["path"], source=first["source"], errors=entries
    )


def _is_payload_path(path: str) -> bool:
    segments = path.split(".")
    return len(segments) >= 4 and segments[0] == "events" and segments[2] == "emit"


def _payload_problems(base: Mapping, paths: Iterable[str]) -> list[dict]:
    """Override paths that add keys to an event payload (``events.<i>.emit.<key>``).

    ``emit`` is free-form data, so the model cannot catch a typo such as
    ``events.0.emit.strenght=0.9``; an override may change the keys the
    payload has, not add new ones (set the whole payload to add keys).
    """
    problems = []
    events = base.get("events") if isinstance(base, Mapping) else None
    if not isinstance(events, list):
        return problems
    for path in paths:
        segments = split_path(path)
        if len(segments) < 4 or segments[0] != "events" or segments[2] != "emit":
            continue
        try:
            index = item_index(events, segments[1], "events")
        except ParamsError:
            continue
        if (
            index is None
            or index >= len(events)
            or not isinstance(events[index], Mapping)
        ):
            continue
        node = events[index].get("emit")
        prefix = ".".join(segments[:3])
        for depth, segment in enumerate(segments[3:], start=3):
            if not isinstance(node, Mapping):
                break
            key = split_marker(segment)[1]
            if key not in node:
                known = [str(k) for k in node]
                close = _close_match(key, known)
                hint = (
                    f"; did you mean {'.'.join([*segments[:depth], close])!r}?"
                    if close
                    else ""
                )
                problems.append(
                    {
                        "path": path,
                        "message": f"unknown payload key {key!r}{hint} The payload "
                        f"has {', '.join(known) or 'no keys'}; to add a key, set the "
                        f"payload as a whole ({prefix}={{...}}; it merges with the "
                        "keys it has)",
                        "source": "overrides",
                    }
                )
                break
            node = node[key]
    return problems


def kind_switch_rule(model: type[BaseModel]):
    """``switch(path)`` for :func:`~mango.scenario.deep_merge`: replace on a ``kind`` change only for unions.

    A dict whose ``kind`` changes is replaced when *model* types the path as
    a discriminated union of models (algorithm variants, distributions) and
    merged when it is one flat model whose ``kind`` field allows several
    values, i.e. a mode (:class:`~mango.scenario.CommConfig`); paths the schema does not type (``Any``, dicts)
    follow :func:`~mango.scenario.configfiles.generic_kind_switch`.  On
    paths typed ``str | dict`` or ``Any`` (pattern and factory specs) a
    one-key mapping replaces a one-key mapping with another key
    (``{random: {p: 0.2}}`` over ``{small_world: {k: 4}}``).
    """
    cache: dict[str, bool] = {}

    def switch(path: str) -> bool:
        found = cache.get(path)
        if found is None:
            try:
                members = _variant_models(model, tuple(split_path(path)))
            except (ParamsError, TypeError):
                members = []
            tagged = [m for m in members if "kind" in m.model_fields]
            if not tagged:
                found = generic_kind_switch(path)
            elif len(tagged) == 1 and not issubclass(tagged[0], Distribution):
                # one model: a multi-valued kind is a mode of it (CommConfig),
                # a single-valued kind tags a variant (another kind replaces)
                found = len(_kind_literals(tagged[0])) <= 1
            else:
                found = True
            cache[path] = found
        return found

    specs: dict[str, bool] = {}

    def replaces_specs(path: str) -> bool:
        found = specs.get(path)
        if found is None:
            found = specs[path] = _holds_specs(model, path)
        return found

    switch.replaces_specs = replaces_specs
    return switch


def _holds_specs(model: type[BaseModel], path: str) -> bool:
    """Whether *path* is typed ``str | dict`` or ``Any`` (a pattern or factory spec).

    One-key specs there replace each other in merges (``{random: ...}`` over
    ``{small_world: ...}``); paths typed as models or plain dicts merge.
    """
    types_: list = [model]
    try:
        for segment in split_path(path):
            children: list = []
            for tp in _expand_models(types_):
                children.extend(_children(tp, segment) or [])
            if not children:
                return False
            types_ = children
    except (ParamsError, TypeError):
        return False
    flat = [member for tp in types_ for member in _flatten(tp)]
    if any(_is_model(member) for member in flat):
        return False
    if any(member is _ANY or member is Any or member is object for member in flat):
        return True
    has_str = any(member is str for member in flat)
    has_mapping = any(
        (get_origin(member) or member) in _MAPPING_ORIGINS for member in flat
    )
    return has_str and has_mapping


def _merge_layers(
    model: type[BaseModel], layers: Iterable[tuple[str, Mapping, dict | None]]
) -> tuple[dict, dict[str, str]]:
    """Merge ``(label, data, origins)`` layers, lowest precedence first.

    Each layer's distribution shorthands are expanded against *model* and the
    model defaults it patches are copied in first (see :func:`_seed_defaults`).

    :return: the merged dict and ``{dotted path: label}`` of where each value
        came from
    """
    merged: dict = {}
    origins: dict[str, str] = {}
    switch = kind_switch_rule(model)
    for label, raw, data_origins in layers:
        data = _normalize([model], raw)
        try:
            _seed_defaults(model, merged, data)
        except ParamsError as exc:
            raise ParamsError(
                f"{exc} (in {label})", path=exc.path, source=label
            ) from None
        merged = merge_labeled(merged, data, label, switch=switch)
        if data_origins is None:
            mark_origins(origins, data, label)
        else:
            origins.update(data_origins)
    return merged, origins


def _unknown_entry(
    model: type[BaseModel], path: str, kind: str | None, source: str | None
) -> dict:
    if kind is not None:
        reason = (
            f"it is not a field of the {kind!r} variant (set the 'kind' that has "
            "it, or drop the key)"
        )
    else:
        reason = _unknown_reason(model, path) or "not a known parameter"
    return {"path": path, "message": f"unknown parameter; {reason}", "source": source}


def _unknown_entries(
    model: type[BaseModel],
    layers: Iterable[tuple[str, Mapping, dict | None]],
    merged: Mapping,
    origins: Mapping[str, str],
) -> list[dict]:
    """One error entry per unknown key (see :func:`_unknown_paths`).

    Every layer is checked on its own (a typo is reported even when a later
    layer replaces the value) and then the merged data, where the ``kind``
    of a tagged union can come from another layer or the model default
    (``algorithm.kind=market`` in a preset and ``algorithm.threshold=1`` in
    the overrides).
    """
    entries = []
    for label, raw, data_origins in layers:
        data = _normalize([model], raw)
        for path, kind in _unknown_paths([model], data):
            source = _origin(data_origins, path) if data_origins else None
            entries.append(_unknown_entry(model, path, kind, source or label))
    reported = {entry["path"] for entry in entries}
    for path, kind in _unknown_paths([model], merged):
        if path not in reported:
            entries.append(_unknown_entry(model, path, kind, _origin(origins, path)))
    return entries


def load_params(target: Any, *sources: Any, overrides: Any = None) -> BaseModel:
    """Build and validate the parameters of a scenario.

    Layers, lowest precedence first: the model's defaults, the scenario's
    defaults (``target.param_defaults()``), the *sources* in order, then the
    *overrides*.  Layers merge with :func:`deep_merge` semantics; ``"+name"``
    appends to the list of the layers below, or to the model's default list
    when no layer set it (``overrides={"+households": [...]}`` adds one).

    *target* is a params model class or a scenario: any object with a
    ``params_model`` attribute (the model class) and a ``param_defaults()``
    method returning a nested dict of scenario-level defaults.

    Sources may be paths (YAML/JSON, see :func:`read_config_file`), dicts,
    model instances (contributing only their explicitly set fields), ``None``
    (skipped) or lists of these.

    Data paths (``profiles.<name>.path`` and ``from_column`` paths) stay
    relative; a relative path that a preset file sets and that exists only
    relative to that file's folder is rewritten relative to the working
    directory (see :mod:`mango.scenario.profiles`).

    :param target: the params model class or a scenario
    :param sources: presets, applied in order
    :param overrides: ``["a.b=value", ...]`` or ``{"a.b": value}`` (see
        :func:`parse_overrides`); every path is checked with :func:`check_path`
    :return: the validated params model instance
    :raises ParamsError: for unknown keys (with did-you-mean suggestions) and
        invalid values; the message names each dotted path and the file or
        layer it came from

    Example::

        params = load_params(scenario, "presets/base.yaml", "presets/winter.yaml",
                             overrides=["ev.n=50", "algorithm=proportional"])
        params = load_params(Params, {"ev": {"n": 5}})
    """
    model = _params_model(target)
    layers: list[tuple[str, dict, dict | None]] = []
    files: dict[str, Path] = {}
    if not _is_model(target):
        defaults = target.param_defaults() or {}
        if not isinstance(defaults, Mapping):
            raise TypeError(
                f"param_defaults() must return a dict, got {type(defaults).__name__}"
            )
        label = getattr(target, "_defaults_label", "scenario defaults")
        layers.append((label, dict(defaults), None))
    for index, source in enumerate(_flatten_sources(sources), start=1):
        if isinstance(source, str | os.PathLike):
            data, origins, used = read_tracked(
                Path(source), keep=True, switch=kind_switch_rule(model)
            )
            layers.append((display_path(used[-1]), data, origins))
            files.update((display_path(item), item) for item in used)
        elif isinstance(source, Mapping):
            layers.append((f"source {index} (dict)", dict(source), None))
        elif isinstance(source, BaseModel):
            label = f"source {index} ({type(source).__name__} instance)"
            layers.append((label, _instance_data(source), None))
        else:
            raise TypeError(
                f"A params source must be a path, a dict or a model instance, got "
                f"{type(source).__name__} (source {index})."
            )
    items = override_items(overrides)
    parsed = {key: value for key, value, _ in items}
    problems = []
    for path in parsed:
        reason = _unknown_reason(model, path)
        if reason is not None:
            problems.append(
                {
                    "path": path,
                    "message": f"unknown parameter; {reason}",
                    "source": "overrides",
                }
            )
    if problems:
        lines = [f"Invalid overrides for {model.__name__}:"]
        lines += [f"  {item['path']}: {item['message']}" for item in problems]
        raise ParamsError(
            "\n".join(lines),
            path=problems[0]["path"],
            source="overrides",
            errors=problems,
        )
    if any(_is_payload_path(path) for path in parsed):
        below, _ = _merge_layers(model, layers)
        problems = _payload_problems(below, parsed)
        if problems:
            lines = [f"Invalid overrides for {model.__name__}:"]
            lines += [f"  {item['path']}: {item['message']}" for item in problems]
            raise ParamsError(
                "\n".join(lines),
                path=problems[0]["path"],
                source="overrides",
                errors=problems,
            )
    if parsed:
        nested: dict = {}
        for path, value in parsed.items():
            set_path(nested, path, value)
        layers.append(("overrides", nested, None))
    texts = {key: raw for key, _, raw in items if raw is not None}
    merged, origins = _merge_layers(model, layers)
    rebase_preset_paths(merged, origins, files)
    unknown = _unknown_entries(model, layers, merged, origins)
    try:
        params = model.model_validate(merged)
    except ValidationError as exc:
        raise _params_error(
            model, exc, merged, origins, unknown=unknown, texts=texts
        ) from exc
    if unknown:
        raise _params_error(model, None, merged, origins, unknown=unknown)
    return params


def _overlay_dump(target: Any, base: Mapping, overrides: Mapping[str, Any]) -> Any:
    """``load_params(target, base, overrides=overrides)`` for a complete *base* dump.

    *base* is a :func:`dump_params` result (every field present, already
    normalised and valid), so only the subtrees the dotted *overrides* touch
    are merged and checked again.  Returns ``None`` when the result is not
    valid; the caller then runs :func:`load_params` for the precise error.
    Studies use it to resolve thousands of design points quickly.
    """
    model = _params_model(target)
    nested: dict = {}
    if _payload_problems(base, overrides):
        return None
    try:
        for path, value in overrides.items():
            set_path(nested, path, value)
        data = _normalize([model], nested)
        if _unknown_paths([model], data):
            return None
        touched = {key: copy.deepcopy(base[key]) for key in data if key in base}
        _seed_defaults(model, touched, data)
        merged_part = deep_merge(touched, data, kind_switch=kind_switch_rule(model))
        if _unknown_paths([model], merged_part):
            return None
        return model.model_validate({**base, **merged_part})
    except (ParamsError, ValidationError, ValueError, TypeError):
        return None


def _resolve_dump(target: Any, data: Mapping, defaults: Mapping | None = None) -> Any:
    """``load_params(target, data)`` for a complete :func:`dump_params` dict, quickly.

    When the scenario defaults (*defaults*: ``target.param_defaults()``,
    passed in to reuse it) add nothing to *data*, the result is *data*
    validated as it is; otherwise (e.g. a preset deleted a default profile
    with ``"-name"``) the full :func:`load_params` runs.
    """
    if defaults is None and not _is_model(target):
        defaults = target.param_defaults() or {}
    if not defaults or deep_merge(defaults, data) == data:
        fast = _overlay_dump(target, data, {})
        if fast is not None:
            return fast
    return load_params(target, data)


# --------------------------------------------------------------------------- output


def params_schema(target: Any) -> dict:
    """Return the JSON Schema of the params model (for editor autocompletion).

    :param target: a params model class or a scenario
    :return: the JSON Schema dict

    Example::

        import json
        Path("params.schema.json").write_text(json.dumps(params_schema(Params), indent=2))
    """
    return _params_model(target).model_json_schema()


def dump_params(params: BaseModel) -> dict:
    """Return *params* as JSON-compatible data (``model_dump(mode="json")``).

    Durations appear as canonical strings (``"15min"``), datetimes as ISO
    strings (a named time zone as ``"...+02:00[Europe/Berlin]"``), sets as
    sorted lists (identical in every process) and distributions as
    ``{"kind": ..., ...}``; the result loads back with :func:`load_params`.

    :param params: a params model instance
    :return: plain data

    Example::

        dump_params(params)["time"]   # {"start": "2024-06-01T00:00:00", "horizon": "2d", "step": "15min"}
    """
    if not isinstance(params, BaseModel):
        raise TypeError(
            f"Expected a params model instance, got {type(params).__name__}"
        )
    return model_json_data(params)


def params_to_yaml(params: BaseModel) -> str:
    """Return *params* as a YAML document (e.g. a resolved preset).

    :param params: a params model instance
    :return: the YAML text

    Example::

        Path("config.resolved.yaml").write_text(params_to_yaml(params))
    """
    return yaml.safe_dump(
        dump_params(params),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
