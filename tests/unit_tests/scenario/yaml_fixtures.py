"""Importable agents, factories, kits and outcomes for the scenario/study file tests.

Scenario files reference functions as ``"tests.unit_tests.scenario.yaml_fixtures:x"``
and process workers import them again, so they live at module level here.
:func:`python_scenario` builds the scenario of ``C5_SCENARIO`` with the Python
API (the equivalence test compares both).
"""

from typing import Literal

from pydantic import BaseModel

from mango import Agent
from mango.scenario import (
    Scenario,
    ScenarioComponent,
    ScenarioParams,
    Uniform,
    register_component,
)
from mango.simulation import Behavior

REF = "tests.unit_tests.scenario.yaml_fixtures"


class EVParams(BaseModel, extra="forbid"):
    n: int = 2
    capacity_kwh: float = 60.0
    soc: Uniform = Uniform(0.2, 0.8)


class Params(ScenarioParams):
    ev: EVParams = EVParams()
    algorithm: Literal["greedy", "proportional"] = "greedy"
    gain: float = 0.1


class NamedParams(Params):
    """A params field whose name is a scenario file key (set via ``defaults:``)."""

    name: str = "default"


class RequiredParams(ScenarioParams):
    site: str


class Household(Agent):
    def __init__(self, pv_peak_kw):
        super().__init__()
        self.pv_peak_kw = pv_peak_kw
        self.pv_kw = 0.0

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        self.pv_kw = self.pv_peak_kw * float(self.rng.random())


class EV(Agent):
    def __init__(self, soc, capacity_kwh, gain, algorithm):
        super().__init__()
        self.soc = soc
        self.capacity_kwh = capacity_kwh
        self.gain = gain
        self.algorithm = algorithm
        self.power_kw = 0.0

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        factor = 1.0 if self.algorithm == "greedy" else 0.5
        self.power_kw = (
            factor * self.gain * self.capacity_kwh * float(self.rng.random())
        )
        self.soc = min(1.0, self.soc + self.power_kw / self.capacity_kwh)


class Coordinator(Agent):
    def handle_message(self, content, meta):
        pass


class Stats:
    def __init__(self):
        self.peak_kw = 0.0


class Feeder(Behavior):
    """Sums the PV of the installed households; exposes ``import_kw`` and ``stats``."""

    def __init__(self):
        self.households = []
        self.import_kw = 0.0
        self.stats = Stats()
        self.buses = {}

    def install(self, agent, **kwargs):
        if isinstance(agent, Household):
            self.households.append(agent)
        if "bus" in kwargs:
            self.buses[agent.aid] = kwargs["bus"]

    def on_step(self, environment, clock, step_size_s):
        self.import_kw = -sum(h.pv_kw for h in self.households)
        self.stats.peak_kw = max(self.stats.peak_kw, abs(self.import_kw))


@register_component("yaml_test_grid")
class ToyGrid(ScenarioComponent):
    """A feeder (resource ``feeder``) with ``n_households`` households."""

    name = "toy_grid"

    class Params(BaseModel, extra="forbid"):
        n_households: int = 3
        pv_share: float = 1.0
        pv_peak_kw: Uniform = Uniform(3.0, 8.0)

    def build(self, ctx):
        p = ctx.p
        feeder = ctx.provide("feeder", Feeder())
        ctx.add_behavior(feeder)
        homes = ctx.group(
            "hh",
            p.n_households,
            lambda e: Household(
                pv_peak_kw=p.pv_peak_kw.draw(e.rng)
                if e.rng.random() < p.pv_share
                else 0
            ),
            install=lambda e: {"bus": e.index},
        )
        ctx.record("pv_kw", homes, attr="pv_kw")


def make_ev(e):
    return EV(
        soc=e.p.ev.soc.draw(e.rng),
        capacity_kwh=e.p.ev.capacity_kwh,
        gain=e.p.gain,
        algorithm=e.p.algorithm,
    )


def make_coordinator(e):
    return Coordinator()


def ev_install(e):
    return {"bus": e.index}


def ev_name(e):
    return f"car_{e.index}"


def ev_count(p):
    return p.ev.n + 1


def same_bus(src, dst):
    return src.attrs.get("bus") == dst.attrs.get("bus")


def count_agents(ctx):
    return len(ctx.world.agents)


def ev_power(agent):
    return agent.power_kw


def mark_leader(ctx):
    ctx.agents("coordinator")[0].leader = True


def make_environment(ctx):
    from mango.simulation import DefaultEnvironment

    return DefaultEnvironment(space=ctx.space, behavior=ctx.behavior)


#: Calls of :func:`make_communication` (tests reset it).
COMM_CALLS = []


def make_communication(ctx):
    from mango.scenario import build_communication

    COMM_CALLS.append(ctx.comm.kind)
    return build_communication(ctx.comm, ctx.seeds.rng("comm"))


def mean_soc(run):
    return float(run.ts("soc_kwh").mean())


def peak_import(run):
    return float(run.ts("import_kw").abs().max())


def self_consumption(run):
    pv_kwh = run.ts("pv_kw").sum() * run.step_h
    export_kwh = run.ts("import_kw").clip(upper=0).abs().sum() * run.step_h
    return float(1 - export_kwh / pv_kwh) if pv_kwh else 1.0


def peak_import_kw(run):
    return float(run.ts("import_kw").max())


def min_voltage_pu(run):
    return float(1.0 - 0.001 * run.ts("ev_power_kw").max())


NOT_CALLABLE = 42


def make_ev_scenario():
    """The stand-in for ``ev_study.scenario:scenario`` of the side-by-side."""
    scenario = Scenario("ev_feeder", params=Params)
    scenario.use(ToyGrid(), prefix="grid")
    coordinator = scenario.group("coordinator", 1, make_coordinator)
    ev = scenario.group("ev", "ev.n", make_ev, install=ev_install)
    scenario.link("reports", "grid.hh", coordinator, pattern="many_to_one")
    scenario.link("dispatch", ev, coordinator, pattern="many_to_one")
    scenario.record("soc_kwh", ev, attr="soc")
    scenario.record("ev_power_kw", ev, ev_power)
    scenario.record_world("import_kw", feeder_import_kw)
    return scenario


def feeder_import_kw(ctx):
    return ctx.resource("feeder").import_kw


ev_scenario = make_ev_scenario()

#: The concept C5 shape: components with kind + params, links, recorders with
#: attr, fn and a resource path (``{ref}`` is replaced by :data:`REF`).
C5_SCENARIO = f"""
name: c5
params: {REF}:Params
seed: 42
time: {{start: 2024-06-01T00:00, horizon: 3h, step: 1h}}
ev: {{n: 3}}
components:
  grid: {{kind: yaml_test_grid, n_households: 4, pv_peak_kw: {{uniform: [2, 6]}}}}
groups:
  coordinator: {{factory: "{REF}:make_coordinator", count: 1}}
  ev: {{factory: "{REF}:make_ev", count: ev.n, install: "{REF}:ev_install"}}
links:
  - {{name: reports, from: grid.hh, to: coordinator, pattern: many_to_one}}
  - {{name: dispatch, from: ev, to: coordinator, pattern: many_to_one}}
  - {{name: peers, from: ev, pattern: {{random: {{per_target: [0, 2]}}}}}}
record:
  - {{name: soc_kwh, group: ev, attr: soc}}
  - {{name: ev_power_kw, group: ev, fn: "{REF}:ev_power"}}
  - {{name: import_kw, resource: feeder.import_kw}}
  - {{name: peak_kw, resource: feeder.stats.peak_kw, entity: grid}}
  - {{name: n_agents, fn: "{REF}:count_agents"}}
"""


def python_c5_scenario():
    """The scenario of :data:`C5_SCENARIO`, written with the Python API."""
    from mango.scenario import patterns

    scenario = Scenario("c5", params=Params)
    scenario.use(ToyGrid(n_households=4, pv_peak_kw=Uniform(2, 6)), prefix="grid")
    scenario.defaults(
        {
            "seed": 42,
            "time": {"start": "2024-06-01T00:00", "horizon": "3h", "step": "1h"},
            "ev": {"n": 3},
        }
    )
    coordinator = scenario.group("coordinator", 1, make_coordinator)
    ev = scenario.group("ev", "ev.n", make_ev, install=ev_install)
    scenario.link("reports", "grid.hh", coordinator, pattern="many_to_one")
    scenario.link("dispatch", ev, coordinator, pattern="many_to_one")
    scenario.link("peers", ev, pattern=patterns.random(per_target=(0, 2)))
    scenario.record("soc_kwh", ev, attr="soc")
    scenario.record("ev_power_kw", ev, ev_power)
    scenario.record_world("import_kw", lambda ctx: ctx.resource("feeder").import_kw)
    scenario.record_world(
        "peak_kw", lambda ctx: ctx.resource("feeder").stats.peak_kw, entity="grid"
    )
    scenario.record_world("n_agents", count_agents)
    return scenario
