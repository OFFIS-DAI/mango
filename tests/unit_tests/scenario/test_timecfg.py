import datetime as dt
import random
import zoneinfo

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

import yaml  # noqa: E402
from pydantic import BaseModel, ValidationError  # noqa: E402

from mango.scenario import (  # noqa: E402
    Duration,
    TimeConfig,
    TimePoint,
    format_duration,
    parse_duration,
    parse_time_point,
)

# ----------------------------------------------------------------- durations


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("15min", 900),
        ("1h", 3600),
        ("2d", 172800),
        ("1h30min", 5400),
        ("1h 30min", 5400),
        (" 1 h 30 min ", 5400),
        ("90s", 90),
        ("500ms", 0.5),
        ("1w", 604800),
        ("2weeks", 1209600),
        ("3 days", 259200),
        ("1hour", 3600),
        ("2hrs", 7200),
        ("1hr", 3600),
        ("10mins", 600),
        ("1minute", 60),
        ("5sec", 5),
        ("1second", 1),
        ("2secs", 2),
        ("1.5h", 5400),
        (".5min", 30),
        ("1e3s", 1000),
        ("1H30MIN", 5400),
        ("900", 900),
        ("12.5", 12.5),
        ("0", 0),
    ],
)
def test_parse_duration_strings(text, seconds):
    assert parse_duration(text) == pytest.approx(seconds, abs=0)


def test_parse_duration_numbers_and_timedeltas():
    assert parse_duration(900) == 900.0
    assert isinstance(parse_duration(900), float)
    assert parse_duration(0.25) == 0.25
    assert parse_duration(dt.timedelta(minutes=15)) == 900.0


@pytest.mark.parametrize(
    ("value", "match"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("-5", "negative"),
        ("-1h", "negative"),
        (-1, "negative"),
        (dt.timedelta(seconds=-1), "negative"),
        ("5m", "ambiguous"),
        ("5x", "unknown unit 'x'"),
        ("abc", "expected seconds"),
        ("1h-", "expected seconds"),
        ("h1", "expected seconds"),
        ("1h 30", "expected seconds"),
        (True, "expected seconds"),
        (None, "NoneType"),
        ([1], "list"),
        (float("nan"), "finite"),
        (float("inf"), "finite"),
    ],
)
def test_parse_duration_rejects_invalid(value, match):
    with pytest.raises(ValueError, match=match):
        parse_duration(value)


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "0s"),
        (1, "1s"),
        (90, "1min30s"),
        (900, "15min"),
        (3600, "1h"),
        (5400, "1h30min"),
        (86400, "1d"),
        (8 * 86400, "8d"),
        (604800, "1w"),
        (1209600, "2w"),
        (0.5, "500ms"),
        (0.1, "100ms"),
        (3600.25, "1h250ms"),
        (0.0001, "0.0001s"),
    ],
)
def test_format_duration_is_canonical(seconds, text):
    assert format_duration(seconds) == text
    assert parse_duration(text) == seconds


def test_format_duration_normalizes_strings_and_rejects_negative():
    assert format_duration("90min") == "1h30min"
    assert format_duration("900") == "15min"
    with pytest.raises(ValueError):
        format_duration(-1)


def test_format_parse_round_trip_random_values():
    rng = random.Random(7)
    values = [rng.uniform(0, 10**7) for _ in range(300)]
    values += [round(v, 3) for v in values]
    values += [float(rng.randrange(0, 10**9)) for _ in range(100)]
    for value in values:
        assert parse_duration(format_duration(value)) == value


class _Cfg(BaseModel):
    every: Duration = 3600.0
    maybe: Duration | None = None


def test_duration_field_validates_and_serializes():
    cfg = _Cfg(every="900", maybe="1h30min")
    assert cfg.every == 900.0 and cfg.maybe == 5400.0
    assert cfg.model_dump() == {"every": "15min", "maybe": "1h30min"}
    assert cfg.model_dump(mode="json") == {"every": "15min", "maybe": "1h30min"}
    assert _Cfg.model_validate(cfg.model_dump()) == cfg
    assert _Cfg().model_dump()["every"] == "1h"
    with pytest.raises(ValidationError, match="ambiguous"):
        _Cfg(every="5m")


def test_duration_json_schema_allows_number_or_string():
    schema = _Cfg.model_json_schema()["properties"]["every"]
    types_ = {option["type"] for option in schema["anyOf"]}
    assert types_ == {"number", "string"}


# ----------------------------------------------------------------- time points

START = dt.datetime(2024, 6, 1)


@pytest.mark.parametrize(
    ("value", "offset"),
    [
        (0, 0.0),
        (3600, 3600.0),
        (90.5, 90.5),
        ("12h", 43200.0),
        ("1d6h", 108000.0),
        ("2024-06-01T12:00", 43200.0),
        ("2024-06-02", 86400.0),
        (dt.datetime(2024, 6, 1, 6), 21600.0),
        (dt.date(2024, 6, 3), 172800.0),
        (dt.timedelta(hours=1), 3600.0),
    ],
)
def test_parse_time_point(value, offset):
    assert parse_time_point(value, START) == offset


def test_parse_time_point_offsets_need_no_start():
    assert parse_time_point("12h", None) == 43200.0
    assert parse_time_point(60) == 60.0


@pytest.mark.parametrize("value", ["2024-06-01T12:00", dt.datetime(2024, 6, 1)])
def test_parse_time_point_absolute_needs_start(value):
    with pytest.raises(ValueError, match="absolute times need time.start"):
        parse_time_point(value, None)


def test_parse_time_point_errors():
    with pytest.raises(ValueError, match="before time.start"):
        parse_time_point("2024-05-31T23:00", START)
    with pytest.raises(ValueError, match="negative"):
        parse_time_point(-5, START)
    with pytest.raises(ValueError, match="Invalid time point 'noon'"):
        parse_time_point("noon", START)
    with pytest.raises(ValueError, match="timezone-aware and a naive"):
        parse_time_point("2024-06-01T12:00Z", START)


def test_parse_time_point_timezone_aware():
    start = dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc)
    assert parse_time_point("2024-06-01T01:00Z", start) == 3600.0
    assert parse_time_point("2024-06-01T03:00+02:00", start) == 3600.0


def test_named_time_zones_are_saved_and_read_back():
    berlin = zoneinfo.ZoneInfo("Europe/Berlin")
    time = TimeConfig(start=dt.datetime(2024, 3, 31, tzinfo=berlin), horizon="5h")
    dumped = time.model_dump(mode="json")
    assert dumped["start"] == "2024-03-31T00:00:00+01:00[Europe/Berlin]"
    loaded = TimeConfig.model_validate(dumped)
    assert loaded.start.tzinfo == berlin and loaded == time
    # elapsed time across the DST switch is real time
    assert loaded.end == dt.datetime(2024, 3, 31, 6, tzinfo=berlin)
    assert parse_time_point("2024-03-31T03:00[Europe/Berlin]", loaded.start) == 7200.0
    assert TimeConfig(start="2024-03-31T00:00[Europe/Berlin]").start == time.start
    # a fixed offset is a different start: it does not follow the DST rules
    fixed = TimeConfig(start="2024-03-31T00:00+01:00", horizon="5h")
    assert fixed.model_dump(mode="json")["start"] == "2024-03-31T00:00:00+01:00"
    assert fixed.start.tzinfo != berlin


def test_time_zones_that_cannot_be_saved_are_rejected():
    class Opaque(dt.tzinfo):  # DST rules, but no name to save them by
        def utcoffset(self, value):
            return None if value is None else dt.timedelta(hours=1 + (value.month > 3))

        def dst(self, value):
            return dt.timedelta(0)

    with pytest.raises(ValidationError, match="cannot be saved and replayed"):
        TimeConfig(start=dt.datetime(2024, 1, 1, tzinfo=Opaque()))
    with pytest.raises(ValidationError, match="unknown time zone 'Mars/Base'"):
        TimeConfig(start="2024-01-01T00:00[Mars/Base]")


class _Event(BaseModel):
    at: TimePoint


def test_time_point_field():
    assert _Event(at=3600).at == 3600.0
    assert _Event(at="12h").at == 43200.0
    assert _Event(at="2024-06-01T12:00").at == dt.datetime(2024, 6, 1, 12)
    assert _Event(at=dt.date(2024, 6, 2)).at == dt.datetime(2024, 6, 2)
    assert _Event(at="12h").model_dump() == {"at": "12h"}
    assert _Event(at="2024-06-01T12:00").model_dump() == {"at": "2024-06-01T12:00:00"}
    for event in (_Event(at="12h"), _Event(at="2024-06-01T12:00")):
        assert _Event.model_validate(event.model_dump()) == event
    with pytest.raises(ValidationError, match="Invalid time point"):
        _Event(at="tomorrow")


# ----------------------------------------------------------------- TimeConfig


def test_time_config_defaults():
    time = TimeConfig()
    assert time.start is None and time.end is None
    assert time.horizon_s == 86400.0 and time.step_s == 3600.0
    assert time.step_h == 1.0 and time.n_steps == 24
    assert not time.is_event_driven
    assert time.model_dump() == {"start": None, "horizon": "1d", "step": "1h"}


def test_time_config_from_strings():
    time = TimeConfig(start="2024-06-01T00:00", horizon="2d", step="15min")
    assert time.start == dt.datetime(2024, 6, 1)
    assert time.horizon_s == 172800.0
    assert time.step_s == 900.0 and time.step_h == 0.25
    assert time.n_steps == 192
    assert time.end == dt.datetime(2024, 6, 3)
    assert time.offset("2024-06-01T12:00") == 43200.0
    assert time.offset("6h") == 21600.0
    dumped = time.model_dump(mode="json")
    assert dumped == {"start": "2024-06-01T00:00:00", "horizon": "2d", "step": "15min"}
    assert TimeConfig.model_validate(dumped) == time


def test_time_config_from_yaml():
    data = yaml.safe_load("{start: 2024-06-01T00:00, horizon: 2d, step: 15min}")
    assert TimeConfig.model_validate(data).n_steps == 192
    data = yaml.safe_load("{start: 2024-06-01, horizon: 86400, step: 900}")
    time = TimeConfig.model_validate(data)
    assert time.start == dt.datetime(2024, 6, 1) and time.n_steps == 96


def test_time_config_horizon_must_be_multiple_of_step():
    with pytest.raises(ValidationError) as info:
        TimeConfig(horizon="1d", step="7min")
    message = str(info.value)
    assert "horizon 1d is not a multiple of step 7min" in message
    assert "1d2min or 23h55min" in message
    with pytest.raises(ValidationError, match="not a multiple"):
        TimeConfig(horizon="1h", step="2h")
    assert TimeConfig(horizon="1h", step="1h").n_steps == 1
    assert TimeConfig(horizon=0.3, step=0.1).n_steps == 3


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"horizon": 0}, "horizon must be > 0"),
        ({"step": 0}, "step must be > 0"),
        ({"step": "-1h"}, "negative"),
        ({"step": "fast"}, "Invalid duration"),
        ({"stepp": "1h"}, "Extra inputs are not permitted"),
    ],
)
def test_time_config_rejects_invalid(kwargs, match):
    with pytest.raises(ValidationError, match=match):
        TimeConfig(**kwargs)


def test_time_config_event_mode():
    time = TimeConfig(start="2024-06-01T00:00", horizon="1d", step="event")
    assert time.is_event_driven
    assert time.step == "event"
    assert time.step_s is None and time.step_h is None and time.n_steps is None
    assert time.horizon_s == 86400.0
    assert time.model_dump()["step"] == "event"
    assert TimeConfig.model_validate(time.model_dump()) == time
    assert TimeConfig(step=" Event ").is_event_driven
    assert TimeConfig(horizon="7min", step="event").horizon_s == 420.0


def test_time_config_offset_without_start():
    time = TimeConfig()
    assert time.offset("2h") == 7200.0
    with pytest.raises(ValueError, match="absolute times need time.start"):
        time.offset("2024-06-01T00:00")


def test_time_config_schema_mentions_event():
    schema = TimeConfig.model_json_schema()
    step = schema["properties"]["step"]
    assert "event" in step["anyOf"][1]["examples"]
    assert schema["additionalProperties"] is False
