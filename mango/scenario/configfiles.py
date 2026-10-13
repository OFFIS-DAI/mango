"""Raw configuration layers: config files with ``extends``, deep merging, dotted
paths and ``path=value`` overrides (plain data, no params models).

``__all__`` is the public part.  The other names without an underscore
(``CONFIG_SUFFIXES``, ``read_tracked``, ``merge_labeled``, ``mark_origins``,
``split_path``, ``split_marker``, ...) are the package-internal interface that
:mod:`~mango.scenario.params` and :mod:`~mango.scenario.yaml_scenario` build on.
"""

from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .errors import ParamsError

__all__ = ["deep_merge", "read_config_file", "parse_overrides", "set_path", "get_path"]

#: File suffixes of scenario, study and preset files.
CONFIG_SUFFIXES = (".yaml", ".yml", ".json")
# --------------------------------------------------------------------------- merging


def split_marker(key: Any) -> tuple[str, Any]:
    if isinstance(key, str) and len(key) > 1 and key[0] in "+-":
        return key[0], key[1:]
    return "", key


#: Mapping keys whose ``kind`` is a mode selector of one flat model with shared
#: fields (:class:`~mango.scenario.CommConfig`), not the tag of a discriminated
#: union: switching it keeps the other settings (``comm.kind=custom`` keeps
#: ``loss_percent``, ``delay_s`` and ``options``).
FLAT_KIND_KEYS = frozenset({"comm"})

#: ``switch(path) -> bool``: whether a dict at dotted *path* whose ``kind``
#: changes is replaced (a discriminated union switches variant) instead of
#: merged; ``None`` (default) applies :func:`generic_kind_switch`.  A switch
#: may also carry ``switch.replaces_specs(path) -> bool``: whether a one-key
#: spec (``{small_world: {...}}``) is replaced by a one-key spec with another
#: key instead of merged into a two-key mapping.
KindSwitch = Callable[[str], bool]


def generic_kind_switch(path: str) -> bool:
    """The schema-free rule: every ``kind`` change replaces, except under :data:`FLAT_KIND_KEYS`.

    ``components.<prefix>`` of a scenario file always switches (its ``kind``
    names the kit, whatever the prefix).
    """
    parent, _, last = path.rpartition(".")
    return last not in FLAT_KIND_KEYS or parent == "components"


def _is_spec_switch(
    base: Mapping, update: Mapping, path: str, switch: KindSwitch | None
) -> bool:
    """One one-key spec over another with a different key: ``{random: ...}`` over ``{ring: ...}``.

    Replaced, not merged, where the switch's ``replaces_specs(path)`` says
    the path holds such specs (a ``str | dict`` or ``Any`` parameter), so a
    pattern or factory spec never becomes a two-key mapping.
    """
    if len(base) != 1 or len(update) != 1:
        return False
    ((old_key, old_value),) = base.items()
    ((new_key, new_value),) = update.items()
    if old_key == new_key or not all(
        value is None or isinstance(value, Mapping | list | tuple)
        for value in (old_value, new_value)
    ):
        return False
    rule = getattr(switch, "replaces_specs", None)
    return bool(rule is not None and rule(path))


def _is_kind_switch(
    base: Mapping, update: Mapping, path: str, switch: KindSwitch | None
) -> bool:
    if _is_spec_switch(base, update, path, switch):
        return True
    if not ("kind" in base and "kind" in update and base["kind"] != update["kind"]):
        return False
    return (switch or generic_kind_switch)(path)


def list_index(key: Any) -> int | None:
    """The list index a mapping key stands for (``0``, ``"3"``), else ``None``."""
    if isinstance(key, int) and not isinstance(key, bool):
        return key if key >= 0 else None
    if isinstance(key, str) and key.isdigit() and key.isascii():
        return int(key)
    return None


def is_index_patch(update: Mapping) -> bool:
    return bool(update) and all(list_index(key) is not None for key in update)


def item_name(item: Any) -> Any:
    """The ``name`` of a list item (a mapping or a model), else ``None``."""
    if isinstance(item, Mapping):
        return item.get("name")
    return getattr(item, "name", None)


def item_index(items: Sequence, key: Any, path: str = "") -> int | None:
    """The list index *key* stands for: an index (``"0"``) or the name of an item.

    Lists of named items (``events``, ``phases``) can be addressed by name
    (``events.outage.at``); a name that two items share is ambiguous.

    :raises ParamsError: for a name used by several items
    """
    index = list_index(key)
    if index is not None or not isinstance(key, str):
        return index
    matches = [i for i, item in enumerate(items) if item_name(item) == key]
    if len(matches) > 1:
        raise ParamsError(
            f"Cannot address '{path}.{key}': items {matches[0]} and {matches[1]} of "
            f"{path!r} are both named {key!r}; use an index ('{path}.{matches[0]}') "
            "or unique names.",
            path=f"{path}.{key}" if path else key,
        )
    return matches[0] if matches else None


def is_item_patch(items: Sequence, update: Mapping) -> bool:
    """True when *update* changes items of the list *items* by index or name."""
    if not update:
        return False
    if is_index_patch(update):
        return True
    named = any(item_name(item) is not None for item in items)
    return named and all(
        isinstance(key, str) and not split_marker(key)[0] for key in update
    )


def _unknown_item(items: Sequence, key: Any, path: str) -> ParamsError:
    from ._util import did_you_mean

    names = [str(n) for n in (item_name(item) for item in items) if n is not None]
    hint = did_you_mean(str(key), names)
    return ParamsError(
        f"Cannot set '{path}.{key}': {path!r} has no item named {key!r}.{hint} "
        f"Names: {', '.join(names) or '(none)'}; or use an index (0..{len(items) - 1}).",
        path=f"{path}.{key}",
    )


def _merge_value(
    current: Any,
    value: Any,
    path: str,
    keep: bool = False,
    switch: KindSwitch | None = None,
) -> Any:
    """Merge one *value* over *current* (the value already at *path*)."""
    if isinstance(value, Mapping):
        if isinstance(current, list | tuple) and is_item_patch(current, value):
            return _patch_items(current, value, path, keep, switch)
        if isinstance(current, Mapping) and not _is_kind_switch(
            current, value, path, switch
        ):
            return _merge(current, value, f"{path}.", keep, switch)
        return _merge({}, value, f"{path}.", keep, switch)
    return copy.deepcopy(value)


def _patch_items(
    items: Sequence,
    update: Mapping,
    path: str,
    keep: bool = False,
    switch: KindSwitch | None = None,
) -> list:
    """Apply ``{index or name: value}`` to a copy of the list *items* at *path*."""
    result = copy.deepcopy(list(items))
    for key, value in update.items():
        index = item_index(result, key, path)
        if index is None:
            raise _unknown_item(result, key, path)
        if index >= len(result):
            raise ParamsError(
                f"Cannot set '{path}.{key}': {path!r} has {len(result)} item"
                f"{'s' * (len(result) != 1)} (indices 0..{len(result) - 1}); "
                f"override the whole list or append with '+{path.rpartition('.')[2]}'.",
                path=f"{path}.{key}",
            )
        result[index] = _merge_value(
            result[index], value, f"{path}.{key}", keep, switch
        )
    return result


def _merge(
    base: Mapping,
    update: Mapping,
    prefix: str,
    keep: bool = False,
    switch: KindSwitch | None = None,
) -> dict:
    """Merge *update* over *base*; see :func:`deep_merge`.

    With *keep* the result is itself a layer that is merged over lower layers
    later (a preset file merged over the files it extends): a ``"-name"``
    marker is kept so that it also deletes ``name`` below, and a ``"+name"``
    marker whose list is not in *base* is kept (merged with an earlier
    ``"+name"``) so that it appends to the list below.
    """
    result = {key: copy.deepcopy(value) for key, value in base.items()}
    for key, value in update.items():
        marker, name = split_marker(key)
        path = f"{prefix}{name}"
        if keep and marker == "+" and name not in result and f"-{name}" not in result:
            items = list(value) if isinstance(value, list | tuple) else [value]
            result[key] = list(result.get(key, [])) + copy.deepcopy(items)
        elif keep and marker == "-":
            result.pop(name, None)
            result.pop(f"+{name}", None)
            result[key] = None
        elif marker == "+":
            current = result.get(name, [])
            if isinstance(current, tuple):
                current = list(current)
            if not isinstance(current, list):
                raise ParamsError(
                    f"Cannot append to {path!r} with {key!r}: it is a "
                    f"{type(current).__name__}, not a list.",
                    path=path,
                )
            items = list(value) if isinstance(value, list | tuple) else [value]
            result[name] = current + copy.deepcopy(items)
        elif marker == "-":
            result.pop(name, None)
        else:
            if keep:
                result.pop(f"+{name}", None)  # a plain value replaces the list below
            result[name] = _merge_value(result.get(name), value, path, keep, switch)
    return result


def deep_merge(
    base: Mapping, update: Mapping, *, kind_switch: KindSwitch | None = None
) -> dict:
    """Merge *update* over *base* and return a new dict (inputs are not changed).

    - nested dicts merge recursively; scalars and lists replace;
    - a dict whose keys are all list indices (``{"0": {...}}``) over a list
      updates those items (``households.0.pv_kw=5``); an index past the end
      raises ``ParamsError``; items with a ``name`` can also be addressed by
      it (``{"outage": {...}}`` over ``events``, ``events.outage.at=3h``);
    - ``"+name": items`` appends items (a list or one value) to the list
      ``name``;
    - ``"-name": ...`` deletes ``name`` (the value is ignored);
    - when both sides are dicts with different ``"kind"`` values the update
      replaces the base dict (switching a discriminated union, e.g.
      ``algorithm.kind=market`` drops greedy-only fields) -- except under
      ``comm``, whose ``kind`` selects a mode of one flat model
      (``comm.kind=custom`` keeps ``loss_percent``, ``delay_s`` and
      ``options``).  :func:`~mango.scenario.load_params` decides this from
      the params model instead: only real discriminated unions switch.

    :param base: the lower-precedence mapping
    :param update: the higher-precedence mapping
    :param kind_switch: ``fn(dotted_path) -> bool`` deciding whether a
        ``kind`` change at that path replaces the dict (default: the rule
        above)
    :return: the merged dict
    :raises ParamsError: when ``"+name"`` targets a value that is not a list,
        or a list index is out of range

    Example::

        deep_merge({"ev": {"n": 3, "tags": ["a"]}}, {"ev": {"n": 5, "+tags": ["b"]}})
        # {"ev": {"n": 5, "tags": ["a", "b"]}}
        deep_merge({"algo": {"kind": "greedy", "threshold": 1}}, {"algo": {"kind": "market"}})
        # {"algo": {"kind": "market"}}
        deep_merge({"comm": {"kind": "simple", "delay_s": 0.1}},
                   {"comm": {"kind": "custom", "factory": "m:f"}})
        # {"comm": {"kind": "custom", "delay_s": 0.1, "factory": "m:f"}}
    """
    return _merge(base, update, "", switch=kind_switch)


# --------------------------------------------------------------------------- files


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def load_mapping(path: Path) -> dict:
    shown = display_path(path)
    suffix = path.suffix.lower()
    if suffix not in CONFIG_SUFFIXES:
        raise ParamsError(
            f"Unsupported config file {shown!r}: use a .yaml, .yml or .json file.",
            source=shown,
        )
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ParamsError(
            f"Config file not found: {shown!r} (cwd: {Path.cwd()}).", source=shown
        ) from None
    except OSError as exc:
        raise ParamsError(f"Cannot read config file {shown!r}: {exc}", source=shown)
    try:
        data = json.loads(text) if suffix == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ParamsError(f"Cannot parse {shown!r}: {exc}", source=shown) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ParamsError(
            f"{shown!r} must contain a mapping at the top level, got "
            f"{type(data).__name__}.",
            source=shown,
        )
    return data


def resolve_extends(ref: Any, referencing: Path) -> Path:
    shown = display_path(referencing)
    if not isinstance(ref, str | os.PathLike) or not str(ref).strip():
        raise ParamsError(
            f"{shown!r}: 'extends' must be a file path or a list of paths, got "
            f"{ref!r}.",
            path="extends",
            source=shown,
        )
    candidate = Path(ref).expanduser()
    tried = (
        [candidate]
        if candidate.is_absolute()
        else [Path.cwd() / candidate, referencing.parent / candidate]
    )
    for option in tried:
        if option.is_file():
            return option.resolve()
    raise ParamsError(
        f"{shown!r}: extends {str(ref)!r}, which was not found (tried "
        f"{', '.join(str(option) for option in dict.fromkeys(tried))}).",
        path="extends",
        source=shown,
    )


def mark_origins(
    origins: dict[str, str], data: Mapping, label: str, prefix: str = ""
) -> None:
    for key, value in data.items():
        path = f"{prefix}{split_marker(key)[1]}"
        origins[path] = label
        if isinstance(value, Mapping):
            mark_origins(origins, value, label, f"{path}.")


def read_tracked(
    path: Path,
    stack: tuple[Path, ...] = (),
    keep: bool = False,
    switch: KindSwitch | None = None,
) -> tuple[dict, dict[str, str], list[Path]]:
    """Read *path* with its ``extends`` parents merged in.

    :param keep: keep the ``"+name"``/``"-name"`` markers that are meant for
        the layers below the file (a preset merged by :func:`load_params`)
    :param switch: the ``kind`` switch rule (see :func:`deep_merge`)
    :return: the merged data, ``{dotted path: file label}`` and the files read
    """
    path = Path(path).expanduser().resolve()
    if path in stack:
        chain = " -> ".join(display_path(item) for item in (*stack, path))
        raise ParamsError(f"Cycle in 'extends': {chain}.", source=display_path(path))
    data = load_mapping(path)
    extends = data.pop("extends", None)
    parents = (
        []
        if extends is None
        else list(extends)
        if isinstance(extends, list | tuple)
        else [extends]
    )
    merged: dict = {}
    origins: dict[str, str] = {}
    files: list[Path] = []
    for ref in parents:
        parent = resolve_extends(ref, path)
        parent_data, parent_origins, parent_files = read_tracked(
            parent, (*stack, path), keep, switch
        )
        merged = merge_labeled(merged, parent_data, display_path(parent), keep, switch)
        origins.update(parent_origins)
        files.extend(item for item in parent_files if item not in files)
    merged = merge_labeled(merged, data, display_path(path), keep, switch)
    mark_origins(origins, data, display_path(path))
    files.append(path)
    return merged, origins, files


def merge_labeled(
    base: Mapping,
    update: Mapping,
    label: str,
    keep: bool = False,
    switch: KindSwitch | None = None,
) -> dict:
    try:
        return _merge(base, update, "", keep, switch)
    except ParamsError as exc:
        raise ParamsError(f"{exc} (in {label})", path=exc.path, source=label) from None


def read_config_file(path: str | os.PathLike) -> dict:
    """Read a YAML (``.yaml``/``.yml``) or JSON (``.json``) config file.

    A top-level ``extends: <path or list of paths>`` is resolved first:
    relative paths are looked up relative to the current working directory,
    then relative to the referencing file's directory.  Parents are merged in
    list order and the file itself over them, with :func:`deep_merge`
    semantics; cycles are detected.

    The result is a preset layer: ``"-name"`` markers and ``"+name"`` markers
    whose list none of the files sets are kept, so that passing the result to
    :func:`load_params` removes or appends to the defaults and the presets
    below, exactly as passing the path does.

    :param path: the file
    :return: the merged mapping (without the ``extends`` key)
    :raises ParamsError: for missing or unparsable files, non-mapping content
        or ``extends`` cycles (the message names the file)

    Example::

        # presets/winter.yaml:  extends: base.yaml
        #                       time: {start: 2024-01-15T00:00}
        data = read_config_file("presets/winter.yaml")
    """
    return read_tracked(Path(path), keep=True)[0]


# --------------------------------------------------------------------------- paths


def split_path(path: str) -> list[str]:
    if not isinstance(path, str):
        raise TypeError(f"A parameter path must be a str, got {type(path).__name__}")
    segments = path.strip().split(".")
    if any(not segment.strip() for segment in segments):
        raise ParamsError(
            f"Invalid parameter path {path!r}: expected dotted names like 'ev.n'.",
            path=path,
        )
    return [segment.strip() for segment in segments]


class _OverrideLoader(yaml.SafeLoader):
    """``SafeLoader`` with YAML 1.2 core-schema scalars for override values.

    YAML 1.1 reads ``010`` as 8 (octal), ``1:30`` as 90 (base 60) and
    ``on``/``no`` as booleans; on the command line these are almost always
    meant as ``10``, a text and a text.
    """


_OverrideLoader.yaml_implicit_resolvers = {
    first: [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag
        not in (
            "tag:yaml.org,2002:bool",
            "tag:yaml.org,2002:int",
            "tag:yaml.org,2002:float",
        )
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_OverrideLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)
_OverrideLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$"),
    list("-+0123456789"),
)
_OverrideLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(
        r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
        r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"
    ),
    list("-+0123456789."),
)


def _construct_int(loader: yaml.SafeLoader, node: yaml.Node) -> int:
    text = loader.construct_scalar(node)
    if text.startswith("0o"):
        return int(text[2:], 8)
    if text.startswith("0x"):
        return int(text[2:], 16)
    return int(text, 10)


_OverrideLoader.add_constructor("tag:yaml.org,2002:int", _construct_int)


def _parse_override(text: str) -> tuple[str, Any, str]:
    if "=" not in text:
        key = text.strip()
        if key.rsplit(".", 1)[-1].startswith("-") and len(key) > 1:
            split_path(key)  # a removal marker needs no value: '-events'
            return key, None, ""
        raise ParamsError(
            f"Invalid override {text!r}: expected 'path=value', e.g. 'agents.n=50', "
            "or a removal such as '-events' or 'time.-step'.",
            source="overrides",
        )
    key, raw = text.split("=", 1)
    key = key.strip()
    split_path(key)
    try:
        value = yaml.load(raw, Loader=_OverrideLoader) if raw.strip() else None
    except yaml.YAMLError as exc:
        raise ParamsError(
            f"Invalid override {text!r}: the value is not valid YAML ({exc}). Quote "
            "it, e.g. name='a: b'.",
            path=key,
            source="overrides",
        ) from exc
    return key, value, raw.strip()


def parse_overrides(overrides: Any) -> dict[str, Any]:
    """Parse overrides into an ordered ``{dotted path: value}`` dict.

    Accepts ``None``, one ``"a.b=value"`` string, a list of such strings, or
    a mapping ``{"a.b": value}`` (values taken as they are).  A path whose
    last segment starts with ``-`` removes that key (``"-events"``,
    ``"time.-step"``; a trailing ``=`` is optional).  String values
    are parsed as YAML with the YAML 1.2 rules for scalars: ``ev.n=50`` gives
    an int, ``ev.n=010`` the int 10, ``algorithm=market`` a str,
    ``flag=true`` a bool (``on``/``no`` stay text), ``time.horizon=1:30`` the
    text ``"1:30"`` and ``ev.soc={uniform: [0.1, 0.9]}`` a dict.  Quote values
    that must stay text (``label='2024'``).  Later entries for the same path
    win.

    :param overrides: the overrides
    :return: the parsed overrides in order
    :raises ParamsError: for malformed entries

    Example::

        parse_overrides(["ev.n=50", "ev.soc={uniform: [0.1, 0.9]}"])
        # {"ev.n": 50, "ev.soc": {"uniform": [0.1, 0.9]}}
    """
    return {key: value for key, value, _ in override_items(overrides)}


def override_items(overrides: Any) -> list[tuple[str, Any, str | None]]:
    """``(path, value, raw text)`` per override path, in order, later entries winning.

    The raw text is the value as typed after ``=`` (``None`` for mapping
    overrides, whose values are taken as they are).
    """
    if overrides is None:
        return []
    items: list[tuple[str, Any, str | None]] = []
    if isinstance(overrides, Mapping):
        for key, value in overrides.items():
            if not isinstance(key, str):
                raise ParamsError(
                    f"Override keys must be dotted path strings, got {key!r}.",
                    source="overrides",
                )
            split_path(key)
            items.append((key.strip(), value, None))
    elif isinstance(overrides, str):
        items.append(_parse_override(overrides))
    elif isinstance(overrides, Iterable):
        for entry in overrides:
            if not isinstance(entry, str):
                raise ParamsError(
                    f"Overrides must be 'path=value' strings, got {entry!r}.",
                    source="overrides",
                )
            items.append(_parse_override(entry))
    else:
        raise ParamsError(
            f"Overrides must be a list of 'path=value' strings or a dict, got "
            f"{type(overrides).__name__}.",
            source="overrides",
        )
    result: dict[str, tuple[str, Any, str | None]] = {}
    for item in items:
        result.pop(item[0], None)
        result[item[0]] = item
    return list(result.values())


def set_path(data: dict, path: str, value: Any) -> None:
    """Set ``data[a][b] = value`` for ``path="a.b"``, creating dicts on the way.

    A segment such as ``"0"`` indexes into an existing list.

    :param data: the nested dict to change in place
    :param path: dotted path
    :param value: the value to set
    :raises ParamsError: when a segment on the way holds a scalar, or a list
        index does not exist

    Example::

        d = {}
        set_path(d, "ev.n", 5)   # d == {"ev": {"n": 5}}
    """
    segments = split_path(path)
    node: Any = data
    for depth, segment in enumerate(segments):
        last = depth == len(segments) - 1
        where = ".".join(segments[:depth]) or "the root"
        if isinstance(node, list):
            index = item_index(node, segment, where)
            if index is None or index >= len(node):
                raise ParamsError(
                    f"Cannot set {path!r}: {where!r} is a list of {len(node)} items "
                    f"and {segment!r} is not one of its indices or item names.",
                    path=path,
                )
            if last:
                node[index] = value
                return
            node = node[index]
            continue
        if not isinstance(node, dict):
            raise ParamsError(
                f"Cannot set {path!r}: {where!r} is {node!r}, not a mapping.",
                path=path,
            )
        if last:
            node[segment] = value
            return
        if node.get(segment) is None:
            node[segment] = {}
        node = node[segment]


def get_path(obj: Any, path: str) -> Any:
    """Return the value at dotted *path* in nested mappings, sequences or objects.

    Each segment is looked up as a mapping key, a sequence index (``"0"``)
    or the name of a sequence item, or an attribute, so it works on dicts and
    on params models alike.

    :param obj: the root object
    :param path: dotted path, e.g. ``"ev.n"``
    :return: the value
    :raises KeyError: when a segment does not exist (the message names it)

    Example::

        get_path(params, "ev.n")            # 10
        get_path({"a": [{"b": 1}]}, "a.0.b")  # 1
    """
    current = obj
    segments = split_path(path)
    for depth, segment in enumerate(segments):
        if isinstance(current, Mapping):
            if segment in current:
                current = current[segment]
                continue
        elif isinstance(current, list | tuple):
            try:
                index = item_index(current, segment)
            except ParamsError:
                index = None
            if index is None and segment.startswith("-") and segment[1:].isdigit():
                index = int(segment)  # counted from the end
            if index is not None and -len(current) <= index < len(current):
                current = current[index]
                continue
        elif hasattr(current, segment):
            current = getattr(current, segment)
            continue
        where = ".".join(segments[:depth]) or "the root"
        raise KeyError(f"No {segment!r} in {where!r} while resolving {path!r}")
    return current
