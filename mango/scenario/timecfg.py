"""
Durations, time points and the time configuration of a scenario.

Durations are written as seconds or as strings like ``"15min"``, ``"1h30min"``
or ``"2d"``; they always validate to float seconds and serialize to the
canonical string.  A :class:`TimeConfig` fixes the calendar start, the horizon
and the step of a run.

Example::

    from mango.scenario import TimeConfig, format_duration, parse_duration

    parse_duration("1h30min")    # 5400.0
    format_duration(900)         # "15min"
    time = TimeConfig(start="2024-06-01T00:00", horizon="2d", step="15min")
    time.n_steps                 # 192
    time.offset("2024-06-01T12:00")   # 43200.0
"""

from __future__ import annotations

import datetime as _dt
import math
import numbers
import re
import zoneinfo
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    PlainSerializer,
    WithJsonSchema,
    field_serializer,
    field_validator,
    model_validator,
)

__all__ = [
    "Duration",
    "TimePoint",
    "TimeConfig",
    "parse_duration",
    "format_duration",
    "parse_time_point",
    "format_datetime",
]

_EPS = 1e-9


def _tolerance(t: float) -> float:
    """Relative tolerance for comparing offsets (absolute ``_EPS`` near zero)."""
    return _EPS * max(1.0, abs(t))


def _datetime_at(time: TimeConfig, offset: float) -> _dt.datetime | None:
    """Calendar datetime *offset* seconds after ``time.start`` (None without it)."""
    start = time.start
    if start is None:
        return None
    if start.tzinfo is None:
        return start + _dt.timedelta(seconds=offset)
    utc = start.astimezone(_dt.timezone.utc) + _dt.timedelta(seconds=offset)
    return utc.astimezone(start.tzinfo)


_UNIT_SECONDS: dict[str, float] = {
    "ms": 0.001,
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "second": 1.0,
    "seconds": 1.0,
    "min": 60.0,
    "mins": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hrs": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
    "d": 86400.0,
    "day": 86400.0,
    "days": 86400.0,
    "w": 604800.0,
    "week": 604800.0,
    "weeks": 604800.0,
}
_NUMBER = r"(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?"
_PART = re.compile(rf"\s*({_NUMBER})\s*([a-z]+)\s*", re.IGNORECASE)
_PLAIN_NUMBER = re.compile(rf"\s*[+-]?{_NUMBER}\s*", re.IGNORECASE)
_DURATION_HINT = (
    "expected seconds or a string like '90s', '15min', '1h30min', '2d' "
    "(units: ms, s, min, h, d, w)"
)
# Canonical units, largest first; weeks are only used for whole weeks.
_FORMAT_UNITS_MS = (("d", 86_400_000), ("h", 3_600_000), ("min", 60_000), ("s", 1000))


def _check_seconds(seconds: float, value: Any) -> float:
    if math.isnan(seconds) or math.isinf(seconds):
        raise ValueError(f"Invalid duration {value!r}: must be finite.")
    if seconds < 0:
        raise ValueError(f"Invalid duration {value!r}: must not be negative.")
    return seconds


def parse_duration(value: Any) -> float:
    """Parse a duration to float seconds.

    Accepts numbers (seconds), ``datetime.timedelta`` and strings made of
    one or more ``<number><unit>`` parts with optional spaces, e.g. ``"15min"``,
    ``"1h30min"``, ``"1.5 h"``, ``"500ms"``.  Units: ``ms``; ``s``/``sec``/
    ``second(s)``; ``min``/``minute(s)``; ``h``/``hr``/``hour(s)``;
    ``d``/``day(s)``; ``w``/``week(s)``.  A bare ``m`` is rejected as
    ambiguous (minutes or milliseconds?).

    :param value: the duration
    :return: the duration in seconds (``>= 0``)
    :raises ValueError: for negative, empty, non-finite or malformed input

    Example::

        parse_duration("1h30min")   # 5400.0
        parse_duration(900)         # 900.0
    """
    if isinstance(value, bool):
        raise ValueError(f"Invalid duration {value!r}: {_DURATION_HINT}.")
    if isinstance(value, _dt.timedelta):
        return _check_seconds(value.total_seconds(), value)
    if isinstance(value, numbers.Real):
        return _check_seconds(float(value), value)
    if not isinstance(value, str):
        raise ValueError(
            f"Invalid duration {value!r} of type {type(value).__name__}: "
            f"{_DURATION_HINT}."
        )
    text = value.strip()
    if not text:
        raise ValueError(f"Invalid duration {value!r}: empty; {_DURATION_HINT}.")
    if _PLAIN_NUMBER.fullmatch(text):
        return _check_seconds(float(text), value)
    if text.startswith("-"):
        raise ValueError(f"Invalid duration {value!r}: must not be negative.")
    total = 0.0
    position = 0
    for match in _PART.finditer(text):
        if match.start() != position:
            break
        number, unit = match.group(1), match.group(2).lower()
        if unit not in _UNIT_SECONDS:
            if unit == "m":
                raise ValueError(
                    f"Invalid duration {value!r}: the unit 'm' is ambiguous; "
                    "write 'min' for minutes or 'ms' for milliseconds."
                )
            raise ValueError(
                f"Invalid duration {value!r}: unknown unit {unit!r}; {_DURATION_HINT}."
            )
        if unit == "ms":
            total += float(number) / 1000.0
        else:
            total += float(number) * _UNIT_SECONDS[unit]
        position = match.end()
    if position != len(text):
        raise ValueError(f"Invalid duration {value!r}: {_DURATION_HINT}.")
    return _check_seconds(total, value)


def _format_ms(total_ms: int) -> str:
    if total_ms == 0:
        return "0s"
    if total_ms % 604_800_000 == 0:
        return f"{total_ms // 604_800_000}w"
    parts = []
    rest = total_ms
    for unit, size in _FORMAT_UNITS_MS:
        count, rest = divmod(rest, size)
        if count:
            parts.append(f"{count}{unit}")
    if rest:
        parts.append(f"{rest}ms")
    return "".join(parts)


def format_duration(seconds: float) -> str:
    """Format seconds as the canonical duration string.

    Whole milliseconds are written with the units ``d``, ``h``, ``min``,
    ``s`` and ``ms`` (largest first, zero parts omitted), or as whole weeks
    (``"2w"``); other values fall back to exact seconds (``"0.0001s"``).
    :func:`parse_duration` reads every result back to the same float.

    :param seconds: a non-negative, finite number of seconds
    :return: the canonical string

    Example::

        format_duration(900)     # "15min"
        format_duration(86400)   # "1d"
        format_duration(5400)    # "1h30min"
    """
    seconds = parse_duration(seconds)
    total_ms = round(seconds * 1000)
    if abs(total_ms) < 2**63:
        text = _format_ms(total_ms)
        if parse_duration(text) == seconds:
            return text
    return f"{seconds!r}s"


def _duration_json_schema() -> dict:
    return {
        "anyOf": [
            {"type": "number", "minimum": 0},
            {"type": "string", "examples": ["15min", "1h30min", "2d"]},
        ],
        "description": "seconds, or a duration string like '15min' or '1h30min'",
    }


#: Duration field type: accepts seconds or strings like ``"1h30min"``,
#: validates to float seconds and serializes to the canonical string.
Duration = Annotated[
    float,
    BeforeValidator(parse_duration),
    PlainSerializer(format_duration, return_type=str),
    WithJsonSchema(_duration_json_schema()),
]


_ZONE_SUFFIX = re.compile(r"^(?P<iso>[^\[\]]+)\[(?P<zone>[^\[\]]+)\]$")


def _time_zone(key: str) -> _dt.tzinfo:
    try:
        return zoneinfo.ZoneInfo(key)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise ValueError(
            f"unknown time zone {key!r}; use an IANA name such as 'Europe/Berlin' "
            "(on Windows install the 'tzdata' package)"
        ) from None


def _parse_datetime(value: Any) -> _dt.datetime | None:
    """Parse datetimes, dates and ISO strings (``None`` when *value* is none of them).

    An ISO string may name its time zone in brackets (RFC 9557), e.g.
    ``"2024-03-31T00:00[Europe/Berlin]"`` or
    ``"2024-03-31T00:00+01:00[Europe/Berlin]"``; an unknown zone raises
    ``ValueError``.
    """
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day)
    if not isinstance(value, str):
        return None
    text = value.strip()
    zone = None
    match = _ZONE_SUFFIX.match(text)
    if match is not None:
        text, zone = match["iso"].strip(), match["zone"].strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if zone is None:
        return parsed
    tz = _time_zone(zone)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def format_datetime(value: _dt.datetime) -> str:
    """Write a datetime as ISO 8601, keeping a named time zone.

    A ``zoneinfo.ZoneInfo`` zone is appended in brackets (RFC 9557), so the
    text reads back to the same zone and daylight saving rules; naive and
    fixed-offset datetimes give plain ``isoformat()``.

    :param value: the datetime
    :return: e.g. ``"2024-06-01T00:00:00"`` or
        ``"2024-03-31T00:00:00+01:00[Europe/Berlin]"``

    Example::

        format_datetime(datetime(2024, 3, 31, tzinfo=ZoneInfo("Europe/Berlin")))
        # "2024-03-31T00:00:00+01:00[Europe/Berlin]"
    """
    text = value.isoformat()
    key = getattr(value.tzinfo, "key", None)
    if isinstance(value.tzinfo, zoneinfo.ZoneInfo) and key:
        text += f"[{key}]"
    return text


def _coerce_start(value: Any) -> Any:
    """A ``start`` field's input: a date is midnight, ``"...[Zone]"`` is parsed."""
    if isinstance(value, _dt.date) and not isinstance(value, _dt.datetime):
        return _dt.datetime(value.year, value.month, value.day)
    if isinstance(value, str) and _ZONE_SUFFIX.match(value.strip()):
        return _parse_datetime(value)
    return value


def _dump_datetime(value: _dt.datetime | None) -> str | None:
    return None if value is None else format_datetime(value)


def _check_time_zone(value: _dt.datetime, what: str) -> _dt.datetime:
    """Keep naive datetimes and named zones; normalise fixed offsets to ``timezone``.

    Other zones (e.g. ``dateutil``/``pytz`` zones with DST rules) cannot be
    written so that they read back with the same rules, so they are rejected.
    """
    tz = value.tzinfo
    if tz is None or isinstance(tz, _dt.timezone):
        return value
    if isinstance(tz, zoneinfo.ZoneInfo) and getattr(tz, "key", None):
        return value
    try:
        offset = tz.utcoffset(None)  # fixed offsets do not depend on the date
    except Exception:
        offset = None
    if isinstance(offset, _dt.timedelta):
        return value.replace(tzinfo=_dt.timezone(offset))
    raise ValueError(
        f"{what} has the time zone {tz!r}, which cannot be saved and replayed; use "
        "zoneinfo.ZoneInfo('Europe/Berlin'), a fixed offset, or a string such as "
        "'2024-06-01T00:00[Europe/Berlin]'"
    )


def _utc_delta(later: _dt.datetime, earlier: _dt.datetime) -> float:
    """Elapsed seconds between two datetimes (real time for aware ones, across DST)."""
    if later.tzinfo is not None and earlier.tzinfo is not None:
        later = later.astimezone(_dt.timezone.utc)
        earlier = earlier.astimezone(_dt.timezone.utc)
    return (later - earlier).total_seconds()


def _coerce_time_point(value: Any) -> float | _dt.datetime:
    if isinstance(value, _dt.datetime | _dt.date):
        return _parse_datetime(value)
    if not isinstance(value, str):
        return parse_duration(value)
    try:
        return parse_duration(value)
    except ValueError as duration_error:
        parsed = _parse_datetime(value)
        if parsed is None:
            raise ValueError(
                f"Invalid time point {value!r}: expected an offset from the start "
                "(seconds or a duration like '12h') or an ISO datetime like "
                "'2024-06-01T12:00'."
            ) from duration_error
        return parsed


def _format_time_point(value: float | _dt.datetime) -> str:
    if isinstance(value, _dt.datetime):
        return format_datetime(value)
    return format_duration(value)


#: Time point field type: an offset from the scenario start (seconds or a
#: duration string, validated to float seconds) or an absolute datetime (ISO
#: string or datetime).  Resolve it with :meth:`TimeConfig.offset`.
TimePoint = Annotated[
    float | _dt.datetime,
    BeforeValidator(_coerce_time_point),
    PlainSerializer(_format_time_point, return_type=str),
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "number", "minimum": 0},
                {"type": "string", "examples": ["12h", "2024-06-01T12:00"]},
            ],
            "description": "offset from the start (duration) or an ISO datetime",
        }
    ),
]


def parse_time_point(value: Any, start: _dt.datetime | None = None) -> float:
    """Resolve a time point to seconds since the scenario start.

    Numbers and duration strings are offsets; ``datetime``/``date`` objects
    and ISO strings are absolute and need *start*.

    :param value: the time point, e.g. ``3600``, ``"12h"``,
        ``"2024-06-01T12:00"``
    :param start: the scenario start (``time.start``), or None
    :return: the offset in seconds (``>= 0``)
    :raises ValueError: for malformed values, absolute times without *start*,
        mixed naive/aware datetimes and times before *start*

    Example::

        start = datetime(2024, 6, 1)
        parse_time_point("12h", start)                # 43200.0
        parse_time_point("2024-06-02T00:00", start)   # 86400.0
    """
    point = _coerce_time_point(value)
    if not isinstance(point, _dt.datetime):
        return point
    shown = format_datetime(point)
    if start is None:
        raise ValueError(
            f"Invalid time point {shown}: absolute times need time.start; set "
            "time.start or use an offset such as '12h'."
        )
    if (point.tzinfo is None) != (start.tzinfo is None):
        raise ValueError(
            f"Invalid time point {shown}: cannot compare a timezone-aware and a "
            f"naive datetime (time.start is {format_datetime(start)})."
        )
    offset = _utc_delta(point, start)
    if offset < 0:
        raise ValueError(
            f"Invalid time point {shown}: it lies before time.start "
            f"({format_datetime(start)})."
        )
    return offset


def _parse_step(value: Any) -> float | str:
    if isinstance(value, str) and value.strip().lower() == "event":
        return "event"
    return parse_duration(value)


def _format_step(value: float | str) -> str:
    return value if isinstance(value, str) else format_duration(value)


#: Step field type: a duration or the literal ``"event"`` (event-driven).
Step = Annotated[
    float | Literal["event"],
    BeforeValidator(_parse_step),
    PlainSerializer(_format_step, return_type=str),
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "number", "exclusiveMinimum": 0},
                {"type": "string", "examples": ["15min", "1h", "event"]},
            ],
            "description": "fixed step (duration) or 'event' for event-driven runs",
        }
    ),
]


class TimeConfig(BaseModel, extra="forbid"):
    """Calendar start, horizon and step of a scenario run.

    :param start: calendar datetime of simulation time 0 (optional; needed
        for absolute time points and calendar columns)
    :param horizon: run length (duration), must be > 0
    :param step: fixed step (duration) that divides the horizon exactly, or
        ``"event"`` to jump from event to event

    Example::

        time = TimeConfig(start="2024-06-01T00:00", horizon="1d", step="15min")
        time.step_s, time.n_steps, time.end   # 900.0, 96, datetime(2024, 6, 2)
        TimeConfig(horizon="1d", step="event").is_event_driven   # True
    """

    start: _dt.datetime | None = None
    horizon: Duration = 86400.0
    step: Step = 3600.0

    @field_validator("start", mode="before")
    @classmethod
    def _date_to_datetime(cls, value: Any) -> Any:
        return _coerce_start(value)

    @field_validator("start")
    @classmethod
    def _check_start_zone(cls, value: _dt.datetime | None) -> _dt.datetime | None:
        return None if value is None else _check_time_zone(value, "time.start")

    @field_serializer("start", when_used="json")
    def _dump_start(self, value: _dt.datetime | None) -> str | None:
        return _dump_datetime(value)

    @model_validator(mode="after")
    def _check_grid(self) -> TimeConfig:
        if self.horizon <= 0:
            raise ValueError("time.horizon must be > 0.")
        if self.step == "event":
            return self
        if self.step <= 0:
            raise ValueError("time.step must be > 0 (or 'event').")
        steps = self.horizon / self.step
        whole = round(steps)
        if whole < 1 or not math.isclose(steps, whole, rel_tol=1e-9, abs_tol=0.0):
            lower = max(1, math.floor(steps)) * self.step
            upper = max(1, math.ceil(steps)) * self.step
            options = sorted({format_duration(lower), format_duration(upper)})
            raise ValueError(
                f"horizon {format_duration(self.horizon)} is not a multiple of step "
                f"{format_duration(self.step)}; use a horizon of "
                f"{' or '.join(options)}, or a step that divides the horizon."
            )
        return self

    @property
    def is_event_driven(self) -> bool:
        """True when ``step == "event"``."""
        return self.step == "event"

    @property
    def horizon_s(self) -> float:
        """The horizon in seconds."""
        return float(self.horizon)

    @property
    def step_s(self) -> float | None:
        """The fixed step in seconds (None in event mode)."""
        return None if self.is_event_driven else float(self.step)

    @property
    def step_h(self) -> float | None:
        """The fixed step in hours (None in event mode), e.g. for energy sums."""
        step_s = self.step_s
        return None if step_s is None else step_s / 3600.0

    @property
    def n_steps(self) -> int | None:
        """Number of fixed steps to the horizon (None in event mode)."""
        step_s = self.step_s
        return None if step_s is None else round(self.horizon_s / step_s)

    @property
    def end(self) -> _dt.datetime | None:
        """Calendar datetime of the horizon (None without ``start``)."""
        return _datetime_at(self, self.horizon_s)

    def offset(self, time_point: Any) -> float:
        """Resolve *time_point* to seconds since ``start`` (see :func:`parse_time_point`).

        :param time_point: an offset (seconds, ``"12h"``) or an absolute
            datetime/ISO string
        :return: the offset in seconds

        Example::

            TimeConfig(start="2024-06-01T00:00").offset("2024-06-01T06:00")  # 21600.0
        """
        return parse_time_point(time_point, self.start)
