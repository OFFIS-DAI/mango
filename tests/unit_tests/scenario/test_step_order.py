"""The documented order of calls within one step (scenario.rst "Timing within a
step", simulation.rst "Timing within a step").

The latency rule itself is pinned in ``test_stop_and_timing.py``; these tests
pin the order of events, agents, behaviors, deliveries, recorders and stop
checks, the communication settings a send from ``on_step`` passes, and the
event-driven step sizes, so the documentation cannot drift from the code.
"""

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from mango import Agent, Behavior, DefaultEnvironment, sender_addr  # noqa: E402
from mango.scenario import Scenario  # noqa: E402


class Asker(Agent):
    def __init__(self, trace):
        super().__init__()
        self.trace = trace

    def on_step(self, env, clock, step_size_s):
        self.trace.append((clock.time, "on_step", self.aid))
        for peer in self.neighbors(tid="ask"):
            self.schedule_instant_message("question", peer)

    def on_agent_event(self, event):  # emit_to targets the agent
        self.trace.append((event.time, "event", self.aid))

    def handle_message(self, content, meta):
        self.trace.append((self.scheduler.clock.time, f"handles {content}", self.aid))


class Answerer(Agent):
    def __init__(self, trace):
        super().__init__()
        self.trace = trace

    def handle_message(self, content, meta):
        self.trace.append((self.scheduler.clock.time, f"handles {content}", self.aid))
        self.schedule_instant_message("answer", sender_addr(meta))


class Dynamics(Behavior):
    def __init__(self, trace):
        self.trace = trace

    def on_step(self, environment, clock, step_size_s):
        self.trace.append((clock.time, "behavior", step_size_s))


def order_scenario(trace, **time):
    scenario = Scenario("order")
    scenario.time(**{"horizon": 2, "step": 1, **time})
    first = scenario.group("first", 2, lambda e: Asker(trace))
    scenario.group("second", 1, lambda e: Asker(trace))
    answerer = scenario.group("answerer", 1, lambda e: Answerer(trace))
    scenario.link("ask", first, answerer, pattern="many_to_one", directed=True)
    scenario.environment(lambda ctx: DefaultEnvironment(behavior=Dynamics(trace)))
    scenario.event(1, name="go", emit={"kind": "go"}, emit_to="first_0")

    def record(ctx):
        trace.append((ctx.world.clock.time, "record", None))
        return 0.0

    scenario.record_world("rows", record)
    return scenario


def test_one_step_runs_events_agents_behaviors_deliveries_then_recorders():
    trace = []
    order_scenario(trace).run(seed=1)
    step_1 = [entry for entry in trace if entry[0] == 1.0]
    assert step_1 == [
        # deliveries of the step [0, 1): questions stamped 1, replies in the same step
        (1.0, "handles question", "answerer"),
        (1.0, "handles question", "answerer"),
        (1.0, "handles answer", "first_0"),
        (1.0, "handles answer", "first_1"),
        (1.0, "record", None),
        # the step [1, 2): the event at 1 comes after the row at 1
        (1.0, "event", "first_0"),
        (1.0, "on_step", "first_0"),
        (1.0, "on_step", "first_1"),
        (1.0, "on_step", "second"),
        (1.0, "behavior", 1.0),
    ]
    assert trace[0] == (0.0, "record", None)  # the initial row precedes every step


def test_stop_conditions_are_checked_after_the_recorders():
    trace = []
    scenario = order_scenario(trace)

    @scenario.stop_when
    def after_first_row(ctx):
        trace.append((ctx.world.clock.time, "stop check", None))
        return ctx.world.clock.time >= 1.0

    result = scenario.run(seed=1)
    assert result.stats["stop_time_s"] == 1.0
    assert trace[-2:] == [(1.0, "record", None), (1.0, "stop check", None)]


class Sender(Agent):
    def on_step(self, env, clock, step_size_s):
        for peer in self.neighbors(tid="link"):
            self.schedule_instant_message(clock.time, peer)


class Receiver(Agent):
    def __init__(self):
        super().__init__()
        self.got = []

    def handle_message(self, content, meta):
        self.got.append((content, self.scheduler.clock.time))


def test_a_send_from_on_step_passes_the_comm_settings_at_the_step_end():
    scenario = Scenario("outage")
    scenario.time(horizon=4, step=1)
    sender = scenario.group("sender", 1, lambda e: Sender())
    receiver = scenario.group("receiver", 1, lambda e: Receiver())
    scenario.link("link", sender, receiver, pattern="one_to_one")
    outage = {
        "events": [{"name": "out", "at": 1, "until": 2, "comm": {"loss_percent": 1.0}}]
    }
    result = scenario.run(outage, seed=1)
    # on_step at 0 sends with stamp 1, inside the outage [1, 2): lost; the
    # send of on_step at 1 is stamped 2, after the outage: delivered
    assert result.agent("receiver").got == [(1.0, 2.0), (2.0, 3.0), (3.0, 4.0)]


class Ticker(Agent):
    def on_ready(self):
        self.schedule_periodic_task(self.tick, delay=3.0)

    async def tick(self):
        pass


def test_event_mode_steps_agents_and_behaviors_with_the_variable_step_size():
    trace = []
    scenario = Scenario("events")
    scenario.time(horizon=10, step="event")
    scenario.group("ticker", 1, lambda e: Ticker())
    scenario.environment(lambda ctx: DefaultEnvironment(behavior=Dynamics(trace)))
    scenario.event(4, name="boundary", emit={"kind": "x"})
    scenario.run(seed=1)
    # task wake-ups at 3, 6, 9, the event boundary at 4 and the horizon at 10
    assert [(t, size) for t, _, size in trace] == [
        (0.0, 3.0),
        (3.0, 1.0),
        (4.0, 2.0),
        (6.0, 3.0),
        (9.0, 1.0),
    ]
