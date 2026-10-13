"""The low-voltage feeder as an environment behavior."""

from itertools import accumulate

from mango import Behavior


class FeederBehavior(Behavior):
    """A radial feeder: the substation feeds bus 0, bus k feeds bus k + 1.

    Agents installed with a bus (``install(agent, bus=i)``, taken modulo ``n_buses``)
    draw ``agent.net_kw`` there (negative: feed-in); agents without a bus are ignored.
    Each step the feeder computes the import at the substation and a linear voltage
    drop: segment *k* carries the net power of buses *k* and beyond and drops
    ``r_pu_per_kw`` pu per kW of it.
    """

    def __init__(self, n_buses: int, r_pu_per_kw: float):
        self.n_buses, self.r_pu_per_kw = n_buses, r_pu_per_kw
        self.buses: list[tuple[object, int]] = []
        self.import_kw = 0.0
        self.voltages = [1.0] * n_buses

    def install(self, agent, bus: int | None = None, **kwargs) -> None:
        if bus is not None:
            self.buses.append((agent, bus % self.n_buses))

    def on_step(self, environment, clock, step_size_s: float) -> None:
        net_kw = [0.0] * self.n_buses
        for agent, bus in self.buses:
            net_kw[bus] += agent.net_kw
        flows = list(accumulate(reversed(net_kw)))[::-1]  # segment k feeds buses k..n-1
        drops = accumulate(flow * self.r_pu_per_kw for flow in flows)
        self.import_kw = flows[0]
        self.voltages = [1.0 - drop for drop in drops]
