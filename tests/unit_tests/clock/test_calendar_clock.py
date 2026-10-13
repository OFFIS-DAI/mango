import datetime as dt

import pytest

from mango import create_world
from mango.util.clock import AsyncioClock, Clock, ExternalClock


def test_clock_without_calendar():
    clock = ExternalClock(start_time=5)
    assert clock.start_time == 5
    assert clock.start_datetime is None
    assert clock.has_calendar is False
    with pytest.raises(RuntimeError, match="start_datetime"):
        _ = clock.datetime
    with pytest.raises(RuntimeError):
        clock.to_datetime(10)
    with pytest.raises(RuntimeError):
        clock.to_time(dt.datetime(2024, 1, 1))


def test_naive_calendar_conversions():
    start = dt.datetime(2024, 6, 1)
    clock = ExternalClock(start_datetime=start)
    assert clock.has_calendar
    assert clock.start_datetime == start
    assert clock.datetime == start
    clock.set_time(3600)
    assert clock.datetime == dt.datetime(2024, 6, 1, 1)
    assert clock.to_datetime(86400) == dt.datetime(2024, 6, 2)
    assert clock.to_time(dt.datetime(2024, 6, 1, 0, 30)) == 1800.0


def test_start_time_offset():
    clock = ExternalClock(start_time=100, start_datetime="2024-06-01T00:00")
    assert clock.start_time == 100
    assert clock.datetime == dt.datetime(2024, 6, 1)
    assert clock.to_datetime(160) == dt.datetime(2024, 6, 1, 0, 1)
    assert clock.to_time("2024-06-01T00:01:00") == 160.0
    clock.set_time(220)
    assert clock.start_time == 100
    assert clock.datetime == dt.datetime(2024, 6, 1, 0, 2)


def test_str_start_datetime_is_parsed():
    clock = ExternalClock(start_datetime="2024-06-01T12:00")
    assert clock.start_datetime == dt.datetime(2024, 6, 1, 12)


def test_tz_aware_kept_as_given():
    tz = dt.timezone(dt.timedelta(hours=2))
    start = dt.datetime(2024, 6, 1, tzinfo=tz)
    clock = ExternalClock(start_datetime=start)
    clock.set_time(60)
    assert clock.datetime.tzinfo == tz
    assert clock.datetime == dt.datetime(2024, 6, 1, 0, 1, tzinfo=tz)
    # an aware datetime in another zone converts by instant
    assert clock.to_time(dt.datetime(2024, 5, 31, 22, 0, tzinfo=dt.timezone.utc)) == 0
    assert clock.to_time("2024-06-01T00:00:30+02:00") == 30.0


def test_tz_aware_str():
    clock = ExternalClock(start_datetime="2024-06-01T00:00:00+00:00")
    assert clock.start_datetime.tzinfo is not None
    zulu = ExternalClock(start_datetime="2024-06-01T00:00:00Z")
    assert zulu.start_datetime == clock.start_datetime
    assert zulu.to_time("2024-06-01T00:01:00Z") == 60.0


def test_mixing_naive_and_aware_raises():
    clock = ExternalClock(start_datetime=dt.datetime(2024, 6, 1))
    with pytest.raises(TypeError, match="naive"):
        clock.to_time(dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc))


def test_invalid_start_datetime_type():
    with pytest.raises(TypeError):
        ExternalClock(start_datetime=12345)
    with pytest.raises(ValueError):
        ExternalClock(start_datetime="not a date")


def test_base_and_asyncio_clock_datetime():
    class _Clock(Clock):
        def sleep(self, t):
            return None

    with pytest.raises(NotImplementedError):
        _ = _Clock().datetime
    now = AsyncioClock().datetime
    assert now.tzinfo == dt.timezone.utc
    assert abs((dt.datetime.now(dt.timezone.utc) - now).total_seconds()) < 5


@pytest.mark.asyncio
async def test_world_calendar():
    world = create_world(start_datetime="2024-06-01T00:00")
    assert world.clock.has_calendar
    async with world:
        await world.step(step_size_s=900)
        assert world.datetime == dt.datetime(2024, 6, 1, 0, 15)


def test_world_without_calendar_has_no_datetime():
    world = create_world()
    assert not world.clock.has_calendar
    with pytest.raises(RuntimeError):
        _ = world.datetime


def _berlin():
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        return zoneinfo.ZoneInfo("Europe/Berlin")
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("no tz database available")


def test_dst_spring_forward_uses_elapsed_seconds():
    berlin = _berlin()
    clock = ExternalClock(start_datetime=dt.datetime(2024, 3, 31, 0, 0, tzinfo=berlin))
    hour = 3600.0
    local = [clock.to_datetime(h * hour) for h in range(5)]
    # 02:00 does not exist on that day in Berlin: 01:00 CET is followed by 03:00 CEST
    assert [d.hour for d in local] == [0, 1, 3, 4, 5]
    assert all(d.tzinfo is berlin for d in local)
    instants = [d.astimezone(dt.timezone.utc) for d in local]
    assert len(set(instants)) == 5
    for h, d in enumerate(local):
        assert clock.to_time(d) == h * hour
        assert clock.to_time(d.astimezone(dt.timezone.utc)) == h * hour


def test_dst_fall_back_keeps_both_ambiguous_hours():
    berlin = _berlin()
    clock = ExternalClock(start_datetime=dt.datetime(2024, 10, 27, 0, 0, tzinfo=berlin))
    local = [clock.to_datetime(h * 3600.0) for h in range(5)]
    # 02:00-03:00 happens twice (first CEST, then CET)
    assert [(d.hour, d.fold) for d in local] == [(0, 0), (1, 0), (2, 0), (2, 1), (3, 0)]
    assert [clock.to_time(d) for d in local] == [h * 3600.0 for h in range(5)]


def test_naive_start_uses_wall_clock_arithmetic():
    clock = ExternalClock(start_datetime=dt.datetime(2024, 3, 31, 0, 0))
    assert clock.to_datetime(2 * 3600) == dt.datetime(2024, 3, 31, 2, 0)
    assert clock.to_time(dt.datetime(2024, 3, 31, 2, 0)) == 2 * 3600
