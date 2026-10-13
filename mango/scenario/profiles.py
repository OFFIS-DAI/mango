"""
Named time series profiles aligned to the scenario calendar.

A profile (a household load curve, a PV feed-in shape, a price series) is
declared once in the parameters under ``profiles`` and read by agents
through a :class:`Profile` accessor, so an agent asks for "the load now"
instead of computing it from ``clock.time``:

.. code-block:: yaml

    # presets/base.yaml
    time: {start: 2024-06-01T00:00, horizon: 2d, step: 15min}
    profiles:
      h0: {source: csv, path: data/profiles.csv, column: h0, resolution: 15min}
      pv: {source: csv, path: data/profiles.csv, column: pv, resolution: 15min, noise: 0.05}

Inside a scenario, ``ctx.profile("h0")`` returns the profile and
``e.profile("pv")`` the entity's own variant (column chosen by the entity
index, multiplicative noise from the entity's random stream).

Files are read once per process (cached by absolute path, modification time
and columns), so every run and every entity of a worker shares one copy.

**Alignment.**  A profile whose ``time_column`` holds datetimes is *dated*:
it is placed at its own dates and, unless ``wrap: true`` is set, reading
outside them is an error (a July run on January data fails instead of
silently reusing January).  Other profiles repeat periodically (``wrap``
defaults to true) and, without ``start``, are anchored at ``time.start``:
moving ``time.start`` earlier for a warm-up moves them too, so pin
``start:`` to keep them fixed in calendar time (``plan()`` warns when a
warm-up phase meets an unpinned profile).

**Relative paths** stay relative in the parameters, so run ids do not depend
on where the project folder lives.  They are resolved

1. against the project root: the working directory, and in study workers
   also the study's ``project_root`` (the working directory of the process
   that planned the study);
2. when a preset file set the path and the file is not found there, against
   the folder of that preset: :func:`~mango.scenario.load_params` then
   rewrites the path relative to the working directory (e.g.
   ``../data/x.csv`` in ``presets/base.yaml`` becomes ``data/x.csv``), so
   workers and replays find it without knowing the preset.

Example::

    from mango.scenario import ProfileSpec, load_profile

    spec = ProfileSpec(source="inline", values=[0.2, 0.5, 0.9, 0.4], resolution="6h")
    load = load_profile(spec, name="load")
    load.at(7 * 3600)     # 0.5 (step interpolation)
    load(clock)           # the value at the clock's current time
"""

from __future__ import annotations

import bisect
import datetime as _dt
import math
import numbers
import os
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

from ._util import data_path_candidates, did_you_mean
from .errors import ScenarioError
from .timecfg import (
    _EPS,
    Duration,
    _check_time_zone,
    _coerce_start,
    _dump_datetime,
    _parse_datetime,
    _tolerance,
    _utc_delta,
    parse_duration,
)

if TYPE_CHECKING:
    import numpy

__all__ = [
    "ProfileSpec",
    "Profile",
    "ProfileName",
    "load_profile",
    "read_column",
    "clear_profile_cache",
]

_PARQUET_SUFFIXES = (".parquet", ".pq")


def _check_profile_name(name: str) -> str:
    if not name or "." in name or "/" in name:
        raise ValueError(
            f"invalid profile name {name!r}: use a non-empty name without '.' or "
            "'/' (names appear in parameter paths such as 'profiles.h0.scale' and "
            "in random stream names)"
        )
    return name


#: Key type of ``ScenarioParams.profiles``: a non-empty name without ``.``/``/``.
ProfileName = Annotated[str, AfterValidator(_check_profile_name)]


# --------------------------------------------------------------------------- spec


def _source_is_optional(schema: dict) -> None:
    """``source`` is inferred when omitted, so presets may leave it out."""
    required = [name for name in schema.get("required", []) if name != "source"]
    if required:
        schema["required"] = required
    else:
        schema.pop("required", None)


class ProfileSpec(BaseModel, extra="forbid", json_schema_extra=_source_is_optional):
    """Where a profile comes from and how it maps onto the scenario calendar.

    :param source: ``"csv"``, ``"parquet"`` or ``"inline"``; inferred when
        omitted (``values`` → inline, the path suffix otherwise)
    :param path: the table file (csv/parquet); relative paths are resolved as
        described in :mod:`mango.scenario.profiles`
    :param column: the value column, or a list of columns (entities pick one
        by index); default: every column except ``time_column``
    :param values: the samples of an inline profile
    :param time_column: a column with the sample times (datetimes, or numbers
        = seconds since ``start``); without it samples are ``resolution`` apart
    :param start: calendar time of the first sample (default: the scenario's
        ``time.start``, so the profile moves with it); not used with a
        datetime ``time_column``
    :param resolution: spacing of the samples, e.g. ``"15min"`` (required
        without ``time_column``; with it, the spacing of the last sample used
        for wrapping, default: the median spacing)
    :param scale: factor applied to every value
    :param noise: relative standard deviation of multiplicative Gaussian noise
        drawn per entity and sample (``e.profile``; ``ctx.profile`` is
        noise-free)
    :param interpolation: ``"step"`` (hold the sample) or ``"linear"``
    :param wrap: repeat the profile periodically; otherwise reading outside
        it raises an error.  Default (``None``): no wrapping for a dated
        profile (a datetime ``time_column``), wrapping for all others

    Example::

        ProfileSpec(source="csv", path="data/profiles.csv", column="h0", resolution="15min")
        ProfileSpec(source="inline", values=[1, 2, 3], resolution="1h", scale=2.0)
    """

    source: Literal["csv", "parquet", "inline"]
    path: str | None = None
    column: str | list[str] | None = None
    values: list[float] | None = Field(None, min_length=1)
    time_column: str | None = None
    start: _dt.datetime | None = None
    resolution: Duration | None = None
    scale: float = Field(1.0, allow_inf_nan=False)
    noise: float = Field(0.0, ge=0.0, allow_inf_nan=False)
    interpolation: Literal["step", "linear"] = "step"
    wrap: bool | None = None

    @model_serializer(mode="wrap")
    def _omit_default_wrap(self, handler: Any) -> Any:
        data = handler(self)
        if isinstance(data, dict) and data.get("wrap", False) is None:
            data.pop("wrap")
        return data

    @model_validator(mode="before")
    @classmethod
    def _infer_source(cls, data: Any) -> Any:
        if isinstance(data, Mapping) and "source" not in data:
            data = dict(data)
            if data.get("values") is not None:
                data["source"] = "inline"
            elif isinstance(data.get("path"), str | os.PathLike):
                suffix = Path(data["path"]).suffix.lower()
                data["source"] = "parquet" if suffix in _PARQUET_SUFFIXES else "csv"
        return data

    @field_validator("path", mode="before")
    @classmethod
    def _path_text(cls, value: Any) -> Any:
        if isinstance(value, os.PathLike):
            return Path(value).as_posix()
        return value

    @field_validator("values")
    @classmethod
    def _finite_values(cls, value: list[float] | None) -> list[float] | None:
        if value is not None:
            for index, item in enumerate(value):
                if not math.isfinite(item):
                    raise ValueError(
                        f"values[{index}] is {item}; values must be finite"
                    )
        return value

    @field_validator("column")
    @classmethod
    def _check_columns(cls, value: Any) -> Any:
        if isinstance(value, list):
            if not value:
                raise ValueError("column must not be an empty list")
            if len(set(value)) != len(value):
                raise ValueError(f"column lists a column twice: {value}")
        return value

    @field_validator("start", mode="before")
    @classmethod
    def _parse_start(cls, value: Any) -> Any:
        return _coerce_start(value)

    @field_validator("start")
    @classmethod
    def _check_start_zone(cls, value: _dt.datetime | None) -> _dt.datetime | None:
        return None if value is None else _check_time_zone(value, "profile start")

    @field_serializer("start", when_used="json")
    def _dump_start(self, value: _dt.datetime | None) -> str | None:
        return _dump_datetime(value)

    @model_validator(mode="after")
    def _check_source(self) -> ProfileSpec:
        if self.source == "inline":
            if self.values is None:
                raise ValueError("an inline profile needs values: [...]")
            for name in ("path", "column", "time_column"):
                if getattr(self, name) is not None:
                    raise ValueError(
                        f"an inline profile takes values, not {name} (a preset that "
                        f"switches a profile to inline can delete it with '-{name}')"
                    )
        else:
            if self.path is None or not str(self.path).strip():
                raise ValueError(f"a {self.source} profile needs path: <file>")
            if self.values is not None:
                raise ValueError(
                    f"a {self.source} profile reads its values from path; drop values "
                    "or use source: inline (a preset that switches a profile to a file "
                    "can delete them with '-values')"
                )
            columns = [self.column] if isinstance(self.column, str) else self.column
            if self.time_column is not None and columns and self.time_column in columns:
                raise ValueError(
                    f"time_column {self.time_column!r} is also listed as a value column"
                )
        if self.time_column is None and self.resolution is None:
            raise ValueError(
                "resolution is required without time_column, e.g. resolution: 15min"
            )
        if self.resolution is not None and self.resolution <= 0:
            raise ValueError("resolution must be > 0")
        return self

    @property
    def columns(self) -> tuple[str, ...] | None:
        """The configured value columns (``None``: all non-time columns)."""
        if self.column is None:
            return None
        return (self.column,) if isinstance(self.column, str) else tuple(self.column)


# --------------------------------------------------------------------------- files


def resolve_data_path(path: str | os.PathLike, *, what: str = "data file") -> Path:
    """Return the absolute file a (relative) data path refers to.

    Relative paths are tried against the working directory, then the study's
    project root (in study workers).

    :param path: the path as written in the parameters
    :param what: description for the error message, e.g. ``"profile 'h0'"``
    :return: the resolved absolute path
    :raises ScenarioError: if no candidate exists (listing the tried paths)

    Example::

        resolve_data_path("data/profiles.csv", what="profile 'h0'")
    """
    tried = data_path_candidates(os.fspath(path))
    for option in tried:
        if option.is_file():
            return option.resolve()
    listing = ", ".join(str(option) for option in dict.fromkeys(tried))
    raise ScenarioError(
        f"{what}: file {os.fspath(path)!r} not found (tried {listing}). Relative "
        "paths are resolved against the project root (the working directory, or "
        "the study's project_root in workers) and, when a preset set them, the "
        "preset's folder; run from the project root or give an absolute path."
    )


def _rebase(path: str, preset: Path) -> str | None:
    """The path relative to cwd of *path* read relative to *preset*'s folder, or None."""
    candidate = Path(path).expanduser()
    if candidate.is_absolute() or (Path.cwd() / candidate).is_file():
        return None
    target = (preset.parent / candidate).resolve()
    if not target.is_file():
        return None
    try:
        return Path(os.path.relpath(target, Path.cwd())).as_posix()
    except ValueError:  # another drive on Windows
        return target.as_posix()


def rebase_preset_paths(
    merged: dict, origins: Mapping[str, str], files: Mapping[str, Path]
) -> None:
    """Rewrite preset-relative data paths relative to the working directory (in place).

    Visits ``profiles.<name>.path`` and the ``path`` of every ``from_column``
    distribution.  A relative path that was set by a preset file (according
    to *origins*), does not exist relative to the working directory but
    exists relative to the preset's folder is replaced by its path relative
    to the working directory; everything else is left unchanged.

    :param merged: the merged parameter data
    :param origins: ``{dotted path: layer label}``
    :param files: ``{layer label: preset file}`` of the file layers
    """
    if not files:
        return

    def origin(path: str) -> str | None:
        while path:
            if path in origins:
                return origins[path]
            path = path.rpartition(".")[0]
        return None

    def visit(node: Any, dotted: str, under_profiles: bool) -> None:
        if isinstance(node, dict):
            value = node.get("path")
            if isinstance(value, str) and (
                under_profiles or node.get("kind") == "from_column"
            ):
                preset = files.get(origin(f"{dotted}.path") or "")
                if preset is not None:
                    rebased = _rebase(value, preset)
                    if rebased is not None:
                        node["path"] = rebased
            for key, child in node.items():
                child_path = f"{dotted}.{key}" if dotted else str(key)
                if dotted == "" and key == "profiles" and isinstance(child, dict):
                    for name, spec in child.items():
                        visit(spec, f"profiles.{name}", True)
                else:
                    visit(child, child_path, False)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, f"{dotted}.{index}", False)

    visit(merged, "", False)


@dataclass(frozen=True)
class _Table:
    """A read-only table of float value columns (plus sample times)."""

    columns: tuple[str, ...]
    data: Any  # numpy array (rows x columns), float64, read-only
    times: Any  # numpy float64 seconds since origin / since 0, or None
    origin: _dt.datetime | None
    path: str


_CACHE: dict[tuple, Any] = {}
_CACHE_LOCK = threading.Lock()


def clear_profile_cache() -> None:
    """Forget every table read by profiles and :class:`~mango.scenario.FromColumn`.

    Files are cached per process for the lifetime of the process; call this
    to free the memory or to force a re-read.

    Example::

        clear_profile_cache()
    """
    with _CACHE_LOCK:
        _CACHE.clear()


def _cached(key: tuple, load):
    with _CACHE_LOCK:
        found = _CACHE.get(key)
    if found is not None:
        return found
    value = load()
    with _CACHE_LOCK:
        return _CACHE.setdefault(key, value)


def _file_key(path: Path) -> tuple:
    stat = path.stat()
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _require_pyarrow(path: Path) -> None:
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        raise ImportError(
            f"Reading the Parquet file {str(path)!r} needs pyarrow: pip install "
            "'mango-agents[scenario-all]' (or pyarrow)."
        ) from None


def _read_frame(source: str, path: Path, columns: list[str] | None):
    """Read *columns* (None: all) of a csv/parquet file into a DataFrame."""
    import pandas as pd

    if source == "parquet":
        _require_pyarrow(path)
        import pyarrow.parquet as pq

        available = list(pq.read_schema(path).names)
        _check_available(path, columns, available)
        return pd.read_parquet(path, columns=columns, engine="pyarrow")
    header = list(pd.read_csv(path, nrows=0).columns)
    _check_available(path, columns, header)
    return pd.read_csv(path, usecols=columns)


def _check_available(path: Path, columns: list[str] | None, available: list) -> None:
    for column in columns or ():
        if column not in available:
            hint = did_you_mean(column, [str(c) for c in available])
            raise ScenarioError(
                f"{str(path)!r} has no column {column!r}.{hint} Columns: "
                f"{', '.join(map(str, available))}."
            )


def _read_table(
    source: str, path: Path, columns: tuple[str, ...] | None, time_column: str | None
) -> _Table:
    """Read a profile table (the uncached reader)."""
    import numpy as np
    import pandas as pd

    wanted = None
    if columns is not None:
        wanted = list(columns) + ([time_column] if time_column else [])
    frame = _read_frame(source, path, wanted)
    if time_column is not None and time_column not in frame.columns:
        _check_available(path, [time_column], list(frame.columns))
    names = (
        list(columns)
        if columns is not None
        else [str(c) for c in frame.columns if c != time_column]
    )
    if not names:
        raise ScenarioError(f"{str(path)!r} has no value columns.")
    if len(frame) == 0:
        raise ScenarioError(f"{str(path)!r} has no rows.")
    blocks = []
    for name in names:
        series = frame[name]
        try:
            values = pd.to_numeric(series, errors="raise").to_numpy(dtype=float)
        except (ValueError, TypeError):
            raise ScenarioError(
                f"Column {name!r} of {str(path)!r} is not numeric (e.g. "
                f"{series.iloc[0]!r}); profiles need numbers."
            ) from None
        bad = np.flatnonzero(~np.isfinite(values))
        if bad.size:
            raise ScenarioError(
                f"Column {name!r} of {str(path)!r} has a missing or non-finite value "
                f"in data row {int(bad[0])} (0-based)."
            )
        blocks.append(values)
    data = np.column_stack(blocks)
    data.flags.writeable = False
    times, origin = None, None
    if time_column is not None:
        times, origin = _parse_times(frame[time_column], path, time_column)
    return _Table(tuple(names), data, times, origin, str(path))


def _parse_times(series, path: Path, column: str):
    """Seconds since the first sample (wall-clock seconds for naive datetimes)."""
    import numpy as np
    import pandas as pd

    what = f"time_column {column!r} of {str(path)!r}"
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        times = series.to_numpy(dtype=float)
        origin = None
    else:
        try:
            stamps = pd.DatetimeIndex(pd.to_datetime(series))
        except (ValueError, TypeError) as exc:
            try:  # offsets that change, e.g. +01:00 / +02:00 across a DST change
                stamps = pd.DatetimeIndex(pd.to_datetime(series, utc=True))
            except (ValueError, TypeError):
                raise ScenarioError(
                    f"{what} holds neither numbers (seconds) nor datetimes: {exc}"
                ) from None
        origin = stamps[0].to_pydatetime()
        times = np.asarray((stamps - stamps[0]).total_seconds(), dtype=float)
    if not np.all(np.isfinite(times)):
        raise ScenarioError(f"{what} has missing values.")
    if origin is None or origin.tzinfo is not None:
        _check_increasing(times, what, naive=False)
    times.flags.writeable = False
    return times, origin


def _check_increasing(times: Any, what: str, *, naive: bool) -> None:
    import numpy as np

    if times.size > 1 and not np.all(np.diff(times) > 0):
        row = int(np.flatnonzero(np.diff(times) <= 0)[0]) + 1
        hint = (
            " Naive local times repeat an hour when clocks go back; use UTC or "
            "offset-qualified timestamps (2024-10-27T02:00+02:00), or give "
            "time.start a time zone so the local times can be placed."
            if naive
            else ""
        )
        raise ScenarioError(
            f"{what} must be strictly increasing (data row {row} is not after the "
            f"previous one).{hint}"
        )


def _dst_message(wall: Any, tzinfo: _dt.tzinfo, what: str) -> str:
    """Explain which naive local time could not be placed in *tzinfo*.

    Written by mango (not pandas' text, whose hints name ``tz_localize``
    arguments that profiles do not have).
    """
    fix = (
        "use UTC or offset-qualified timestamps (such as 2024-06-01T12:00+02:00), "
        "or remove the row"
    )
    for stamp in wall:
        naive = stamp.to_pydatetime()
        early = naive.replace(tzinfo=tzinfo, fold=0)
        late = naive.replace(tzinfo=tzinfo, fold=1)
        if early.utcoffset() == late.utcoffset():
            continue
        shown = naive.isoformat(sep=" ", timespec="minutes")
        back = early.astimezone(_dt.timezone.utc).astimezone(tzinfo)
        if back.replace(tzinfo=None) != naive:
            return (
                f"{what} contains {shown}, which does not exist in {tzinfo} "
                f"(clocks jump forward at that hour); {fix}."
            )
        first = early.isoformat(timespec="minutes")
        second = late.isoformat(timespec="minutes")
        return (
            f"{what} contains {shown}, which occurs twice in {tzinfo} (clocks go "
            "back at that hour) and the order of the rows does not tell which of "
            f"the two is meant; write it with its offset ({first} or {second}) "
            "or use UTC timestamps."
        )
    return (
        f"{what} holds local times that cannot be placed in the scenario's time "
        f"zone {tzinfo} around a daylight-saving change; {fix}."
    )


def _localize_times(
    origin: _dt.datetime, times: Any, tzinfo: _dt.tzinfo, what: str
) -> tuple[_dt.datetime, Any]:
    """Place naive wall-clock samples in *tzinfo*; return the aware origin and elapsed seconds."""
    import numpy as np
    import pandas as pd

    wall = pd.Timestamp(origin) + pd.to_timedelta(np.asarray(times), unit="s")
    try:
        local = pd.DatetimeIndex(wall).tz_localize(
            tzinfo, ambiguous="infer", nonexistent="raise"
        )
    except Exception:  # pandas' NonExistentTimeError/AmbiguousTimeError
        raise ScenarioError(_dst_message(wall, tzinfo, what)) from None
    elapsed = np.asarray((local - local[0]).total_seconds(), dtype=float)
    _check_increasing(elapsed, what, naive=True)
    elapsed.flags.writeable = False
    return local[0].to_pydatetime(), elapsed


def _table(spec: ProfileSpec, name: str) -> _Table:
    import numpy as np

    if spec.source == "inline":
        data = np.asarray(spec.values, dtype=float).reshape(-1, 1)
        data.flags.writeable = False
        return _Table((name,), data, None, None, "<inline>")
    path = resolve_data_path(spec.path, what=f"profile {name!r}")
    key = (
        "table",
        spec.source,
        *_file_key(path),
        spec.columns,
        spec.time_column,
    )
    return _cached(
        key, lambda: _read_table(spec.source, path, spec.columns, spec.time_column)
    )


def read_column(path: str | os.PathLike, column: str) -> numpy.ndarray:
    """Return one column of a csv/parquet table (cached per process).

    Numeric columns come back as ``float64``, others as an object array.
    Relative paths resolve like profile paths.

    :param path: the table (``.parquet``/``.pq`` is read as Parquet, anything
        else as CSV)
    :param column: the column name
    :return: the read-only values
    :raises ScenarioError: for missing files or columns

    Example::

        read_column("data/households.csv", "annual_kwh")[:3]
    """
    import pandas as pd

    resolved = resolve_data_path(path, what=f"column {column!r}")
    source = "parquet" if resolved.suffix.lower() in _PARQUET_SUFFIXES else "csv"

    def load():
        series = _read_frame(source, resolved, [column])[column]
        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(
            series
        ):
            values = series.to_numpy(dtype=float)
        else:
            values = series.to_numpy(dtype=object)
        if len(values) == 0:
            raise ScenarioError(f"{str(resolved)!r} has no rows.")
        values.flags.writeable = False
        return values

    return _cached(("column", source, *_file_key(resolved), column), load)


# --------------------------------------------------------------------------- accessor


def _is_real(value: Any) -> bool:
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def _delta(later: _dt.datetime, earlier: _dt.datetime) -> float:
    """Elapsed seconds; a naive side is read as wall time of the other side's zone."""
    if (later.tzinfo is None) != (earlier.tzinfo is None):
        if later.tzinfo is None:
            later = later.replace(tzinfo=earlier.tzinfo)
        else:
            later = later.replace(tzinfo=None)
    return _utc_delta(later, earlier)


class Profile:
    """Read access to one profile column on the scenario's time axis.

    Created by ``ctx.profile(name)``, ``e.profile(name)`` or
    :func:`load_profile`.  Positions are mapped onto the samples by elapsed
    seconds since the profile's first sample: ``index = floor((t - start) /
    resolution)`` (wrapping around the end when ``wrap`` is true), with step
    or linear interpolation.

    Attributes: ``name``, ``column``, ``values`` (read-only numpy array,
    scaled and with the entity's noise), ``resolution_s``, ``period_s``
    (length covered before wrapping) and ``start`` (calendar time of the
    first sample, or ``None``).

    Example::

        class Household(Agent):
            def __init__(self, load_profile):
                super().__init__()
                self.load_profile = load_profile

            def on_step(self, env, clock, step_size_s):
                self.load_kw = self.load_profile.at(clock)
    """

    __slots__ = (
        "name",
        "column",
        "values",
        "resolution_s",
        "period_s",
        "start",
        "interpolation",
        "wrap",
        "_dated",
        "_regular",
        "_shift",
        "_values_list",
        "_offsets_list",
    )

    def __init__(
        self,
        *,
        name: str,
        column: str,
        values: Any,
        resolution_s: float,
        offsets: Any = None,
        start: _dt.datetime | None = None,
        scenario_start: _dt.datetime | None = None,
        interpolation: str = "step",
        wrap: bool = True,
        dated: bool = False,
    ):
        import numpy as np

        values = np.array(values, dtype=float)
        values.flags.writeable = False
        if values.ndim != 1 or values.size == 0:
            raise ScenarioError(f"Profile {name!r} needs a non-empty 1-d series.")
        if not resolution_s > 0:
            raise ScenarioError(f"Profile {name!r}: resolution must be > 0.")
        self.name = name
        self.column = column
        self.values = values
        self.resolution_s = float(resolution_s)
        self.start = start
        self.interpolation = interpolation
        self.wrap = wrap
        self._dated = dated
        if (
            start is not None
            and start.tzinfo is None
            and getattr(scenario_start, "tzinfo", None) is not None
        ):  # a naive start is wall time of the scenario's zone
            start = start.replace(tzinfo=scenario_start.tzinfo)
            self.start = start
        self._regular = offsets is None
        if offsets is None:
            offsets = np.arange(values.size, dtype=float) * self.resolution_s
        offsets = np.asarray(offsets, dtype=float)
        if offsets.shape != values.shape:
            raise ScenarioError(
                f"Profile {name!r}: {offsets.size} times for {values.size} values."
            )
        self.period_s = float(offsets[-1] - offsets[0]) + self.resolution_s
        self._shift = (
            _delta(scenario_start, start)
            if start is not None and scenario_start is not None
            else 0.0
        )
        self._values_list = values.tolist()
        self._offsets_list = offsets.tolist()

    def __repr__(self) -> str:
        anchor = f", start={self.start.isoformat()}" if self.start else ""
        return (
            f"<Profile {self.name!r} column={self.column!r} n={self.values.size} "
            f"resolution={self.resolution_s:g}s{anchor}>"
        )

    def __len__(self) -> int:
        return int(self.values.size)

    # -- positions ---------------------------------------------------------------

    def _position(self, t: Any) -> float:
        """Seconds on the profile's own axis (0 = ``start``) for a time *t*."""
        if _is_real(t):
            return float(t) + self._shift
        if isinstance(t, _dt.datetime):
            if self.start is None:
                raise ScenarioError(
                    f"Profile {self.name!r} has no calendar start, so it cannot be "
                    f"read at {t.isoformat()}; set time.start (or profiles."
                    f"{self.name}.start) or read it with seconds since the start."
                )
            return _delta(t, self.start)
        if isinstance(t, str):
            parsed = _parse_datetime(t)
            if parsed is not None:
                return self._position(parsed)
            return self._position(parse_duration(t))
        if hasattr(t, "time"):
            if self.start is not None and getattr(t, "has_calendar", False):
                return self._position(t.datetime)
            return self._position(float(t.time))
        raise TypeError(
            f"Profile.at() takes a clock, seconds since the scenario start or a "
            f"datetime, got {type(t).__name__}."
        )

    def _lookup(self, x: float) -> float:
        offsets = self._offsets_list
        values = self._values_list
        first = offsets[0]
        rel = x - first
        if self.wrap:
            rel = math.fmod(rel, self.period_s)
            if rel < 0:
                rel += self.period_s
            if rel >= self.period_s - _tolerance(self.period_s):
                rel = 0.0
        elif rel < -_tolerance(rel) or rel > self.period_s + _tolerance(self.period_s):
            end = first + self.period_s
            if self.start is not None:
                # offsets are elapsed seconds since the scenario start
                covers = (
                    f"covers {_shift(self.start, first).isoformat()} to "
                    f"{_shift(self.start, end).isoformat()}"
                )
                reads = _shift(self.start, x).isoformat()
            else:
                covers, reads = f"covers {first:g} s to {end:g} s", f"{x:g} s"
            why = (
                " (a profile dated by its time_column does not wrap by default)"
                if self._dated
                else ""
            )
            raise ScenarioError(
                f"Profile {self.name!r} {covers} and does not wrap{why}; the run reads "
                f"it at {reads}, outside its data. Use data covering the run, align "
                f"time.start, or set profiles.{self.name}.wrap: true to repeat it "
                "periodically."
            )
        rel = max(rel, 0.0)
        position = first + rel
        if self._regular:
            index = int(math.floor(rel / self.resolution_s + _EPS))
        else:
            index = bisect.bisect_right(offsets, position + _tolerance(position)) - 1
        index = min(max(index, 0), len(values) - 1)
        if self.interpolation == "step":
            return values[index]
        t0, v0 = offsets[index], values[index]
        if index + 1 < len(values):
            t1, v1 = offsets[index + 1], values[index + 1]
        elif self.wrap:
            t1, v1 = first + self.period_s, values[0]
        else:
            return v0
        fraction = (position - t0) / (t1 - t0)
        return v0 + min(max(fraction, 0.0), 1.0) * (v1 - v0)

    # -- public ------------------------------------------------------------------

    def at(self, t: Any) -> float:
        """Return the profile value at *t*.

        :param t: a clock (its calendar time when both the clock and the
            profile have a calendar, else ``clock.time``), seconds since the
            scenario start, a ``datetime`` or a string (ISO datetime or a
            duration such as ``"6h"``)
        :return: the (interpolated) value
        :raises ScenarioError: outside a non-wrapping profile, or for a
            datetime when the profile has no calendar start

        Example::

            load.at(clock)
            load.at(3600.0)
            load.at(datetime(2024, 6, 1, 12))
        """
        return self._lookup(self._position(t))

    __call__ = at

    def window(self, start: Any, end: Any) -> numpy.ndarray:
        """Return the values at ``start, start + resolution, ...`` before *end*.

        Useful for forecasts: ``profile.window(clock.time, clock.time + 86400)``.

        :param start: first time (anything :meth:`at` accepts)
        :param end: exclusive end time
        :return: numpy array of values

        Example::

            next_day = pv.window(clock, clock.time + 86400)
        """
        import numpy as np

        first, last = self._position(start), self._position(end)
        if last < first:
            raise ValueError(f"window end {end!r} lies before its start {start!r}.")
        count = int(math.ceil((last - first) / self.resolution_s - _EPS))
        return np.array(
            [self._lookup(first + k * self.resolution_s) for k in range(count)],
            dtype=float,
        )


def _column_index(spec_columns: Sequence[str], key: Any, name: str) -> int:
    if key is None:
        return 0
    if isinstance(key, str):
        if key in spec_columns:
            return list(spec_columns).index(key)
        raise ScenarioError(
            f"Profile {name!r} has no column {key!r}; columns: "
            f"{', '.join(spec_columns)}."
        )
    if isinstance(key, numbers.Integral) and not isinstance(key, bool):
        return int(key) % len(spec_columns)
    raise TypeError(
        f"A profile key must be a column name or an int (e.g. the entity index), "
        f"got {type(key).__name__}."
    )


def _resolve_key(spec: ProfileSpec, name: str, key: Any) -> int:
    """The column index *key* selects (reads the table once per process)."""
    return _column_index(_table(spec, name).columns, key, name)


def load_profile(
    spec: ProfileSpec | Mapping,
    *,
    name: str = "profile",
    key: Any = None,
    scenario_start: _dt.datetime | None = None,
    rng: Any = None,
) -> Profile:
    """Build a :class:`Profile` from a spec (files are read once per process).

    Inside scenarios use ``ctx.profile(name)``/``e.profile(name)``, which
    call this with the run's calendar and the entity's noise stream.

    :param spec: a :class:`ProfileSpec` or its dict form
    :param name: the profile name (messages, ``Profile.name``)
    :param key: the column: a name, or an int taken modulo the number of
        columns (default: the first column)
    :param scenario_start: calendar time of scenario offset 0 (default
        anchor of the profile, and the reference of second offsets)
    :param rng: a ``numpy.random.Generator`` for the multiplicative noise
        (``None``: no noise)
    :return: the profile

    Example::

        pv = load_profile({"source": "csv", "path": "data/profiles.csv",
                           "column": "pv", "resolution": "15min"}, name="pv")
    """
    if not isinstance(spec, ProfileSpec):
        spec = ProfileSpec.model_validate(spec)
    table = _table(spec, name)
    index = _column_index(table.columns, key, name)
    values = table.data[:, index] * spec.scale
    if spec.noise > 0 and rng is not None:
        values = values * (1.0 + spec.noise * rng.standard_normal(values.size))
    if table.times is not None:
        offsets = table.times
        origin = table.origin
        if origin is not None and origin.tzinfo is None:
            what = f"time_column {spec.time_column!r} of {table.path!r}"
            zone = getattr(scenario_start, "tzinfo", None)
            if zone is not None:  # one time axis: elapsed seconds in the zone
                origin, offsets = _localize_times(origin, offsets, zone, what)
            else:
                _check_increasing(offsets, what, naive=True)
        if spec.resolution is not None:
            resolution = spec.resolution
        elif offsets.size > 1:
            import numpy as np

            resolution = float(np.median(np.diff(offsets)))
        else:
            raise ScenarioError(
                f"Profile {name!r} has a single sample; set its resolution."
            )
        if table.origin is not None:
            if spec.start is not None:
                raise ScenarioError(
                    f"Profile {name!r}: start is only used without a datetime "
                    f"time_column ({spec.time_column!r} already dates the samples)."
                )
            start = origin
        else:
            start = spec.start if spec.start is not None else scenario_start
    else:
        offsets = None
        resolution = spec.resolution
        start = spec.start if spec.start is not None else scenario_start
    dated = table.origin is not None
    return Profile(
        name=name,
        column=table.columns[index],
        values=values,
        resolution_s=resolution,
        offsets=offsets,
        start=start,
        scenario_start=scenario_start,
        interpolation=spec.interpolation,
        wrap=effective_wrap(spec, dated),
        dated=dated,
    )


def _shift(base: _dt.datetime, seconds: float, *, wall: bool = False) -> _dt.datetime:
    """*base* plus *seconds* of elapsed time (wall-clock time with *wall* or a naive *base*).

    Python adds a ``timedelta`` to an aware datetime as wall-clock time, which
    is off by the daylight-saving shift between the two instants.
    """
    if wall or base.tzinfo is None:
        return base + _dt.timedelta(seconds=seconds)
    moved = base.astimezone(_dt.timezone.utc) + _dt.timedelta(seconds=seconds)
    return moved.astimezone(base.tzinfo)


def coverage_problems(params: Any) -> list[str]:
    """The dated, non-wrapping profiles of *params* that do not cover the run.

    Reading such a profile outside its dates fails, so the run is certain to
    fail: ``mango-scenario validate`` reports these as errors.  The messages
    are those :func:`profile_warnings` (and ``plan()``) give.

    :param params: the run's parameters
    :return: one message per profile

    Example::

        coverage_problems(scenario.resolve_params("presets/july.yaml"))
    """
    return profile_warnings(params, coverage=True, _coverage_only=True)


def effective_wrap(spec: ProfileSpec, dated: bool) -> bool:
    """``spec.wrap``, or its default: no wrapping for dated profiles, else wrapping."""
    return (not dated) if spec.wrap is None else spec.wrap


def profile_warnings(
    params: Any,
    *,
    names: Any = None,
    coverage: bool = True,
    _coverage_only: bool = False,
) -> list:
    """Alignment problems of the profiles in ``params.profiles`` (``plan()`` warnings).

    - a dated profile that does not wrap and does not cover the run window
      (``time.start`` to ``time.end``, the horizon as elapsed time, also
      across daylight-saving changes): reading it will fail;
    - a profile without ``start`` (anchored at ``time.start``) in a run whose
      first phase is not recorded (a warm-up): moving ``time.start`` for the
      warm-up moves the profile, too.

    Files that cannot be read are skipped (reading them reports the error).

    :param params: the run's parameters
    :param names: only these profiles (default: all)
    :param coverage: include the coverage check
    :return: the warning messages
    """
    profiles = getattr(params, "profiles", None) or {}
    time = getattr(params, "time", None)
    start = getattr(time, "start", None)
    phases = getattr(params, "phases", None) or []
    warm_up = bool(phases) and getattr(phases[0], "record", True) is False
    messages = []
    for name, spec in profiles.items():
        if names is not None and name not in names:
            continue
        try:
            table = _table(spec, name)
        except Exception:  # noqa: BLE001 - reported when the profile is read
            continue
        dated = table.origin is not None
        if coverage and dated and start is not None and not effective_wrap(spec, dated):
            origin = table.origin
            if origin.tzinfo is None and start.tzinfo is not None:
                origin = origin.replace(tzinfo=start.tzinfo)
            times = table.times
            first = float(times[0]) if times is not None and len(times) else 0.0
            last = float(times[-1]) if times is not None and len(times) else 0.0
            if spec.resolution is not None:
                last += spec.resolution
            elif times is not None and len(times) > 1:
                last += float(times[-1] - times[-2])
            # naive table times are wall-clock seconds of the zone, aware ones
            # elapsed seconds; the run spans elapsed time (horizon_s)
            wall = table.origin.tzinfo is None
            begin = _shift(origin, first, wall=wall)
            end = _shift(origin, last, wall=wall)
            run_end = _shift(start, time.horizon_s)
            tolerance = 1e-6
            if _delta(start, begin) < -tolerance or _delta(run_end, end) > tolerance:
                messages.append(
                    f"profile {name!r} has data from {begin.isoformat()} to "
                    f"{end.isoformat()}, but the run spans {start.isoformat()} to "
                    f"{run_end.isoformat()}: reading it outside its dates will fail "
                    "(dated profiles do not wrap by default; set "
                    f"profiles.{name}.wrap: true to repeat it on purpose)"
                )
        if warm_up and not dated and spec.start is None and not _coverage_only:
            messages.append(
                f"profile {name!r} has no start, so it is anchored at time.start "
                f"({start.isoformat() if start is not None else 'offset 0'}); the "
                f"warm-up phase {phases[0].name!r} begins there too, so moving "
                "time.start for the warm-up shifts the profile in calendar time. Set "
                f"profiles.{name}.start to pin it (to time.start's value to keep the "
                "current alignment)"
            )
    return messages


def _profile_spec(params: Any, name: str) -> ProfileSpec:
    if not isinstance(name, str):
        raise TypeError(f"A profile name must be a str, got {type(name).__name__}.")
    profiles = getattr(params, "profiles", None) or {}
    spec = profiles.get(name)
    if spec is None:
        known = sorted(profiles)
        hint = did_you_mean(name, known)
        listing = ", ".join(repr(k) for k in known) if known else "none"
        raise ScenarioError(
            f"Unknown profile {name!r}.{hint} Profiles in params.profiles: {listing} "
            "(declare it in a preset: profiles: {" + name + ": {source: csv, path: "
            "..., column: ...}})."
        )
    return spec
