"""
Results of a scenario run: a tidy table plus everything needed to replay it.

A :class:`RunResult` holds the run's time series as one long table with the
columns ``run_id, time, [datetime], phase, group, entity, variable, value``
(``time`` in seconds since the scenario start; ``datetime`` only with a
calendar), the resolved configuration, the seed and run statistics.
:meth:`~mango.scenario.RunResult.save` writes a run folder and
:func:`~mango.scenario.load_run` reads it back;
:meth:`~mango.scenario.RunResult.checksum` is identical for both, whatever the file format.

Besides the sampled time series a run may hold *event logs* (tables of
rows that agents append with :meth:`ctx.log <mango.scenario.BuildContext.log>`,
``result.log(name)``) and *metrics* (run-level values,
``result.metrics``).

The group, entity, variable, phase and run id columns are categoricals
(compact for large runs; with pandas 2 pass ``observed=True`` to
``groupby`` on them, the default of pandas 3).

``__all__`` is the public part; ``recording_series``, ``assemble_frame``,
``concat_frames``, ``compact_labels``, ``set_run_id``, ``log_frame``,
``log_checksum`` and the ``read_run_*`` readers are the package-internal
interface of the runtime, the runner and the study store.

A row at time ``t > 0`` is sampled *after* the step that ended at ``t``.
What it stands for depends on the recorder's ``kind``: a *flow* (a value
computed in ``on_step`` for the step, e.g. a power) describes the interval
from the previous row to ``t`` and belongs to the phase active during it; a
*state* (a level changed in handlers, tasks or events, e.g. a queue length)
holds from ``t`` until the next row and belongs to the phase that starts at
or before ``t``.  The row at ``t = 0`` is the initial state.
:meth:`~mango.scenario.RunResult.integrate`,
:meth:`~mango.scenario.RunResult.time_average` and
:meth:`~mango.scenario.RunResult.bins` weight the rows with these intervals
(energy from power, vehicle-minutes from a queue length, daily totals).

Example::

    result = scenario.run(seed=7)
    result.integrate("power_kw")                    # kWh, summed over entities
    result.time_average("queue_len")                # a state, kind="state" declared
    result.wide("soc_kwh")                          # time x entity
    result.save("results/ev_seed7")
    again = load_run("results/ev_seed7")
    assert again.checksum() == result.checksum()
"""

from __future__ import annotations

import array
import datetime as _dt
import hashlib
import itertools
import json
import math
import numbers
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel

from ._util import (
    canonical_json,
    content_hash,
    did_you_mean,
    have_pyarrow,
    provenance,
    require_pandas,
    stable_ref,
    to_plain,
    utc_now,
    write_json_atomic,
)
from .errors import ScenarioError
from .events import MAIN_PHASE, phase_starts, sample_phase_index
from .params import dump_params, load_params
from .timecfg import TimeConfig, _tolerance

if TYPE_CHECKING:
    import pandas

    from mango.agent.core import Agent
    from mango.simulation.world import SimulationWorld

__all__ = [
    "RunResult",
    "load_run",
    "compute_run_id",
    "tidy_value",
    "tidy_frame",
    "frame_checksum",
    "RESULT_COLUMNS",
    "LOG_COLUMNS",
    "MAIN_PHASE",
    "TIME_INDEX_UNITS",
]

# files of a run folder
SPEC_FILE = "spec.json"
MANIFEST_FILE = "manifest.json"
ERROR_FILE = "error.txt"
OUTCOMES_FILE = "outcomes.json"
CONFIG_FILE = "config.resolved.yaml"
METRICS_FILE = "metrics.json"
LOGS_DIR = "logs"

#: Column order of a run's tidy table (``datetime`` only with a calendar).
RESULT_COLUMNS: tuple[str, ...] = (
    "run_id",
    "time",
    "datetime",
    "phase",
    "group",
    "entity",
    "variable",
    "value",
)


#: Columns every event-log row gets before its fields (``datetime`` only with a calendar).
LOG_COLUMNS: tuple[str, ...] = ("run_id", "time", "datetime", "phase")

_TIMESERIES = "timeseries"
_FORMATS = ("parquet", "csv")
_TEXT_COLUMN = "value_text"
_STRING_COLUMNS = ("run_id", "phase", "group", "entity", "variable")
_LOG_DTYPES = ("int64", "float64", "bool", "str")
_CHECKSUM_CHUNK = 100_000


def _pandas():
    return require_pandas("Scenario results need")


def _require_pyarrow(action: str) -> None:
    if not have_pyarrow():
        raise ImportError(
            f"{action} needs pyarrow: pip install 'mango-agents[scenario-all]' "
            "(or pass format='csv')."
        )


# --------------------------------------------------------------------------- values


def tidy_value(value: Any) -> float | str:
    """Convert a recorded value to what the tidy table stores.

    Numbers, bools and numpy scalars become ``float``; ``None`` becomes NaN;
    strings stay strings; anything else becomes its JSON text (canonical
    JSON when possible, e.g. dataclasses such as ``Position2D`` and pydantic
    models, otherwise ``json.dumps(value, default=str)``).

    :param value: the recorded value
    :return: a float or a str

    Example::

        tidy_value(3), tidy_value(True), tidy_value(None)    # 3.0, 1.0, nan
        tidy_value(Position2D(1.0, 2.0))                      # '{"x":1.0,"y":2.0}'
    """
    if value is None:
        return math.nan
    numpy = sys.modules.get("numpy")
    if numpy is not None and isinstance(value, numpy.generic):
        value = value.item()
    if isinstance(value, str):
        return value
    if isinstance(value, bool | numbers.Real):
        return float(value)
    try:
        return canonical_json(value)
    except TypeError:
        return json.dumps(value, default=str, sort_keys=True)


def _phase_lookup(phases: Sequence[tuple[float, str]] | None):
    if not phases:
        return lambda t: MAIN_PHASE
    ordered = sorted(phases, key=lambda item: item[0])
    if len(ordered) == 1:
        name = ordered[0][1]
        return lambda t: name
    starts = [start for start, _ in ordered]
    names = [name for _, name in ordered]

    def lookup(t: float) -> str:
        return names[sample_phase_index(starts, t)]

    return lookup


def tidy_frame(
    records: Iterable[Mapping],
    *,
    run_id: str,
    phases: Sequence[tuple[float, str]] | None = None,
    calendar: bool = False,
) -> pandas.DataFrame:
    """Build a run's tidy table from recording rows.

    :param records: rows as returned by
        :func:`~mango.simulation.recording.recordings_to_records`
    :param run_id: the run id written into every row
    :param phases: ``[(start_seconds, phase_name), ...]``; a sample at time
        ``t > 0`` is the state after the step that ended at ``t`` and belongs
        to the phase active during that step (the last one starting *before*
        ``t``); the sample at ``t = 0`` belongs to the first phase (default:
        everything is ``"main"``)
    :param calendar: include the ``datetime`` column
    :return: the data frame with :data:`RESULT_COLUMNS` (``run_id``,
        ``phase``, ``group``, ``entity`` and ``variable`` as categoricals)

    Example::

        frame = tidy_frame(world.to_records(groups=groups), run_id="abc123")
    """
    pd = _pandas()
    rows = list(records)
    phase_of = _phase_lookup(phases)
    times = [float(row["time"]) for row in rows]
    values = [tidy_value(row["value"]) for row in rows]
    data: dict[str, Any] = {
        "run_id": [run_id] * len(rows),
        "time": pd.Series(times, dtype="float64"),
    }
    if calendar:
        data["datetime"] = pd.to_datetime(
            pd.Series([row.get("datetime") for row in rows], dtype=object)
        )
    data["phase"] = [phase_of(t) for t in times]
    data["group"] = [row.get("group") for row in rows]
    data["entity"] = [row.get("entity") for row in rows]
    data["variable"] = [row["variable"] for row in rows]
    data["value"] = _value_series(values)
    columns = [c for c in RESULT_COLUMNS if calendar or c != "datetime"]
    frame = pd.DataFrame(data, columns=columns)
    for name in _STRING_COLUMNS:
        frame[name] = compact_labels(frame[name])
    return frame


def _value_series(values: list):
    pd = _pandas()
    if all(isinstance(value, float) for value in values):
        return pd.Series(values, dtype="float64")
    return pd.Series(values, dtype=object)


def _numeric(series):
    if series.dtype == object and all(isinstance(v, float) for v in series.tolist()):
        return series.astype("float64")
    return series


# --------------------------------------------------------------------------- columnar assembly


def _label_dtype():
    """The dtype pandas gives a column of Python strings (``str`` in pandas 3, else object)."""
    return _pandas().Series(["x"]).dtype


def _labels_index(labels: Iterable[Any]):
    pd = _pandas()
    return pd.Index(list(labels), dtype=_label_dtype())


def compact_labels(column: Any) -> Any:
    """A label column as a categorical, categories in order of first appearance.

    In-memory and loaded frames get identical dtypes this way, whatever the
    file format stored (plain strings, Parquet dictionaries, CSV text).
    """
    pd = _pandas()
    codes, uniques = pd.factorize(column)
    categorical = pd.Categorical.from_codes(
        codes, categories=_labels_index(uniques.tolist())
    )
    if isinstance(column, pd.Series):
        return pd.Series(categorical, index=column.index, name=column.name)
    return categorical


def _first_seen(codes: Any, labels: Sequence[Any]) -> Any:
    """Categorical from *codes* into *labels*, renumbered to the order of first appearance."""
    import numpy as np

    pd = _pandas()
    # pd.unique keeps the order of appearance and needs no sort of all rows
    order = np.asarray([code for code in pd.unique(codes) if code >= 0], dtype=np.int64)
    remap = np.full(len(labels) + 1, -1, dtype=_code_dtype(len(labels)))
    remap[order] = np.arange(len(order))  # remap[-1] = -1 (missing)
    return pd.Categorical.from_codes(
        remap[codes], categories=_labels_index(labels[i] for i in order)
    )


def _series_labels(labels: Sequence[Any], lengths: Any) -> Any:
    """Categorical column of per-series labels repeated per row (``None`` = missing)."""
    import numpy as np

    pd = _pandas()
    index: dict[Any, int] = {}
    codes = np.empty(len(labels), dtype=np.int32)
    for i, (label, length) in enumerate(zip(labels, lengths)):
        if label is None:
            codes[i] = -1
        elif length:  # empty series add no category (as after a round trip)
            codes[i] = index.setdefault(label, len(index))
        else:
            codes[i] = -1
    return pd.Categorical.from_codes(
        np.repeat(codes, lengths), categories=_labels_index(index)
    )


def recording_series(world: SimulationWorld, groups: Mapping[str, str]) -> list[tuple]:
    """The world's recordings as ``(group, entity, variable, times, values)`` series.

    Same rows, order and group rules as
    :func:`~mango.simulation.recording.recordings_to_records`, without
    building one dict per row.
    """
    from mango.simulation.recording import WORLD_ENTITY

    series: list[tuple] = []
    for key, recording in world.data_collections.items():
        if len(recording.time) != len(recording.timeseries):
            raise ValueError(
                f"World recording '{key}' has {len(recording.timeseries)} values "
                f"but {len(recording.time)} times; append one time per value."
            )
        owner = recording.entity or WORLD_ENTITY
        variable = recording.variable or key
        series.append((owner, owner, variable, recording.time, recording.timeseries))
    agents = world.agents
    for key, recording in world.data_agent_collections.items():
        for aid, values in recording.timeseries.items():
            times = recording.agent_time.get(aid)
            if times is None:
                if len(values) != len(recording.time):
                    raise ValueError(
                        f"Cannot align recording '{key}' of agent '{aid}': "
                        f"{len(values)} values but {len(recording.time)} times."
                    )
                times = recording.time
            elif len(times) != len(values):
                raise ValueError(
                    f"Recording '{key}' of agent '{aid}' has {len(values)} values "
                    f"but {len(times)} times."
                )
            if aid in groups:
                group = groups[aid]
            elif aid in agents:
                group = agents[aid].description.category
            else:
                group = None
            series.append((group, aid, key, times, values))
    return series


def assemble_frame(
    series: Sequence[tuple],
    *,
    run_id: str,
    phases: Sequence[tuple[float, str]] | None = None,
    to_datetime: Any = None,
    state_variables: Iterable[str] = (),
) -> pandas.DataFrame:
    """Build a run's tidy table column by column from recorded series.

    The result equals :func:`tidy_frame` of the same rows, but no per-row
    Python objects besides the values are created and the label columns are
    categoricals built from codes (about 30 bytes per row instead of ~80,
    and a fraction of the peak memory).

    :param series: ``(group, entity, variable, times, values)`` tuples in row
        order (see :func:`recording_series`)
    :param run_id: the run id of every row
    :param phases: ``[(start_seconds, phase_name), ...]`` (see :func:`tidy_frame`)
    :param to_datetime: ``fn(seconds) -> datetime`` of a calendar clock, or
        ``None`` (no ``datetime`` column)
    :param state_variables: variables recorded as states: their rows belong
        to the phase that starts at or before their time (a state holds
        from its row on), the other rows to the phase of the interval that
        ended at their time
    """
    import numpy as np

    pd = _pandas()
    lengths = np.fromiter(
        (len(s[3]) for s in series), dtype=np.int64, count=len(series)
    )
    n = int(lengths.sum())
    time = np.fromiter(
        itertools.chain.from_iterable(s[3] for s in series), dtype=np.float64, count=n
    )
    value = _value_column(series, n)
    data: dict[str, Any] = {
        "run_id": _series_labels([run_id] * len(series), lengths),
        "time": time,
    }
    ordered = sorted(phases or [(0.0, MAIN_PHASE)], key=lambda item: item[0])
    names = [name for _, name in ordered]
    several = len(set(names)) > 1
    unique_times = codes = None
    if to_datetime is not None or several:
        # row -> index into the distinct times, without sorting all rows
        unique_times = np.sort(pd.unique(time))
        codes = np.searchsorted(unique_times, time)
    if to_datetime is not None:
        stamps = pd.to_datetime(
            pd.Series([to_datetime(float(t)) for t in unique_times], dtype=object)
        )
        data["datetime"] = pd.Series(stamps.array.take(codes), copy=False)
    if several:
        starts = [start for start, _ in ordered]
        small = _code_dtype(len(names))
        index = np.fromiter(
            (sample_phase_index(starts, float(t)) for t in unique_times),
            dtype=small,
            count=len(unique_times),
        )
        phase_codes = index[codes]
        states = set(state_variables)
        if states:
            held = np.fromiter(
                (_held_phase_index(starts, float(t)) for t in unique_times),
                dtype=small,
                count=len(unique_times),
            )
            is_state = np.repeat([s[2] in states for s in series], lengths)
            phase_codes = np.where(is_state, held[codes], phase_codes)
            del is_state
        del codes
        data["phase"] = _first_seen(phase_codes, names)
        del phase_codes
    else:
        del codes
        data["phase"] = _series_labels([names[0]] * len(series), lengths)
    data["group"] = _series_labels([s[0] for s in series], lengths)
    data["entity"] = _series_labels([s[1] for s in series], lengths)
    data["variable"] = _series_labels([s[2] for s in series], lengths)
    data["value"] = value
    columns = [c for c in RESULT_COLUMNS if to_datetime is not None or c != "datetime"]
    # copy=False: the columns are fresh, so the frame takes them as they are
    return pd.DataFrame(data, columns=columns, copy=False)


def _code_dtype(n: int) -> Any:
    """The smallest signed int type for codes ``-1 .. n - 1``."""
    import numpy as np

    return np.int8 if n < 127 else np.int16 if n < 32767 else np.int32


def _value_column(series: Sequence[tuple], n: int) -> Any:
    """The ``value`` column: float64 when every value is a number, else object."""
    import numpy as np

    pd = _pandas()
    converted: list = []
    numeric = True
    for item in series:
        values = item[4]
        if isinstance(values, array.array) and values.typecode == "d":
            converted.append(values)
            continue
        if all(type(value) is float for value in values):
            converted.append(values)
            continue
        tidy = [tidy_value(value) for value in values]
        converted.append(tidy)
        if numeric and not all(isinstance(value, float) for value in tidy):
            numeric = False
    if numeric:
        column = np.empty(n, dtype=np.float64)
        position = 0
        for values in converted:
            size = len(values)
            if isinstance(values, array.array):
                column[position : position + size] = np.frombuffer(
                    values, dtype=np.float64
                )
            else:
                column[position : position + size] = np.fromiter(
                    values, np.float64, count=size
                )
            position += size
        return pd.Series(column, copy=False)
    objects = np.empty(n, dtype=object)
    position = 0
    for values in converted:
        objects[position : position + len(values)] = (
            values.tolist() if isinstance(values, array.array) else values
        )
        position += len(values)
    return pd.Series(objects, dtype=object, copy=False)


def _held_phase_index(starts: Sequence[float], t: float) -> int:
    """Index of the phase active from offset *t* on (the last one starting at or before it)."""
    import bisect

    return max(bisect.bisect_right(starts, t + _tolerance(t)) - 1, 0)


def concat_frames(frames: Sequence[pandas.DataFrame]) -> pandas.DataFrame:
    """Concatenate tidy tables, keeping the label columns categorical (union of categories)."""
    pd = _pandas()
    frames = list(frames)
    for name in (*_STRING_COLUMNS, "phase"):
        columns = [f[name] for f in frames if name in f.columns]
        if len(columns) != len(frames) or not all(
            isinstance(c.dtype, pd.CategoricalDtype) for c in columns
        ):
            continue
        labels = dict.fromkeys(
            itertools.chain.from_iterable(c.cat.categories.tolist() for c in columns)
        )
        categories = _labels_index(labels)
        frames = [
            f.assign(**{name: f[name].cat.set_categories(categories)}) for f in frames
        ]
    return pd.concat(frames, ignore_index=True)


def _constant_labels(label: str, n: int) -> Any:
    import numpy as np

    pd = _pandas()
    return pd.Categorical.from_codes(
        np.zeros(n, dtype=np.int8), categories=_labels_index([label] if n else [])
    )


def set_run_id(result: RunResult, run_id: str) -> None:
    """Give *result* (its tables included) another run id, e.g. the study's."""
    result.run_id = run_id
    result.frame["run_id"] = _constant_labels(run_id, len(result.frame))
    for frame in result.logs.values():
        frame["run_id"] = _constant_labels(run_id, len(frame))


# --------------------------------------------------------------------------- event logs


def _plain_scalar(value: Any) -> Any:
    numpy = sys.modules.get("numpy")
    if numpy is not None and isinstance(value, numpy.generic):
        return value.item()
    return value


def _log_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return canonical_json(value)
    except TypeError:
        return json.dumps(value, default=str, sort_keys=True)


def _log_column(values: list) -> tuple[Any, str]:
    """A log field as a column: int64, float64, bool or str (others as JSON text)."""
    import numpy as np

    pd = _pandas()
    values = [_plain_scalar(value) for value in values]
    present = [value for value in values if value is not None]
    complete = len(present) == len(values)
    if present and complete and all(type(value) is bool for value in present):
        return pd.Series(values, dtype=bool), "bool"
    if present and complete and all(type(value) is int for value in present):
        try:
            return pd.Series(np.array(values, dtype=np.int64)), "int64"
        except OverflowError:
            pass
    if all(isinstance(value, bool | int | float) for value in present):
        numbers_ = [math.nan if value is None else float(value) for value in values]
        if not any(type(value) is int and abs(value) > 2**53 for value in present):
            return pd.Series(numbers_, dtype="float64"), "float64"
    texts = [None if value is None else _log_text(value) for value in values]
    return pd.Series(texts, dtype=_label_dtype()), "str"


def log_frame(
    rows: Sequence[tuple[float, str, Mapping]], *, run_id: str, to_datetime: Any = None
) -> pandas.DataFrame:
    """Build an event-log table from ``(time, phase, fields)`` rows.

    Columns: ``run_id, time, [datetime], phase`` and the fields in order of
    first appearance (empty where a row lacks a field).
    """
    import numpy as np

    pd = _pandas()
    n = len(rows)
    data: dict[str, Any] = {
        "run_id": _constant_labels(run_id, n),
        "time": np.fromiter((row[0] for row in rows), dtype=np.float64, count=n),
    }
    if to_datetime is not None:
        data["datetime"] = pd.to_datetime(
            pd.Series([to_datetime(float(row[0])) for row in rows], dtype=object)
        )
    data["phase"] = compact_labels(pd.Series([row[1] for row in rows], dtype=object))
    fields = dict.fromkeys(key for row in rows for key in row[2])
    for name in fields:
        data[name] = _log_column([row[2].get(name) for row in rows])[0]
    return pd.DataFrame(data)


def _field_dtype(column: Any) -> str:
    pd = _pandas()
    if pd.api.types.is_bool_dtype(column.dtype):
        return "bool"
    if pd.api.types.is_integer_dtype(column.dtype):
        return "int64"
    if pd.api.types.is_float_dtype(column.dtype):
        return "float64"
    return "str"


def _log_fields(frame: pandas.DataFrame) -> list[str]:
    return [c for c in frame.columns if c not in LOG_COLUMNS]


def _cell_text(value: Any) -> str:
    value = _plain_scalar(value)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return "" if math.isnan(value) else repr(value)
    return str(value)


def log_checksum(frame: pandas.DataFrame) -> str:
    """SHA-256 of an event-log table (time, phase and fields; format independent)."""
    digest = hashlib.sha256()
    fields = _log_fields(frame)
    digest.update("\x1f".join(fields).encode("utf-8"))
    digest.update(b"\n")
    columns = [frame[name].tolist() for name in ("time", "phase", *fields)]
    for row in zip(*columns):
        line = "\x1f".join(
            (repr(float(row[0])), _text(row[1]), *(_cell_text(v) for v in row[2:]))
        )
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def log_and_metric_differences(stored: RunResult, replayed: RunResult) -> list[str]:
    """Lines naming the event logs and metrics in which two results differ."""
    lines = []
    for name in dict.fromkeys([*stored.logs, *replayed.logs]):
        a, b = stored.logs.get(name), replayed.logs.get(name)
        if a is None or b is None or log_checksum(a) != log_checksum(b):
            rows = [None if f is None else len(f) for f in (a, b)]
            lines.append(f"log {name}: stored {rows[0]} rows, replayed {rows[1]} rows")
    for name in dict.fromkeys([*stored.metrics, *replayed.metrics]):
        a, b = stored.metrics.get(name), replayed.metrics.get(name)
        if canonical_json(a) != canonical_json(b):
            lines.append(f"metric {name}: stored {a!r}, replayed {b!r}")
    return lines


def _write_logs(logs: Mapping[str, pandas.DataFrame], folder: Path, fmt: str) -> dict:
    """Write ``logs/<name>.<fmt>``; returns the manifest entry (rows and field types)."""
    target = folder / LOGS_DIR
    schema: dict[str, dict] = {}
    for name, frame in logs.items():
        target.mkdir(exist_ok=True)
        path = target / f"{name}.{fmt}"
        if fmt == "parquet":
            frame.to_parquet(path, index=False, engine="pyarrow")
        else:
            frame.to_csv(path, index=False)
        for other in _FORMATS:
            if other != fmt:
                (target / f"{name}.{other}").unlink(missing_ok=True)
        schema[name] = {
            "rows": len(frame),
            "columns": {c: _field_dtype(frame[c]) for c in _log_fields(frame)},
        }
    if target.is_dir():  # tables of an earlier run in this folder
        for path in target.iterdir():
            stem, _, suffix = path.name.rpartition(".")
            if suffix in _FORMATS and stem not in logs:
                path.unlink()
        if not any(target.iterdir()):
            target.rmdir()
    return schema


def _read_log(
    folder: Path, name: str, entry: Mapping, fmt: str, tz: _dt.tzinfo | None
) -> pandas.DataFrame:
    pd = _pandas()
    path = folder / LOGS_DIR / f"{name}.{fmt}"
    if not path.exists():
        raise FileNotFoundError(
            f"Run folder {folder} is missing {LOGS_DIR}/{path.name}."
        )
    types = {c: t for c, t in (entry.get("columns") or {}).items() if t in _LOG_DTYPES}
    if fmt == "parquet":
        _require_pyarrow(f"Reading {path}")
        frame = pd.read_parquet(path, engine="pyarrow")
    else:
        text = [c for c, t in types.items() if t == "str"]
        frame = pd.read_csv(
            path,
            float_precision="round_trip",
            keep_default_na=False,
            na_values={
                c: [""]
                for c in [
                    *text,
                    *(c for c, t in types.items() if t == "float64"),
                    "datetime",
                ]
            },
            dtype={
                "run_id": str,
                "phase": str,
                **{c: (str if t == "str" else t) for c, t in types.items()},
            },
        )
    frame["time"] = frame["time"].astype("float64")
    if "datetime" in frame.columns:
        frame["datetime"] = _parse_datetimes(frame["datetime"], tz)
    for column in ("run_id", "phase"):
        frame[column] = compact_labels(frame[column])
    for column, kind in types.items():
        if kind == "str" and column in frame.columns:
            frame[column] = frame[column].astype(_label_dtype())
    return frame


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value)


def _value_key(value: Any) -> str:
    if isinstance(value, str):
        return "s:" + value
    if value is None:
        return "f:nan"
    return "f:" + repr(float(value))


def frame_checksum(frame: pandas.DataFrame) -> str:
    """Return the SHA-256 hex digest of a tidy table's content.

    It covers ``time, phase, group, entity, variable, value`` of every row in
    order (``run_id`` and the derived ``datetime`` are left out), with floats
    by exact ``repr``, so it is the same for a run in memory and after a
    Parquet or CSV round trip.

    :param frame: a tidy run table
    :return: 64 hex characters

    Example::

        frame_checksum(result.frame) == load_run(path).checksum()   # True
    """
    digest = hashlib.sha256()
    names = ("time", "phase", "group", "entity", "variable", "value")
    for start in range(0, len(frame), _CHECKSUM_CHUNK):  # bounded memory
        chunk = frame.iloc[start : start + _CHECKSUM_CHUNK]
        columns = [chunk[name].tolist() for name in names]
        for t, phase, group, entity, variable, value in zip(*columns):
            line = "\x1f".join(
                (
                    repr(float(t)),
                    _text(phase),
                    _text(group),
                    _text(entity),
                    _text(variable),
                    _value_key(value),
                )
            )
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def compute_run_id(scenario: str, params: BaseModel | Mapping, seed: Mapping) -> str:
    """Return the content-hash run id of a scenario reference, params and seed.

    :param scenario: the scenario reference (``"module:attr"``) or name; a
        ``"__main__:attr"`` reference counts as the script's module name (see
        ``stable_ref`` in ``mango.scenario._util``)
    :param params: the resolved params (model or its :func:`~mango.scenario.dump_params` dict)
    :param seed: the seed dict (``{"entropy": ..., "spawn_key": [...]}``)
    :return: 12 hex characters; any change of a parameter or the seed gives a
        new id

    Example::

        compute_run_id("studies.ev:scenario", params, {"entropy": 7, "spawn_key": []})
    """
    data = dump_params(params) if isinstance(params, BaseModel) else dict(params)
    key = stable_ref(scenario)
    return content_hash({"scenario": key, "params": data, "seed": dict(seed)})


# --------------------------------------------------------------------------- storage


def _to_storage(frame: pandas.DataFrame) -> pandas.DataFrame:
    pd = _pandas()
    stored = frame.copy()
    values = frame["value"].tolist()
    if frame["value"].dtype == object:
        text = [json.dumps(v) if isinstance(v, str) else None for v in values]
        stored["value"] = pd.Series(
            [math.nan if isinstance(v, str) else float(v) for v in values],
            dtype="float64",
            index=frame.index,
        )
        stored[_TEXT_COLUMN] = pd.Series(text, dtype=object, index=frame.index)
    return stored


def _parse_datetimes(column: pandas.Series, tz: _dt.tzinfo | None) -> pandas.Series:
    """Parse a stored ``datetime`` column back to the run's time zone.

    Text (CSV) of an aware calendar holds one UTC offset per row, and a DST
    switch mixes offsets, so it is parsed as UTC and converted to *tz*.
    """
    pd = _pandas()
    if pd.api.types.is_datetime64_any_dtype(column):
        if tz is not None and getattr(column.dt, "tz", None) is not None:
            return column.dt.tz_convert(tz)
        return column
    if tz is None:
        return pd.to_datetime(column, format="ISO8601")
    return pd.to_datetime(column, format="ISO8601", utc=True).dt.tz_convert(tz)


def _from_storage(
    stored: pandas.DataFrame, tz: _dt.tzinfo | None = None
) -> pandas.DataFrame:
    frame = stored.copy()
    frame["time"] = frame["time"].astype("float64")
    if "datetime" in frame.columns:
        frame["datetime"] = _parse_datetimes(frame["datetime"], tz)
    if _TEXT_COLUMN in frame.columns:
        texts = frame.pop(_TEXT_COLUMN).tolist()
        numbers_ = frame["value"].astype("float64").tolist()
        merged = [
            json.loads(text) if isinstance(text, str) and text != "" else number
            for text, number in zip(texts, numbers_)
        ]
        frame["value"] = _value_series(merged)
    else:
        frame["value"] = frame["value"].astype("float64")
    for name in _STRING_COLUMNS:
        if name in frame.columns:
            frame[name] = compact_labels(frame[name])
    columns = [c for c in RESULT_COLUMNS if c in frame.columns]
    return frame[columns].reset_index(drop=True)


def _resolve_format(format: str) -> str:
    if format == "auto":
        return "parquet" if have_pyarrow() else "csv"
    if format not in _FORMATS:
        raise ValueError(
            f"Unknown result format {format!r}; use 'auto', 'parquet' or 'csv'."
        )
    if format == "parquet":
        _require_pyarrow("Writing Parquet results")
    return format


def _write_timeseries(frame: pandas.DataFrame, folder: Path, fmt: str) -> Path:
    stored = _to_storage(frame)
    target = folder / f"{_TIMESERIES}.{fmt}"
    if fmt == "parquet":
        stored.to_parquet(target, index=False, engine="pyarrow")
    else:
        stored.to_csv(target, index=False)
    for other in _FORMATS:
        if other != fmt:
            (folder / f"{_TIMESERIES}.{other}").unlink(missing_ok=True)
    return target


def _read_timeseries(
    folder: Path,
    fmt: str | None,
    tz: _dt.tzinfo | None = None,
    variables: Sequence[str] | None = None,
) -> tuple[pandas.DataFrame, str]:
    """Read a run's table; *variables* keeps only their rows (filtered while reading)."""
    pd = _pandas()
    if fmt is None:
        found = [f for f in _FORMATS if (folder / f"{_TIMESERIES}.{f}").exists()]
        if not found:
            raise FileNotFoundError(
                f"Run folder {folder} has no {_TIMESERIES}.parquet or {_TIMESERIES}.csv."
            )
        fmt = found[0]
    path = folder / f"{_TIMESERIES}.{fmt}"
    if not path.exists():
        raise FileNotFoundError(f"Run folder {folder} is missing {path.name}.")
    wanted = None if variables is None else list(variables)
    if fmt == "parquet":
        _require_pyarrow(f"Reading {path}")
        filters = None if wanted is None else [("variable", "in", wanted)]
        stored = pd.read_parquet(path, engine="pyarrow", filters=filters)
    else:
        options = dict(
            float_precision="round_trip",
            keep_default_na=False,
            na_values={"value": [""], _TEXT_COLUMN: [""], "datetime": [""]},
            dtype={name: str for name in (*_STRING_COLUMNS, _TEXT_COLUMN)},
        )
        if wanted is None:
            stored = pd.read_csv(path, **options)
        else:  # filter chunk by chunk, so a large table is never held whole
            with pd.read_csv(path, chunksize=200_000, **options) as chunks:
                parts = [chunk[chunk["variable"].isin(wanted)] for chunk in chunks]
            stored = (
                pd.concat(parts, ignore_index=True)
                if parts
                else pd.read_csv(path, nrows=0, **options)
            )
    return _from_storage(stored, tz), fmt


def _run_tz(folder: Path) -> _dt.tzinfo | None:
    config_file = folder / CONFIG_FILE
    if not config_file.exists():
        return None
    config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    start = TimeConfig.model_validate(config.get("time", {})).start
    return None if start is None else start.tzinfo


def read_run_frame(
    path: str | os.PathLike, *, variables: Sequence[str] | None = None
) -> pandas.DataFrame:
    """The tidy table of a saved run, optionally only the rows of *variables*.

    Cheaper than :func:`load_run` when only some variables are needed: with
    Parquet the other rows are never read.
    """
    folder = Path(path)
    manifest = json.loads((folder / MANIFEST_FILE).read_text(encoding="utf-8"))
    frame, _ = _read_timeseries(
        folder, manifest.get("format"), _run_tz(folder), variables
    )
    return frame


def read_run_log(path: str | os.PathLike, name: str) -> pandas.DataFrame:
    """The event log *name* of a saved run (without reading its time series)."""
    folder = Path(path)
    manifest = json.loads((folder / MANIFEST_FILE).read_text(encoding="utf-8"))
    entries = manifest.get("logs") or {}
    if name not in entries:
        raise KeyError(f"No event log {name!r} in run folder {folder}.")
    fmt = manifest.get("format") or "parquet"
    return _read_log(folder, name, entries[name], fmt, _run_tz(folder))


def read_run_variables(path: str | os.PathLike) -> list[str]:
    """The variables a saved run recorded (reads only the ``variable`` column)."""
    pd = _pandas()
    folder = Path(path)
    manifest = json.loads((folder / MANIFEST_FILE).read_text(encoding="utf-8"))
    fmt = manifest.get("format") or "parquet"
    file = folder / f"{_TIMESERIES}.{fmt}"
    if fmt == "parquet":
        column = pd.read_parquet(file, engine="pyarrow", columns=["variable"])
    else:
        column = pd.read_csv(file, usecols=["variable"], dtype=str)
    return list(dict.fromkeys(column["variable"].tolist()))


def _merged(spans: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Adjacent or overlapping ``(start, end)`` spans joined."""
    merged: list[list[float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + _tolerance(start):
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _span_end(spans: Sequence[tuple[float, float]], t: float, end: float) -> float:
    """End of the recorded span holding offset *t* (cut at *end*); *t* when none does."""
    tol = _tolerance(t)
    for start, stop in spans:
        if start - tol <= t < stop - tol:
            return min(stop, end)
    return t


def _cumulative(begin: Any, end: Any, value: Any, points: Any) -> Any:
    """``∫ value dt`` from -inf to each of *points* over sorted, disjoint intervals."""
    import numpy as np

    width = end - begin
    full = np.concatenate([[0.0], np.cumsum(value * width)])
    k = np.searchsorted(begin, points, side="right")  # intervals starting before x
    last = np.maximum(k - 1, 0)
    partial = np.where(
        k > 0, value[last] * np.clip(points - begin[last], 0.0, width[last]), 0.0
    )
    return full[last] + partial


# --------------------------------------------------------------------------- result

#: Units accepted as ``index=`` of :meth:`RunResult.ts` and :meth:`RunResult.wide`
#: (besides ``"time"``, ``"datetime"`` and ``"step"``).
TIME_INDEX_UNITS: tuple[str, ...] = ("s", "min", "h", "d", "w")


class RunResult:
    """The outcome of one scenario run.

    Attributes: ``run_id``, ``scenario`` (reference or name), ``params``
    (the params model, ``None`` when loaded without a scenario), ``config``
    (resolved params as plain data), ``seed`` (seed dict; replay with
    ``scenario.run(config, seed=result.seed)``), ``time``
    (:class:`~mango.scenario.TimeConfig`), ``frame`` (the tidy table),
    ``outcomes`` (dict), ``stats`` (steps, messages, wall time, agents per
    group, link edges, ``stopped_early``/``stop_time_s``/``stop_reason``),
    ``manifest`` (dict written to ``manifest.json``), ``entities`` (entity
    name → group), ``factories`` (the scenario's communication and
    environment factories as references, ``None`` when unset), ``logs``
    (event-log name → table, see :meth:`log`), ``metrics`` (run-level
    values: :meth:`ctx.set_metric <mango.scenario.BuildContext.set_metric>`
    and :meth:`Scenario.metric <mango.scenario.Scenario.metric>`),
    ``warnings`` (run warnings as text: :meth:`ctx.warn
    <mango.scenario.BuildContext.warn>` and the run's own, e.g. a run that
    recorded no values, with the reason), ``record_kinds`` (variable →
    ``"state"`` or ``"flow"`` for the recorders that declare ``kind=``,
    see :meth:`integrate`), ``world``
    (in-process only: the agents after the run; the world's raw recordings
    are released once a finished run's table is built, ``frame`` holds the
    values) and ``path`` (the run folder once saved or loaded).

    Example::

        result = scenario.run(seed=7)
        pv_kwh = result.integrate("pv_kw")
        result.wide("soc_kwh", group="ev").plot()
        result.save("results/run_7")
    """

    def __init__(
        self,
        *,
        run_id: str,
        scenario: str,
        params: BaseModel | None,
        config: dict,
        seed: dict,
        time: TimeConfig,
        frame: pandas.DataFrame,
        outcomes: dict | None = None,
        stats: dict | None = None,
        manifest: dict | None = None,
        entities: dict[str, str] | None = None,
        world: SimulationWorld | None = None,
        path: str | os.PathLike | None = None,
        factories: dict | None = None,
        logs: dict[str, pandas.DataFrame] | None = None,
        metrics: dict | None = None,
        warnings: list[str] | None = None,
        record_kinds: Mapping[str, str] | None = None,
    ):
        self.run_id = run_id
        self.scenario = scenario
        self.params = params
        self.config = config
        self.seed = seed
        self.time = time
        self.frame = frame
        self.outcomes: dict = {} if outcomes is None else outcomes
        self.stats: dict = {} if stats is None else stats
        self.manifest: dict = {} if manifest is None else manifest
        self.entities: dict[str, str] = {} if entities is None else entities
        self.world = world
        self.path = None if path is None else Path(path)
        self.factories: dict = (
            {"communication": None, "environment": None}
            if factories is None
            else dict(factories)
        )
        self.logs: dict[str, pandas.DataFrame] = {} if logs is None else dict(logs)
        self.metrics: dict = {} if metrics is None else dict(metrics)
        #: run warnings (ctx.warn and the run's own, e.g. no values recorded);
        #: loaded runs read them from the manifest
        self.warnings: list[str] = list(
            (self.manifest.get("warnings") or []) if warnings is None else warnings
        )
        #: how the rows of each declared recorder integrate ("state" or
        #: "flow"); loaded runs read them from the manifest
        self.record_kinds: dict[str, str] = dict(
            (self.manifest.get("record_kinds") or {})
            if record_kinds is None
            else record_kinds
        )

    def __repr__(self) -> str:
        return (
            f"RunResult(run_id={self.run_id!r}, scenario={self.scenario!r}, "
            f"rows={len(self.frame)}, variables={self.variables()})"
        )

    # -- time ----------------------------------------------------------------------

    @property
    def step_s(self) -> float | None:
        """Fixed step in seconds (``None`` for event-driven runs)."""
        return self.time.step_s

    @property
    def step_h(self) -> float | None:
        """Fixed step in hours (``None`` for event-driven runs).

        Every row at ``t > 0`` covers one step, so ``ts("p_kw").sum() *
        step_h`` is the energy of the rows selected (the ``t = 0`` row of an
        unphased run adds the initial state; :meth:`integrate` leaves it out).
        """
        return self.time.step_h

    @property
    def horizon_s(self) -> float:
        """Horizon in seconds."""
        return self.time.horizon_s

    @property
    def start(self) -> _dt.datetime | None:
        """Calendar start (``None`` without ``time.start``)."""
        return self.time.start

    # -- queries -------------------------------------------------------------------

    def variables(self) -> list[str]:
        """Recorded variables in recording order.

        Example::

            result.variables()     # ["import_kw", "soc_kwh"]
        """
        return list(dict.fromkeys(self.frame["variable"].tolist()))

    def groups(self) -> list[str]:
        """Declared entity groups in declaration order (including empty groups).

        Example::

            result.groups()        # ["coordinator", "ev"]
        """
        per_group = self.stats.get("agents_per_group")
        if per_group:
            return list(per_group)
        return list(dict.fromkeys(self.entities.values()))

    def _declared_phases(self) -> list[str]:
        phases = (self.config or {}).get("phases") or []
        return [p.get("name") for p in phases if isinstance(p, dict)] + [MAIN_PHASE]

    def _select(
        self,
        variable: str,
        *,
        group: Any = None,
        entity: Any = None,
        phase: Any = None,
    ) -> pandas.DataFrame:
        frame = self.frame
        known = self.variables()
        if variable not in known:
            if not known:
                from .events import empty_run_reason

                raise KeyError(
                    f"No variable {variable!r} in run {self.run_id}: the run recorded "
                    f"no values ({empty_run_reason(self.config, self.stats)})."
                )
            hint = did_you_mean(str(variable), known)
            raise KeyError(
                f"No variable {variable!r} in run {self.run_id}.{hint} Variables: {known}"
            )
        selected = frame[frame["variable"] == variable]
        for column, wanted in (("group", group), ("entity", entity), ("phase", phase)):
            if wanted is None:
                continue
            wanted_list = [wanted] if isinstance(wanted, str) else list(wanted)
            present = list(dict.fromkeys(selected[column].tolist()))
            missing = [w for w in wanted_list if w not in present]
            if missing:
                note = ""
                if column == "phase" and missing[0] in self._declared_phases():
                    note = (
                        f" (phase {missing[0]!r} is declared but has no samples of "
                        "it: record: false, or no sample time falls into it)"
                    )
                raise KeyError(
                    f"Variable {variable!r} has no {column} {missing[0]!r}{note}; its "
                    f"{column}s are {present}."
                )
            selected = selected[selected[column].isin(wanted_list)]
        return selected

    def _time_index(self, selected: pandas.DataFrame, index: str) -> pandas.Index:
        """The time axis *index* (see :meth:`ts`) of the rows *selected*."""
        pd = _pandas()
        if not isinstance(index, str):
            raise TypeError(f"index must be a str, got {index!r}.")
        times = selected["time"].astype("float64")
        if index == "time":
            return pd.Index(times.to_numpy(), name="time")
        if index == "datetime":
            if self.start is None or "datetime" not in selected.columns:
                raise ValueError(
                    f"index='datetime' needs a calendar start; run {self.run_id} has "
                    "none (set time.start, e.g. scenario.time(start='2024-06-01', "
                    "...)); use index='time', 'step' or a unit such as 'h' or 'd'."
                )
            return pd.Index(selected["datetime"], name="datetime")
        if index == "step":
            step_s = self.step_s
            if step_s is None:
                raise ValueError(
                    f"index='step' needs a fixed step; run {self.run_id} is "
                    "event-driven (time.step: event); use index='time' or a unit "
                    "such as 'h'."
                )
            steps = (times / step_s).round().astype("int64")
            return pd.Index(steps.to_numpy(), name="step")
        from .timecfg import _UNIT_SECONDS

        unit_s = _UNIT_SECONDS.get(index.strip())
        if unit_s is None:
            raise ValueError(
                f"Unknown index {index!r}; use 'time' (seconds), 'datetime', 'step' "
                f"or a unit: {', '.join(TIME_INDEX_UNITS)}."
            )
        return pd.Index((times / unit_s).to_numpy(), name=f"time_{index.strip()}")

    def ts(
        self,
        variable: str,
        *,
        group: str | Sequence[str] | None = None,
        entity: str | Sequence[str] | None = None,
        phase: str | Sequence[str] | None = None,
        index: str = "time",
    ) -> pandas.Series:
        """Return one variable in long form (index = time in seconds by default).

        All entities are stacked, so ``.sum()`` sums over entities *and* time
        and ``.max()`` is the maximum over both; use :meth:`wide` for one
        column per entity (e.g. to plot).

        :param variable: the variable name
        :param group: restrict to these groups
        :param entity: restrict to these entities
        :param phase: restrict to these phases
        :param index: the time axis: ``"time"`` (seconds since the start),
            ``"datetime"`` (calendar time; needs ``time.start``),
            ``"step"`` (the step number ``time / step`` as an int: rounds
            or ticks of a model with ``step=1``; needs a fixed step) or a
            unit, ``"min"``, ``"h"``, ``"d"`` or ``"w"`` (time since the
            start in that unit, a float; the index is named ``time_<unit>``)
        :return: a ``pandas.Series`` named *variable*
        :raises KeyError: for unknown variables, groups, entities or phases
            (listing the known ones)
        :raises ValueError: for an unknown *index*, ``"datetime"`` without
            ``time.start`` and ``"step"`` in an event-driven run

        Example::

            peak = result.ts("import_kw").max()
            soc_ev0 = result.ts("soc_kwh", entity="ev_0")
            infected = result.ts("I", index="d")             # days since the start
        """
        selected = self._select(variable, group=group, entity=entity, phase=phase)
        series = selected["value"].rename(variable)
        series.index = self._time_index(selected, index)
        return _numeric(series)

    def wide(
        self,
        variable: str,
        *,
        group: str | Sequence[str] | None = None,
        phase: str | Sequence[str] | None = None,
        index: str = "time",
    ) -> pandas.DataFrame:
        """Return one variable as a time × entity table (entities in recording order).

        :param variable: the variable name
        :param group: restrict to these groups
        :param phase: restrict to these phases
        :param index: the time axis, as for :meth:`ts`: ``"time"``
            (seconds), ``"datetime"``, ``"step"`` or a unit such as ``"h"``
            or ``"d"``
        :return: a ``pandas.DataFrame``, one column per entity
        :raises KeyError: as :meth:`ts`, and for an entity label that occurs
            in several groups (pass *group*)
        :raises ValueError: as :meth:`ts`

        Example::

            result.wide("soc_kwh", group="ev").plot()
            result.wide("load_kw", index="datetime").iloc[:96]   # the first day
            # calendar totals and means: bins(), not resample() (rows are end-labelled)
            result.bins("load_kw", "1D", how="integrate")
        """
        selected = self._select(variable, group=group, phase=phase)
        owners = selected[["entity", "group"]].drop_duplicates()
        shared = owners["entity"][owners["entity"].duplicated()]
        if len(shared):
            label = shared.iloc[0]
            groups = owners.loc[owners["entity"] == label, "group"].tolist()
            raise KeyError(
                f"Variable {variable!r} has entity {label!r} in several groups "
                f"({groups}), e.g. the categories of aggregate='count' over several "
                "groups; pass group= to select one."
            )
        selected = selected.drop_duplicates(["time", "entity"], keep="last")
        order = list(dict.fromkeys(selected["entity"].tolist()))
        axis = self._time_index(selected, index)
        selected = selected.assign(_time_axis=axis)
        table = selected.pivot(index="_time_axis", columns="entity", values="value")
        table.index.name = axis.name
        table = table.reindex(columns=order)
        table.columns.name = "entity"
        return table.apply(_numeric) if len(table.columns) else table

    def integrate(
        self,
        variable: str,
        *,
        kind: str | None = None,
        group: str | Sequence[str] | None = None,
        entity: str | Sequence[str] | None = None,
        phase: str | Sequence[str] | None = None,
        unit: Any = "1h",
        per_entity: bool = False,
    ) -> Any:
        """Sum ``value x interval length`` over the rows of *variable*.

        Which interval a row stands for depends on what the value is (the
        recorder's ``kind=``, or *kind* here):

        - ``"flow"``: the value describes the interval that ended at the
          row (computed in ``on_step`` for the step, e.g. a power): the row
          at ``t`` covers the time since the entity's previous row, or since
          the start of the row's phase when that is later (the first row
          after a ``record: false`` warm-up).  The ``t = 0`` row (the
          initial state) is left out.  With a fixed step and no ``every=``
          this is ``ts(...).sum() * step_h`` over the rows at ``t > 0``.
        - ``"state"``: a level that holds until it changes (a queue length
          or an inventory changed in handlers, tasks or events): the row at
          ``t`` holds until the entity's next row, cut at the end of its
          recorded phases and at the end of the run (a stop or ``until``).
          This is exact for event-driven runs and recorders without
          ``every=``.

        Integrating a state as a flow charges every level to the interval
        *before* it was set: in event-driven runs the error does not shrink
        with more data.

        :param variable: a numeric variable, e.g. a power in kW
        :param kind: ``"flow"`` or ``"state"``; default: the recorder's
            declared ``kind`` (:attr:`record_kinds`)
        :param group: restrict to these groups
        :param entity: restrict to these entities
        :param phase: restrict to the time of these phases
        :param unit: the time unit of the result (a duration: ``"1h"``
            gives kWh from kW, ``"1s"`` kJ from kW)
        :param per_entity: return a ``pandas.Series`` per entity instead of
            the total
        :return: the integral (a float), or a Series indexed by entity
        :raises KeyError: as :meth:`ts`, and for phases the run does not have
        :raises ValueError: when neither *kind* nor the recorder says
            whether the variable is a state or a flow
        :raises TypeError: for non-numeric values

        Example::

            result.integrate("power_kw")                         # kWh (a declared flow)
            result.integrate("power_kw", phase="main", per_entity=True)
            result.integrate("queue_len", kind="state", unit="1min")  # vehicle-minutes
        """
        from .timecfg import parse_duration

        unit_s = parse_duration(unit)
        if unit_s <= 0:
            raise ValueError(f"integrate(unit={unit!r}): the unit must be positive.")
        rows = self._intervals(
            "integrate", variable, kind, group=group, entity=entity, phase=phase
        )
        contribution = rows["value"] * rows["width"] / unit_s
        if per_entity:
            total = contribution.groupby(rows["entity"], observed=True).sum()
            order = [e for e in rows.attrs["entities"] if e in total.index]
            return total.reindex(order).rename(variable)
        return float(contribution.sum())

    def time_average(
        self,
        variable: str,
        *,
        kind: str | None = None,
        group: str | Sequence[str] | None = None,
        entity: str | Sequence[str] | None = None,
        phase: str | Sequence[str] | None = None,
        per_entity: bool = False,
    ) -> Any:
        """Time-weighted mean of *variable* over the recorded time.

        ``integrate(variable, unit="1s") / recorded duration``: the rows are
        weighted as in :meth:`integrate` (by the declared ``kind`` or
        *kind*), and the duration is the time of the recorded phases up to
        the end of the run (:attr:`recorded_s`; with *phase*, of those
        phases).  So a ``record: false`` warm-up, an early stop and
        ``every=`` are accounted for; dividing by :attr:`horizon_s` is
        not.  Over several entities it is the time average of their sum
        (an entity without rows for part of the time counts 0 there).

        :param variable: a numeric variable
        :param kind: ``"flow"`` or ``"state"`` (default: the recorder's)
        :param group: restrict to these groups
        :param entity: restrict to these entities
        :param phase: restrict to the time of these phases
        :param per_entity: return a ``pandas.Series`` per entity
        :return: the mean (a float, NaN without recorded time), or a Series
        :raises KeyError: as :meth:`integrate`
        :raises ValueError: as :meth:`integrate`

        Example::

            result.time_average("queue_len")                 # mean queue length (a state)
            result.time_average("spread", kind="flow", phase="main")
        """
        rows = self._intervals(
            "time_average", variable, kind, group=group, entity=entity, phase=phase
        )
        duration = self._recorded_duration(rows.attrs["phases"])
        scale = math.nan if duration <= 0 else 1.0 / duration
        contribution = rows["value"] * rows["width"] * scale
        if per_entity:
            total = contribution.groupby(rows["entity"], observed=True).sum()
            order = [e for e in rows.attrs["entities"] if e in total.index]
            return total.reindex(order).rename(variable)
        return float(contribution.sum()) if duration > 0 else math.nan

    @property
    def recorded_s(self) -> float:
        """Seconds of recorded time: the phases without ``record: false``, up to the end of the run.

        The end of the run is the stop time of a run that stopped early (or
        ran ``until`` a time), else the horizon.  :meth:`time_average`
        divides by it.

        Example::

            result.integrate("power_kw", unit="1s") / result.recorded_s   # mean power
        """
        return self._recorded_duration(None)

    def bins(
        self,
        variable: str,
        freq: Any,
        *,
        how: str = "mean",
        kind: str | None = None,
        group: str | Sequence[str] | None = None,
        unit: Any = "1h",
    ) -> pandas.DataFrame:
        """Reduce *variable* to calendar or time bins: a bin × entity table.

        Every row stands for an interval (see :meth:`integrate`), and each
        interval is split at the bin edges, so a day's total holds exactly
        the time of that day: binning the ``datetime`` of the rows with
        pandas (``wide(v, index="datetime").resample("1D")``) puts the last
        interval of each day into the next day, because a flow row is
        labelled by the *end* of its interval.

        :param variable: a numeric variable
        :param freq: the bin width: with ``time.start`` a pandas frequency
            (``"1D"``, ``"1h"``, ``"15min"``, ``"W"``, ``"MS"``: calendar
            days, weeks or months in the run's time zone) or a duration;
            without it a duration (``"1d"``, ``"6h"``, seconds as a number)
        :param how: ``"mean"`` (time-weighted mean over the recorded time of
            the bin), ``"integrate"`` (sum of ``value x time`` in *unit*,
            e.g. kWh per day) or ``"last"`` (the value at the end of the
            bin: the level held for a state, the last interval for a flow)
        :param kind: ``"flow"`` or ``"state"`` (default: the recorder's)
        :param group: restrict to these groups
        :param unit: the time unit of ``how="integrate"``
        :return: a ``pandas.DataFrame`` indexed by the bin start
            (``datetime`` with a calendar, else ``time`` in seconds), one
            column per entity; bins without recorded time are NaN (0 for
            ``"integrate"``)
        :raises KeyError: as :meth:`ts`
        :raises ValueError: for an unknown *how* or *freq*, and as
            :meth:`integrate`

        Example::

            daily_kwh = result.bins("load_kw", "1D", how="integrate")       # kWh per day
            hourly = result.bins("queue_len", "1h")                         # mean per hour
            daily_kwh.sum(axis=1)                                           # all entities
        """
        import numpy as np

        from .timecfg import parse_duration

        pd = _pandas()
        if how not in ("mean", "integrate", "last"):
            raise ValueError(
                f"bins(how={how!r}): use 'mean' (time-weighted), 'integrate' (value x "
                "time) or 'last' (the value at the end of each bin)."
            )
        unit_s = parse_duration(unit)
        if unit_s <= 0:
            raise ValueError(f"bins(unit={unit!r}): the unit must be positive.")
        rows = self._intervals("bins", variable, kind, group=group)
        edges, labels = self._bin_edges(freq)
        index = pd.Index(labels, name="datetime" if self.start is not None else "time")
        columns = {}
        for name in rows.attrs["entities"]:
            part = rows[rows["entity"] == name]
            begin = part["begin"].to_numpy(dtype=float)
            end = begin + part["width"].to_numpy(dtype=float)
            value = part["value"].to_numpy(dtype=float)
            order = np.argsort(begin, kind="stable")
            begin, end, value = begin[order], end[order], value[order]
            if not len(begin):
                columns[name] = np.full(
                    len(labels), 0.0 if how == "integrate" else np.nan
                )
                continue
            if how == "last":
                k = np.searchsorted(begin, edges[1:], side="left") - 1
                result = np.where(k >= 0, value[np.maximum(k, 0)], np.nan)
                covered = _cumulative(begin, end, np.ones_like(value), edges)
                result = np.where(np.diff(covered) > 0, result, np.nan)
            else:
                area = np.diff(_cumulative(begin, end, value, edges))
                if how == "integrate":
                    result = area / unit_s
                else:
                    covered = np.diff(
                        _cumulative(begin, end, np.ones_like(value), edges)
                    )
                    with np.errstate(invalid="ignore", divide="ignore"):
                        result = np.where(covered > 0, area / covered, np.nan)
            columns[name] = result
        table = pd.DataFrame(columns, index=index)
        table.columns.name = "entity"
        return table

    # -- intervals -----------------------------------------------------------------

    def _kind_of(self, what: str, variable: str, kind: str | None) -> str:
        """The kind *kind*, else the recorder's declared one; an error when neither says."""
        from .reducers import KIND_HINT, RECORD_KINDS

        if kind is None:
            kind = self.record_kinds.get(variable)
            if kind is None:
                raise ValueError(
                    f"{what}({variable!r}) needs to know what the value is: pass "
                    f"{KIND_HINT}, or declare it on the recorder (record(..., "
                    "kind='state'|'flow'), YAML 'kind:'). The two weight each row "
                    "with a different interval, so the result depends on it."
                )
        if kind not in RECORD_KINDS:
            raise ValueError(f"{what}(kind={kind!r}): use 'flow' or 'state'.")
        return kind

    def _end_s(self) -> float:
        """Offset where the run ended: the stop or ``until`` time, else the horizon."""
        end = self.stats.get("sim_time_s")
        if isinstance(end, numbers.Real) and not isinstance(end, bool) and end > 0:
            return min(float(end), self.horizon_s)
        return self.horizon_s

    def _phase_spans(self) -> list[tuple[str, float, float, bool]]:
        """``(name, start, end, recorded)`` of every phase, cut at the end of the run."""
        end = self._end_s()
        phases = (self.config or {}).get("phases") or []
        if not phases:
            return [(MAIN_PHASE, 0.0, end, True)]
        try:
            from .events import Phase, Schedule

            grid = Schedule.__new__(Schedule)  # only the phase part of a schedule
            grid.time = self.time
            grid.warnings = []
            plans = grid._build_phases([Phase.model_validate(p) for p in phases])
        except (ValueError, TypeError):  # a hand-edited config: no phase bounds
            return [(MAIN_PHASE, 0.0, end, True)]
        return [
            (plan.name, plan.start, min(plan.end, end), bool(plan.record))
            for plan in plans
            if plan.start < end - _tolerance(end) or plan.start == 0.0
        ]

    def _selected_spans(
        self, what: str, variable: str, phase: Any
    ) -> list[tuple[str, float, float, bool]]:
        spans = self._phase_spans()
        if phase is None:
            return spans
        wanted = [phase] if isinstance(phase, str) else list(phase)
        names = [name for name, *_ in spans]
        missing = [w for w in wanted if w not in names]
        if missing:
            raise KeyError(
                f"{what}({variable!r}): the run has no phase {missing[0]!r}; its "
                f"phases are {names}."
            )
        return [span for span in spans if span[0] in wanted]

    def _recorded_duration(self, spans: Any) -> float:
        spans = self._phase_spans() if spans is None else spans
        return math.fsum(max(end - start, 0.0) for _, start, end, rec in spans if rec)

    def _intervals(
        self,
        what: str,
        variable: str,
        kind: str | None,
        *,
        group: Any = None,
        entity: Any = None,
        phase: Any = None,
    ) -> pandas.DataFrame:
        """The rows of *variable* with the interval each stands for.

        Columns ``entity, value, begin, width`` (seconds); rows of zero
        width are dropped.  ``attrs``: ``entities`` (in recording order)
        and ``phases`` (the selected phase spans).
        """
        import numpy as np

        pd = _pandas()
        selected = self._select(variable, group=group, entity=entity)
        kind = self._kind_of(what, variable, kind)
        values = _numeric(selected["value"])
        if values.dtype == object:
            raise TypeError(
                f"{what}({variable!r}): the variable has non-numeric values; "
                f"{what} numeric recorders only."
            )
        spans = self._selected_spans(what, variable, phase)
        times = selected["time"].astype("float64")
        owners = [
            selected[column].astype(object).where(selected[column].notna(), "<none>")
            for column in ("group", "entity")
        ]
        keys = [owner.values for owner in owners]
        end = self._end_s()
        if kind == "flow":
            previous = times.groupby(keys).shift(1)
            starts = selected["phase"].map(self._phase_starts())
            begin = pd.concat([previous, starts], axis=1).max(axis=1)
            begin = begin.where(begin.notna(), 0.0)
            begin = begin.where(begin < times, times)
            finish = times.where(times > _tolerance(0.0), 0.0)  # the t = 0 row: none
            if phase is not None:  # a flow row belongs to the phase of its interval
                wanted = {name for name, *_ in spans}
                inside = selected["phase"].astype(object).isin(wanted)
                finish = finish.where(inside, begin)
        else:
            following = times.groupby(keys).shift(-1)
            recorded = _merged([(s, e) for _, s, e, rec in self._phase_spans() if rec])
            limit = np.array([_span_end(recorded, t, end) for t in times.tolist()])
            finish = pd.Series(
                np.fmin(following.fillna(np.inf).to_numpy(), limit), index=times.index
            )
            begin = times
            if phase is not None:  # cut each hold to the selected phases
                lo, hi = begin.to_numpy(), finish.to_numpy()
                width = np.zeros(len(lo))
                for _, s, e, _ in spans:
                    width += np.clip(np.fmin(hi, e) - np.fmax(lo, s), 0.0, None)
                finish = begin + width
        width = (finish - begin).clip(lower=0.0)
        frame = pd.DataFrame(
            {
                "entity": selected["entity"].astype(object).to_numpy(),
                "value": values.astype("float64").to_numpy(),
                "begin": begin.to_numpy(dtype=float),
                "width": width.to_numpy(dtype=float),
            }
        )
        frame.attrs["entities"] = list(dict.fromkeys(frame["entity"].tolist()))
        frame.attrs["phases"] = spans
        kept = frame[frame["width"] > 0].reset_index(drop=True)
        kept.attrs.update(frame.attrs)
        return kept

    def _bin_edges(self, freq: Any) -> tuple[Any, list]:
        """Bin edges in seconds (``n + 1``) and the bin labels (``n``) covering the run."""
        import numpy as np

        from .timecfg import parse_duration

        pd = _pandas()
        end = self._end_s()
        start = self.start
        if start is None:
            try:
                width = parse_duration(freq)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"bins(freq={freq!r}): without time.start the bins are durations "
                    f"such as '1d' or '6h' ({exc})."
                ) from None
            if width <= 0:
                raise ValueError(f"bins(freq={freq!r}): the width must be positive.")
            count = max(int(math.ceil(end / width - 1e-9)), 1)
            edges = np.arange(count + 1, dtype=float) * width
            return edges, edges[:-1].tolist()
        from pandas.tseries.frequencies import to_offset

        offset: Any
        try:
            offset = to_offset(freq)
        except (TypeError, ValueError):
            try:
                offset = pd.Timedelta(parse_duration(freq), "s")
            except (TypeError, ValueError):
                raise ValueError(
                    f"bins(freq={freq!r}): use a pandas frequency such as '1D', '1h', "
                    "'W' or 'MS', or a duration such as '6h'."
                ) from None
        first = pd.Timestamp(start)
        beyond = first + pd.Timedelta(end, "s") + offset
        probe = pd.Series([0, 0], index=pd.DatetimeIndex([first, beyond]))
        stamps = probe.resample(offset, closed="left", label="left").size().index
        seconds = np.array([(stamp - first).total_seconds() for stamp in stamps])
        count = max(int(np.sum(seconds < end - _tolerance(end))), 1)
        return seconds[: count + 1], list(stamps[:count])

    def _phase_starts(self) -> dict[str, float]:
        """Phase name -> start offset (seconds), from the stored config."""
        phases = (self.config or {}).get("phases") or []
        if not phases:
            return {MAIN_PHASE: 0.0}
        try:
            return phase_starts(self.time, phases)
        except (ValueError, TypeError):  # a hand-edited config: no phase bounds
            return {}

    def log(self, name: str) -> pandas.DataFrame:
        """Return the event log *name*: one row per :meth:`ctx.log <mango.scenario.BuildContext.log>` call.

        Columns: ``run_id, time, [datetime], phase`` and the logged fields
        (in order of first appearance; empty where a row lacks a field).
        ``time`` is the clock when the row was logged (the step start in
        ``on_step``, the delivery time in message handlers).

        :param name: the log name (inside a kit qualified, e.g. ``"market.trades"``)
        :return: a ``pandas.DataFrame`` in logging order
        :raises KeyError: for names the run has no rows of (listing the known ones)

        Example::

            trades = result.log("trades")
            trades.groupby("buyer")["price"].agg(["count", "mean"])
        """
        found = self.logs.get(name)
        if found is None:
            known = list(self.logs)
            raise KeyError(
                f"No event log {name!r} in run {self.run_id}.{did_you_mean(str(name), known)} "
                f"Logs: {known} (a log exists once a row was logged with ctx.log)."
            )
        return found

    def agent(self, name: str) -> Agent:
        """Return the agent *name* of an in-process run (after the run).

        :raises ScenarioError: for loaded results (no world)
        :raises KeyError: for unknown names

        Example::

            result.agent("ev_0").soc_kwh
        """
        if self.world is None:
            raise ScenarioError(
                "Agents are only available for runs executed in this process; "
                "results loaded from disk have no world."
            )
        agents = self.world.agents
        if name not in agents:
            hint = did_you_mean(name, list(agents))
            raise KeyError(f"No agent {name!r} in the run.{hint}")
        return agents[name]

    def checksum(self) -> str:
        """SHA-256 of the recorded content, independent of the file format.

        It covers the time series (:func:`~mango.scenario.result.frame_checksum`)
        and, when the run
        has any, the event logs and metrics.  Two runs with the same
        scenario, params and seed have equal checksums.

        Example::

            assert scenario.run(seed=7).checksum() == scenario.run(seed=7).checksum()
        """
        base = frame_checksum(self.frame)
        if not self.logs and not self.metrics:
            return base
        digest = hashlib.sha256(f"timeseries {base}".encode())
        for name in sorted(self.logs):
            digest.update(f"\nlog {name} {log_checksum(self.logs[name])}".encode())
        if self.metrics:
            digest.update(f"\nmetrics {canonical_json(self.metrics)}".encode())
        return digest.hexdigest()

    # -- persistence ---------------------------------------------------------------

    def save(self, path: str | os.PathLike, format: str = "auto") -> Path:
        """Write the run folder; ``manifest.json`` is written last (atomically).

        Files: ``timeseries.parquet`` (``format="auto"`` with pyarrow, or
        ``"parquet"``) or ``timeseries.csv``, ``logs/<name>.parquet|csv``
        (one per event log), ``metrics.json`` (when the run has metrics),
        ``config.resolved.yaml``, ``outcomes.json`` and ``manifest.json``
        (run id, status, scenario, seed, checksum, format, rows, ``logs``
        (rows and field types per log), stats, entities, factories,
        provenance and any entries already in ``manifest``).  When the scenario has a
        communication factory, ``config.resolved.yaml`` starts with a
        comment naming it (``comm.kind`` is then not what ran).

        :param path: the run folder (created if missing)
        :param format: ``"auto"``, ``"parquet"`` or ``"csv"``
        :return: the folder path

        Example::

            result.save(Path("results") / result.run_id)
        """
        folder = Path(path)
        folder.mkdir(parents=True, exist_ok=True)
        fmt = _resolve_format(format)
        _write_timeseries(self.frame, folder, fmt)
        logs = _write_logs(self.logs, folder, fmt)
        metrics_file = folder / METRICS_FILE
        if self.metrics:
            write_json_atomic(metrics_file, self.metrics)
        else:
            metrics_file.unlink(missing_ok=True)
        header = ""
        factory = self.factories.get("communication")
        # a params-level factory ("radio:make (comm.factory)") is what comm says
        if factory and not factory.endswith("(comm.factory)"):
            header = (
                f"# comm.kind/comm.factory are not used: the communication simulation "
                f"is built by the scenario's factory {factory} (ctx.comm is passed in)\n"
            )
        (folder / CONFIG_FILE).write_text(
            header + yaml.safe_dump(self.config, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        (folder / OUTCOMES_FILE).write_text(
            json.dumps(to_plain(self.outcomes, default=str), indent=2),
            encoding="utf-8",
        )
        manifest = {
            "run_id": self.run_id,
            "status": "done",
            "scenario": self.scenario,
            "seed": self.seed,
            "stats": to_plain(self.stats, default=str),
            "entities": self.entities,
            "factories": dict(self.factories),
            "provenance": provenance(),
            "saved": utc_now(),
        }
        manifest.update(self.manifest)
        manifest.update(
            run_id=self.run_id,
            checksum=self.checksum(),
            format=fmt,
            rows=len(self.frame),
        )
        if logs:
            manifest["logs"] = logs
        else:
            manifest.pop("logs", None)
        if self.record_kinds:
            manifest["record_kinds"] = dict(self.record_kinds)
        self.manifest = manifest
        write_json_atomic(folder / MANIFEST_FILE, manifest)
        self.path = folder
        return folder


def load_run(path: str | os.PathLike, *, scenario: Any = None) -> RunResult:
    """Read a run folder written by :meth:`~mango.scenario.RunResult.save`.

    :param path: the run folder
    :param scenario: optionally the scenario (or params model) to rebuild
        typed ``params`` from the stored config; otherwise ``params`` is None
    :return: the :class:`RunResult` (without ``world``)
    :raises FileNotFoundError: if the folder or its ``manifest.json`` is
        missing (an unfinished run)

    Example::

        result = load_run("results/fleet/runs/3f2a9c1b7d4e")
        result.ts("import_kw").max()
    """
    folder = Path(path)
    if not folder.is_dir():
        raise FileNotFoundError(f"No run folder at {folder}.")
    manifest_file = folder / MANIFEST_FILE
    if not manifest_file.exists():
        raise FileNotFoundError(
            f"{folder} has no manifest.json: the run did not finish or the folder "
            "is not a run folder."
        )
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    config_file = folder / CONFIG_FILE
    config = (
        yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        if config_file.exists()
        else {}
    )
    outcomes_file = folder / OUTCOMES_FILE
    outcomes = (
        json.loads(outcomes_file.read_text(encoding="utf-8")) or {}
        if outcomes_file.exists()
        else {}
    )
    params = load_params(scenario, config) if scenario is not None else None
    time = (
        params.time
        if params is not None
        else TimeConfig.model_validate(config.get("time", {}))
    )
    tz = None if time.start is None else time.start.tzinfo
    frame, fmt = _read_timeseries(folder, manifest.get("format"), tz)
    logs = {
        name: _read_log(folder, name, entry, fmt, tz)
        for name, entry in (manifest.get("logs") or {}).items()
    }
    metrics_file = folder / METRICS_FILE
    metrics = (
        json.loads(metrics_file.read_text(encoding="utf-8")) or {}
        if metrics_file.exists()
        else {}
    )
    return RunResult(
        run_id=manifest.get("run_id", folder.name),
        scenario=manifest.get("scenario"),
        params=params,
        config=config,
        seed=manifest.get("seed", {}),
        time=time,
        frame=frame,
        outcomes=outcomes,
        stats=manifest.get("stats") or {},
        manifest=manifest,
        entities=manifest.get("entities") or {},
        path=folder,
        factories=manifest.get("factories"),
        logs=logs,
        metrics=metrics,
    )
