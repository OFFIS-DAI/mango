"""
Experimental designs: which factor combinations a study runs.

A *factor* is a parameter path the study varies (or fixes), declared as an
uncertainty or a lever of a :class:`~mango.scenario.Study`.  Factor specs:

- a list gives levels: ``[5, 10, 20]``;
- a dict with exactly one reserved key: ``{"values": [...]}`` (levels),
  ``{"levels": {"ideal": 0.0, "lossy": 0.1}}`` (labeled levels),
  ``{"value": x}`` (fixed, e.g. a dict or list value), or a continuous range
  ``{"range": [lo, hi]}``, ``{"int_range": [lo, hi]}``,
  ``{"log_range": [lo, hi]}`` (only for sampling designs);
- any other dict gives labeled levels ``{label: value}``;
- anything else is a fixed value.

A *design* turns the factors into :class:`DesignPoint` objects:
:class:`FullFactorial` crosses all levels, :class:`Explicit` takes rows and
:class:`FromCsv` reads them from a CSV file; factors a row does not set are
crossed with it.  Sampling designs (:class:`Lhs`, :class:`Sobol`,
:class:`RandomSample`) sample the continuous factors and cross the samples
with the levels of the discrete factors; they derive from
:class:`SamplingDesign`.

Example::

    from mango.scenario.designs import Factor, FullFactorial
    from mango.util.seeding import SeedTree

    factors = [
        Factor.parse("ev.n", [5, 10, 20]),
        Factor.parse("comm.loss_percent", {"ideal": 0.0, "lossy": 0.1}),
        Factor.parse("algorithm", ["greedy", "proportional"], role="lever"),
    ]
    points = FullFactorial().generate(factors, SeedTree(1).child("design"))
    len(points)                  # 12
    points[1].values             # {"ev.n": 5, "comm.loss_percent": 0.0, "algorithm": "proportional"}
    points[1].labels             # {"comm.loss_percent": "ideal"}
"""

from __future__ import annotations

import csv
import difflib
import itertools
import json
import math
import numbers
import os
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import yaml

from ._util import canonical_json, did_you_mean, to_plain, warn_user
from .distributions import _is_number
from .errors import StudyError

if TYPE_CHECKING:
    from mango.util.seeding import SeedTree

__all__ = [
    "Factor",
    "DesignPoint",
    "Design",
    "FullFactorial",
    "Explicit",
    "FromCsv",
    "SamplingDesign",
    "Lhs",
    "Sobol",
    "RandomSample",
    "SOBOL_BLOCKS",
    "scale_unit",
    "resolve_design",
    "design_kinds",
    "cross",
    "FACTOR_ROLES",
    "CONTINUOUS_KINDS",
]

#: Roles a factor can have in a study (metadata and column order only).
FACTOR_ROLES: tuple[str, ...] = ("uncertainty", "lever")

#: Factor kinds that describe a continuous range (sampling designs only).
CONTINUOUS_KINDS: tuple[str, ...] = ("range", "int_range", "log_range")

_RESERVED = ("values", "levels", "value", *CONTINUOUS_KINDS)


def _plain(value: Any, what: str) -> Any:
    """Return *value* as plain JSON data (tuples as lists, models as dicts)."""
    try:
        return to_plain(value)
    except TypeError as exc:
        raise StudyError(f"{what} is not plain data: {exc}") from None


# --------------------------------------------------------------------------- factors


def _check_distribution_like(
    spec: Mapping, what: str, admits_distribution: bool | None = None
) -> None:
    """Catch mappings that read as labeled levels but look like a distribution.

    ``{"uniform": [2, 4]}`` (a distribution kind as the only key) raises;
    ``{"low": 2, "high": 6}`` (the field names of one distribution) warns,
    because such labels can be meant (``{"levels": ...}`` keeps them quietly).
    The warning is skipped when the target field is known not to accept a
    distribution (*admits_distribution* is ``False``): there ``low``/``high``
    can only be labels.
    """
    if not spec:  # no labels: the caller reports "has no levels"
        return
    from .distributions import distribution_kinds

    kinds = distribution_kinds()

    def text(value: Any) -> str:
        return json.dumps(to_plain(value, default=str), default=str)

    if len(spec) == 1:
        (key,) = spec
        if str(key) in kinds:
            raise StudyError(
                f"{what}: {text(dict(spec))} would be one labeled level named {key!r}. "
                f'For the distribution as the factor\'s value write {{"value": '
                f"{text(dict(spec))}}}; to sample a range in a design write "
                '{"range": [low, high]}.'
            )
        return
    if admits_distribution is False:
        return
    keys = {str(key) for key in spec}
    for kind, cls in kinds.items():
        if keys <= set(cls.model_fields) - {"kind"}:
            labels = ", ".join(repr(key) for key in spec)
            meant = text({"value": {"kind": kind, **dict(spec)}})
            warn_user(
                f"{what}: {text(dict(spec))} is read as {len(spec)} labeled levels "
                f"({labels}), each a fixed value, but the labels are the fields of a "
                f"{kind} distribution. For one {kind} distribution write {meant}; to "
                'sample a range in a design write {"range": [low, high]}; to keep the '
                f'labeled levels write {{"levels": {text(dict(spec))}}}.'
            )
            return


@dataclass(frozen=True)
class Factor:
    """One varied (or fixed) parameter of a study.

    Create factors with :meth:`parse`.  Values are stored as plain JSON data
    (a distribution instance becomes its ``{"kind": ...}`` dict).

    :param path: the dotted parameter path, e.g. ``"ev.n"``
    :param kind: ``"levels"``, ``"fixed"``, ``"range"``, ``"int_range"`` or
        ``"log_range"``
    :param levels: the level values (one value for ``"fixed"``, empty for
        ranges)
    :param labels: one label per level for labeled factors, else ``None``
    :param bounds: ``(low, high)`` for ranges, else ``None``
    :param role: ``"uncertainty"`` or ``"lever"``

    Example::

        Factor.parse("comm.loss_percent", {"ideal": 0.0, "lossy": 0.1}).items()
        # [("ideal", 0.0), ("lossy", 0.1)]
    """

    path: str
    kind: str
    levels: tuple[Any, ...] = ()
    labels: tuple[str, ...] | None = None
    bounds: tuple[float, float] | None = None
    role: str = "uncertainty"

    @classmethod
    def parse(
        cls,
        path: str,
        spec: Any,
        role: str = "uncertainty",
        *,
        admits_distribution: bool | None = None,
    ) -> Factor:
        """Parse a factor spec (see the module docstring for the forms).

        Labels that are the field names of a distribution (``{"low": 2,
        "high": 6}``) warn that a distribution may have been meant, unless
        *admits_distribution* is ``False``.  :class:`~mango.scenario.Study`
        passes it from the scenario's params model (see
        :func:`~mango.scenario.params.admits_distribution`), so labels on a
        plain ``float`` field stay quiet.

        :param path: the dotted parameter path
        :param spec: the spec: a list, a dict or a fixed value
        :param role: ``"uncertainty"`` or ``"lever"``
        :param admits_distribution: whether the parameter at *path* accepts a
            distribution; ``None`` (unknown) keeps the warning
        :return: the factor
        :raises StudyError: for empty level lists, invalid ranges, dicts that
            mix reserved keys with labels and values that are not plain data

        Example::

            Factor.parse("ev.n", [5, 10]).levels             # (5, 10)
            Factor.parse("ev.n", 10).kind                    # "fixed"
            Factor.parse("grid.pv_share", {"range": [0.2, 0.9]}).bounds   # (0.2, 0.9)
        """
        if not isinstance(path, str) or not path.strip():
            raise StudyError(f"A factor path must be a non-empty str, got {path!r}.")
        if role not in FACTOR_ROLES:
            raise StudyError(
                f"Factor {path!r}: unknown role {role!r}; use one of {list(FACTOR_ROLES)}."
            )
        path = path.strip()
        what = f"Factor {path!r}"
        if isinstance(spec, list | tuple):
            return cls._from_values(path, spec, role, what)
        if isinstance(spec, Mapping):
            reserved = [key for key in spec if key in _RESERVED]
            if reserved and len(spec) > 1:
                raise StudyError(
                    f"{what}: {dict(spec)!r} mixes the reserved key {reserved[0]!r} "
                    "with other keys; use exactly one of "
                    f"{list(_RESERVED)}, or only labels for labeled levels."
                )
            if reserved:
                key = reserved[0]
                inner = spec[key]
                if key == "values":
                    if not isinstance(inner, list | tuple):
                        raise StudyError(
                            f"{what}: 'values' needs a list of levels, got {inner!r}."
                        )
                    return cls._from_values(path, inner, role, what)
                if key == "levels":
                    if not isinstance(inner, Mapping):
                        raise StudyError(
                            f"{what}: 'levels' needs a mapping {{label: value}}, got "
                            f"{inner!r}."
                        )
                    return cls._from_labeled(path, inner, role, what)
                if key == "value":
                    value = _plain(inner, f"{what}: the fixed value")
                    return cls(path, "fixed", (value,), role=role)
                return cls._from_range(path, key, inner, role, what)
            _check_distribution_like(spec, what, admits_distribution)
            if len(spec) == 1:  # one label: more likely a misspelt reserved key
                (label,) = spec
                close = difflib.get_close_matches(
                    str(label), _RESERVED, n=1, cutoff=0.75
                )
                if close:
                    raise StudyError(
                        f"{what}: unknown key {label!r}; did you mean {close[0]!r}? "
                        f"(A mapping with one key is read as one labeled level; "
                        f"the factor keys are {', '.join(_RESERVED)}.)"
                    )
            return cls._from_labeled(path, spec, role, what)
        value = _plain(spec, f"{what}: the fixed value")
        return cls(path, "fixed", (value,), role=role)

    @classmethod
    def _from_values(cls, path, values, role, what) -> Factor:
        if not values:
            raise StudyError(
                f"{what} has no levels; give at least one value, e.g. [5, 10]."
            )
        levels = tuple(
            _plain(value, f"{what}: level {i}") for i, value in enumerate(values)
        )
        return cls(path, "levels", levels, role=role)

    @classmethod
    def _from_labeled(cls, path, mapping, role, what) -> Factor:
        if not mapping:
            raise StudyError(
                f"{what} has no levels; give at least one {{label: value}} entry."
            )
        labels = tuple(str(label) for label in mapping)
        if len(set(labels)) != len(labels):
            raise StudyError(f"{what}: duplicate labels {list(labels)}.")
        levels = tuple(
            _plain(value, f"{what}: level {label!r}")
            for label, value in zip(labels, mapping.values())
        )
        return cls(path, "levels", levels, labels, role=role)

    @classmethod
    def _from_range(cls, path, kind, bounds, role, what) -> Factor:
        example = {"range": "[0.2, 0.9]", "int_range": "[1, 10]"}.get(
            kind, "[0.01, 10.0]"
        )
        if (
            not isinstance(bounds, list | tuple)
            or len(bounds) != 2
            or not all(_is_number(b) for b in bounds)
        ):
            raise StudyError(
                f"{what}: {kind!r} needs [low, high] numbers, e.g. "
                f"{{{kind}: {example}}}, got {bounds!r}."
            )
        low, high = (float(b) for b in bounds)
        if not (math.isfinite(low) and math.isfinite(high)) or not low < high:
            raise StudyError(
                f"{what}: {kind!r} needs finite bounds with low < high, got {list(bounds)}."
            )
        if kind == "int_range" and not all(float(b).is_integer() for b in bounds):
            raise StudyError(
                f"{what}: 'int_range' needs integer bounds, got {list(bounds)}."
            )
        if kind == "log_range" and low <= 0:
            raise StudyError(f"{what}: 'log_range' needs low > 0, got {list(bounds)}.")
        return cls(path, kind, bounds=(low, high), role=role)

    # -- views -------------------------------------------------------------------

    @property
    def is_continuous(self) -> bool:
        """True for ``range``, ``int_range`` and ``log_range`` factors."""
        return self.kind in CONTINUOUS_KINDS

    @property
    def is_fixed(self) -> bool:
        """True for a fixed value."""
        return self.kind == "fixed"

    @property
    def is_labeled(self) -> bool:
        """True for labeled levels (``{label: value}``)."""
        return self.labels is not None

    def items(self) -> list[tuple[str | None, Any]]:
        """Return ``[(label or None, value), ...]`` for discrete factors.

        :raises StudyError: for continuous factors
        """
        if self.is_continuous:
            raise StudyError(
                f"Factor {self.path!r} is a {self.kind}; it has no levels."
            )
        labels = self.labels or (None,) * len(self.levels)
        return list(zip(labels, self.levels))

    def label_of(self, value: Any) -> str | None:
        """Return the label of a level value (``None`` if unlabeled or unknown)."""
        if self.labels is None:
            return None
        key = canonical_json(value)
        for label, level in zip(self.labels, self.levels):
            if canonical_json(level) == key:
                return label
        return None

    def to_spec(self) -> Any:
        """Return the factor as a spec (plain data) that :meth:`parse` reads back.

        Example::

            Factor.parse("algorithm", {"values": ["a", "b"]}).to_spec()   # ["a", "b"]
        """
        if self.is_continuous:
            low, high = self.bounds
            if self.kind == "int_range":
                return {self.kind: [int(low), int(high)]}
            return {self.kind: [low, high]}
        if self.is_fixed:
            value = self.levels[0]
            return {"value": value} if isinstance(value, dict | list) else value
        if self.is_labeled:
            return {"levels": dict(zip(self.labels, self.levels))}
        return list(self.levels)

    def describe(self) -> str:
        """Short human-readable form, e.g. ``"ideal=0.0 | lossy=0.1"``."""
        if self.is_continuous:
            low, high = self.bounds
            return f"{self.kind} [{low:g}, {high:g}]"
        if self.is_fixed:
            return f"fixed {_short(self.levels[0])}"
        return " | ".join(
            _short(value) if label is None else f"{label}={_short(value)}"
            for label, value in self.items()
        )


def _short(value: Any, limit: int = 40) -> str:
    text = json.dumps(value) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[: limit - 3] + "..."


# --------------------------------------------------------------------------- points


@dataclass
class DesignPoint:
    """One combination of factor values produced by a design.

    :param values: ``{path: value}`` for every factor of the point (plain data)
    :param labels: ``{path: label}`` for labeled factors
    :param meta: design metadata, e.g. ``{"row": 3}`` or (Sobol)
        ``{"block": "A", "column": 0, "sample": 7}``

    Example::

        DesignPoint({"ev.n": 5}, {}, {"row": 0})
    """

    values: dict
    labels: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)


def cross(
    points: Sequence[DesignPoint], factors: Sequence[Factor]
) -> list[DesignPoint]:
    """Cross design points with the levels of discrete *factors*.

    Each point is combined with every level combination of *factors* (the
    points vary slowest, the last factor fastest).  Values keep the order of
    the points' own values followed by *factors*.

    :param points: the design's own points
    :param factors: discrete factors the points do not set
    :return: ``len(points) * prod(levels)`` points
    :raises StudyError: if a factor is continuous

    Example::

        cross([DesignPoint({"x": 0.3})], [Factor.parse("algorithm", ["a", "b"])])
        # two points: x=0.3 with algorithm a, then with b
    """
    continuous = [f.path for f in factors if f.is_continuous]
    if continuous:
        raise StudyError(
            f"Cannot cross continuous factor(s) {continuous} with design points; give "
            "them levels or use a sampling design."
        )
    grids = list(itertools.product(*(f.items() for f in factors)))
    result = []
    for point in points:
        for combo in grids:
            values = dict(point.values)
            labels = dict(point.labels)
            for factor, (label, value) in zip(factors, combo):
                values[factor.path] = value
                if label is not None:
                    labels[factor.path] = label
            result.append(DesignPoint(values, labels, dict(point.meta)))
    return result


# --------------------------------------------------------------------------- designs

_DESIGNS: dict[str, type] = {}


class Design:
    """Base class of experimental designs.

    Any object with a ``generate(factors, seeds) -> list[DesignPoint]`` method
    can serve as a design; subclassing :class:`Design` adds registration under
    a ``kind`` (for study files: ``design: full_factorial`` or
    ``design: {csv: design.csv}``) and :meth:`to_spec`.

    Subclasses that set the class attribute ``kind`` are registered and can be
    created with ``resolve_design``.

    Example::

        class OneAtATime(Design):
            kind = "one_at_a_time"

            def generate(self, factors, seeds):
                base = {f.path: f.items()[0][1] for f in factors}
                points = [DesignPoint(dict(base))]
                for f in factors:
                    for label, value in f.items()[1:]:
                        points.append(DesignPoint({**base, f.path: value}))
                return points
    """

    kind: ClassVar[str | None] = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        kind = cls.__dict__.get("kind")
        if isinstance(kind, str):
            _DESIGNS[kind] = cls

    def generate(self, factors: Sequence[Factor], seeds: SeedTree) -> list[DesignPoint]:
        """Return the design points for *factors*.

        :param factors: the study factors, uncertainties first, then levers
        :param seeds: the design's own seed tree (the study passes
            ``root.child("design")``)
        :return: the points in run order
        """
        raise NotImplementedError

    def to_spec(self) -> Any:
        """Return the design as plain data (as written to ``study.yaml``)."""
        return self.kind

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


def design_kinds() -> dict[str, type]:
    """Return the registered design classes by kind.

    Example::

        sorted(design_kinds())    # ["csv", "explicit", "full_factorial", "lhs", "random", "sobol"]
    """
    return dict(_DESIGNS)


def resolve_design(spec: Any) -> Any:
    """Turn a design spec into a design object.

    Accepts a design object (anything with ``generate``), ``None`` (full
    factorial), a kind name (``"full_factorial"``) or a one-key mapping
    ``{kind: arguments}``: a mapping of arguments is passed as keywords,
    anything else as the single positional argument
    (``{"csv": "design.csv"}``, ``{"explicit": [...]}``).

    :param spec: the spec
    :return: the design
    :raises StudyError: for unknown kinds (with a did-you-mean suggestion) and
        invalid arguments

    Example::

        resolve_design("full_factorial")          # FullFactorial()
        resolve_design({"csv": "design.csv"})     # FromCsv("design.csv")
    """
    if spec is None:
        return FullFactorial()
    if callable(getattr(spec, "generate", None)) and not isinstance(spec, type):
        return spec
    if isinstance(spec, str):
        kind, args = spec, None
    elif isinstance(spec, Mapping) and len(spec) == 1:
        kind, args = next(iter(spec.items()))
    else:
        raise StudyError(
            f"Invalid design {spec!r}: give a design object, a kind such as "
            "'full_factorial' or a one-key mapping such as {'csv': 'design.csv'}."
        )
    cls = _DESIGNS.get(kind)
    if cls is None:
        hint = did_you_mean(str(kind), list(_DESIGNS))
        raise StudyError(
            f"Unknown design {kind!r}.{hint} Known designs: {sorted(_DESIGNS)}."
        )
    try:
        if args is None:
            return cls()
        if isinstance(args, Mapping):
            return cls(**args)
        return cls(args)
    except TypeError as exc:
        raise StudyError(f"Invalid arguments for design {kind!r}: {exc}") from None


class FullFactorial(Design):
    """Cross all levels of all factors.

    Order: factors in declaration order (uncertainties first, then levers);
    the last factor varies fastest.  Fixed factors appear in every point.
    Continuous ranges are rejected.

    Example::

        # ev.n: [5, 10, 20], comm.loss_percent: {ideal, lossy}, algorithm: [a, b]
        len(FullFactorial().generate(factors, seeds))    # 3 * 2 * 2 = 12
    """

    kind = "full_factorial"

    def generate(self, factors: Sequence[Factor], seeds: SeedTree) -> list[DesignPoint]:
        continuous = [f for f in factors if f.is_continuous]
        if continuous:
            f = continuous[0]
            raise StudyError(
                f"FullFactorial cannot cross the continuous factor {f.path!r} "
                f"({f.to_spec()!r}); give it levels (a list or {{values: [...]}}) or use "
                "a sampling design."
            )
        return cross([DesignPoint({}, {}, {})], factors)


def _normalize_rows(rows: Any, what: str) -> list[dict]:
    if isinstance(rows, str | bytes) or not isinstance(rows, Sequence):
        raise StudyError(
            f"{what} needs a list of rows ({{path: value}} mappings), got "
            f"{type(rows).__name__}."
        )
    if not rows:
        raise StudyError(f"{what} needs at least one row.")
    result = []
    for i, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise StudyError(
                f"{what}: row {i} must be a mapping {{path: value}}, got {row!r}."
            )
        bad = [key for key in row if not isinstance(key, str) or not key.strip()]
        if bad:
            raise StudyError(f"{what}: row {i} has invalid factor path {bad[0]!r}.")
        result.append(
            {
                key.strip(): _plain(value, f"{what}: row {i}, {key!r}")
                for key, value in row.items()
            }
        )
    return result


#: the reserved row column that sets the replications of one design point
REPLICATIONS_COLUMN = "replications"


def check_replications(value: Any, what: str) -> int:
    """Validate a per-point or per-subexperiment replication count (an int >= 1)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise StudyError(f"{what}: replications must be an int >= 1, got {value!r}.")
    return value


def _rows_to_points(
    rows: list[dict], factors: Sequence[Factor], what: str
) -> list[DesignPoint]:
    by_path = {f.path: f for f in factors}
    points = []
    first_row: dict[str, int] = {}
    repeated: list[tuple[int, int]] = []
    for i, given in enumerate(rows):
        meta: dict = {"row": i}
        row = dict(given)
        if REPLICATIONS_COLUMN in row and REPLICATIONS_COLUMN not in by_path:
            meta[REPLICATIONS_COLUMN] = check_replications(
                row.pop(REPLICATIONS_COLUMN), f"{what}: row {i}"
            )
        values: dict = {}
        labels: dict = {}
        ordered = [f.path for f in factors if f.path in row]
        ordered += [path for path in row if path not in by_path]
        for path in ordered:
            value = row[path]
            factor = by_path.get(path)
            if factor is not None and factor.is_labeled:
                if isinstance(value, str) and value in factor.labels:
                    labels[path] = value
                    value = factor.levels[factor.labels.index(value)]
                else:
                    label = factor.label_of(value)
                    if label is not None:
                        labels[path] = label
            values[path] = value
        rest = [f for f in factors if f.path not in row]
        continuous = [f.path for f in rest if f.is_continuous]
        if continuous:
            raise StudyError(
                f"{what}: row {i} does not set the continuous factor(s) {continuous}; "
                "set them in every row or give them levels."
            )
        key = canonical_json(values)
        if key in first_row:
            repeated.append((first_row[key], i))
        else:
            first_row[key] = i
        points.extend(cross([DesignPoint(values, labels, meta)], rest))
    if repeated:
        shown = ", ".join(f"{a} and {b}" for a, b in repeated[:3])
        more = f" (and {len(repeated) - 3} more)" if len(repeated) > 3 else ""
        warn_user(
            f"{what}: rows {shown}{more} are identical (rows count from 0). "
            "Identical rows get the same seeds, so they run once and add no "
            "replications; use replications=N for more runs of every point, or a "
            "'replications' column for more runs of some points (an unequal "
            "allocation)."
        )
    return points


class Explicit(Design):
    """Run the given rows (``{path: value}`` mappings) as design points.

    A row value that is a label of a labeled factor stands for that level.
    Factors a row does not set are crossed with it (row slowest, last factor
    fastest); paths not declared as factors become factors of their own.
    The reserved key ``replications`` (an int >= 1; unless a factor of that
    path is declared) sets the number of replications of that row's points
    instead of the study's ``replications``: an unequal allocation, e.g.
    more runs where the outcome varies most.  Replications keep the indices
    ``0..n-1``, so a point's first runs are the same as with fewer.

    :param rows: the rows

    Example::

        Explicit([
            {"ev.n": 5, "algorithm": "greedy"},
            {"ev.n": 20, "algorithm": "proportional", "comm.loss_percent": "lossy"},
        ])
        Explicit([{"servers": 2}, {"servers": 3, "replications": 20}])
    """

    kind = "explicit"

    def __init__(self, rows: Sequence[Mapping[str, Any]]):
        self.rows = _normalize_rows(rows, "Explicit design")

    def generate(self, factors: Sequence[Factor], seeds: SeedTree) -> list[DesignPoint]:
        return _rows_to_points(self.rows, factors, "Explicit design")

    def to_spec(self) -> Any:
        return {self.kind: [dict(row) for row in self.rows]}

    def __repr__(self) -> str:
        return f"Explicit(<{len(self.rows)} rows>)"


def _parse_cell(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return text


class FromCsv(Design):
    """Read design rows from a CSV file (header = factor paths).

    Cells are parsed like override values (``5`` → int, ``0.1`` → float,
    ``greedy`` → str, ``[1, 2]`` → list); quote cells YAML would read
    otherwise (``'no'``).  Rows behave as in :class:`Explicit`, including a
    ``replications`` column for per-row replication counts.  The file is
    read when the study is planned; relative paths resolve against the
    current directory.

    :param path: the CSV file
    :param delimiter: the column delimiter

    Example::

        # design.csv:
        #   ev.n,algorithm
        #   5,greedy
        #   20,proportional
        Study(scenario, design=FromCsv("design.csv"), replications=3)
    """

    kind = "csv"

    def __init__(self, path: str | os.PathLike, *, delimiter: str = ","):
        if not isinstance(path, str | os.PathLike):
            raise StudyError(f"FromCsv needs a file path, got {path!r}.")
        self.path = Path(path)
        self.delimiter = delimiter

    def read_rows(self) -> list[dict]:
        """Read and parse the rows.

        :raises StudyError: if the file is missing, empty, has duplicate or
            empty header names, or a row with missing or empty cells
        """
        what = f"FromCsv({str(self.path)!r})"
        try:
            text = self.path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            raise StudyError(
                f"{what}: no such file (resolved to {self.path.resolve()})."
            ) from None
        reader = csv.reader(text.splitlines(), delimiter=self.delimiter)
        lines = [line for line in reader if any(cell.strip() for cell in line)]
        if not lines:
            raise StudyError(f"{what}: the file is empty; it needs a header row.")
        header = [cell.strip() for cell in lines[0]]
        if any(not name for name in header):
            raise StudyError(f"{what}: the header has an empty column name: {header}.")
        duplicates = sorted({name for name in header if header.count(name) > 1})
        if duplicates:
            raise StudyError(f"{what}: duplicate header column(s) {duplicates}.")
        if len(lines) == 1:
            raise StudyError(f"{what}: the file has a header but no rows.")
        rows = []
        for number, line in enumerate(lines[1:], start=2):
            if len(line) != len(header):
                raise StudyError(
                    f"{what}: line {number} has {len(line)} cells, the header has "
                    f"{len(header)}."
                )
            row = {}
            for name, cell in zip(header, line):
                if not cell.strip():
                    raise StudyError(
                        f"{what}: line {number}, column {name!r} is empty; every row "
                        "must set every column."
                    )
                row[name] = _parse_cell(cell.strip())
            rows.append(row)
        return _normalize_rows(rows, what)

    def generate(self, factors: Sequence[Factor], seeds: SeedTree) -> list[DesignPoint]:
        return _rows_to_points(
            self.read_rows(), factors, f"FromCsv({str(self.path)!r})"
        )

    def to_spec(self) -> Any:
        return {self.kind: str(self.path)}

    def __repr__(self) -> str:
        return f"FromCsv({str(self.path)!r})"


class SamplingDesign(Design):
    """Base class of designs that sample the continuous factors.

    Subclasses implement :meth:`unit_samples`; this class scales the samples
    to the factor ranges (``range``: linear, ``int_range``: integers within
    the bounds, ``log_range``: log-uniform), keeps fixed factors fixed and
    crosses discrete factors with more than one level with the samples
    (samples vary slowest).

    Example::

        class RandomUniform(SamplingDesign):
            kind = "uniform_random"

            def __init__(self, n):
                self.n = n

            def unit_samples(self, dimensions, seeds):
                rng = seeds.generator()
                return [(row, {"sample": j})
                        for j, row in enumerate(rng.random((self.n, dimensions)))]
    """

    def unit_samples(
        self, dimensions: int, seeds: SeedTree
    ) -> Sequence[tuple[Sequence[float], dict]]:
        """Return ``[(point in [0, 1)^dimensions, meta), ...]``.

        :param dimensions: the number of continuous factors
        :param seeds: the design's seed tree
        """
        raise NotImplementedError

    def generate(self, factors: Sequence[Factor], seeds: SeedTree) -> list[DesignPoint]:
        continuous = [f for f in factors if f.is_continuous]
        if not continuous:
            raise StudyError(
                f"{type(self).__name__} samples continuous factors, but the study has "
                "none; declare one as {range: [low, high]} or use FullFactorial."
            )
        discrete = [f for f in factors if not f.is_continuous]
        points = []
        for unit, meta in self.unit_samples(len(continuous), seeds):
            if len(unit) != len(continuous):
                raise StudyError(
                    f"{type(self).__name__}.unit_samples returned a point with "
                    f"{len(unit)} coordinates for {len(continuous)} continuous factors."
                )
            sampled = {f.path: scale_unit(f, u) for f, u in zip(continuous, unit)}
            points.append(DesignPoint(sampled, {}, dict(meta)))
        order = [f.path for f in factors]
        crossed = cross(points, discrete)
        for point in crossed:
            point.values = {path: point.values[path] for path in order}
        return crossed


def scale_unit(factor: Factor, u: float) -> float | int:
    """Map ``u`` in ``[0, 1)`` to a value of a continuous *factor*.

    Example::

        scale_unit(Factor.parse("x", {"int_range": [1, 3]}), 0.99)    # 3
    """
    low, high = factor.bounds
    u = min(max(float(u), 0.0), 1.0)
    if factor.kind == "int_range":
        return int(min(math.floor(low + u * (high - low + 1)), high))
    if factor.kind == "log_range":
        return math.exp(math.log(low) + u * (math.log(high) - math.log(low)))
    if factor.kind == "range":
        return low + u * (high - low)
    raise StudyError(f"Factor {factor.path!r} is not continuous.")


# --------------------------------------------------------------------------- samplers

#: Blocks of a Saltelli design (``DesignPoint.meta["block"]``).
SOBOL_BLOCKS: tuple[str, ...] = ("A", "AB", "BA", "B")


def _qmc() -> Any:
    try:
        from scipy.stats import qmc
    except ImportError as exc:
        raise ImportError(
            "The Lhs and Sobol designs need scipy: pip install "
            "'mango-agents[scenario-all]' (or pip install scipy)"
        ) from exc
    return qmc


def _engine(cls: Any, dimensions: int, generator: Any, **kwargs: Any) -> Any:
    """Create a ``scipy.stats.qmc`` engine driven by a numpy *generator*."""
    try:
        return cls(dimensions, rng=generator, **kwargs)
    except TypeError:  # scipy < 1.15 calls the argument 'seed'
        return cls(dimensions, seed=generator, **kwargs)


def _check_n(n: Any, design: str) -> int:
    if isinstance(n, bool) or not isinstance(n, numbers.Integral) or n < 1:
        raise StudyError(
            f"{design} needs n, the number of samples, as an int >= 1, got {n!r}."
        )
    return int(n)


def _check_flag(value: Any, name: str, design: str) -> bool:
    if not isinstance(value, bool):
        raise StudyError(f"{design}: {name} must be True or False, got {value!r}.")
    return value


class Lhs(SamplingDesign):
    """Latin hypercube sample of the continuous factors (``scipy.stats.qmc``).

    Draws *n* points; each continuous factor's range is cut into *n* equal
    strata and every stratum is used exactly once.  Discrete factors with
    several levels are crossed with the samples (samples vary slowest),
    fixed factors stay fixed.  The sample is drawn from the study's design
    seed, so the same study seed gives the same points.  Points carry
    ``meta = {"sample": j}`` (the ``sample`` column of the study frame).
    A study with this design defaults to
    ``common_random_numbers="levers"``: every sample gets its own seed.
    Needs scipy.

    :param n: number of samples (per combination of discrete levels)
    :param scramble: place points randomly within their strata (``False``:
        at the stratum centres)

    Example::

        study = Study(scenario,
                      uncertainties={"grid.pv_share": {"range": [0.2, 0.9]},
                                     "ev.n": {"int_range": [5, 40]}},
                      # a str field; a tagged union is varied as "algorithm.kind"
                      levers={"algorithm": ["greedy", "proportional"]},
                      design=Lhs(64), out="results/lhs")
        len(study.plan())        # 64 samples x 2 algorithms = 128 runs
    """

    kind = "lhs"

    def __init__(self, n: int, *, scramble: bool = True):
        self.n = _check_n(n, "Lhs")
        self.scramble = _check_flag(scramble, "scramble", "Lhs")

    def unit_samples(self, dimensions: int, seeds: SeedTree) -> list:
        engine = _engine(
            _qmc().LatinHypercube,
            dimensions,
            seeds.generator(),
            scramble=self.scramble,
        )
        return [(row, {"sample": j}) for j, row in enumerate(engine.random(self.n))]

    def to_spec(self) -> Any:
        return {self.kind: {"n": self.n, "scramble": self.scramble}}

    def __repr__(self) -> str:
        return f"Lhs(n={self.n}{'' if self.scramble else ', scramble=False'})"


class RandomSample(SamplingDesign):
    """Independent uniform random sample of the continuous factors.

    Draws *n* points from the study's design seed (a numpy generator, no
    scipy needed); otherwise like :class:`Lhs`.  Points carry
    ``meta = {"sample": j}``; a study defaults to
    ``common_random_numbers="levers"``.

    :param n: number of samples (per combination of discrete levels)

    Example::

        Study(scenario, uncertainties={"comm.loss_percent": {"range": [0.0, 0.2]}},
              design=RandomSample(100), out="results/random")
    """

    kind = "random"

    def __init__(self, n: int):
        self.n = _check_n(n, "RandomSample")

    def unit_samples(self, dimensions: int, seeds: SeedTree) -> list:
        sample = seeds.generator().random((self.n, dimensions))
        return [(row, {"sample": j}) for j, row in enumerate(sample)]

    def to_spec(self) -> Any:
        return {self.kind: {"n": self.n}}

    def __repr__(self) -> str:
        return f"RandomSample(n={self.n})"


class Sobol(SamplingDesign):
    """Saltelli design for Sobol' sensitivity indices of the continuous factors.

    A scrambled Sobol' sequence with ``2 d`` dimensions (``d`` continuous
    factors) gives two independent ``n x d`` matrices *A* and *B*; the
    matrix *AB_i* is *A* with column *i* taken from *B*.  For every sample
    *j* the design runs ``A_j``, ``AB_0j`` ... ``AB_(d-1)j`` and ``B_j``:
    ``n (d + 2)`` points per combination of discrete levels (crossed as in
    :class:`Lhs`).  With ``second_order=True`` the matrices *BA_i* (*B*
    with column *i* from *A*) are added before ``B_j``: ``n (2 d + 2)``
    points.  Each point carries ``meta = {"block": "A" | "AB" | "BA" | "B",
    "column": i or None, "sample": j}`` and, for *AB*/*BA* points, the
    swapped factor's path as ``"factor"``;
    ``StudyFrame.sobol_indices()`` reads them back.

    *n* must be a power of two (the balance properties of Sobol' points
    and ``scipy.stats.sobol_indices`` require it); any other *n* is rounded
    up to the next power of two with a warning.  A study with this design
    keeps ``common_random_numbers=True`` by default: the points of
    replication *r* share one seed, so the estimators compare matched
    points on the same noise.  Needs scipy; the second-order analysis needs
    SALib.

    :param n: base samples per combination of discrete levels (a power of
        two)
    :param second_order: add the *BA* blocks for second-order indices
    :param scramble: scramble the Sobol' sequence (recommended; ``False``
        starts at the lower bounds)
    :raises StudyError: for an invalid *n*
    :raises ImportError: for ``second_order=True`` without SALib

    Example::

        study = Study(scenario,
                      uncertainties={"grid.pv_share": {"range": [0.2, 0.9]},
                                     "ev.capacity_kwh": {"range": [40, 100]}},
                      design=Sobol(256), replications=3,
                      outcomes=[kpi.self_consumption], out="results/sobol_pv")
        len(study.plan())                                 # 256 * (2 + 2) * 3 = 3072
        study.run(workers=16)
        load_study("results/sobol_pv").sobol_indices()    # S1 and ST per factor
    """

    kind = "sobol"

    def __init__(self, n: int, *, second_order: bool = False, scramble: bool = True):
        n = _check_n(n, "Sobol")
        size = 1 << (n - 1).bit_length()
        if size != n:
            warnings.warn(
                f"Sobol: n={n} is not a power of two; using n={size} (the balance "
                "properties of Sobol' points and scipy.stats.sobol_indices need 2^m "
                "samples).",
                UserWarning,
                stacklevel=2,
            )
        self.n = size
        self.second_order = _check_flag(second_order, "second_order", "Sobol")
        self.scramble = _check_flag(scramble, "scramble", "Sobol")
        if self.second_order:
            _require_salib("Sobol(second_order=True)")

    def unit_samples(self, dimensions: int, seeds: SeedTree) -> list:
        engine = _engine(
            _qmc().Sobol, 2 * dimensions, seeds.generator(), scramble=self.scramble
        )
        base = engine.random(self.n)
        a, b = base[:, :dimensions], base[:, dimensions:]
        samples: list = []
        for j in range(self.n):
            samples.append((a[j], {"block": "A", "column": None, "sample": j}))
            for i in range(dimensions):
                row = a[j].copy()
                row[i] = b[j, i]
                samples.append((row, {"block": "AB", "column": i, "sample": j}))
            if self.second_order:
                for i in range(dimensions):
                    row = b[j].copy()
                    row[i] = a[j, i]
                    samples.append((row, {"block": "BA", "column": i, "sample": j}))
            samples.append((b[j], {"block": "B", "column": None, "sample": j}))
        return samples

    def generate(self, factors: Sequence[Factor], seeds: SeedTree) -> list[DesignPoint]:
        points = super().generate(factors, seeds)
        continuous = [f.path for f in factors if f.is_continuous]
        for point in points:
            column = point.meta.get("column")
            if column is not None:
                point.meta["factor"] = continuous[column]
        return points

    def to_spec(self) -> Any:
        return {
            self.kind: {
                "n": self.n,
                "second_order": self.second_order,
                "scramble": self.scramble,
            }
        }

    def __repr__(self) -> str:
        extra = ", second_order=True" if self.second_order else ""
        extra += "" if self.scramble else ", scramble=False"
        return f"Sobol(n={self.n}{extra})"


def _require_salib(what: str) -> Any:
    try:
        import SALib
    except ImportError as exc:
        raise ImportError(
            f"{what} needs SALib for the second-order Sobol' analysis: pip install "
            "SALib (first-order and total indices need only scipy: use "
            "second_order=False)"
        ) from exc
    return SALib
