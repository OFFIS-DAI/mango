import asyncio
import datetime as _dt
import heapq
import itertools
import time
from abc import ABC, abstractmethod


class Clock(ABC):
    """
    Abstract class for clocks that can be used in mango
    """

    @property
    def time(self) -> float:
        """
        Returns the current time of the clock
        """
        raise NotImplementedError

    @property
    def datetime(self) -> _dt.datetime:
        """
        Returns the current time of the clock as calendar date and time
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not provide calendar time."
        )

    @abstractmethod
    def sleep(self, t: float) -> asyncio.Future:
        raise NotImplementedError


class AsyncioClock(Clock):
    """
    The AsyncioClock
    """

    def __init__(self):
        pass

    @property
    def time(self) -> float:
        """
        Current time using the time module
        """
        return time.time()

    @property
    def datetime(self) -> _dt.datetime:
        """
        Current wall-clock time as a timezone-aware UTC datetime
        """
        return _dt.datetime.fromtimestamp(time.time(), tz=_dt.timezone.utc)

    def sleep(self, t) -> asyncio.Future:
        """
        Sleeping via asyncio sleep
        """
        return asyncio.sleep(t)


def _parse_datetime(value: "_dt.datetime | str", what: str) -> _dt.datetime:
    if isinstance(value, str):
        # Python < 3.11 does not accept the "Z" (UTC) suffix
        if value.endswith(("Z", "z")):
            value = value[:-1] + "+00:00"
        return _dt.datetime.fromisoformat(value)
    if isinstance(value, _dt.datetime):
        return value
    raise TypeError(
        f"{what} must be a datetime or an ISO 8601 string, got {type(value).__name__}"
    )


class ExternalClock(Clock):
    """
    An external clock that proceeds only when set_time is called.

    The clock counts seconds (``time``). Given a *start_datetime* it also has a
    calendar: ``start_time`` corresponds to ``start_datetime``, and
    :attr:`datetime`, :meth:`to_datetime` and :meth:`to_time` convert between
    clock seconds and calendar time. Naive and timezone-aware datetimes are
    kept as given: results are naive for a naive start and in the start's
    timezone for an aware one. Clock seconds are elapsed (physical) seconds,
    so for an aware start the arithmetic is done in UTC and a daylight saving
    switch is handled correctly (the calendar jumps, the clock does not);
    a naive start uses plain wall-clock arithmetic.

    :param start_time: initial clock time in seconds
    :param start_datetime: calendar date and time of *start_time*, as a
        ``datetime`` or an ISO 8601 string (parsed with
        ``datetime.fromisoformat``); ``None`` means no calendar

    Example::

        clock = ExternalClock(start_datetime="2024-06-01T00:00")
        clock.set_time(3600)
        clock.datetime  # datetime(2024, 6, 1, 1, 0)
    """

    def __init__(
        self,
        start_time: float = 0,
        start_datetime: "_dt.datetime | str | None" = None,
    ):
        self._time: float = start_time
        self._start_time: float = start_time
        self._start_datetime: _dt.datetime | None = (
            None
            if start_datetime is None
            else _parse_datetime(start_datetime, "start_datetime")
        )
        # heap of (wake time, insertion number, future): sleepers wake in time
        # order, sleepers with the same wake time in the order they slept
        self._futures: list[tuple[float, int, asyncio.Future]] = []
        self._sleep_count = itertools.count()
        self._time_listeners: list = []

    @property
    def time(self) -> float:
        """
        Current time of the external clock
        """
        return self._time

    @property
    def start_time(self) -> float:
        """
        Clock time in seconds at which the clock started
        """
        return self._start_time

    @property
    def start_datetime(self) -> _dt.datetime | None:
        """
        Calendar date and time of :attr:`start_time`, or ``None`` without calendar
        """
        return self._start_datetime

    @property
    def has_calendar(self) -> bool:
        """
        Whether the clock was created with a ``start_datetime``

        Example::

            ExternalClock().has_calendar  # False
            ExternalClock(start_datetime="2024-06-01").has_calendar  # True
        """
        return self._start_datetime is not None

    @property
    def datetime(self) -> _dt.datetime:
        """
        Current time as calendar date and time (requires a calendar)
        """
        return self.to_datetime(self._time)

    def _require_calendar(self) -> _dt.datetime:
        if self._start_datetime is None:
            raise RuntimeError(
                "This ExternalClock has no calendar; create it with "
                "start_datetime=... (e.g. create_world(start_datetime="
                "'2024-06-01T00:00')) to convert between clock seconds and "
                "calendar time."
            )
        return self._start_datetime

    def to_datetime(self, t: float) -> _dt.datetime:
        """
        Convert clock seconds *t* into calendar date and time

        :param t: clock time in seconds
        :return: the calendar time ``t - start_time`` elapsed seconds after
            ``start_datetime`` (in the start's timezone when it is aware)

        Example::

            clock = ExternalClock(start_datetime="2024-06-01T00:00")
            clock.to_datetime(90 * 60)  # datetime(2024, 6, 1, 1, 30)
        """
        start = self._require_calendar()
        elapsed = _dt.timedelta(seconds=t - self._start_time)
        if start.tzinfo is None:
            return start + elapsed
        utc = start.astimezone(_dt.timezone.utc) + elapsed
        return utc.astimezone(start.tzinfo)

    def to_time(self, dt: "_dt.datetime | str") -> float:
        """
        Convert a calendar date and time into clock seconds

        :param dt: a ``datetime`` or ISO 8601 string, naive or timezone-aware
            like ``start_datetime``
        :return: the clock time in seconds at *dt*

        Example::

            clock = ExternalClock(start_datetime="2024-06-01T00:00")
            clock.to_time("2024-06-02T00:00")  # 86400.0
        """
        start = self._require_calendar()
        value = _parse_datetime(dt, "dt")
        if (value.tzinfo is None) != (start.tzinfo is None):
            raise TypeError(
                f"Cannot compare {value!r} with the clock's start_datetime "
                f"{start!r}: mix of naive and timezone-aware datetimes."
            )
        if start.tzinfo is not None:
            # subtracting two datetimes with the same tzinfo ignores their
            # offsets, so compare the UTC instants
            value = value.astimezone(_dt.timezone.utc)
            start = start.astimezone(_dt.timezone.utc)
        return self._start_time + (value - start).total_seconds()

    def set_time(self, t: float):
        """
        New time is set
        """
        if t < self._time:
            raise ValueError(f"Time must be > {self._time} but is {t}.")
        # set time
        self._time = t
        # wake every sleeper due at t (heap pops: cost grows with the number
        # woken, not with the number of all sleepers)
        futures = self._futures
        while futures and futures[0][0] <= t:
            future = heapq.heappop(futures)[2]
            # set result of future, if future is not already done
            if not future.done():
                future.set_result(True)
        for listener in list(self._time_listeners):
            listener(t)

    def add_time_listener(self, listener) -> None:
        """Call ``listener(t)`` at the end of every :meth:`set_time`.

        Listeners run synchronously, in the order they were added, after the
        clock moved and the due sleepers were woken (their tasks run later,
        when the event loop gets control), so a listener can change what the
        woken tasks will see, e.g. switch a communication model at a boundary.

        :param listener: a callable taking the new time

        Example::

            clock.add_time_listener(lambda t: print("now", t))
        """
        self._time_listeners.append(listener)

    def remove_time_listener(self, listener) -> None:
        """Remove a listener added with :meth:`add_time_listener` (no error if absent)."""
        try:
            self._time_listeners.remove(listener)
        except ValueError:
            pass

    def sleep(self, t: float) -> asyncio.Future:
        """
        Sleeps for t based on the external clock
        """
        f = asyncio.Future()
        if t <= 0:
            # trigger directly if time is <= 0
            f.set_result(None)
            return f
        heapq.heappush(self._futures, (self.time + t, next(self._sleep_count), f))
        return f

    def get_next_activity(self) -> float:
        return None if len(self._futures) == 0 else self._futures[0][0]
