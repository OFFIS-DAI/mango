"""The EV feeder scenario: who exists, how they are linked, what is recorded.

Parameter values come from presets/base.yaml; check with
``mango-scenario plan ev_study.scenario:scenario -p presets/base.yaml``.
"""

from mango.scenario import Scenario

from .agents import CoordinatorAgent, EVAgent
from .kits import Neighbourhood
from .params import Params

scenario = Scenario("ev_feeder", params=Params)
scenario.use(Neighbourhood(), prefix="grid")  # feeder, households, profiles, recorders

coordinator = scenario.group(
    "coordinator", 1, lambda e: CoordinatorAgent(e.p.algorithm, e.time.step_s)
)
ev = scenario.group(
    "ev",
    "ev.n",
    lambda e: EVAgent(
        capacity_kwh=e.p.ev.capacity_kwh,
        max_power_kw=e.p.ev.max_power_kw,
        soc=e.p.ev.soc.draw(e.rng),
        arrival_h=e.p.ev.arrival_h.draw(e.rng),
        departure_h=e.p.ev.departure_h.draw(e.rng),
    ),
    install=lambda e: {"bus": e.index},
)

scenario.link("reports", "grid.hh", coordinator, pattern="many_to_one")
scenario.link("dispatch", ev, coordinator, pattern="many_to_one")
scenario.record("soc_kwh", ev, lambda a: a.soc_kwh, kind="state")  # a level
scenario.record("ev_power_kw", ev, lambda a: a.power_kw, kind="flow")  # per step
