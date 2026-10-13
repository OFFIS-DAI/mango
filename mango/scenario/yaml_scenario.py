"""
Scenario and study files: the YAML front-end of :class:`~mango.scenario.Scenario`
and :class:`~mango.scenario.Study`.

A *scenario file* compiles 1:1 to the Python builder API (it is never a
superset of it): ``params`` names the params model, ``parameters`` declares
further parameters by their defaults (compiled to a
:class:`~mango.scenario.ScenarioParams` subclass, see :func:`load_scenario`),
any params field may appear at the top level as a scenario default
(``seed``, ``time``, ``comm``, ``profiles``, ``phases``, ``events`` and your
own fields), ``components``
uses kits (:meth:`~mango.scenario.Scenario.use`), ``groups``/``links``/``record`` declare
groups, links and recorders, and functions are import references
``"package.module:name"``.  ``extends`` merges parent files first
(:func:`~mango.scenario.deep_merge` semantics, ``"+key"`` appends to a list,
``"-key"`` deletes a key).

A *study file* holds the arguments of :class:`~mango.scenario.Study`; its
``scenario`` is an import reference or a scenario file.  ``extends`` merges a
parent study: factors (``uncertainties``/``levers``) merge per factor path,
where a single value fixes a factor and a new spec replaces the parent's.

Relative paths in both formats (``extends``, a study's ``scenario`` file,
``base`` presets, ``design: {csv: ...}``, profile paths) are looked up
relative to the working directory first, then relative to the file that
sets them; ``out`` is relative to the working directory.

Example::

    # scenarios/ev.yaml
    name: ev_feeder
    params: ev_study.params:Params
    time: {start: 2024-06-01T00:00, horizon: 1d, step: 15min}
    components:
      grid: {kind: neighbourhood, n_households: 30}
    groups:
      coordinator: {factory: "ev_study.agents:make_coordinator", count: 1}
      ev: {factory: "ev_study.agents:make_ev", count: ev.n,
           install: "ev_study.agents:ev_install"}
    links:
      - {name: reports, from: grid.hh, to: coordinator, pattern: many_to_one}
      - {name: dispatch, from: ev, to: coordinator, pattern: many_to_one}
    record:
      - {name: soc_kwh, group: ev, attr: soc_kwh}
      - {name: import_kw, resource: feeder.import_kw}

    # studies/fleet.yaml
    scenario: scenarios/ev.yaml
    base: presets/base.yaml
    uncertainties: {ev.n: [5, 10, 20]}
    levers: {algorithm: [greedy, proportional]}
    replications: 10
    outcomes: [ev_study.kpi:self_consumption]
    out: results/fleet

    from mango.scenario import load_scenario, load_study_file

    result = load_scenario("scenarios/ev.yaml").run(seed=7)
    report = load_study_file("studies/fleet.yaml").run(workers=8)
"""

from __future__ import annotations

import copy
import difflib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    create_model,
    field_validator,
    model_validator,
)

from ._util import (
    content_hash,
    data_path_candidates,
    did_you_mean,
    import_ref_or_raise,
    to_plain,
)
from .builder import Scenario
from .configfiles import (
    CONFIG_SUFFIXES,
    display_path,
    get_path,
    load_mapping,
    parse_overrides,
    read_tracked,
    resolve_extends,
    set_path,
    split_marker,
)
from .context import check_every, every_text
from .errors import ParamsError, ScenarioError, StudyError
from .params import (
    ScenarioParams,
    _data_path,
    _origin,
    _variant_models,
    load_params,
)
from .profiles import rebase_preset_paths
from .reducers import check_kind, check_window

if TYPE_CHECKING:
    from .study import Study

__all__ = [
    "load_scenario",
    "load_study_file",
    "read_study_file",
    "scenario_file_schema",
    "study_file_schema",
    "is_scenario_file",
    "FileScenario",
    "SCENARIO_FILE_KEYS",
    "STUDY_FILE_KEYS",
    "document_kind",
]

_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
_REF_TEXT = "an import reference 'package.module:name'"


# --------------------------------------------------------------------------- helpers


def is_scenario_file(ref: Any) -> bool:
    """True when *ref* names a scenario file (``.yaml``, ``.yml`` or ``.json``).

    Example::

        is_scenario_file("scenarios/ev.yaml")      # True
        is_scenario_file("ev_study.scenario:scenario")   # False
    """
    if not isinstance(ref, str | os.PathLike):
        return False
    return os.fspath(ref).strip().lower().endswith(CONFIG_SUFFIXES)


def _check_ref(value: Any) -> Any:
    if not isinstance(value, str):
        raise ValueError(f"expected {_REF_TEXT}, got {value!r}")
    text = value.strip()
    module, sep, attr = text.partition(":")
    if not sep:
        module, sep, attr = text.rpartition(".")
    parts = [*module.split("."), *attr.split(".")]
    if not sep or not all(part.isidentifier() for part in parts):
        raise ValueError(f"expected {_REF_TEXT}, got {value!r}")
    return text


#: An import reference ``"package.module:name"`` (validated, not imported).
Ref = Annotated[str, BeforeValidator(_check_ref)]


def _plain_type(value: Any, allowed: tuple, expected: str) -> Any:
    if (isinstance(value, bool) and bool not in allowed) or not isinstance(
        value, allowed
    ):
        raise ValueError(f"expected {expected}, got {value!r}")
    return value


def _field_names(model: type[BaseModel]) -> list[str]:
    return [field.alias or name for name, field in model.model_fields.items()]


def _suggestion(name: str, known: list[str]) -> str:
    close = difflib.get_close_matches(str(name), known, n=1)
    return f"; did you mean {close[0]!r}?" if close else ""


def _validation_problems(
    model: type[BaseModel], exc: ValidationError, data: Mapping, origins: Mapping
) -> list[dict]:
    """``[{path, message, source}]`` for a validation error of a file format model."""
    problems: list[dict] = []
    seen: set[str] = set()
    for error in exc.errors(include_url=False):
        loc = tuple(error["loc"])
        path = _data_path(loc, data)
        if error["type"] == "extra_forbidden":
            parent = path.split(".")[:-1] if path else []
            known = sorted(
                {
                    name
                    for m in _variant_models(model, tuple(parent))
                    for name in _field_names(m)
                }
            )
            key = path.rpartition(".")[2]
            message = (
                f"unknown key{_suggestion(key, known)} (known: {', '.join(known)})"
            )
        elif error["type"] == "missing":
            message = "required key missing"
            path = ".".join(str(part) for part in loc)
        else:
            message = str(error["msg"]).removeprefix("Value error, ")
        if path in seen:
            continue
        seen.add(path)
        problems.append(
            {"path": path, "message": message, "source": _origin(origins, path)}
        )
    return problems


def _problem_text(header: str, problems: list[dict], main: str) -> str:
    lines = [f"{header} ({len(problems)} error{'s' * (len(problems) != 1)}):"]
    for item in problems:
        source = item.get("source")
        where = f" [from {source}]" if source and source != main else ""
        lines.append(f"  {item['path'] or '(top level)'}: {item['message']}{where}")
    return "\n".join(lines)


def _locate(path: str | os.PathLike) -> Path:
    """The file a (relative) path names: cwd first, then the study's project root."""
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    for option in data_path_candidates(os.fspath(candidate)):
        if option.is_file():
            return option
    return Path.cwd() / candidate


def _relative_ref(path: Path) -> str:
    """*path* relative to the working directory (posix), else absolute."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _resolve_file(value: str, files: list[Path], *, what: str, label: str) -> str:
    """Resolve a relative *value* against cwd, then the folders of *files*."""
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        tried = [candidate]
    else:
        tried = [Path.cwd() / candidate]
        tried += [file.parent / candidate for file in reversed(files)]
    for index, option in enumerate(dict.fromkeys(tried)):
        if option.is_file():
            return value if index == 0 else _relative_ref(option)
    listing = ", ".join(str(option) for option in dict.fromkeys(tried))
    raise StudyError(f"{label}: {what} {value!r} not found (tried {listing}).")


def _import_callable(ref: str, what: str, label: str) -> Any:
    obj = import_ref_or_raise(ref, f"{label}: {what}")
    if not callable(obj):
        raise ScenarioError(
            f"{label}: {what}: {ref!r} is a {type(obj).__name__}, not a function."
        )
    return obj


# --------------------------------------------------------------------------- scenario format


class _FileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScenarioFileGroup(_FileModel):
    """A group: ``scenario.group(name, count, factory, install=, position=, naming=)``."""

    factory: Ref = Field(
        description="'module:fn' building one agent: fn(e) -> Agent (e: EntityContext)"
    )
    count: int | str = Field(
        1,
        description="an int >= 0, a params path such as 'ev.n' or 'module:fn' of the "
        "params",
    )
    install: Ref | None = Field(
        None, description="'module:fn' returning install attributes: fn(e) -> dict"
    )
    position: Ref | None = Field(
        None, description="'module:fn' returning the entity's position: fn(e)"
    )
    naming: str | None = Field(
        None,
        description="entity names: a format string with {group} and {index}, or "
        "'module:fn' fn(e) -> str",
    )

    @field_validator("count", mode="before")
    @classmethod
    def _count(cls, value: Any) -> Any:
        return _plain_type(
            value, (int, str), "an int, a params path such as 'ev.n' or 'module:fn'"
        )

    @field_validator("naming", mode="before")
    @classmethod
    def _naming(cls, value: Any) -> Any:
        return value if value is None else _plain_type(value, (str,), "a str")


class ScenarioFileLink(_FileModel):
    """A link: ``scenario.link(name, from, to, pattern=, where=, directed=)``."""

    name: str = Field(
        description="link name = topology id (agents: neighbors(tid=...))"
    )
    source: str = Field(alias="from", description="source group, e.g. 'grid.hh'")
    target: str | None = Field(
        None, alias="to", description="target group (default: within the source)"
    )
    pattern: str | dict[str, Any] = Field(
        "all_to_all",
        description="a pattern name (one_to_one, many_to_one, one_to_many, "
        "all_to_all, complete, ring, line), a factory {random: {...}}, {by_attr: "
        "name}, {small_world: {k, p}}, {scale_free: {m}}, {grid: {cols}}, "
        "{networkx: {generator, ...}}, {edges: {file}}, {reverse: <pattern>}, "
        "{'module:factory': {...}}, 'module:fn', a parameter path or {param: path} "
        "(resolved per run; '$path' values inside factory arguments too)",
    )
    where: Ref | None = Field(
        None, description="'module:fn' filter: fn(src_entity, dst_entity) -> bool"
    )
    directed: bool = False
    delay: Any = Field(
        None,
        description="extra message delay along the link: seconds, a duration "
        "('1w') or a distribution ({choice: [0, 1w, 2w]}; a lead time of L "
        "steps is delay (L - 1) * step); '$path' (or a "
        "parameter path) reads it per run, also inside a distribution "
        "({uniform: [$lead.min_s, $lead.max_s]})",
    )
    loss: float | str | None = Field(
        None,
        description="extra probability that a message along the link is lost; "
        "'$path' (or a parameter path) reads it per run",
    )

    @field_validator("pattern", mode="before")
    @classmethod
    def _pattern(cls, value: Any) -> Any:
        return _plain_type(value, (str, dict), "a pattern name or a one-key mapping")


class ScenarioFileRecord(_FileModel):
    """A recorder.

    ``group`` with ``attr`` or ``fn`` records every agent of the group
    (``scenario.record``); ``fn`` alone is a world recorder
    (``scenario.record_world``); ``resource: name.attr.path`` records an
    attribute of a resource a kit provides.
    """

    name: str = Field(description="the variable name in the results")
    group: str | list[str] | None = Field(
        None,
        description="the group of a per-agent recorder, or a list of groups (one "
        "variable over all of them)",
    )
    attr: str | None = Field(None, description="(dotted) agent attribute")
    fn: Ref | None = Field(
        None, description="'module:fn': fn(agent) with group, else fn() or fn(ctx)"
    )
    resource: str | None = Field(
        None,
        description="'<resource>.<attribute path>': the provided resource, then "
        "attributes, mapping keys or list indices",
    )
    every: str | float | None = Field(
        None,
        description="record only at multiples of this duration, e.g. '1h', or a "
        "parameter path holding it, e.g. 'market.period_s'",
    )
    entity: str | None = Field(
        None, description="owner name of a world or resource recorder (default 'world')"
    )
    aggregate: str | None = Field(
        None,
        description="reduce across the group: count, sum, mean, min, max or "
        "'module:fn' fn(values) (per-agent recorders)",
    )
    window: str | None = Field(
        None,
        description="with every: last, sum, mean, min or max of the samples in "
        "(t - every, t]",
    )
    kind: str | None = Field(
        None,
        description="flow (the value describes the interval that ended at the row, "
        "e.g. a power set in on_step) or state (a level that holds until it "
        "changes, e.g. a queue length); integrate() and time_average() use it",
    )
    categories: list[Any] | None = Field(
        None,
        description="with aggregate: count, the categories that always get a row "
        "(0 while absent), in this order",
    )

    @field_validator("every", mode="before")
    @classmethod
    def _every(cls, value: Any) -> Any:
        if value is None:
            return value
        return _plain_type(
            value, (str, int, float), "a duration such as '1h' or a parameter path"
        )

    @model_validator(mode="after")
    def _one_source(self) -> ScenarioFileRecord:
        if self.resource is not None:
            extra = [key for key in ("group", "attr", "fn") if getattr(self, key)]
            if extra:
                raise ValueError(
                    f"a resource recorder takes no {', '.join(extra)}; use "
                    "'resource: <name>.<attribute>' alone"
                )
        elif self.group is not None:
            if (self.attr is None) == (self.fn is None):
                raise ValueError(
                    "a per-agent recorder needs exactly one of attr and fn"
                )
            if self.entity is not None:
                raise ValueError("entity applies to world and resource recorders only")
        elif self.aggregate is not None or self.categories is not None:
            raise ValueError(
                "aggregate reduces across the agents of a group; it needs group "
                "(world and resource recorders record one value)"
            )
        elif self.attr is not None:
            raise ValueError(
                "attr needs a group (agent attribute); for an attribute of a resource "
                "use 'resource: <name>.<attribute>'"
            )
        elif self.fn is None:
            raise ValueError(
                "a recorder needs group with attr or fn, fn alone (world) or resource"
            )
        return self


class ScenarioFileStop(_FileModel):
    """A stop condition: ``scenario.stop_when(fn, every=every)``."""

    fn: Ref = Field(description="'module:fn': fn(ctx) -> bool | str")
    every: str | float | None = Field(
        None, description="check only at multiples of this duration, e.g. '10s'"
    )


class ScenarioFileComponent(BaseModel):
    """A kit under its prefix: ``{kind: <kind or 'module:Class'>, <kit parameters>}``."""

    model_config = ConfigDict(extra="allow")

    kind: str = Field(
        description="a registered component kind (component_kinds()) or "
        "'module:Class'; the other keys are the kit's constructor arguments"
    )


class ScenarioFile(_FileModel):
    """The keys of a scenario file besides params fields (see the module docs)."""

    extends: str | list[str] | None = Field(
        None, description="parent file(s), merged first (deep merge, '+key', '-key')"
    )
    name: str | None = Field(
        None, description="scenario name (default: the file name; not inherited)"
    )
    params: Ref | None = Field(
        None, description="'module:Params', a ScenarioParams subclass"
    )
    parameters: dict[str, Any] | None = Field(
        None,
        description="parameters declared by their defaults, e.g. {epsilon: 0.2, n: "
        "100, opinion: {mu: 0.5}, noise: {normal: [0, 0.1]}}: the type of each is "
        "inferred from its default (bool, int, float, str, list, a distribution "
        "shorthand; a mapping becomes a nested group of parameters), or declared "
        "in the long form {default, type, min, max, gt, lt, choices, help}, e.g. "
        "{default: 0.25, min: 0, max: 1, help: confidence bound}, {default: "
        "pairwise, choices: [pairwise, messages]} or {default: ring, type: "
        "pattern} (a link pattern spec); compiled to a ScenarioParams subclass "
        "(extending 'params' when given)",
    )
    defaults: dict[str, Any] | None = Field(
        None,
        description="params defaults (dotted keys allowed); for fields whose names "
        "are scenario file keys",
    )
    environment: Ref | None = Field(
        None, description="'module:fn' environment factory fn(ctx)"
    )
    communication: Ref | None = Field(
        None, description="'module:fn' communication factory fn(ctx)"
    )
    components: dict[str, ScenarioFileComponent] = Field(
        default_factory=dict, description="kits by prefix"
    )
    groups: dict[str, ScenarioFileGroup] = Field(
        default_factory=dict, description="groups by name, built in this order"
    )
    links: list[ScenarioFileLink] = Field(default_factory=list)
    record: list[ScenarioFileRecord] = Field(default_factory=list)
    on_build: list[Ref] = Field(
        default_factory=list,
        description="'module:fn' hooks fn(ctx) run before the world starts",
    )
    stop_when: Ref | ScenarioFileStop | list[Ref | ScenarioFileStop] | None = Field(
        None,
        description="'module:fn' condition fn(ctx) -> bool | str that ends a run "
        "early, or {fn: 'module:fn', every: '10s'} (scenario.stop_when)",
    )
    metrics: dict[str, Ref] = Field(
        default_factory=dict,
        description="run-level metrics by name: 'module:fn' fn(ctx) evaluated once "
        "at the end of each run (scenario.metric)",
    )


#: The keys a scenario file reserves (all other top-level keys are params fields).
SCENARIO_FILE_KEYS: tuple[str, ...] = tuple(_field_names(ScenarioFile))


class FileScenario(Scenario):
    """A :class:`~mango.scenario.Scenario` compiled from a scenario file.

    Created by :func:`~mango.scenario.load_scenario`.  :attr:`ref` is the file's path
    (relative to the working directory when below it), so studies and their
    worker processes load the same file; :meth:`fingerprint` also covers the
    file's resolved content.

    Example::

        scenario = load_scenario("scenarios/ev.yaml")
        scenario.ref         # "scenarios/ev.yaml"
        scenario.path        # absolute path of the file
    """

    def __init__(
        self,
        name: str,
        params: type[ScenarioParams],
        *,
        path: Path,
        document: Mapping,
    ):
        super().__init__(name, params=params)
        self.path = path
        self._document = copy.deepcopy(dict(document))
        self._file_ref = _relative_ref(path)

    def __repr__(self) -> str:
        return f"FileScenario({self.name!r}, path={self._file_ref!r})"

    @property
    def ref(self) -> str:
        """The scenario file's path (relative to the working directory if below it)."""
        return self._file_ref

    @property
    def document(self) -> dict:
        """The file's content after ``extends`` (a copy)."""
        return copy.deepcopy(self._document)

    @property
    def _defaults_label(self) -> str:
        return f"scenario defaults of {self._file_ref}"

    def _declaration_stage(self, params: Any, seeds: Any) -> Any:
        try:
            return super()._declaration_stage(params, seeds)
        except ScenarioError as exc:
            if type(exc) is not ScenarioError or str(exc).startswith(self._file_ref):
                raise
            raise ScenarioError(f"{self._file_ref}: {exc}") from exc

    def fingerprint(self) -> str:
        """The structure hash of :meth:`~mango.scenario.Scenario.fingerprint` plus the file content."""
        return content_hash(
            {
                "scenario": super().fingerprint(),
                "file": to_plain(self._document, default=str),
            }
        )


def _scenario_problems(
    data: Mapping, origins: Mapping, label: str
) -> tuple[ScenarioFile | None, list[dict]]:
    reserved = {key: value for key, value in data.items() if key in SCENARIO_FILE_KEYS}
    try:
        return ScenarioFile.model_validate(reserved), []
    except ValidationError as exc:
        return None, _validation_problems(ScenarioFile, exc, reserved, origins)


def _params_model(ref: str | None, label: str) -> type[ScenarioParams]:
    if ref is None:
        return ScenarioParams
    model = import_ref_or_raise(ref, f"{label}: params")
    if not (isinstance(model, type) and issubclass(model, ScenarioParams)):
        raise ScenarioError(
            f"{label}: params: {ref!r} is {model!r}, not a ScenarioParams "
            "subclass (class Params(ScenarioParams): ...)."
        )
    return model


def _model_name(stem: str) -> str:
    words = [word for word in "".join(c if c.isalnum() else " " for c in stem).split()]
    return "".join(word[:1].upper() + word[1:] for word in words) or "File"


def _is_distribution(value: Any) -> bool:
    from .distributions import distribution_kinds

    if not isinstance(value, Mapping):
        return False
    kinds = distribution_kinds()
    if "kind" in value:
        # a group may have a field named kind (e.g. a long-form declaration)
        kind = value["kind"]
        return isinstance(kind, str) and kind in kinds
    key = next(iter(value), None)
    return len(value) == 1 and isinstance(key, str) and key in kinds


def _element_type(items: list) -> Any:
    if items and all(isinstance(item, bool) for item in items):
        return bool
    if items and all(
        isinstance(item, int) and not isinstance(item, bool) for item in items
    ):
        return int
    if items and all(
        isinstance(item, int | float) and not isinstance(item, bool) for item in items
    ):
        return float
    if items and all(isinstance(item, str) for item in items):
        return str
    return Any


#: Keys of a parameter's long form ``{default: ..., min: ..., help: ...}``.
_LONG_FORM_KEYS = ("default", "type", "min", "max", "gt", "lt", "choices", "help")

#: ``type:`` values of the long form.
_LONG_FORM_TYPES: dict[str, Any] = {
    "pattern": str | dict[str, Any],
    "bool": bool,
    "int": int,
    "float": float,
    "str": str,
    "list": list[Any],
    "dict": dict[str, Any],
    "any": Any,
}


def _is_long_form(value: Any) -> bool:
    return isinstance(value, Mapping) and "default" in value


def _warn_missing_default(value: Mapping, where: str) -> None:
    """Warn when a group of parameters looks like a long-form declaration.

    ``{help: ..., min: 0}`` without ``default`` is a nested group with the
    fields ``help`` and ``min``; that is rarely meant when it uses ``help``
    or ``choices``.
    """
    keys = {str(key) for key in value}
    if not keys <= set(_LONG_FORM_KEYS) - {"default"}:
        return
    if not (
        isinstance(value.get("help"), str) or isinstance(value.get("choices"), list)
    ):
        return
    from ._util import warn_user

    listed = ", ".join(str(key) for key in value)
    warn_user(
        f"{where}: {{{listed}}} has no 'default', so it declares a group of "
        f"parameters named {listed}; add 'default: <value>' to declare one "
        "parameter with these settings."
    )


def _long_form_field(name: str, spec: Mapping, where: str, owner: str) -> tuple:
    """Compile ``{default, type, min, max, gt, lt, choices, help}`` to a typed field."""
    from pydantic import TypeAdapter

    unknown = [str(key) for key in spec if key not in _LONG_FORM_KEYS]
    if unknown:
        hint = did_you_mean(unknown[0], list(_LONG_FORM_KEYS))
        raise ScenarioError(
            f"{where}: {unknown[0]!r} is not a key of a parameter declaration.{hint} "
            f"A mapping with 'default' declares one parameter with the keys "
            f"{', '.join(_LONG_FORM_KEYS)}; a nested group of parameters cannot have "
            "a parameter named 'default' (use a Python params model for that)."
        )
    default = spec["default"]
    kind = spec.get("type")
    choices = spec.get("choices")
    if kind is not None:
        if kind not in _LONG_FORM_TYPES:
            hint = did_you_mean(str(kind), list(_LONG_FORM_TYPES))
            raise ScenarioError(
                f"{where}: type {kind!r} is unknown.{hint} Types: "
                f"{', '.join(_LONG_FORM_TYPES)}."
            )
        annotation = _LONG_FORM_TYPES[kind]
    elif isinstance(default, Mapping) and not _is_distribution(default):
        raise ScenarioError(
            f"{where}: the default {default!r} is a mapping; say what it is with "
            "type: pattern (a link pattern spec such as {small_world: {k: 4}}) or "
            "type: dict (free settings)."
        )
    else:
        annotation, _ = _parameter_field(name, default, where, owner)
    if choices is not None:
        if not isinstance(choices, list) or not choices:
            raise ScenarioError(
                f"{where}: choices must be a non-empty list, got {choices!r}."
            )
        if not all(isinstance(item, str | int | float | bool) for item in choices):
            raise ScenarioError(
                f"{where}: choices must be strings, numbers or booleans, got "
                f"{choices!r}."
            )
        annotation = Literal[tuple(choices)]
    bounds = {
        target: spec[key]
        for key, target in (("min", "ge"), ("max", "le"), ("gt", "gt"), ("lt", "lt"))
        if spec.get(key) is not None
    }
    for key, bound in bounds.items():
        if isinstance(bound, bool) or not isinstance(bound, int | float):
            raise ScenarioError(f"{where}: {key} must be a number, got {bound!r}.")
    if bounds and annotation not in (int, float):
        raise ScenarioError(
            f"{where}: min/max/gt/lt bound numbers, but the parameter is "
            f"{getattr(annotation, '__name__', annotation)} (default {default!r})."
        )
    description = spec.get("help")
    if description is not None and not isinstance(description, str):
        raise ScenarioError(f"{where}: help must be a text, got {description!r}.")
    constraints = {**bounds}
    if description is not None:
        constraints["description"] = description
    try:
        checked = TypeAdapter(
            Annotated[annotation, Field(**constraints)]
        ).validate_python(default)
    except ValidationError as exc:
        raise ScenarioError(
            f"{where}: the default {default!r} does not satisfy its own declaration: "
            f"{exc.errors()[0]['msg']}."
        ) from None
    if isinstance(checked, list | dict):
        return annotation, Field(
            default_factory=lambda v=checked: copy.deepcopy(v), **constraints
        )
    return annotation, Field(checked, **constraints)


def _parameter_field(name: str, value: Any, where: str, owner: str) -> tuple:
    from pydantic import TypeAdapter

    from .distributions import Dist

    if not name.isidentifier() or name.startswith("_"):
        raise ScenarioError(
            f"{where}: {name!r} is not a valid parameter name (use letters, digits "
            "and '_', not starting with a digit or '_')."
        )
    if _is_long_form(value):
        return _long_form_field(name, value, where, owner)
    if isinstance(value, bool):
        return bool, value
    if isinstance(value, int):
        return int, value
    if isinstance(value, float):
        return float, value
    if isinstance(value, str):
        return str, value
    if value is None:
        return Any, None
    if isinstance(value, list):
        element = _element_type(value)
        return list[element], Field(default_factory=lambda v=value: copy.deepcopy(v))
    if _is_distribution(value):
        try:
            default = TypeAdapter(Dist).validate_python(value)
        except ValidationError as exc:
            raise ScenarioError(
                f"{where}: invalid distribution {value!r}: {exc.errors()[0]['msg']}"
            ) from None
        return Dist, default
    if isinstance(value, Mapping):
        if not value:
            return dict[str, Any], Field(default_factory=dict)
        _warn_missing_default(value, where)
        nested = _nested_parameters(value, f"{owner}{_model_name(name)}", where)
        return nested, Field(default_factory=nested)
    raise ScenarioError(
        f"{where}: cannot infer a parameter type from {value!r} "
        f"({type(value).__name__}); use a bool, number, string, list, mapping or a "
        "distribution shorthand such as {normal: [0, 1]}."
    )


def _nested_parameters(block: Mapping, name: str, where: str) -> type[BaseModel]:
    fields = {
        key: _parameter_field(str(key), value, f"{where}.{key}", name)
        for key, value in block.items()
    }
    return create_model(name, __config__=ConfigDict(extra="forbid"), **fields)


def _parameters_model(
    block: Mapping, base: type[ScenarioParams], stem: str, label: str
) -> type[ScenarioParams]:
    """Compile a ``parameters:`` block to a subclass of *base* (types from defaults)."""
    taken = [key for key in block if key in base.model_fields]
    if taken:
        raise ScenarioError(
            f"{label}: parameters.{taken[0]}: {base.__name__} already has a field "
            f"{taken[0]!r}; set its value at the top level ({taken[0]}: ...) instead "
            "of declaring it."
        )
    clash = [key for key in block if key in SCENARIO_FILE_KEYS]
    if clash:
        raise ScenarioError(
            f"{label}: parameters.{clash[0]}: {clash[0]!r} is a scenario file key; "
            "choose another parameter name."
        )
    name = f"{_model_name(stem)}Parameters"
    fields = {
        str(key): _parameter_field(str(key), value, f"{label}: parameters.{key}", name)
        for key, value in block.items()
    }
    return create_model(name, __base__=base, **fields)


def _use_components(
    scenario: Scenario, spec: ScenarioFile, origins: Mapping, label: str
) -> None:
    from .components import resolve_component

    for prefix, entry in spec.components.items():
        where = f"components.{prefix}"
        source = _origin(origins, where) or label
        try:
            cls = resolve_component(entry.kind)
        except (ScenarioError, TypeError) as exc:
            raise ScenarioError(f"{label}: {where}.kind: {exc}") from exc
        kwargs = dict(entry.model_extra or {})
        try:
            component = cls(**kwargs)
        except ParamsError as exc:
            raise ParamsError(
                f"{label}: {where}: {exc}",
                path=f"{where}.{exc.path}" if exc.path else where,
                source=source,
            ) from exc
        except TypeError as exc:
            raise ScenarioError(
                f"{label}: {where}: {cls.__name__}({', '.join(kwargs)}) failed: {exc}"
            ) from exc
        try:
            scenario.use(component, prefix=prefix)
        except (ScenarioError, TypeError) as exc:
            raise ScenarioError(f"{label}: {where}: {exc}") from exc


def _nested(values: Mapping) -> dict:
    nested: dict = {}
    for key, value in values.items():
        set_path(nested, str(key), copy.deepcopy(value))
    return nested


def _check_params(
    scenario: Scenario,
    layer: Mapping,
    origins: Mapping,
    label: str,
) -> None:
    """Validate the file's params defaults now, naming the file (and parent files).

    ``phases`` and ``events`` are checked item by item only: their checks
    against ``time`` (horizon, calendar start) depend on the presets of a run
    and happen when it is planned or built.
    """
    model = scenario.params_model
    schedule = {key: layer[key] for key in ("phases", "events") if key in layer}
    rest = {key: value for key, value in layer.items() if key not in schedule}
    problems = []
    try:
        load_params(scenario, rest)
    except ParamsError as exc:
        for entry in exc.errors:
            source = entry.get("source")
            if source == "source 1 (dict)":  # load_params' label of *rest*
                source = _origin(origins, entry.get("path") or "") or label
            elif source is None:
                continue  # e.g. a required field the file need not set
            problems.append({**entry, "source": source})
    if schedule:
        items = create_model(
            "ScheduleItems",
            **{key: (model.model_fields[key].annotation, ...) for key in schedule},
        )
        try:
            items.model_validate(schedule)
        except ValidationError as exc:
            for entry in _validation_problems(items, exc, schedule, origins):
                problems.append({**entry, "source": entry["source"] or label})
    if problems:
        first = problems[0]
        raise ParamsError(
            _problem_text(
                f"Invalid parameters in scenario file {label!r} for {model.__name__}",
                problems,
                label,
            ),
            path=first["path"],
            source=first["source"],
            errors=problems,
        )


def _unknown_top_level(
    data: Mapping, model: type[BaseModel], prefixes: Any, origins: Mapping
) -> list[dict]:
    """Problems for top-level keys that are neither file keys nor params fields."""
    allowed = [*SCENARIO_FILE_KEYS, *model.model_fields, *prefixes]
    problems = []
    for key in data:
        if key in allowed:
            continue
        suggestion = _suggestion(key, allowed)
        hint = "" if suggestion else _declare_hint(key, data.get(key), model)
        problems.append(
            {
                "path": key,
                "message": f"unknown key; neither a scenario file key nor a field of "
                f"{model.__name__}{suggestion}{hint}",
                "source": _origin(origins, key),
            }
        )
    return problems


def _declare_hint(key: str, value: Any, model: type[BaseModel]) -> str:
    """How to declare a new parameter (the file sets values of declared ones)."""
    shown = value if isinstance(value, bool | int | float | str) else "..."
    declare = f"declare it under 'parameters:' ({{parameters: {{{key}: {shown}}}}})"
    if model is ScenarioParams:
        return (
            f"; this scenario declares no parameters of its own: {declare}, or "
            f"subclass ScenarioParams in Python (class Params(ScenarioParams): "
            f"{key}: ... = ...) and reference it with 'params: module:Params'"
        )
    ref = f"{model.__module__}:{model.__qualname__}"
    return f"; {declare}, or add the field to {model.__name__} ({ref})"


def _raise_format(problems: list[dict], label: str) -> None:
    first = problems[0]
    raise ParamsError(
        _problem_text(f"Invalid scenario file {label!r}", problems, label),
        path=first["path"],
        source=first["source"] or label,
        errors=problems,
    )


def _apply_params(
    scenario: Scenario,
    spec: ScenarioFile,
    data: Mapping,
    origins: Mapping,
    files: list[Path],
    label: str,
) -> None:
    fields = scenario.params_model.model_fields
    problems = _unknown_top_level(data, scenario.params_model, (), origins)
    if problems:
        _raise_format(problems, label)
    top = {key: copy.deepcopy(value) for key, value in data.items() if key in fields}
    by_label = {display_path(file): file for file in files}
    rebase_preset_paths(top, origins, by_label)
    layers = [(top, origins)] if top else []
    if spec.defaults:
        defaults = _nested(spec.defaults)
        owner = _origin(origins, "defaults") or label
        default_origins = {key: owner for key in defaults}
        rebase_preset_paths(defaults, default_origins, by_label)
        layers.append((defaults, default_origins))
    for layer, layer_origins in layers:
        _check_params(scenario, layer, layer_origins, label)
        scenario.defaults(layer)


def _naming(value: str | None, where: str, label: str) -> Any:
    if value is None or "{" in value or ":" not in value:
        return value
    return _import_callable(value, where, label)


def _declare_groups(scenario: Scenario, spec: ScenarioFile, label: str) -> None:
    from mango.agent.core import Agent

    for name, group in spec.groups.items():
        where = f"groups.{name}"
        factory = import_ref_or_raise(group.factory, f"{label}: {where}.factory")
        if isinstance(factory, type) and issubclass(factory, Agent):
            raise ScenarioError(
                f"{label}: {where}.factory: {group.factory!r} is an Agent class; a group "
                f"factory is a function fn(e) -> Agent (e.g. def make_{name}(e): return "
                f"{factory.__name__}(...)), or use the component "
                f"{{kind: agent_population, factory: {group.factory!r}, count: ...}}."
            )
        count: Any = group.count
        if isinstance(count, str) and ":" in count:
            count = _import_callable(count, f"{where}.count", label)
        install = position = None
        if group.install is not None:
            install = _import_callable(group.install, f"{where}.install", label)
        if group.position is not None:
            position = _import_callable(group.position, f"{where}.position", label)
        naming = _naming(group.naming, f"{where}.naming", label)
        try:
            scenario.group(
                name, count, factory, install=install, position=position, naming=naming
            )
        except (ScenarioError, TypeError) as exc:
            raise ScenarioError(f"{label}: {where}: {exc}") from exc


def _rebase_edge_files(pattern: Any, files: list[Path]) -> Any:
    """``{edges: {file: ...}}`` relative to a scenario file: rewrite it relative to cwd."""
    from .profiles import _rebase

    if not isinstance(pattern, dict) or len(pattern) != 1:
        return pattern
    ((name, args),) = pattern.items()
    if name == "reverse":
        return {name: _rebase_edge_files(args, files)}
    if name != "edges":
        return pattern
    file = args.get("file") if isinstance(args, dict) else args
    if not isinstance(file, str) or file.startswith("$"):
        return pattern
    for source in reversed(files):
        rebased = _rebase(file, source)
        if rebased is not None:
            if isinstance(args, dict):
                return {name: {**args, "file": rebased}}
            return {name: rebased}
    return pattern


def _declare_links(
    scenario: Scenario, spec: ScenarioFile, label: str, files: list[Path] = ()
) -> None:
    for index, link in enumerate(spec.links):
        where = f"links.{index} ({link.name})"
        pattern: Any = _rebase_edge_files(link.pattern, list(files))
        if isinstance(pattern, str) and ":" in pattern:
            pattern = _import_callable(pattern, f"{where}.pattern", label)
        where_fn = None
        if link.where is not None:
            where_fn = _import_callable(link.where, f"{where}.where", label)
        try:
            scenario.link(
                link.name,
                link.source,
                link.target,
                pattern=pattern,
                where=where_fn,
                directed=link.directed,
                delay=link.delay,
                loss=link.loss,
            )
        except (ScenarioError, TypeError, ValueError) as exc:
            raise ScenarioError(f"{label}: {where}: {exc}") from exc


def _resource_getter(path: str, ctx: Any) -> tuple[str, str]:
    """Split ``"name.attr.path"`` at the longest name a resource resolves to.

    Names resolve like ``ctx.resource``: a kit's own resource, the exact
    (qualified) name, then the one kit resource of that local name.
    """
    segments = path.split(".")
    for cut in range(len(segments), 0, -1):
        name = ".".join(segments[:cut])
        if ctx._resource_key(name) is not None:
            return name, ".".join(segments[cut:])
    return segments[0], ".".join(segments[1:])


def _record_resource(
    scenario: Scenario,
    name: str,
    path: str,
    every: Any,
    entity: str,
    window: str | None = None,
    kind: str | None = None,
) -> None:
    spec = check_every(every)
    check_window(window, every, f"record({name!r})")
    kind = check_kind(kind, window, f"record({name!r})")
    scenario._check_every_path(f"record({name!r})", spec)

    def declare(ctx: Any) -> None:
        seconds = ctx._every(f"record({name!r})", spec)
        resource, attribute = _resource_getter(path, ctx)
        obj = ctx.resource(resource)
        if attribute:

            def getter() -> Any:
                return get_path(obj, attribute)

        else:

            def getter() -> Any:
                return obj

        ctx.record_world(
            name, getter, every=seconds, entity=entity, window=window, kind=kind
        )

    scenario._declare(
        "record",
        declare,
        "record_resource",
        name,
        path,
        every_text(spec),
        entity,
        *(() if window is None else (window,)),
        *(() if kind is None else (f"kind={kind}",)),
    )


def _declare_records(scenario: Scenario, spec: ScenarioFile, label: str) -> None:
    for index, record in enumerate(spec.record):
        where = f"record.{index} ({record.name})"
        try:
            if record.resource is not None:
                if not record.resource.strip() or "" in record.resource.split("."):
                    raise ScenarioError(
                        f"resource must be '<name>' or '<name>.<attribute path>', got "
                        f"{record.resource!r}."
                    )
                _record_resource(
                    scenario,
                    record.name,
                    record.resource,
                    record.every,
                    record.entity or "world",
                    record.window,
                    record.kind,
                )
            elif record.group is not None:  # one group or a list of groups
                fn = None
                if record.fn is not None:
                    fn = _import_callable(record.fn, f"{where}.fn", label)
                aggregate = record.aggregate
                if aggregate is not None and ":" in aggregate:
                    aggregate = _import_callable(aggregate, f"{where}.aggregate", label)
                scenario.record(
                    record.name,
                    record.group,
                    fn,
                    attr=record.attr,
                    every=record.every,
                    aggregate=aggregate,
                    window=record.window,
                    kind=record.kind,
                    categories=record.categories,
                )
            else:
                fn = _import_callable(record.fn, f"{where}.fn", label)
                scenario.record_world(
                    record.name,
                    fn,
                    every=record.every,
                    entity=record.entity or "world",
                    window=record.window,
                    kind=record.kind,
                )
        except ScenarioError as exc:
            if str(exc).startswith(f"{label}: "):
                raise
            raise ScenarioError(f"{label}: {where}: {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise ScenarioError(f"{label}: {where}: {exc}") from exc


def load_scenario(path: str | os.PathLike) -> FileScenario:
    """Compile a scenario file into a :class:`~mango.scenario.Scenario`.

    The file is read with its ``extends`` chain, validated against the file
    format (unknown keys fail with a did-you-mean suggestion and the file
    they came from), then translated call by call: ``Scenario(name,
    params=...)``, ``use()`` per component, ``defaults()`` for the params
    fields at the top level and in ``defaults``, ``environment()``,
    ``communication()``, ``group()``, ``link()``, ``record()``/
    ``record_world()`` and ``on_build()``.  References are imported now, so
    a missing module fails here; params defaults are validated now (the
    checks of ``phases``/``events`` against ``time`` wait for the run's
    params).  ``name`` defaults to the file name and is not inherited
    through ``extends``.

    Recorders: ``{name, group, attr}`` or ``{name, group, fn}`` record each
    agent of a group (``group`` may be a list of groups), ``{name, fn}`` is a
    world recorder (``fn()`` or ``fn(ctx)``), and ``{name, resource:
    "<resource>.<path>"}`` records an attribute of a resource a kit provides
    (``ctx.provide``; names resolve like ``ctx.resource``), resolved when the
    run is planned.  All take ``every`` (a duration or a parameter path)
    and, with it, ``window`` (``last``, ``sum``, ``mean``, ``min``, ``max``),
    and ``kind`` (``flow`` or ``state``, see :meth:`Scenario.record
    <mango.scenario.Scenario.record>`); per-agent recorders take
    ``aggregate`` (``count``, ``sum``, ``mean``, ``min``, ``max`` or
    ``'module:fn'``) and, with ``count``, ``categories``; world and resource recorders take
    ``entity`` (default ``"world"``).  ``metrics: {name: 'module:fn'}``
    declares run-level metrics (``scenario.metric``).

    ``parameters`` declares the file's own parameters by their defaults,
    without Python: each type is inferred from the default (``bool``,
    ``int``, ``float``, ``str``, lists of these, a distribution shorthand
    such as ``{normal: [0, 1]}``; a mapping becomes a nested group of
    parameters, an empty mapping a free ``dict``).  The block compiles to a
    :class:`~mango.scenario.ScenarioParams` subclass that forbids unknown
    keys (extending ``params`` when given), so presets, ``-s`` overrides and
    study factors are type-checked: ``-s epsilon=0.3`` works, ``-s
    epsilon=high`` and ``-s epsilonn=0.3`` fail.  An ``int`` default only
    takes ints: write ``1.0`` for a float.

    A relative *path* is looked up in the working directory, then in a
    study's project root (inside study workers).

    :param path: the scenario file (``.yaml``, ``.yml`` or ``.json``)
    :return: the scenario (a :class:`~mango.scenario.yaml_scenario.FileScenario`; its ``ref`` is the path,
        so studies can run it in worker processes)
    :raises ParamsError: for unreadable files, unknown keys and invalid
        values (``err.path`` and ``err.source`` name the key and file)
    :raises ScenarioError: for references that cannot be imported, unknown
        component kinds and invalid declarations (the message names the file
        and the key)

    Example::

        scenario = load_scenario("scenarios/ev.yaml")
        print(scenario.plan(overrides=["ev.n=20"]))
        result = scenario.run("presets/base.yaml", seed=7)

        # scenarios/opinions.yaml
        #   parameters: {epsilon: 0.2, n: 100, net: {k: 4, rewire: 0.1}}
        #   groups: {person: {factory: "model:make_person", count: n}}
        #   links: [{name: friends, from: person,
        #            pattern: {small_world: {k: $net.k, p: $net.rewire}}}]
        load_scenario("scenarios/opinions.yaml").run(overrides=["epsilon=0.3"])
    """
    if not isinstance(path, str | os.PathLike):
        raise TypeError(
            f"load_scenario() needs a file path, got {type(path).__name__}."
        )
    data, origins, files = read_tracked(_locate(path))
    label = display_path(files[-1])
    spec, problems = _scenario_problems(data, origins, label)
    model = None
    if spec is not None:
        model = _params_model(spec.params, label)
        if spec.parameters:
            model = _parameters_model(spec.parameters, model, files[-1].stem, label)
    elif not any(
        item["path"].split(".")[0] in ("params", "parameters") for item in problems
    ):
        try:
            model = _params_model(data.get("params"), label)
            if isinstance(data.get("parameters"), Mapping) and data["parameters"]:
                model = _parameters_model(
                    data["parameters"], model, files[-1].stem, label
                )
        except ScenarioError:
            model = None  # reported once the format errors are fixed
    if model is not None:
        components = data.get("components")
        prefixes = list(components) if isinstance(components, Mapping) else []
        problems += _unknown_top_level(data, model, prefixes, origins)
    if problems:
        wrong = _wrong_kind(label, data, "scenario")
        if wrong is not None and document_kind(data) == "study":
            raise ParamsError(wrong, source=label)
        _raise_format(problems, label)
    own_name = spec.name is not None and origins.get("name") == label
    name = spec.name if own_name else files[-1].stem  # names are not inherited
    try:
        scenario = FileScenario(name, model, path=files[-1].resolve(), document=data)
    except ScenarioError as exc:
        raise ScenarioError(f"{label}: name: {exc}") from exc
    _use_components(scenario, spec, origins, label)
    _apply_params(scenario, spec, data, origins, files, label)
    if spec.environment is not None:
        scenario.environment(_import_callable(spec.environment, "environment", label))
    if spec.communication is not None:
        scenario.communication(
            _import_callable(spec.communication, "communication", label)
        )
    _declare_groups(scenario, spec, label)
    _declare_links(scenario, spec, label, files)
    _declare_records(scenario, spec, label)
    for index, ref in enumerate(spec.on_build):
        scenario.on_build(_import_callable(ref, f"on_build.{index}", label))
    _declare_stops(scenario, spec, label)
    for name, ref in spec.metrics.items():
        try:
            scenario.metric(name, _import_callable(ref, f"metrics.{name}", label))
        except ScenarioError as exc:
            raise ScenarioError(f"{label}: metrics.{name}: {exc}") from exc
    return scenario


def _declare_stops(scenario: Scenario, spec: ScenarioFile, label: str) -> None:
    stops = spec.stop_when
    if stops is None:
        return
    for index, stop in enumerate(stops if isinstance(stops, list) else [stops]):
        where = "stop_when" if not isinstance(stops, list) else f"stop_when.{index}"
        ref, every = (stop, None) if isinstance(stop, str) else (stop.fn, stop.every)
        try:
            scenario.stop_when(_import_callable(ref, where, label), every=every)
        except (ScenarioError, TypeError, ValueError) as exc:
            raise ScenarioError(f"{label}: {where}: {exc}") from exc


def _merge_schemas(base: dict, params: dict) -> dict:
    schema = copy.deepcopy(base)
    defs = schema.setdefault("$defs", {})
    for key, value in params.get("$defs", {}).items():
        if key in defs and defs[key] != value:
            raise ScenarioError(
                f"The params model defines {key!r}, which clashes with a scenario file "
                "schema definition; rename the model."
            )
        defs[key] = value
    for key, value in params.get("properties", {}).items():
        if key not in schema["properties"]:
            schema["properties"][key] = value
    return schema


def scenario_file_schema(target: Any = None) -> dict:
    """Return the JSON Schema of scenario files (for editors and form tools).

    Without *target* the params part is :class:`~mango.scenario.ScenarioParams`
    and other top-level keys are allowed (they are fields of your params
    model); with a params model class or a scenario (kits included), every
    params field is described and unknown top-level keys are rejected.

    :param target: ``None``, a ``ScenarioParams`` subclass or a scenario
    :return: the JSON Schema (draft 2020-12) as a dict

    Example::

        import json
        schema = scenario_file_schema(load_scenario("scenarios/ev.yaml"))
        Path("scenario.schema.json").write_text(json.dumps(schema, indent=2))
    """
    from .params import params_schema

    base = ScenarioFile.model_json_schema()
    params = params_schema(ScenarioParams if target is None else target)
    schema = _merge_schemas(base, params)
    schema["title"] = "mango scenario file"
    schema["description"] = (
        "A mango.scenario scenario file. Besides the keys below, the top level "
        "holds params fields as scenario defaults."
    )
    schema["additionalProperties"] = target is None
    return {"$schema": _SCHEMA_DIALECT, **schema}


# --------------------------------------------------------------------------- study format


def _factor_specs(value: Any) -> Any:
    if value is None or isinstance(value, Mapping):
        return value
    raise ValueError(f"expected a mapping {{path: levels}}, got {value!r}")


class StudyFile(_FileModel):
    """The keys of a study file: the arguments of :class:`~mango.scenario.Study`."""

    extends: str | list[str] | None = Field(
        None,
        description="parent study file(s); factors merge per path, other keys replace",
    )
    name: str | None = Field(
        None, description="study name (default: the file name; not inherited)"
    )
    scenario: str = Field(
        description="'module:attr' of a scenario (or factory), or a scenario file"
    )
    base: str | dict[str, Any] | list[str | dict[str, Any]] | None = Field(
        None, description="preset file(s) or params applied to every run"
    )
    overrides: dict[str, Any] | list[str] | None = Field(
        None, description="'a.b=value' strings or {a.b: value} for every run"
    )
    seed: int | None = Field(None, ge=0, description="the study seed")
    uncertainties: dict[str, Any] | None = Field(
        None, description="factors describing the world: {path: levels}"
    )
    levers: dict[str, Any] | None = Field(
        None, description="factors describing what is compared: {path: levels}"
    )
    design: str | dict[str, Any] | None = Field(
        None,
        description="full_factorial, {lhs: {n}}, {sobol: {n, second_order}}, "
        "{random: {n}}, {csv: path} or {explicit: [rows]}",
    )
    replications: int = Field(1, ge=1)
    run_order: Literal["point", "replication"] = Field(
        "point",
        description="point (the replications of a point together) or replication "
        "(every point of a replication together: an interrupted study stays balanced)",
    )
    common_random_numbers: bool | Literal["levers"] | None = Field(
        None,
        description="true, false or levers (default: levers for lhs/random, else true)",
    )
    outcomes: list[str] | dict[str, str] | None = Field(
        None, description="['module:fn', ...] or {name: 'module:fn'}"
    )
    subexperiments: list[dict[str, Any]] | None = None
    out: str | None = Field(None, description="the study folder (relative to cwd)")
    format: Literal["auto", "parquet", "csv"] = "auto"
    executor: Literal["sequential", "process", "slurm"] | dict[str, Any] | None = Field(
        None,
        description="default executor of 'run': sequential, process, slurm or "
        "{kind, workers, runs_per_worker, slurm: {SlurmExecutor options}}",
    )

    @field_validator("replications", "seed", mode="before")
    @classmethod
    def _int(cls, value: Any) -> Any:
        return value if value is None else _plain_type(value, (int,), "an int")

    @field_validator("common_random_numbers", mode="before")
    @classmethod
    def _crn(cls, value: Any) -> Any:
        if value is None or value == "levers":
            return value
        return _plain_type(value, (bool,), "true, false or levers")

    @field_validator("uncertainties", "levers", mode="before")
    @classmethod
    def _factors(cls, value: Any) -> Any:
        return _factor_specs(value)

    @field_validator("outcomes", mode="after")
    @classmethod
    def _outcome_refs(cls, value: Any) -> Any:
        refs = value.values() if isinstance(value, Mapping) else (value or [])
        for ref in refs:
            _check_ref(ref)
        return value


#: The keys of a study file.
STUDY_FILE_KEYS: tuple[str, ...] = tuple(StudyFile.model_fields)

_FACTOR_KEYS = ("uncertainties", "levers")


def _as_study_error(exc: ParamsError) -> StudyError:
    return StudyError(str(exc))


def _merge_factor_maps(
    merged: dict, update: Mapping, origins: dict, source: Path
) -> None:
    """Merge the factor maps of *update* into *merged* (per path, in place)."""
    declared: dict[str, set[str]] = {}
    for role in _FACTOR_KEYS:
        if role not in update:
            continue
        value = update[role]
        if not isinstance(value, Mapping):
            merged[role] = copy.deepcopy(value)  # reported by the validation
            origins[role] = source
            declared[role] = set()
            continue
        current = merged.get(role)
        result = dict(current) if isinstance(current, Mapping) else {}
        names = set()
        for key, spec in value.items():
            marker, path = split_marker(key)
            if marker == "-":
                result.pop(path, None)
                continue
            if marker == "+":
                raise StudyError(
                    f"{display_path(source)}: {role}: '+{path}' is not supported; give the "
                    f"factor as '{path}: <levels>' (it replaces the parent's levels)."
                )
            result[path] = copy.deepcopy(spec)
            names.add(path)
        merged[role] = result
        origins[role] = source
        declared[role] = names
    # a factor the update declares in one role leaves the other role
    for role, names in declared.items():
        other = "levers" if role == "uncertainties" else "uncertainties"
        if (
            other in merged
            and isinstance(merged[other], Mapping)
            and other not in declared
        ):
            merged[other] = {
                path: spec for path, spec in merged[other].items() if path not in names
            }


def _merge_study(merged: dict, origins: dict, update: Mapping, source: Path) -> None:
    """Merge one study file over the merged parents (in place)."""
    _merge_factor_maps(merged, update, origins, source)
    for key, value in update.items():
        if key in _FACTOR_KEYS:
            continue
        marker, name = split_marker(key)
        if marker == "-":
            merged.pop(name, None)
            origins.pop(name, None)
            continue
        if marker == "+":
            current = merged.get(name)
            current = [] if current is None else current
            if not isinstance(current, list):
                current = [current] if name == "base" else current
            if not isinstance(current, list):
                raise StudyError(
                    f"{display_path(source)}: cannot append to {name!r} with {key!r}: it "
                    f"is a {type(current).__name__}, not a list."
                )
            items = list(value) if isinstance(value, list | tuple) else [value]
            merged[name] = current + copy.deepcopy(items)
        elif name == "overrides" and merged.get("overrides") and value is not None:
            try:
                combined = parse_overrides(merged["overrides"])
                combined.update(parse_overrides(value))
            except ParamsError as exc:
                raise StudyError(f"{display_path(source)}: overrides: {exc}") from None
            merged[name] = combined
        else:
            merged[name] = copy.deepcopy(value)
        origins[name] = source


def _read_study(path: Path, stack: tuple[Path, ...] = ()) -> tuple[dict, dict, list]:
    path = Path(path).expanduser().resolve()
    if path in stack:
        chain = " -> ".join(display_path(item) for item in (*stack, path))
        raise StudyError(f"Cycle in 'extends': {chain}.")
    try:
        data = load_mapping(path)
    except ParamsError as exc:
        raise _as_study_error(exc) from None
    parents = data.pop("extends", None)
    if parents is None:
        parents = []
    elif not isinstance(parents, list | tuple):
        parents = [parents]
    merged: dict = {}
    origins: dict[str, Path] = {}
    files: list[Path] = []
    for ref in parents:
        try:
            parent = resolve_extends(ref, path)
        except ParamsError as exc:
            raise _as_study_error(exc) from None
        parent_data, parent_origins, parent_files = _read_study(parent, (*stack, path))
        _merge_study(merged, origins, parent_data, parent)
        origins.update(parent_origins)
        files += [item for item in parent_files if item not in files]
    _merge_study(merged, origins, data, path)
    files.append(path)
    return merged, origins, files


def _resolve_study_paths(
    kwargs: dict, origins: Mapping[str, Path], files: list[Path], label: str
) -> None:
    def chain(key: str) -> list[Path]:
        owner = origins.get(key)
        return [file for file in files if file != owner] + ([owner] if owner else [])

    scenario = kwargs["scenario"]
    if is_scenario_file(scenario):
        resolved = _resolve_file(
            scenario, chain("scenario"), what="scenario file", label=label
        )
        kwargs["scenario"] = _relative_ref(Path(resolved))
    base = kwargs.get("base")
    if isinstance(base, str | list):
        items = base if isinstance(base, list) else [base]
        resolved_items = [
            _resolve_file(item, chain("base"), what="base preset", label=label)
            if isinstance(item, str)
            else item
            for item in items
        ]
        kwargs["base"] = resolved_items if isinstance(base, list) else resolved_items[0]
    design = kwargs.get("design")
    if isinstance(design, Mapping) and len(design) == 1 and "csv" in design:
        spec = design["csv"]  # a path or {path: ..., ...}
        path = spec.get("path") if isinstance(spec, Mapping) else spec
        if isinstance(path, str):
            path = _resolve_file(path, chain("design"), what="design CSV", label=label)
            kwargs["design"] = {
                "csv": {**spec, "path": path} if isinstance(spec, Mapping) else path
            }


def read_study_file(path: str | os.PathLike) -> dict:
    """Read, merge (``extends``) and validate a study file.

    :param path: the study file (``.yaml``, ``.yml`` or ``.json``)
    :return: the keyword arguments of :class:`~mango.scenario.Study`
        (including ``scenario``), with relative paths resolved: a path that
        exists relative to the working directory is kept, one found relative
        to the file that sets it is rewritten relative to the working
        directory
    :raises StudyError: for unreadable files, unknown keys (with did-you-mean
        suggestions and the file they came from), invalid values and missing
        files

    Example::

        kwargs = read_study_file("studies/sobol_pv.yaml")
        kwargs["design"]       # {"sobol": {"n": 256, "second_order": False}}
    """
    if not isinstance(path, str | os.PathLike):
        raise TypeError(f"A study file path must be a str, got {type(path).__name__}.")
    data, origins, files = _read_study(Path(path))
    label = display_path(files[-1])
    labels = {key: display_path(file) for key, file in origins.items()}
    try:
        spec = StudyFile.model_validate(data)
    except ValidationError as exc:
        wrong = _wrong_kind(label, data, "study")
        if wrong is not None:
            raise StudyError(wrong) from None
        problems = _validation_problems(StudyFile, exc, data, labels)
        raise StudyError(
            _problem_text(f"Invalid study file {label!r}", problems, label)
        ) from None
    kwargs = {  # values as written, model defaults for missing keys
        key: copy.deepcopy(data[key]) if key in data else getattr(spec, key)
        for key in STUDY_FILE_KEYS
        if key != "extends"
    }
    if kwargs["name"] is None or origins.get("name") != files[-1]:
        kwargs["name"] = files[-1].stem  # a study's name is not inherited
    if kwargs["out"] is not None and origins.get("out") != files[-1]:
        # nor its folder: a sibling of the parent's folder named after this study
        kwargs["out"] = str(Path(kwargs["out"]).parent / kwargs["name"])
    _resolve_study_paths(kwargs, origins, files, label)
    return kwargs


def load_study_file(path: str | os.PathLike, **changes: Any) -> Study:
    """Create a :class:`~mango.scenario.Study` from a study file.

    Keys: ``name`` (default: the file name; never inherited through
    ``extends``), ``out`` (the study folder; through ``extends`` the default
    is the parent's folder with this study's name, ``results/<name>`` next
    to ``results/<parent>``), ``scenario`` (``"module:attr"``
    or a scenario file, see :func:`~mango.scenario.load_scenario`), ``base``, ``overrides``,
    ``seed``, ``uncertainties``, ``levers``, ``design`` (``full_factorial``,
    ``{lhs: {n: 64}}``, ``{sobol: {n: 256, second_order: false}}``,
    ``{random: {n: 100}}``, ``{csv: design.csv}`` or ``{explicit: [...]}``),
    ``replications``, ``run_order`` (``point`` or ``replication``),
    ``common_random_numbers`` (``true``, ``false`` or
    ``levers``), ``outcomes`` (a list of
    ``"module:fn"`` or ``{name: "module:fn"}``), ``subexperiments``,
    ``format``, ``executor`` (defaults of ``run``: ``{workers: 8,
    runs_per_worker: 20, slurm: {partition: short, runs_per_task: 4}}``) and
    ``extends``.

    ``extends: <parent study>`` merges the parent first: ``uncertainties``
    and ``levers`` merge per factor path (a new spec replaces the parent's,
    a single value fixes the factor, ``"-path"`` drops it, a path declared
    in the other role moves there), ``"+key"`` appends to a list (e.g.
    ``+outcomes``), ``"-key"`` drops a key and every other key replaces the
    parent's value (so a new ``design`` swaps the design).

    :param path: the study file
    :param changes: :class:`~mango.scenario.Study` keyword arguments that
        replace the file's values (e.g. ``out="results/test"``)
    :return: the study
    :raises StudyError: for invalid files or study definitions (naming the
        file)

    Example::

        # studies/sobol_pv.yaml
        #   extends: studies/fleet_comparison.yaml
        #   uncertainties:
        #     ev.n: 10                          # fixed now
        #     grid.pv_share: {range: [0.2, 0.9]}
        #   levers: {algorithm: [proportional]}
        #   design: {sobol: {n: 256, second_order: false}}
        study = load_study_file("studies/sobol_pv.yaml")
        study.run(workers=16)
        load_study("results/sobol_pv").sobol_indices()
    """
    from .study import Study

    kwargs = read_study_file(path)
    unknown = [key for key in changes if key not in kwargs]
    if unknown:
        raise TypeError(
            f"load_study_file() got unknown arguments {unknown}; use Study arguments: "
            f"{', '.join(kwargs)}."
        )
    kwargs.update(changes)
    label = display_path(Path(path).resolve())
    scenario = kwargs.pop("scenario")
    try:
        return Study(scenario, **kwargs)
    except StudyError as exc:
        raise StudyError(f"{label}: {exc}") from exc


def document_kind(keys: Any) -> str:
    """Classify a config document by its top-level keys.

    :param keys: the keys (of the file after ``extends``)
    :return: ``"study"`` (keys such as ``scenario``, ``uncertainties``,
        ``design``), ``"scenario"`` (``groups``, ``links``, ``components``,
        ``record``, ``params``, ...) or ``"preset"`` (parameter keys only)

    Example::

        document_kind({"scenario", "levers"})     # "study"
    """
    keys = set(keys)
    if keys & (set(STUDY_FILE_KEYS) - {"extends", "name", "seed"}):
        return "study"
    if keys & (set(SCENARIO_FILE_KEYS) - {"extends"}):
        return "scenario"
    return "preset"


_LOADERS = {
    "study": "a study file; load it with Study.from_yaml() or load_study_file() "
    "(mango-scenario run/plan/validate take it directly)",
    "scenario": "a scenario file; load it with load_scenario()",
    "preset": "a preset (parameter values only); pass it as params to "
    "scenario.run()/load_params() or as 'base' of a study",
}


def _wrong_kind(label: str, data: Mapping, expected: str) -> str | None:
    kind = document_kind(data)
    if kind == expected:
        return None
    if kind == "preset":  # unless the keys are misspelt file keys
        known = STUDY_FILE_KEYS if expected == "study" else SCENARIO_FILE_KEYS
        if any(difflib.get_close_matches(str(key), known, cutoff=0.75) for key in data):
            return None
    return f"{label!r} is {_LOADERS[kind]}, not a {expected} file."


def study_file_schema() -> dict:
    """Return the JSON Schema of study files.

    :return: the JSON Schema (draft 2020-12) as a dict

    Example::

        Path("study.schema.json").write_text(json.dumps(study_file_schema(), indent=2))
    """
    schema = StudyFile.model_json_schema()
    # a file that extends another may leave 'scenario' to its parent
    schema.pop("required", None)
    schema["anyOf"] = [{"required": ["scenario"]}, {"required": ["extends"]}]
    schema["title"] = "mango study file"
    schema["description"] = (
        "A mango.scenario study file: the arguments of mango.scenario.Study."
    )
    return {"$schema": _SCHEMA_DIALECT, **schema}
