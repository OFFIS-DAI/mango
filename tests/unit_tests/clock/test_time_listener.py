import asyncio

import pytest

from mango.util.clock import ExternalClock


@pytest.mark.asyncio
async def test_time_listeners_run_after_the_clock_moved_and_sleepers_woke():
    clock = ExternalClock()
    seen = []
    sleeper = clock.sleep(5)

    def listener(t):
        seen.append((t, clock.time, sleeper.done()))

    clock.add_time_listener(listener)
    clock.add_time_listener(lambda t: seen.append("second"))
    clock.set_time(5)
    assert seen == [(5, 5, True), "second"]
    clock.remove_time_listener(listener)
    clock.remove_time_listener(listener)  # absent: no error
    clock.set_time(6)
    assert seen[-1] == "second" and len(seen) == 3
    await asyncio.sleep(0)
