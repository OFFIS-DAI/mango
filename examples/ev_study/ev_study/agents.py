"""Households, EVs and the charging coordinator of the EV study, and their messages."""

from dataclasses import dataclass

from mango import Agent, on_message


@dataclass
class NetPowerReport:
    """Household -> coordinator, every step (link ``reports``)."""

    sender: str
    net_kw: float


@dataclass
class EVStatus:
    """EV -> coordinator, every step (link ``dispatch``)."""

    sender: str
    time: float  # simulation time of this status
    plugged_since: float | None  # simulation time of plug-in; None while away
    need_kwh: float  # energy missing to a full battery after this step
    max_power_kw: float


@dataclass
class Dispatch:
    """Coordinator -> EV: the charging power until the next dispatch arrives."""

    power_kw: float


def hour_of_day(clock) -> float:
    now = clock.datetime
    return now.hour + now.minute / 60 + now.second / 3600


class HouseholdAgent(Agent):
    """Load (``annual_kwh`` x the h0 profile) minus PV (``pv_peak_kw`` x the pv profile)."""

    def __init__(self, pv_peak_kw, annual_kwh, load_profile, pv_profile, step_s):
        super().__init__()
        self.pv_peak_kw, self.annual_kwh = pv_peak_kw, annual_kwh
        self.load_profile, self.pv_profile = load_profile, pv_profile
        self.step_h = step_s / 3600
        self.load_kw = self.pv_kw = self.net_kw = self.pv_kwh = 0.0

    def on_step(self, env, clock, step_size_s: float) -> None:
        self.load_kw = (
            self.annual_kwh / 1000 * self.load_profile.at(clock)
        )  # h0: per MWh/a
        self.pv_kw = self.pv_peak_kw * self.pv_profile.at(clock)
        self.net_kw = self.load_kw - self.pv_kw
        self.pv_kwh += self.pv_kw * self.step_h
        for coordinator in self.neighbors(tid="reports"):
            self.schedule_instant_message(
                NetPowerReport(self.aid, self.net_kw), coordinator
            )


class EVAgent(Agent):
    """Comes home at ``arrival_h`` with ``soc`` x ``capacity_kwh``, leaves at ``departure_h``.

    While plugged in it charges with the last dispatched power, at most ``max_power_kw``
    and never beyond a full battery.  A lost dispatch keeps the previous power.
    """

    def __init__(self, capacity_kwh, max_power_kw, soc, arrival_h, departure_h):
        super().__init__()
        self.capacity_kwh, self.max_power_kw = capacity_kwh, max_power_kw
        self.arrival_kwh = soc * capacity_kwh
        self.arrival_h, self.departure_h = arrival_h % 24, departure_h % 24
        self.soc_kwh = self.arrival_kwh
        self.plugged_since = None
        self.setpoint_kw = self.power_kw = self.net_kw = 0.0

    def is_home(self, hour: float) -> bool:
        if self.arrival_h > self.departure_h:  # home overnight
            return hour >= self.arrival_h or hour < self.departure_h
        return self.arrival_h <= hour < self.departure_h

    def on_step(self, env, clock, step_size_s: float) -> None:
        step_h = step_size_s / 3600
        home = self.is_home(hour_of_day(clock))
        if home and self.plugged_since is None:  # arrived (or home at the start)
            self.plugged_since, self.setpoint_kw = clock.time, 0.0
            self.soc_kwh = self.arrival_kwh
        elif not home:
            self.plugged_since = None
        room_kw = (self.capacity_kwh - self.soc_kwh) / step_h
        self.power_kw = (
            min(self.setpoint_kw, self.max_power_kw, room_kw) if home else 0.0
        )
        self.soc_kwh += self.power_kw * step_h
        self.net_kw = self.power_kw
        need_kwh = self.capacity_kwh - self.soc_kwh
        status = EVStatus(
            self.aid, clock.time, self.plugged_since, need_kwh, self.max_power_kw
        )
        for coordinator in self.neighbors(tid="dispatch"):
            self.schedule_instant_message(status, coordinator)

    @on_message(Dispatch)
    def handle_dispatch(self, content: Dispatch, meta) -> None:
        self.setpoint_kw = content.power_kw


class CoordinatorAgent(Agent):
    """Shares the feeder headroom (``limit_kw`` minus the households' net load) among EVs.

    ``greedy`` serves EVs first come, first served, each up to its maximum power;
    ``proportional`` splits the headroom by the energy each EV still needs.
    """

    def __init__(self, algorithm: str, step_s: float, limit_kw: float = 20.0):
        super().__init__()
        self.algorithm, self.step_h, self.limit_kw = algorithm, step_s / 3600, limit_kw
        self.households: dict[str, float] = {}
        self.evs: dict[str, EVStatus] = {}
        self.reports_received = 0
        self.last_load_kw = 0.0
        self.sent: dict[str, float] = {}  # the last power sent to each EV

    @on_message(NetPowerReport)
    def handle_report(self, content: NetPowerReport, meta) -> None:
        self.households[content.sender] = content.net_kw
        self.reports_received += 1

    @on_message(EVStatus)
    def handle_status(self, content: EVStatus, meta) -> None:
        self.evs[content.sender] = content

    def on_step(self, env, clock, step_size_s: float) -> None:
        # Reports are a step old and a dispatch acts a step later: extrapolate the load,
        # and subtract what each EV charged since its last status (lost ones are older).
        load_kw = sum(self.households.values())
        headroom = max(0.0, self.limit_kw - load_kw - 2 * (load_kw - self.last_load_kw))
        self.last_load_kw = load_kw
        need = {
            s.sender: s.need_kwh
            - self.sent.get(s.sender, 0.0) * (clock.time - s.time) / 3600
            for s in self.evs.values()
            if s.plugged_since is not None
        }
        waiting = [s for s in self.evs.values() if need.get(s.sender, 0.0) > 1e-6]
        cap = {
            s.sender: min(s.max_power_kw, need[s.sender] / self.step_h) for s in waiting
        }
        power: dict[str, float] = {}
        if self.algorithm == "greedy":
            for s in sorted(waiting, key=lambda s: (s.plugged_since, s.sender)):
                power[s.sender] = min(cap[s.sender], headroom)
                headroom -= power[s.sender]
        else:  # by need; EVs at their cap pass the rest of their share on
            while waiting:
                total = sum(need[s.sender] for s in waiting)
                full = [
                    s
                    for s in waiting
                    if headroom * need[s.sender] / total >= cap[s.sender]
                ]
                if not full:
                    power.update(
                        {s.sender: headroom * need[s.sender] / total for s in waiting}
                    )
                    break
                for s in full:
                    power[s.sender] = cap[s.sender]
                    headroom -= cap[s.sender]
                waiting = [s for s in waiting if s not in full]
        for ev in self.neighbors(tid="dispatch"):
            self.sent[ev.aid] = power.get(ev.aid, 0.0)
            self.schedule_instant_message(Dispatch(self.sent[ev.aid]), ev)
