"""
The EV neighbourhood of the simulation tutorial, written as a scenario.

Five households with rooftop PV report their net power to a coordinator,
which dispatches three EVs to collect surplus energy and deliver it to
households with a deficit.  ``docs/source/scenario-tutorial.rst`` includes
this file verbatim; ``test_ev_tutorial_acceptance.py`` tests it.
"""

import math
from dataclasses import dataclass

from pydantic import BaseModel

from mango import Agent, Area2D, DefaultEnvironment, Position2D, on_message
from mango.scenario import Scenario, ScenarioParams, Study, Uniform, load_study


# -- messages -------------------------------------------------------------------
@dataclass
class NetPowerReport:
    """Sent by a household to the coordinator every step."""

    sender_aid: str
    net_power_kw: float
    position: Position2D


@dataclass
class EVAssignment:
    """Sent by the coordinator to an EV."""

    target: Position2D
    action: str  # "charge" or "discharge"
    power_kw: float


# -- agents ---------------------------------------------------------------------
class HouseholdAgent(Agent):
    def __init__(self, pv_peak_kw: float, load_kw: float):
        super().__init__()
        self.pv_peak_kw = pv_peak_kw
        self.load_kw = load_kw
        self.grid_import_kwh = 0.0
        self.grid_export_kwh = 0.0
        self.self_consumed_kwh = 0.0

    def on_step(self, env, clock, step_size_s: float) -> None:
        step_h = step_size_s / 3600.0
        now = clock.datetime
        pv = self.pv_output_kw(now.hour + now.minute / 60.0)
        net = pv - self.load_kw
        if net >= 0.0:
            self.self_consumed_kwh += self.load_kw * step_h
            self.grid_export_kwh += net * step_h
        else:
            self.self_consumed_kwh += pv * step_h
            self.grid_import_kwh += -net * step_h
        report = NetPowerReport(self.aid, net, env.space.location(self))
        for coordinator in self.neighbors(tid="reports"):
            self.schedule_instant_message(report, coordinator)

    def pv_output_kw(self, hour: float) -> float:
        """Sinusoidal profile peaking at solar noon."""
        return max(0.0, self.pv_peak_kw * math.sin(math.pi * (hour - 6.0) / 12.0))


class EVAgent(Agent):
    def __init__(
        self,
        capacity_kwh: float,
        soc_kwh: float,
        max_power_kw: float,
        speed: float,
        position: Position2D,
    ):
        super().__init__()
        self.capacity_kwh = capacity_kwh
        self.soc_kwh = soc_kwh
        self.max_power_kw = max_power_kw
        self.speed = speed  # grid units per hour
        self.position = position
        self.target = None
        self.action = "idle"
        self.power_kw = 0.0
        self.delivered_kwh = 0.0

    def on_step(self, env, clock, step_size_s: float) -> None:
        if self.target is None:
            return
        step_h = step_size_s / 3600.0
        dx = self.target.x - self.position.x
        dy = self.target.y - self.position.y
        distance = math.hypot(dx, dy)
        travel = self.speed * step_h
        if distance <= travel:
            # arrived: exchange energy at the target
            self.position = self.target
            energy_kwh = min(self.power_kw, self.max_power_kw) * step_h
            if self.action == "charge":
                self.soc_kwh = min(self.capacity_kwh, self.soc_kwh + energy_kwh)
            elif self.action == "discharge":
                delivered = min(self.soc_kwh, energy_kwh)
                self.soc_kwh -= delivered
                self.delivered_kwh += delivered
        else:
            ratio = travel / distance
            self.position = Position2D(
                self.position.x + dx * ratio, self.position.y + dy * ratio
            )
        env.space.move(self, self.position)

    @on_message(EVAssignment)
    def handle_assignment(self, content: EVAssignment, meta) -> None:
        self.target = content.target
        self.action = content.action
        self.power_kw = content.power_kw


class CoordinatorAgent(Agent):
    def __init__(self, threshold_kw: float):
        super().__init__()
        self.threshold_kw = threshold_kw
        self.reports: dict[str, NetPowerReport] = {}

    @on_message(NetPowerReport)
    def handle_report(self, content: NetPowerReport, meta) -> None:
        self.reports[content.sender_aid] = content

    def on_step(self, env, clock, step_size_s: float) -> None:
        reports = self.reports.values()
        urgent = [r for r in reports if abs(r.net_power_kw) > self.threshold_kw]
        # surpluses before deficits, the largest first
        urgent.sort(key=lambda r: (r.net_power_kw < 0, -abs(r.net_power_kw)))
        for ev, report in zip(self.neighbors(tid="dispatch"), urgent):
            action = "charge" if report.net_power_kw > 0 else "discharge"
            power_kw = abs(report.net_power_kw)
            self.schedule_instant_message(
                EVAssignment(report.position, action, power_kw), ev
            )


# -- parameters -----------------------------------------------------------------
class HouseholdParams(BaseModel, extra="forbid"):
    pv_peak_kw: float
    load_kw: float
    x: float
    y: float


class EVParams(BaseModel, extra="forbid"):
    n: int = 3
    capacity_kwh: float = 40.0
    soc_kwh: Uniform = Uniform(10.0, 20.0)
    max_power_kw: float = 3.3
    speed: float = 3.0  # grid units per hour


class AreaParams(BaseModel, extra="forbid"):
    width: float = 10.0
    height: float = 10.0


class Params(ScenarioParams):
    households: list[HouseholdParams] = [
        HouseholdParams(pv_peak_kw=6.0, load_kw=2.0, x=1.0, y=3.0),
        HouseholdParams(pv_peak_kw=4.0, load_kw=1.5, x=1.0, y=8.0),
        HouseholdParams(pv_peak_kw=7.0, load_kw=2.5, x=5.0, y=2.0),
        HouseholdParams(pv_peak_kw=5.0, load_kw=1.0, x=7.0, y=8.0),
        HouseholdParams(pv_peak_kw=3.0, load_kw=2.0, x=9.0, y=5.0),
    ]
    load_factor: Uniform = Uniform(0.9, 1.1)  # per household, scales its load
    ev: EVParams = EVParams()
    area: AreaParams = AreaParams()
    threshold_kw: float = 0.3


# -- scenario: time and space ---------------------------------------------------
scenario = Scenario("ev_neighbourhood", params=Params)
scenario.time(start="2024-06-21T00:00", horizon="1d", step="1h")


@scenario.environment
def environment(ctx):
    return DefaultEnvironment(space=Area2D(ctx.p.area.width, ctx.p.area.height))


# -- groups ---------------------------------------------------------------------
def make_household(e):
    home = e.p.households[e.index]
    load_kw = home.load_kw * e.p.load_factor.draw(e.rng)
    return HouseholdAgent(pv_peak_kw=home.pv_peak_kw, load_kw=load_kw)


def household_position(e):
    home = e.p.households[e.index]
    return Position2D(home.x, home.y)


def make_ev(e):
    ev, area = e.p.ev, e.p.area
    x = float(e.rng.uniform(0.0, area.width))
    y = float(e.rng.uniform(0.0, area.height))
    return EVAgent(
        capacity_kwh=ev.capacity_kwh,
        soc_kwh=ev.soc_kwh.draw(e.rng),
        max_power_kw=ev.max_power_kw,
        speed=ev.speed,
        position=Position2D(x, y),
    )


coordinator = scenario.group(
    "coordinator", 1, lambda e: CoordinatorAgent(threshold_kw=e.p.threshold_kw)
)
households = scenario.group(
    "household",
    lambda p: len(p.households),
    make_household,
    position=household_position,
)
evs = scenario.group("ev", "ev.n", make_ev, position=lambda e: e.agent.position)

# -- links and recorders --------------------------------------------------------
scenario.link("reports", households, coordinator, pattern="many_to_one")
scenario.link("dispatch", evs, coordinator, pattern="many_to_one")

scenario.record("soc_kwh", evs, attr="soc_kwh")
scenario.record("delivered_kwh", evs, attr="delivered_kwh")
scenario.record("x", evs, attr="position.x")
scenario.record("y", evs, attr="position.y")
scenario.record("import_kwh", households, attr="grid_import_kwh")
scenario.record("export_kwh", households, attr="grid_export_kwh")
scenario.record("self_consumed_kwh", households, attr="self_consumed_kwh")


# -- outcomes -------------------------------------------------------------------
def delivered_kwh(run):
    """Energy the EVs delivered to households with a deficit."""
    return float(run.wide("delivered_kwh").iloc[-1].sum())


def local_share(run):
    """Share of household demand met by PV on site or energy brought by EVs."""
    consumed = run.wide("self_consumed_kwh").iloc[-1].sum()
    imported = run.wide("import_kwh").iloc[-1].sum()
    local = consumed + min(delivered_kwh(run), imported)
    return float(local / (consumed + imported))


# -- study ----------------------------------------------------------------------
def fleet_study(out):
    """2 fleet sizes x 2 replications = 4 runs."""
    return Study(
        scenario,
        uncertainties={"ev.n": [2, 3]},
        replications=2,
        seed=2026,
        outcomes=[local_share, delivered_kwh],
        out=out,
    )


def main(out="results/ev_fleet", workers=2):
    study = fleet_study(out)
    print(study.plan())
    print(study.run(workers=workers))
    frame = load_study(out)
    print(frame.groupby("ev.n")[["local_share", "delivered_kwh"]].mean())
    return frame
