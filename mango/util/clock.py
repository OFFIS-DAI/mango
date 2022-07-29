import time
from typing import Tuple, List
import asyncio
import bisect
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

    @abstractmethod
    def sleep(self, t: float):
        raise NotImplementedError


class AsyncioClock (Clock):
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

    def sleep(self, t):
        """
        Sleeping via asyncio sleep
        """
        return asyncio.sleep(t)


class ExternalClock(Clock):
    """
    An external clock that proceeds only when set_time is called
    """
    def __init__(self, start_time: float = 0):
        self._time: float = start_time
        self._futures: List[Tuple[float, asyncio.Future]] = []  # list of all futures to be triggered

    @property
    def time(self):
        """
        Current time of the external clock
        """
        return self._time

    def set_time(self, t: float):
        """
        New time is set
        """
        if t < self._time:
            raise ValueError('Time must be > %s but is %s.', self._time, t)
        # set time
        self._time = t
        # search for all futures that have to be triggerd
        keys = [k[0] for k in self._futures]
        threshold = bisect.bisect_right(keys, t)
        # store
        current_futures, self._futures = self._futures[:threshold], self._futures[threshold:]
        # Tuple of time, future
        for _, future in current_futures:
            # set result of future
            future.set_result(None)

    def sleep(self, t: float):
        """
        Sleeps for t based on the external clock
        """
        f = asyncio.Future()
        if t <= 0:
            # trigger directly if time is <= 0
            f.set_result(None)
            return f
        # insert future in sorted list of futures
        keys = [k[0] for k in self._futures]
        index = bisect.bisect_right(keys, self.time + t,)
        self._futures.insert(index, (self.time + t, f))
        return f
