import datetime
from typing import Tuple, List
import asyncio
import bisect


class Clock:
    """

    """
    @property
    def time(self) -> float:
        raise NotImplementedError

    def sleep(self, t: float):
        raise NotImplementedError


class AsyncioClock (Clock):
    """

    """
    def __init__(self):
        self.loop = asyncio.get_event_loop()

    @property
    def time(self) -> float:
        """

        """
        return datetime.datetime.now().timestamp()

    def sleep(self, t):
        return asyncio.sleep(t)


class ExternalClock(Clock):
    """

    """
    def __init__(self, start_time: float = 0):
        self._time: float = start_time
        self._loop = asyncio.get_event_loop()
        self._tasks: List[Tuple[float, asyncio.Future]] = []

    @property
    def time(self):
        """

        """
        return self._time

    def set_time(self, t: float):
        """

        """
        self._time = t
        keys = [k[0] for k in self._tasks]
        threshold = bisect.bisect_right(keys, t)
        tasks, self._tasks = self._tasks[:threshold], self._tasks[threshold:]
        for (time, future) in tasks:
            future.set_result(None)

    def sleep_until(self, t: float):
        f = self._loop.create_future()
        if t <= 0:
            f.set_result(None)
            return f
        keys = [k[0] for k in self._tasks]
        index = bisect.bisect_right(keys, t,)
        self._tasks.insert(index, (t, f))
        return f

    def sleep(self, t: float):
        return self.sleep_until(self.time + t)
