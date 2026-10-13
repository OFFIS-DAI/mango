"""
Importable scenarios for test_script.py and test_cli.py.

``scenario`` uses everything a generated script must reproduce: phases with
``record: false``, a communication outage, emits (to all and to one entity),
an action, profiles with per-entity noise, random and attribute links, a
world recorder with ``every`` and inline lambdas.
"""

from pydantic import BaseModel

from mango import Agent
from mango.scenario import (
    Scenario,
    ScenarioComponent,
    ScenarioParams,
    Uniform,
    patterns,
)


class NodeParams(BaseModel, extra="forbid"):
    n: int = 4
    gain: Uniform = Uniform(0.5, 1.5)


class Params(ScenarioParams):
    node: NodeParams = NodeParams()
    boost: float = 2.0


class Node(Agent):
    def __init__(self, gain: float, load):
        super().__init__()
        self.gain = gain
        self.load = load
        self.value = 0.0
        self.received = 0
        self.events: list[str] = []

    def on_step(self, env, clock, step_size_s: float) -> None:
        self.value += self.gain * float(self.rng.normal()) + self.load.at(clock)
        for peer in self.neighbors(tid="peers"):
            self.schedule_instant_message(round(self.value, 6), peer)

    def handle_message(self, content, meta) -> None:
        self.received += 1

    def on_global_event(self, event) -> None:
        self.events.append(f"global:{event.name}")

    def on_agent_event(self, event) -> None:
        self.events.append(f"agent:{event.name}")


class Hub(Agent):
    def __init__(self, boost: float):
        super().__init__()
        self.boost = boost
        self.received = 0

    def handle_message(self, content, meta) -> None:
        self.received += 1


def make_node(e):
    return Node(gain=e.draw(e.p.node.gain), load=e.profile("load"))


def node_attrs(e):
    return {"bus": e.index % 2}


def total_received(ctx):
    return sum(agent.received for agent in ctx.agents("node"))


def boost_gains(ctx):
    for agent in ctx.agents("node"):
        agent.gain *= ctx.p.boost


scenario = Scenario("script_demo", params=Params)
scenario.time(start="2024-06-01T00:00", horizon="6h", step="30min")
scenario.defaults(
    {
        "profiles": {
            "load": {"values": [0.1, 0.3, 0.2], "resolution": "1h", "noise": 0.2}
        },
        "comm": {"loss_percent": 0.2},
        "phases": [{"name": "warmup", "until": "1h", "record": False}],
        "events": [
            {
                "name": "outage",
                "at": "2h",
                "until": "3h",
                "comm": {"loss_percent": 1.0},
            },
            {"name": "ping", "at": "3h", "emit": {"kind": "ping"}},
            {"name": "poke", "at": "4h", "emit": {"kind": "poke"}, "emit_to": "node_0"},
            {"name": "boost", "at": "5h", "action": f"{__name__}:boost_gains"},
        ],
    }
)
hub = scenario.group("hub", 1, lambda e: Hub(boost=e.p.boost))
nodes = scenario.group("node", "node.n", make_node, install=node_attrs)
scenario.link("peers", nodes, pattern=patterns.random(per_target=(1, 2)))
scenario.link("buses", nodes, pattern=patterns.by_attr("bus"))
scenario.link("uplink", nodes, hub, pattern="many_to_one")
scenario.record("value", nodes, attr="value")
scenario.record("events", nodes, lambda a: len(a.events))
scenario.record("received", hub, attr="received")
scenario.record_world("total_received", total_received, every="1h")


# -- scenarios whose factory is a closure (over a literal: written out; over an
# object the script cannot rebuild: a placeholder) -------------------------------
def _closure_scenario():
    offset = 3.0
    closure = Scenario("closure_demo")
    closure.group("hub", 1, lambda e: Hub(boost=offset))
    return closure


closure_scenario = _closure_scenario()


def _opaque_closure_scenario():
    import itertools

    counter = itertools.count()  # neither a literal nor importable
    closure = Scenario("opaque_closure_demo")
    closure.group("hub", 1, lambda e: Hub(boost=next(counter)))
    return closure


opaque_closure_scenario = _opaque_closure_scenario()


class OpaqueResource(ScenarioComponent):
    """Provides an object the script cannot rebuild (a placeholder resource)."""

    def build(self, ctx):
        import itertools

        ctx.provide("ids", itertools.count())


opaque_resource_scenario = Scenario("opaque_resource")
opaque_resource_scenario.use(OpaqueResource(), prefix="kit")
opaque_resource_scenario.group("hub", 1, lambda e: Hub(boost=1.0))


# -- a scenario whose factory fails (a run that fails) ---------------------------
def make_broken(e):
    raise ValueError("boom")


failing_scenario = Scenario("failing")
failing_scenario.group("broken", 1, make_broken)


# -- a kit with a local factory function closing over ctx and ctx.p, and a
# behavior that is also a resource ------------------------------------------------
class Meter:
    """A resource whose state is what its constructor makes of the arguments."""

    def __init__(self, scale: float, settings: BaseModel):
        self.scale = scale
        self.settings = settings
        self.readings: list[float] = []


class Tally:
    """A resource changed after construction (cannot be written out)."""

    def __init__(self):
        self.count = 0


class FleetParams(BaseModel, extra="forbid"):
    n: int = 3
    gain: Uniform = Uniform(0.5, 1.5)
    scale: float = 2.0


class Fleet(ScenarioComponent):
    Params = FleetParams

    def build(self, ctx):
        p = ctx.p
        meter = ctx.provide("meter", Meter(p.scale, p))

        def make(e):
            """Nodes with a gain drawn from the kit's parameters."""
            return Node(gain=e.draw(p.gain), load=ctx.profile("load"))

        ctx.group("node", p.n, make)
        ctx.record_world("scale", lambda: meter.scale)
        ctx.record("value", "node", attr="value")


fleet_scenario = Scenario("fleet_demo")
fleet_scenario.time(horizon="2h", step="30min")
fleet_scenario.defaults(
    {"profiles": {"load": {"values": [0.1, 0.2], "resolution": "1h"}}}
)
fleet_scenario.use(Fleet(), prefix="north")


class TallyKit(ScenarioComponent):
    def build(self, ctx):
        tally = ctx.provide("tally", Tally())
        tally.count = 5  # changed after construction


tally_scenario = Scenario("tally_demo")
tally_scenario.use(TallyKit(), prefix="t")
tally_scenario.group("hub", 1, lambda e: Hub(boost=1.0))


# -- a lean scenario with a profile, every= recorders and a world recorder -------
def total_value(ctx):
    return sum(agent.value for agent in ctx.agents("node"))


lean_scenario = Scenario("lean_demo", params=Params)
lean_scenario.time(horizon="3h", step="30min")
lean_scenario.defaults(
    {
        "profiles": {
            "load": {"values": [0.1, 0.3, 0.2], "resolution": "1h", "noise": 0.1}
        }
    }
)
lean_nodes = lean_scenario.group("node", "node.n", make_node, install=node_attrs)
lean_scenario.link("peers", lean_nodes, pattern="ring")
lean_scenario.record("value", lean_nodes, attr="value", every="1h")
lean_scenario.record("received", lean_nodes, attr="received")
lean_scenario.record_world("total", total_value)


# --------------------------------------------------------------- running scripts


def run_generated(source, folder, name="generated.py"):
    """Execute a generated script in this process; return (rows, its globals)."""
    import runpy
    from pathlib import Path

    path = Path(folder) / name
    path.write_text(source, encoding="utf-8")
    namespace = runpy.run_path(str(path), run_name="generated")
    return namespace["main"](), namespace


def rows_checksum(rows):
    """The checksum of the frame of a script's rows, with the phases the script wrote."""
    import pandas as pd

    from mango.scenario.result import compact_labels, frame_checksum, tidy_frame

    frame = tidy_frame(rows, run_id="x")
    frame["phase"] = compact_labels(
        pd.Series([row["phase"] for row in rows], dtype=object)
    )
    return frame_checksum(frame)
