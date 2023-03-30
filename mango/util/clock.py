import asyncio
import bisect
import time
from abc import ABC, abstractmethod
from typing import List, Tuple


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

    def sleep(self, t) -> asyncio.Future:
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
        self._futures: List[
            Tuple[float, asyncio.Future]
        ] = []  # list of all futures to be triggered

    @property
    def time(self) -> float:
        """
        Current time of the external clock
        """
        return self._time

    def set_time(self, t: float):
        """
        New time is set
        """
        if t < self._time:
            raise ValueError(f"Time must be > {self._time} but is {t}.")
        # set time
        self._time = t
        # search for all futures that have to be triggerd
        keys = [k[0] for k in self._futures]
        threshold = bisect.bisect_right(keys, t)
        # store
        current_futures, self._futures = (
            self._futures[:threshold],
            self._futures[threshold:],
        )
        # Tuple of time, future
        for _, future in current_futures:
            # set result of future, if future is not already done
            if not future.done():
                future.set_result(True)

    def sleep(self, t: float) -> asyncio.Future:
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
        index = bisect.bisect_right(
            keys,
            self.time + t,
        )
        self._futures.insert(index, (self.time + t, f))
        return f

    def get_next_activity(self) -> float:
        return None if len(self._futures) == 0 else self._futures[0][0]
