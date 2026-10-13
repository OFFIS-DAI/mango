"""The neighbourhood as a kit: feeder, households with profiles, and their recorders.

Another study reuses it with ``scenario.use(Neighbourhood(), prefix="grid")``; its
parameters then live under ``grid`` (``--set grid.pv_share=0.8``).
"""

from pydantic import BaseModel

from mango.scenario import ScenarioComponent, Uniform

from .agents import HouseholdAgent
from .grid import FeederBehavior


class Neighbourhood(ScenarioComponent):
    class Params(BaseModel):
        n_buses: int = 12
        r_pu_per_kw: float = 0.0006
        n_households: int = 30
        pv_share: float = 0.6
        pv_peak_kw: Uniform = Uniform(3.0, 8.0)
        annual_kwh: Uniform = Uniform(2500.0, 5000.0)

    def build(self, ctx):
        p = ctx.p
        feeder = ctx.set_behavior(FeederBehavior(p.n_buses, p.r_pu_per_kw))
        homes = ctx.group(
            "hh",
            p.n_households,
            lambda e: HouseholdAgent(
                # every draw is made, so pv_share changes only who has PV
                pv_peak_kw=(e.rng.random() < p.pv_share) * p.pv_peak_kw.draw(e.rng),
                annual_kwh=p.annual_kwh.draw(e.rng),
                load_profile=ctx.profile("h0"),
                pv_profile=ctx.profile("pv"),
                step_s=ctx.time.step_s,
            ),
            install=lambda e: {"bus": e.index},
        )
        ctx.record("pv_kw", homes, lambda a: a.pv_kw, kind="flow")  # per step
        ctx.record_world("import_kw", lambda: feeder.import_kw, kind="flow")
        ctx.record_world("min_voltage_pu", lambda: min(feeder.voltages), kind="flow")
