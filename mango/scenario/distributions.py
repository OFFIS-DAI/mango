"""
Typed, serializable probability distributions for scenario parameters.

A distribution is a frozen pydantic model with a ``kind`` tag.  Parameter
models use them as field types, and factories draw from them with the
entity's own random stream, so a value's meaning (range, mean and spread)
lives in the parameter model rather than in the code that reads it.

Fields typed as one concrete class (``soc: Uniform``) accept the full form
``{kind: uniform, low: 0.2, high: 0.8}``, the tagged shorthand
``{uniform: [0.2, 0.8]}``, a bare list of the positional arguments
``[0.2, 0.8]`` and a scalar for the degenerate distribution (``0.5`` means
``Uniform(0.5, 0.5)``).  Fields typed as :data:`Dist` accept every kind
(full form or tagged shorthand) and turn a plain scalar into a
:class:`Constant`.

Example::

    from pydantic import BaseModel
    from mango.scenario import Constant, Dist, Normal, Uniform

    class EVParams(BaseModel, extra="forbid"):
        soc: Uniform = Uniform(0.2, 0.8)
        arrival_h: Normal = Normal(16.0, 1.5)
        delay_s: Dist = Constant(0.0)

    p = EVParams.model_validate({"soc": [0.1, 0.9], "delay_s": {"normal": [0.04, 0.008]}})
    p.soc.draw(rng)          # rng: numpy.random.Generator or random.Random
    p.soc.model_dump()       # {"kind": "uniform", "low": 0.1, "high": 0.9}
"""

from __future__ import annotations

import math
import numbers
import random
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Annotated, Any, ClassVar, Literal, Union

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    GetPydanticSchema,
    model_serializer,
    model_validator,
)

__all__ = [
    "Distribution",
    "Constant",
    "Uniform",
    "IntUniform",
    "Normal",
    "LogNormal",
    "Choice",
    "Bernoulli",
    "FromColumn",
    "Dist",
    "distribution_kinds",
]

_REGISTRY: dict[str, type[Distribution]] = {}


def distribution_kinds() -> dict[str, type[Distribution]]:
    """Return the registered distribution classes by ``kind``.

    Every subclass of :class:`Distribution` that declares a
    ``kind: Literal["..."]`` field registers itself; :data:`Dist` accepts all
    kinds registered before the model that uses it is defined.

    Example::

        sorted(distribution_kinds())[:3]  # ['bernoulli', 'choice', 'constant']
    """
    return dict(_REGISTRY)


def _is_number(value: Any) -> bool:
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def _uses_numpy_api(rng: Any) -> bool:
    return hasattr(rng, "integers") and hasattr(rng, "normal")


def _is_stdlib_api(rng: Any) -> bool:
    return isinstance(rng, random.Random) or rng is random


def _check_rng(rng: Any) -> None:
    if not (_uses_numpy_api(rng) or _is_stdlib_api(rng)):
        raise TypeError(
            "draw() needs a numpy.random.Generator or a random.Random, got "
            f"{type(rng).__name__}"
        )


def _mixed_types(values: list) -> bool:
    """True when numpy would convert some of *values* to another type.

    A list mixing strings with numbers (``[0, "a"]``) becomes a string array
    and a list mixing ``bool`` with numbers an integer array; sized draws
    keep the values of scalar draws instead (an object array).
    """
    has_str = any(isinstance(v, str) for v in values)
    has_bool = any(isinstance(v, bool) for v in values)
    if has_str:
        return not all(isinstance(v, str) for v in values)
    if has_bool:
        return not all(isinstance(v, bool) for v in values)
    return False


def _array(values: list, size: int | tuple[int, ...]):
    import numpy as np

    try:
        out = None if _mixed_types(values) else np.array(values)
    except ValueError:
        out = None
    if out is None or out.shape != (len(values),):
        out = np.empty(len(values), dtype=object)
        out[:] = values
    return out.reshape(size if isinstance(size, tuple) else (size,))


def _count(size: int | tuple[int, ...]) -> int:
    if isinstance(size, tuple):
        return math.prod(size)
    return int(size)


def _stdlib_draws(draw_one, size):
    if size is None:
        return draw_one()
    return _array([draw_one() for _ in range(_count(size))], size)


class Distribution(BaseModel, frozen=True, extra="forbid", allow_inf_nan=False):
    """Base class of all distributions.

    Subclasses declare a ``kind: Literal["<name>"] = "<name>"`` field (which
    registers them for :data:`Dist`), their parameters as fields, the names
    of the positional constructor arguments in ``_positional`` and implement
    ``_draw_numpy``, ``_draw_stdlib`` and :attr:`expected_value`.

    Example::

        u = Uniform(0.2, 0.8)
        u.draw(numpy.random.default_rng(1))            # one float
        u.draw(random.Random(1), size=3)               # numpy array of 3 floats
        u.expected_value                               # 0.5
    """

    _positional: ClassVar[tuple[str, ...]] = ()

    def __init__(self, /, *args: Any, **kwargs: Any):
        if args:
            names = type(self)._positional
            if len(args) > len(names):
                raise TypeError(
                    f"{type(self).__name__}() takes at most {len(names)} positional "
                    f"arguments ({', '.join(names)}), got {len(args)}"
                )
            for name, value in zip(names, args):
                if name in kwargs:
                    raise TypeError(
                        f"{type(self).__name__}() got multiple values for {name!r}"
                    )
                kwargs[name] = value
        super().__init__(**kwargs)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        field = cls.model_fields.get("kind")
        if field is None or not isinstance(field.default, str):
            return
        registered = _REGISTRY.get(field.default)
        if registered is None or not issubclass(cls, registered):
            _REGISTRY[field.default] = cls

    def model_post_init(self, context: Any, /) -> None:
        # A distribution is one value: dumping with exclude_unset must keep
        # every field (incl. ``kind``), never a partial distribution.
        self.__pydantic_fields_set__.update(type(self).model_fields)

    @classmethod
    def _from_list(cls, items: Sequence) -> dict:
        names = cls._positional
        if len(items) > len(names):
            raise ValueError(
                f"{cls._kind()} takes at most {len(names)} positional values "
                f"({', '.join(names)}), got {len(items)}: {list(items)!r}"
            )
        return dict(zip(names, items))

    @classmethod
    def _from_scalar(cls, value: Any) -> dict | None:
        return None

    @classmethod
    def _kind(cls) -> str:
        field = cls.model_fields.get("kind")
        return field.default if field is not None else cls.__name__

    @model_validator(mode="before")
    @classmethod
    def _expand_input(cls, data: Any) -> Any:
        if cls is Distribution:
            return data
        return expand_shorthand(data, cls)

    def draw(self, rng: Any, size: int | tuple[int, ...] | None = None) -> Any:
        """Draw from the distribution.

        :param rng: a ``numpy.random.Generator`` (e.g. ``agent.rng``,
            ``e.rng``) or a ``random.Random``
        :param size: ``None`` for one Python scalar, an int or shape tuple for
            a numpy array of draws
        :return: one value, or an array of shape *size*

        Example::

            Normal(16.0, 1.5).draw(e.rng)           # one float
            Normal(16.0, 1.5).draw(e.rng, size=4)   # array of 4 floats
        """
        _check_rng(rng)
        if _uses_numpy_api(rng):
            return self._draw_numpy(rng, size)
        return _stdlib_draws(lambda: self._draw_stdlib(rng), size)

    def _draw_numpy(self, rng: Any, size: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    def _draw_stdlib(self, rng: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    @property
    def expected_value(self) -> Any:  # pragma: no cover
        """The expected value of a draw."""
        raise NotImplementedError

    def __repr__(self) -> str:
        fields = type(self).model_fields
        parts = []
        keywords = False
        for name in dict.fromkeys((*type(self)._positional, *fields)):
            if name == "kind":
                continue
            field = fields[name]
            value = getattr(self, name)
            text = repr(list(value)) if isinstance(value, tuple) else repr(value)
            if field.is_required() and not keywords:
                parts.append(text)
            elif value != field.default:
                keywords = True
                parts.append(f"{name}={text}")
        return f"{type(self).__name__}({', '.join(parts)})"

    __str__ = __repr__


def expand_shorthand(value: Any, cls: type[Distribution] | None = None) -> Any:
    """Turn a distribution shorthand into the full ``{"kind": ...}`` form.

    Handles the tagged form ``{uniform: [3, 8]}`` (any registered kind), a
    bare list (positional arguments of *cls*) and a scalar (degenerate
    distribution of *cls*, or a :class:`Constant` when *cls* is None).  Other
    input (distribution instances, dicts with ``kind`` or field names) is
    returned unchanged.

    :param value: raw input
    :param cls: the concrete target class, or None for "any kind"
    :return: the expanded value
    :raises ValueError: for a tagged shorthand of a different kind than *cls*
        or malformed positional values

    Example::

        expand_shorthand({"uniform": [3, 8]})   # {"kind": "uniform", "low": 3, "high": 8}
        expand_shorthand([0.2, 0.8], Uniform)   # {"kind": "uniform", "low": 0.2, "high": 0.8}
        expand_shorthand(0.5)                   # {"kind": "constant", "value": 0.5}
    """
    if isinstance(value, Distribution) or value is None:
        return value
    if isinstance(value, Mapping):
        if "kind" in value or len(value) != 1:
            return value
        (tag, args), *_ = value.items()
        target = _REGISTRY.get(tag) if isinstance(tag, str) else None
        if target is None:
            if cls is None:
                raise ValueError(
                    f"Cannot read {dict(value)!r} as a distribution: {tag!r} is not "
                    f"a distribution kind (known: {', '.join(sorted(_REGISTRY))}); "
                    "write e.g. {uniform: [0, 1]} or {kind: uniform, low: 0, high: 1}."
                )
            return value
        if cls is not None and not issubclass(target, cls):
            raise ValueError(
                f"Expected a {cls._kind()!r} distribution, got {tag!r}; this field "
                f"only accepts {cls.__name__}."
            )
        if isinstance(args, Mapping):
            fields = dict(args)
        elif isinstance(args, list | tuple):
            fields = target._from_list(args)
        else:
            fields = target._from_scalar(args)
            if fields is None:
                raise ValueError(
                    f"Invalid {tag} shorthand {dict(value)!r}: expected a list of "
                    f"positional values ({', '.join(target._positional)}) or a "
                    "mapping of fields."
                )
        return {"kind": tag, **fields}
    if isinstance(value, list | tuple):
        if cls is None:
            raise ValueError(
                f"A list {list(value)!r} does not say which distribution it is; "
                "write e.g. {uniform: [0.1, 0.9]} or {choice: [a, b]}."
            )
        return {"kind": cls._kind(), **cls._from_list(value)}
    if cls is None:
        return {"kind": "constant", "value": value}
    fields = cls._from_scalar(value)
    if fields is None:
        return value
    return {"kind": cls._kind(), **fields}


class Constant(Distribution):
    """A degenerate distribution: every draw returns ``value``.

    Example::

        Constant(5).draw(rng)            # 5
        Constant("greedy").draw(rng)     # "greedy"
    """

    _positional: ClassVar[tuple[str, ...]] = ("value",)
    kind: Literal["constant"] = "constant"
    value: Any

    @classmethod
    def _from_list(cls, items: Sequence) -> dict:
        return {"value": list(items)}

    @classmethod
    def _from_scalar(cls, value: Any) -> dict | None:
        return {"value": value}

    def draw(self, rng: Any, size: int | tuple[int, ...] | None = None) -> Any:
        _check_rng(rng)
        if size is None:
            return self.value
        return _array([self.value] * _count(size), size)

    @property
    def expected_value(self) -> Any:
        """``value``."""
        return self.value


class Uniform(Distribution):
    """Continuous uniform distribution on ``[low, high]``.

    Example::

        Uniform(0.2, 0.8).draw(rng)      # e.g. 0.53
    """

    _positional: ClassVar[tuple[str, ...]] = ("low", "high")
    kind: Literal["uniform"] = "uniform"
    low: float
    high: float

    @model_validator(mode="after")
    def _check(self) -> Uniform:
        if self.low > self.high:
            raise ValueError(f"uniform needs low <= high, got {self.low} > {self.high}")
        return self

    @classmethod
    def _from_scalar(cls, value: Any) -> dict | None:
        return {"low": value, "high": value} if _is_number(value) else None

    def _draw_numpy(self, rng: Any, size: Any) -> Any:
        if size is None:
            return float(rng.uniform(self.low, self.high))
        return rng.uniform(self.low, self.high, size)

    def _draw_stdlib(self, rng: Any) -> float:
        return rng.uniform(self.low, self.high)

    @property
    def expected_value(self) -> float:
        """``(low + high) / 2``."""
        return (self.low + self.high) / 2


class IntUniform(Distribution):
    """Discrete uniform distribution on the integers ``low..high`` (inclusive).

    Example::

        IntUniform(1, 6).draw(rng)       # a die roll
    """

    _positional: ClassVar[tuple[str, ...]] = ("low", "high")
    kind: Literal["int_uniform"] = "int_uniform"
    low: int
    high: int

    @model_validator(mode="after")
    def _check(self) -> IntUniform:
        if self.low > self.high:
            raise ValueError(
                f"int_uniform needs low <= high, got {self.low} > {self.high}"
            )
        return self

    @classmethod
    def _from_scalar(cls, value: Any) -> dict | None:
        return {"low": value, "high": value} if _is_number(value) else None

    def _draw_numpy(self, rng: Any, size: Any) -> Any:
        if size is None:
            return int(rng.integers(self.low, self.high, endpoint=True))
        return rng.integers(self.low, self.high, size, endpoint=True)

    def _draw_stdlib(self, rng: Any) -> int:
        return rng.randint(self.low, self.high)

    @property
    def expected_value(self) -> float:
        """``(low + high) / 2``."""
        return (self.low + self.high) / 2


def _phi(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


class Normal(Distribution):
    """Normal distribution, optionally clipped to ``[low, high]``.

    Draws outside the bounds are clipped (not re-drawn), so each draw
    consumes exactly one normal variate.

    Example::

        Normal(16.0, 1.5).draw(rng)                  # arrival hour
        Normal(0.04, 0.01, low=0.0).draw(rng)        # never negative
    """

    _positional: ClassVar[tuple[str, ...]] = ("mean", "std", "low", "high")
    kind: Literal["normal"] = "normal"
    mean: float
    std: float = Field(ge=0)
    low: float | None = None
    high: float | None = None

    @model_validator(mode="after")
    def _check(self) -> Normal:
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError(f"normal needs low <= high, got {self.low} > {self.high}")
        return self

    @classmethod
    def _from_scalar(cls, value: Any) -> dict | None:
        return {"mean": value, "std": 0.0} if _is_number(value) else None

    def _clip(self, value: Any) -> Any:
        if self.low is None and self.high is None:
            return value
        import numpy as np

        return np.clip(value, self.low, self.high)

    def _draw_numpy(self, rng: Any, size: Any) -> Any:
        if size is None:
            return float(self._clip(rng.normal(self.mean, self.std)))
        return self._clip(rng.normal(self.mean, self.std, size))

    def _draw_stdlib(self, rng: Any) -> float:
        return float(self._clip(rng.gauss(self.mean, self.std)))

    @property
    def expected_value(self) -> float:
        """The mean of the (clipped) distribution."""
        low = -math.inf if self.low is None else self.low
        high = math.inf if self.high is None else self.high
        if self.std == 0:
            return min(max(self.mean, low), high)
        a = (low - self.mean) / self.std
        b = (high - self.mean) / self.std
        value = self.mean * (_cdf(b) - _cdf(a)) - self.std * (_phi(b) - _phi(a))
        if self.low is not None:
            value += low * _cdf(a)
        if self.high is not None:
            value += high * (1.0 - _cdf(b))
        return value


class LogNormal(Distribution):
    """Log-normal distribution; ``mean`` and ``sigma`` belong to the underlying normal.

    Example::

        LogNormal(0.0, 0.5).draw(rng)        # positive, median 1.0
    """

    _positional: ClassVar[tuple[str, ...]] = ("mean", "sigma")
    kind: Literal["lognormal"] = "lognormal"
    mean: float
    sigma: float = Field(ge=0)

    @classmethod
    def _from_scalar(cls, value: Any) -> dict | None:
        if not _is_number(value) or value <= 0:
            return None
        return {"mean": math.log(value), "sigma": 0.0}

    def _draw_numpy(self, rng: Any, size: Any) -> Any:
        if size is None:
            return float(rng.lognormal(self.mean, self.sigma))
        return rng.lognormal(self.mean, self.sigma, size)

    def _draw_stdlib(self, rng: Any) -> float:
        return rng.lognormvariate(self.mean, self.sigma)

    @property
    def expected_value(self) -> float:
        """``exp(mean + sigma**2 / 2)``."""
        return math.exp(self.mean + self.sigma**2 / 2)


def _is_duration_text(value: Any) -> bool:
    """True for strings such as ``"1w"`` or ``"15min"`` (not ``"5"``, not ``"red"``)."""
    if not isinstance(value, str):
        return False
    from .timecfg import _PLAIN_NUMBER, parse_duration

    if _PLAIN_NUMBER.fullmatch(value):
        return False
    try:
        parse_duration(value)
    except ValueError:
        return False
    return True


def _check_no_durations(values: Sequence) -> None:
    """Refuse a choice that mixes numbers and duration strings (``[0, "1w"]``).

    Durations are converted to seconds only in delay fields; anywhere else
    such a choice would draw the strings ``"1w"`` and ``"0"`` (sized draws)
    and fail late inside the model.
    """
    durations = [v for v in values if _is_duration_text(v)]
    if durations and any(_is_number(v) for v in values):
        seconds = [_duration_seconds(v) if _is_duration_text(v) else v for v in values]
        raise ValueError(
            f"choice mixes numbers and duration strings ({list(values)!r}): "
            "durations are converted to seconds only in delay fields "
            "(comm.delay_s, scenario.link(delay=...)); write seconds here, "
            f"e.g. {seconds!r}"
        )


def _duration_seconds(value: str) -> float | int:
    from .timecfg import parse_duration

    seconds = parse_duration(value)
    return int(seconds) if float(seconds).is_integer() else seconds


class Choice(Distribution):
    """Pick one of ``values``, uniformly or with probabilities ``p``.

    Duration strings are not converted here: a choice that mixes numbers
    and durations (``[0, "1w"]``) is refused with the values in seconds.
    Only delay fields (``comm.delay_s``, ``scenario.link(delay=...)``)
    convert durations, there ``{choice: [0, "1w", "2w"]}`` is fine.

    Example::

        Choice(["greedy", "proportional"]).draw(rng)
        Choice([1, 2, 3], p=[0.5, 0.25, 0.25]).draw(rng)
    """

    _positional: ClassVar[tuple[str, ...]] = ("values", "p")
    kind: Literal["choice"] = "choice"
    values: tuple[Any, ...] = Field(min_length=1)
    p: tuple[float, ...] | None = None

    @model_validator(mode="after")
    def _check(self) -> Choice:
        _check_no_durations(self.values)
        if self.p is None:
            return self
        if len(self.p) != len(self.values):
            raise ValueError(
                f"choice needs one probability per value, got {len(self.p)} "
                f"probabilities for {len(self.values)} values"
            )
        if any(q < 0 for q in self.p) or not math.isclose(
            math.fsum(self.p), 1.0, rel_tol=0.0, abs_tol=1e-9
        ):
            raise ValueError(
                f"choice probabilities must be >= 0 and sum to 1, got {list(self.p)}"
            )
        return self

    @classmethod
    def _from_list(cls, items: Sequence) -> dict:
        return {"values": list(items)}

    @classmethod
    def _from_scalar(cls, value: Any) -> dict | None:
        return {"values": [value]}

    def _draw_numpy(self, rng: Any, size: Any) -> Any:
        index = rng.choice(len(self.values), size=size, p=self.p)
        if size is None:
            return self.values[int(index)]
        return _array([self.values[int(i)] for i in index.ravel()], size)

    def _draw_stdlib(self, rng: Any) -> Any:
        if self.p is None:
            return rng.choice(self.values)
        return rng.choices(self.values, weights=self.p)[0]

    @property
    def expected_value(self) -> float:
        """The (weighted) mean of numeric ``values``."""
        if not all(_is_number(v) for v in self.values):
            raise TypeError(
                f"choice over non-numeric values has no expected value: "
                f"{list(self.values)!r}"
            )
        weights = self.p or [1.0 / len(self.values)] * len(self.values)
        return math.fsum(v * q for v, q in zip(self.values, weights))


class Bernoulli(Distribution):
    """``True`` with probability ``p``, else ``False``.

    For a field typed ``Bernoulli`` a plain number is read as ``p``.

    Example::

        Bernoulli(0.6).draw(rng)         # has PV?
    """

    _positional: ClassVar[tuple[str, ...]] = ("p",)
    kind: Literal["bernoulli"] = "bernoulli"
    p: float = Field(ge=0, le=1)

    @classmethod
    def _from_scalar(cls, value: Any) -> dict | None:
        if isinstance(value, bool):
            return {"p": 1.0 if value else 0.0}
        return {"p": value} if _is_number(value) else None

    def _draw_numpy(self, rng: Any, size: Any) -> Any:
        if size is None:
            return bool(rng.random() < self.p)
        return rng.random(size) < self.p

    def _draw_stdlib(self, rng: Any) -> bool:
        return rng.random() < self.p

    @property
    def expected_value(self) -> float:
        """``p``."""
        return self.p


class FromColumn(Distribution):
    """Per-entity values from a column of a CSV or Parquet table.

    ``mode="index"`` gives entity *i* row ``i``, so a table of 30 households
    parameterises 30 entities one to one; ``mode="sample"`` draws a uniformly
    random row from the given stream.  When a group is larger than the table
    (index >= rows), ``wrap`` decides: ``None`` (default) reuses rows modulo
    the table length and warns once per table and column (each row usually
    is a distinct unit, e.g. a survey respondent), ``True`` reuses them
    silently, ``False`` raises an error.
    The table is read once per process; relative paths resolve like profile
    paths (see :mod:`mango.scenario.profiles`).

    In index mode pass the entity index: ``dist.draw(e.rng, index=e.index)``,
    or use ``e.draw(dist)``, which does that for every distribution.

    In sample mode ``e.draw(dist)`` draws the entity's row once per table
    (from its own sub-stream, not from ``e.rng``), so all columns of one
    table come from the same row: ``e.draw(e.p.opinion)`` and
    ``e.draw(e.p.age)`` describe the same respondent.  ``e.row(dist)``
    returns that row (to record a respondent id).  ``dist.draw(e.rng)``
    called directly draws an independent row per call.

    Example::

        class HouseholdParams(BaseModel, extra="forbid"):
            annual_kwh: Dist = FromColumn("data/households.csv", "annual_kwh")

        annual = e.draw(e.p.households.annual_kwh)   # row e.index
        FromColumn("data/households.csv", "annual_kwh", "sample").draw(e.rng)
        FromColumn("data/typical_days.csv", "kwh", wrap=True)   # rows repeat on purpose
    """

    _positional: ClassVar[tuple[str, ...]] = ("path", "column", "mode")
    kind: Literal["from_column"] = "from_column"
    path: str = Field(min_length=1)
    column: str = Field(min_length=1)
    mode: Literal["index", "sample"] = "index"
    wrap: bool | None = None

    @model_serializer(mode="wrap")
    def _omit_default_wrap(self, handler: Any) -> Any:
        data = handler(self)
        if isinstance(data, dict) and data.get("wrap", False) is None:
            data.pop("wrap")
        return data

    def _check_rows(self, last: int, n: int) -> None:
        """Apply ``wrap`` when index mode reads row *last* of an *n*-row table."""
        if last < n or self.wrap is True:
            return
        what = f"FromColumn({self.path!r}, {self.column!r})"
        if self.wrap is False:
            raise ValueError(
                f"{what} has {n} rows, but entity index {last} needs row {last}: the "
                "group is larger than the table. Use a table with a row per entity, "
                "set wrap=True to reuse rows modulo the table length, or "
                "mode='sample'."
            )
        message = (
            f"{what} has {n} rows, but the group is larger (entity index {last}): "
            f"rows are reused modulo {n}, so several entities share a row. Set "
            "wrap=True (wrap: true) if that is intended, wrap=False to make it an "
            "error, or use mode='sample'."
        )
        key = (self.path, self.column)
        report = _ROW_REUSE_REPORT.get()
        if report is not None:
            report(key, message)
        elif key not in _WRAP_WARNED:
            _WRAP_WARNED.add(key)
            import warnings

            warnings.warn(message, UserWarning, stacklevel=4)

    @model_validator(mode="before")
    @classmethod
    def _path_text(cls, data: Any) -> Any:
        import os
        from pathlib import Path

        if isinstance(data, Mapping) and isinstance(data.get("path"), os.PathLike):
            data = {**data, "path": Path(data["path"]).as_posix()}
        return data

    def column_values(self) -> Any:
        """The column as a read-only numpy array (``float64`` when numeric).

        Example::

            FromColumn("data/households.csv", "annual_kwh").column_values().mean()
        """
        from .profiles import read_column

        return read_column(self.path, self.column)

    def draw(
        self,
        rng: Any,
        size: int | tuple[int, ...] | None = None,
        *,
        index: int | None = None,
    ) -> Any:
        """Return the value of row *index* (index mode) or of a random row.

        :param rng: the stream for sample mode (checked in both modes)
        :param size: ``None`` for one value; an int or shape for an array (in
            index mode the rows ``index, index + 1, ...``)
        :param index: the entity index (required in index mode)
        :raises ValueError: in index mode without *index*

        Example::

            dist.draw(e.rng, index=e.index)
        """
        _check_rng(rng)
        values = self.column_values()
        n = len(values)
        if self.mode == "index":
            if index is None:
                raise ValueError(
                    f"FromColumn({self.path!r}, {self.column!r}) in index mode needs "
                    "the entity index: dist.draw(e.rng, index=e.index), or "
                    "e.draw(dist)."
                )
            if isinstance(index, bool) or not isinstance(index, numbers.Integral):
                raise TypeError(f"index must be an int, got {index!r}")
            if size is None:
                self._check_rows(int(index), n)
                return _scalar(values[int(index) % n])
            import numpy as np

            self._check_rows(int(index) + _count(size) - 1, n)
            rows = (int(index) + np.arange(_count(size))) % n
            return values[rows].reshape(size if isinstance(size, tuple) else (size,))
        if _uses_numpy_api(rng):
            if size is None:
                return _scalar(values[int(rng.integers(n))])
            rows = rng.integers(0, n, size)
            return values[rows]
        return _stdlib_draws(lambda: _scalar(values[rng.randrange(n)]), size)

    def values_at(self, rows: Any) -> Any:
        """The column's value at row *rows* (an int) or at an array of rows.

        Example::

            dist.values_at(e.row(dist))      # what e.draw(dist) returns
        """
        values = self.column_values()
        if isinstance(rows, numbers.Integral):
            return _scalar(values[int(rows)])
        return values[rows]

    @property
    def expected_value(self) -> float:
        """The mean of the column (numeric columns only)."""
        values = self.column_values()
        if values.dtype == object:
            raise TypeError(
                f"Column {self.column!r} of {self.path!r} is not numeric and has no "
                "expected value."
            )
        return float(values.mean())


#: ``(path, column)`` of FromColumn tables that already warned about reused rows
#: outside a scenario run (a run reports through :data:`_ROW_REUSE_REPORT`)
_WRAP_WARNED: set[tuple[str, str]] = set()

#: ``report(key, message)`` of the scenario run building entities in this
#: context: the run warns once per table and column and keeps the warning
#: with its result (``RunResult.warnings``, the run manifest)
_ROW_REUSE_REPORT: ContextVar[Callable[[tuple, str], None] | None] = ContextVar(
    "mango_scenario_row_reuse_report", default=None
)


@contextmanager
def reporting_row_reuse(report: Callable[[tuple, str], None]) -> Iterator[None]:
    """Send the row-reuse warnings of index-mode FromColumn draws to *report*.

    Scenario runs use it while they build entities, so every run warns (once
    per table and column) and keeps the warning, instead of once per process.

    :param report: ``report(key, message)``; *key* is ``(path, column)``

    Example::

        with reporting_row_reuse(lambda key, message: print(message)):
            dist.draw(rng, index=200)
    """
    token = _ROW_REUSE_REPORT.set(report)
    try:
        yield
    finally:
        _ROW_REUSE_REPORT.reset(token)


def _scalar(value: Any) -> Any:
    return value.item() if hasattr(value, "item") else value


def _dist_schema(source: Any, handler: Any) -> Any:
    members = tuple(_REGISTRY.values())
    union = Annotated[Union[members], Field(discriminator="kind")]  # noqa: UP007
    return handler.generate_schema(union)


#: Field type accepting any registered distribution (full form or tagged
#: shorthand such as ``{normal: [40, 8]}``); a plain scalar becomes a
#: :class:`Constant`.  Defaults are not validated by pydantic, so write them as
#: distributions: ``delay_s: Dist = Constant(0.0)``.
Dist = Annotated[
    Distribution,
    GetPydanticSchema(_dist_schema),
    BeforeValidator(expand_shorthand),
]
