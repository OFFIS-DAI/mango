"""Reductions of recorders: across a group (``aggregate=``) and over time (``window=``).

``scenario.record(name, group, ..., aggregate=...)`` reduces the values of
all members of a group at each sample to one row per group (``"sum"``,
``"mean"``, ``"min"``, ``"max"`` or a function) or to one row per category
(``"count"``).  ``window=`` (with ``every=``) evaluates the recorder at every
step and writes, at each multiple of ``every``, the reduction of the samples
in the window ``(t - every, t]``.

``kind=`` says what a recorded value is, which decides how windows and
:meth:`RunResult.integrate <mango.scenario.RunResult.integrate>` weight it:
a *flow* describes the interval that just ended (computed in ``on_step``
for the step, e.g. a power: the row at ``t`` covers ``[t_prev, t)``), a
*state* is a level that holds until it changes (a queue length or an
inventory changed in handlers, tasks or events: the row at ``t`` holds on
``[t, t_next)``).  A state recorder samples a phase from its first instant
on (a row at the start of every recorded phase), and its ``window="mean"``
is the time-weighted mean of the held values.

``__all__`` is the public part: the names and :class:`Window` and
:func:`group_reduce`, the two reductions the runtime applies (scripts written
by ``to_script`` call them too, so they record the same values);
``check_aggregate``, ``check_window``, ``check_kind`` and
``check_categories`` are the package-internal interface of the declarations.

Example::

    scenario.record("infected", person, lambda a: a.state == "I", aggregate="sum")
    scenario.record("state", person, attr="state", aggregate="count")
    scenario.record("queue", lanes, attr="queue", every="1min", window="max")
    scenario.record("queue_len", lanes, attr="queue", kind="state")
"""

from __future__ import annotations

import math
import numbers
import sys
from collections.abc import Mapping
from typing import Any

from ._util import did_you_mean
from .errors import ScenarioError

__all__ = ["AGGREGATES", "RECORD_KINDS", "WINDOWS", "Window", "group_reduce"]

#: Reductions across the members of a group (``record(..., aggregate=)``);
#: a function ``fn(values) -> value | {label: value}`` works as well.
AGGREGATES: tuple[str, ...] = ("count", "sum", "mean", "min", "max")

#: Reductions over the samples of an ``every`` window (``record(..., window=)``).
WINDOWS: tuple[str, ...] = ("last", "sum", "mean", "min", "max")

#: What a recorded value is (``record(..., kind=)``): ``"flow"`` (describes
#: the interval that ended at the row) or ``"state"`` (holds from the row on).
RECORD_KINDS: tuple[str, ...] = ("state", "flow")

#: Explanation of the two kinds, shared by declaration and analysis errors.
KIND_HINT = (
    "kind='flow' for a value that describes the interval that just ended (set in "
    "on_step for the step, e.g. a power: the row at t covers [t_prev, t)), "
    "kind='state' for a level that holds until it changes (a queue length or an "
    "inventory changed in handlers, tasks or events: the row at t holds on "
    "[t, t_next))"
)


def check_aggregate(value: Any, what: str) -> Any:
    """Validate ``aggregate=``: ``None``, a name of :data:`AGGREGATES` or a function."""
    if value is None or value in AGGREGATES:
        return value
    if isinstance(value, str):
        raise ScenarioError(
            f"{what}: unknown aggregate {value!r}.{did_you_mean(value, AGGREGATES)} "
            f"Use one of {', '.join(map(repr, AGGREGATES))} or a function "
            "fn(values) -> value."
        )
    if callable(value):
        return value
    raise TypeError(
        f"{what}: aggregate must be one of {', '.join(map(repr, AGGREGATES))} or a "
        f"function, got {type(value).__name__}."
    )


def check_window(value: Any, every: Any, what: str) -> Any:
    """Validate ``window=`` (it needs ``every``: the window is ``(t - every, t]``)."""
    if value is None:
        return None
    if not isinstance(value, str) or value not in WINDOWS:
        hint = did_you_mean(value, WINDOWS) if isinstance(value, str) else ""
        raise ScenarioError(
            f"{what}: unknown window {value!r}.{hint} Use one of "
            f"{', '.join(map(repr, WINDOWS))}."
        )
    if every is None:
        raise ScenarioError(
            f"{what}: window={value!r} reduces the samples between two recorded "
            "times, so it needs every= (e.g. every='1min', window='max'); without "
            "every each step is recorded and there is nothing to reduce."
        )
    return value


def check_kind(value: Any, window: Any, what: str) -> str | None:
    """Validate ``kind=`` of a recorder (``None``, ``"state"`` or ``"flow"``).

    A state's ``window="sum"`` is refused: summing the samples of a level
    depends on how often it was sampled (use ``window="mean"``, the
    time-weighted mean, or ``RunResult.integrate``).
    """
    if value is None:
        return None
    if not isinstance(value, str) or value not in RECORD_KINDS:
        hint = did_you_mean(value, RECORD_KINDS) if isinstance(value, str) else ""
        raise ScenarioError(f"{what}: unknown kind {value!r}.{hint} Use {KIND_HINT}.")
    if value == "state" and window == "sum":
        raise ScenarioError(
            f"{what}: window='sum' adds up the samples of a state, which depends on "
            "how often it was sampled; use window='mean' (the time-weighted mean "
            "over the window) or integrate the state with RunResult.integrate."
        )
    return value


def check_categories(value: Any, aggregate: Any, what: str) -> tuple[str, ...] | None:
    """Validate ``categories=`` (only with ``aggregate="count"``); return their labels."""
    if value is None:
        return None
    if aggregate != "count":
        raise ScenarioError(
            f"{what}: categories= declares the categories of aggregate='count' (they "
            "are counted 0 when absent); it needs aggregate='count'."
        )
    if isinstance(value, str | bytes | Mapping) or not hasattr(value, "__iter__"):
        raise TypeError(
            f"{what}: categories must be a list of category values, e.g. "
            f"['S', 'E', 'I', 'R'], got {value!r}."
        )
    labels = [category_label(item) for item in value]
    if not labels:
        raise ScenarioError(f"{what}: categories is empty; list the categories.")
    repeated = sorted({label for label in labels if labels.count(label) > 1})
    if repeated:
        raise ScenarioError(f"{what}: categories {repeated} are listed twice.")
    return tuple(labels)


def aggregate_text(value: Any) -> Any:
    """Process-independent description of an ``aggregate`` (fingerprints, plans)."""
    if value is None or isinstance(value, str):
        return value
    return getattr(value, "__qualname__", None) or type(value).__qualname__


def category_label(value: Any) -> str:
    """The ``entity`` label of a category of ``aggregate="count"``.

    Strings stay as they are; ``True``/``False``, numbers and ``None`` become
    their ``str`` (``"True"``, ``"2"``, ``"0.5"``, ``"None"``).

    Example::

        category_label("I"), category_label(True), category_label(2)   # "I", "True", "2"
    """
    numpy = sys.modules.get("numpy")
    if numpy is not None and isinstance(value, numpy.generic):
        value = value.item()
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, bool | numbers.Number):
        return str(value)
    from .result import tidy_value

    return str(tidy_value(value))


def _number(value: Any, what: str, owner: str) -> float | None:
    """*value* as float, ``None`` for missing values (None, NaN)."""
    if value is None:
        return None
    if isinstance(value, bool | numbers.Real) or (
        (numpy := sys.modules.get("numpy")) is not None
        and isinstance(value, numpy.bool_ | numpy.number)
    ):
        number = float(value)
        return None if math.isnan(number) else number
    raise TypeError(
        f"{what}: the value {value!r} of {owner} is not a number; numeric "
        "reductions need numbers (count categories with aggregate='count')."
    )


def _reduce(how: str, numbers_: list[float], weights: list[float] | None) -> float:
    if how == "sum":
        return math.fsum(numbers_)
    if not numbers_:
        return math.nan
    if how == "min":
        return min(numbers_)
    if how == "max":
        return max(numbers_)
    # mean: weighted by the sample intervals when given (event mode, windows)
    if weights is not None:
        total = math.fsum(weights)
        if total > 0:
            return math.fsum(w * x for w, x in zip(weights, numbers_)) / total
    return math.fsum(numbers_) / len(numbers_)


def group_reduce(
    how: Any, values: list[Any], owners: list[str], what: str
) -> Any | dict[str, Any]:
    """Reduce the member values of one group at one sample.

    :param how: a name of :data:`AGGREGATES` or ``fn(values)``
    :param values: the members' values in index order
    :param owners: the members' names (for error messages)
    :param what: ``"record('x')"`` (for error messages)
    :return: one value, or ``{label: value}`` (``"count"``, or a function
        returning a mapping)

    Example::

        group_reduce("count", ["S", "I", "S"], ["p0", "p1", "p2"], "record('state')")
        # {"S": 2, "I": 1}
        group_reduce("max", [1.0, None, 3.0], ["a", "b", "c"], "record('q')")   # 3.0
    """
    if how == "count":
        counts: dict[str, int] = {}
        for value in values:
            label = category_label(value)
            counts[label] = counts.get(label, 0) + 1
        return counts
    if callable(how):
        return how(list(values))
    present = []
    for value, owner in zip(values, owners):
        number = _number(value, what, f"entity {owner!r}")
        if number is not None:
            present.append(number)
    return _reduce(how, present, None)


class Window:
    """Accumulates the samples of one recorder between two recorded times.

    Keys are entity names, group names or categories; :meth:`add` takes the
    values of one sample with its interval length, :meth:`emit` returns the
    reduction per key and starts the next window.  With *zero_fill* (counts)
    a key that appears within a window counts as 0 for the samples before.

    With *hold* (state recorders) a sample is a level that holds until the
    next one: :meth:`add` first credits the previous sample with the
    interval since it was taken, then holds the new values (``None``: no
    level is recorded from now on, e.g. in a ``record: false`` phase), so
    ``"mean"`` is the time-weighted mean and ``"min"``/``"max"`` the
    extremes of the levels held during the window.

    :param how: a name of :data:`WINDOWS`
    :param what: ``"record('x')"`` (for error messages)
    :param zero_fill: count missing keys as 0 (for ``aggregate="count"``)
    :param hold: weight each sample with the interval after it (states)

    Example::

        window = Window("max", "record('queue')")
        window.add(60.0, {"lane_0": 3})
        window.add(60.0, {"lane_0": 5})
        window.emit()   # {"lane_0": 5.0}

        level = Window("mean", "record('queue')", hold=True)
        level.add(0.0, {"lane_0": 4})     # 4 from t=0 on
        level.add(15.0, {"lane_0": 0})    # 4 held for 15 s, then 0
        level.add(45.0, {"lane_0": 0})    # 0 held for 45 s
        level.emit()   # {"lane_0": 1.0}
    """

    def __init__(
        self, how: str, what: str, *, zero_fill: bool = False, hold: bool = False
    ):
        self.how = how
        self.what = what
        self.zero_fill = zero_fill
        self.hold = hold
        self._weights: list[float] = []
        self._samples: dict[Any, list[tuple[float, Any]]] = {}
        self._held: dict[Any, Any] | None = None

    @property
    def empty(self) -> bool:
        """True when the window holds no sample (no interval with *hold*)."""
        return not self._weights

    def add(self, weight: float, values: Mapping[Any, Any] | None) -> None:
        if self.hold:
            held = self._held
            if held is not None and weight > 0:
                self._credit(weight, held)
            self._held = None if values is None else dict(values)
            return
        self._credit(weight, values)

    def _credit(self, weight: float, values: Mapping[Any, Any]) -> None:
        self._weights.append(weight)
        for key, value in values.items():
            samples = self._samples.get(key)
            if samples is None:
                fill = self._weights[:-1] if self.zero_fill else []
                samples = self._samples[key] = [(w, 0) for w in fill]
            samples.append((weight, value))
        if self.zero_fill:  # keys known from earlier samples of this window
            for key, samples in self._samples.items():
                if len(samples) < len(self._weights):
                    samples.append((weight, 0))

    def emit(self) -> dict[Any, Any]:
        result = {}
        for key, samples in self._samples.items():
            if self.how == "last":
                result[key] = samples[-1][1]
                continue
            weights, present = [], []
            for weight, value in samples:
                number = _number(value, self.what, f"{key!r}")
                if number is not None:
                    weights.append(weight)
                    present.append(number)
            result[key] = _reduce(self.how, present, weights)
        self._weights = []
        self._samples = {}
        return result


def describe(aggregate: Any, window: Any, kind: Any = None) -> str:
    """Short text for plans, e.g. ``" (sum, max per window)"`` or ``" (state)"``."""
    parts = [] if kind is None else [str(kind)]
    if aggregate is not None:
        parts.append(str(aggregate_text(aggregate)))
    if window is not None:
        parts.append(f"{window} per window")
    return f" ({', '.join(parts)})" if parts else ""
