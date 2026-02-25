"""Generate interactive Plotly charts for the EV coordination tutorial.

Outputs two HTML snippets to the _static/ directory.  Each snippet contains
a single Plotly <div>; the first also includes the Plotly.js CDN loader so
the library is available for both charts when they appear on the same page.

Run from the repo root::

    ~/miniconda3/bin/python docs/source/_static/gen_ev_plots.py
"""

import asyncio
import math
from dataclasses import dataclass
from pathlib import Path

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from mango import (
    Agent,
    Position2D,
    create_world,
    step_simulation,
    SimpleCommunicationSimulation,
    DefaultEnvironment,
    Area2D,
    record_agent,
    record_position,
    position_history,
)

OUT_DIR = Path(__file__).parent

# ── Message types ─────────────────────────────────────────────────────────────

@dataclass
class NetPowerReport:
    sender_aid: str
    net_power_kw: float
    position: Position2D


@dataclass
class EVAssignment:
    target: Position2D
    action: str   # "charge" or "discharge"
    power_kw: float


# ── Household ─────────────────────────────────────────────────────────────────

class HouseholdAgent(Agent):
    def __init__(self, pv_peak_kw, load_kw, coordinator_addr):
        super().__init__()
        self.pv_peak_kw = pv_peak_kw
        self.load_kw = load_kw
        self.coordinator_addr = coordinator_addr
        self.grid_import_kwh = 0.0
        self.grid_export_kwh = 0.0
        self.self_consumed_kwh = 0.0

    def on_step(self, env, clock, step_size_s):
        step_h = step_size_s / 3600.0
        pv = self._pv_output_kw(clock.time)
        net = pv - self.load_kw
        if net >= 0.0:
            self.self_consumed_kwh += self.load_kw * step_h
            self.grid_export_kwh += net * step_h
        else:
            self.self_consumed_kwh += pv * step_h
            self.grid_import_kwh += (-net) * step_h
        pos = env.space.location(self)
        self.schedule_instant_message(
            NetPowerReport(self.aid, net, pos), self.coordinator_addr
        )

    def handle_message(self, content, meta):
        pass

    def _pv_output_kw(self, time_s):
        hour = (time_s / 3600.0) % 24.0
        return max(0.0, self.pv_peak_kw * math.sin(math.pi * (hour - 6.0) / 12.0))


# ── EV ────────────────────────────────────────────────────────────────────────

class EVAgent(Agent):
    def __init__(self, capacity_kwh, soc_kwh, max_power_kw, speed):
        super().__init__()
        self.capacity_kwh = capacity_kwh
        self.soc_kwh = soc_kwh
        self.max_power_kw = max_power_kw
        self.speed = speed
        self.target = None
        self.action = "idle"
        self.assigned_power_kw = 0.0

    def on_step(self, env, clock, step_size_s):
        if self.target is None:
            return
        step_h = step_size_s / 3600.0
        current = env.space.location(self)
        dx, dy = self.target.x - current.x, self.target.y - current.y
        dist = math.sqrt(dx * dx + dy * dy)
        travel = self.speed * step_h
        if dist <= travel:
            env.space.move(self, self.target)
            energy = self.assigned_power_kw * step_h
            if self.action == "charge":
                self.soc_kwh = min(self.capacity_kwh, self.soc_kwh + energy)
            elif self.action == "discharge":
                self.soc_kwh = max(0.0, self.soc_kwh - energy)
        else:
            ratio = travel / dist
            env.space.move(
                self, Position2D(current.x + dx * ratio, current.y + dy * ratio)
            )

    def handle_message(self, content, meta):
        if isinstance(content, EVAssignment):
            self.target = content.target
            self.action = content.action
            self.assigned_power_kw = content.power_kw


# ── Coordinator ───────────────────────────────────────────────────────────────

class CoordinatorAgent(Agent):
    def __init__(self, ev_addresses):
        super().__init__()
        self.ev_addresses = ev_addresses
        self._powers = {}
        self._positions = {}

    def handle_message(self, content, meta):
        if isinstance(content, NetPowerReport):
            self._powers[content.sender_aid] = content.net_power_kw
            self._positions[content.sender_aid] = content.position

    def on_step(self, env, clock, step_size_s):
        if not self._powers:
            return
        surpluses = sorted(
            [(k, v) for k, v in self._powers.items() if v > 0.3], key=lambda x: -x[1]
        )
        deficits = sorted(
            [(k, v) for k, v in self._powers.items() if v < -0.3], key=lambda x: x[1]
        )
        ev_iter = iter(self.ev_addresses)
        for haid, power in surpluses:
            addr = next(ev_iter, None)
            if addr is None:
                return
            self.schedule_instant_message(
                EVAssignment(self._positions[haid], "charge", min(power, 3.3)), addr
            )
        for haid, power in deficits:
            addr = next(ev_iter, None)
            if addr is None:
                return
            self.schedule_instant_message(
                EVAssignment(self._positions[haid], "discharge", min(-power, 3.3)), addr
            )


# ── Simulation ────────────────────────────────────────────────────────────────


async def run_simulation():
    env = DefaultEnvironment(space=Area2D(width=10.0, height=10.0))
    world = create_world(
        start_time=0.0,
        communication_sim=SimpleCommunicationSimulation(default_delay_s=0.0),
        environment=env,
    )

    coord = world.register(CoordinatorAgent(ev_addresses=[]))

    h1 = world.register(HouseholdAgent(6.0, 2.0, coord.addr))
    h2 = world.register(HouseholdAgent(4.0, 1.5, coord.addr))
    h3 = world.register(HouseholdAgent(7.0, 2.5, coord.addr))
    h4 = world.register(HouseholdAgent(5.0, 1.0, coord.addr))
    h5 = world.register(HouseholdAgent(3.0, 2.0, coord.addr))

    ev1 = world.register(EVAgent(40.0, 20.0, 3.3, 3.0))
    ev2 = world.register(EVAgent(40.0, 15.0, 3.3, 3.0))
    ev3 = world.register(EVAgent(40.0, 10.0, 3.3, 3.0))
    coord.ev_addresses = [ev1.addr, ev2.addr, ev3.addr]

    household_agents = [h1, h2, h3, h4, h5]
    ev_agents = [ev1, ev2, ev3]
    household_positions = [
        (h1, Position2D(1.0, 3.0)),
        (h2, Position2D(1.0, 8.0)),
        (h3, Position2D(5.0, 2.0)),
        (h4, Position2D(7.0, 8.0)),
        (h5, Position2D(9.0, 5.0)),
    ]

    is_ev = lambda a: isinstance(a, EVAgent)
    is_hh = lambda a: isinstance(a, HouseholdAgent)

    async with world:
        space = world.environment.space
        for agent, pos in household_positions:
            space.move(agent, pos)

        record_agent(world, "soc", lambda a: a.soc_kwh, filter_fn=is_ev)
        record_agent(world, "import", lambda a: a.grid_import_kwh, filter_fn=is_hh)
        record_agent(world, "export", lambda a: a.grid_export_kwh, filter_fn=is_hh)
        record_agent(world, "self_consumed", lambda a: a.self_consumed_kwh, filter_fn=is_hh)
        record_position(world, filter_fn=is_ev)

        for _ in range(24):
            await step_simulation(world, step_size_s=3600.0)

    return world, household_agents, ev_agents, household_positions


# ── Plotting ──────────────────────────────────────────────────────────────────

EV_COLORS = ["royalblue", "crimson", "darkorange"]
EV_LABELS = ["EV 1", "EV 2", "EV 3"]
H_COLORS = ["teal", "purple", "sienna", "olivedrab", "steelblue"]
H_LABELS = ["H1 (6 kW PV)", "H2 (4 kW PV)", "H3 (7 kW PV)",
             "H4 (5 kW PV)", "H5 (3 kW PV)"]
H_LABELS_SHORT = ["H1", "H2", "H3", "H4", "H5"]

# Shared layout settings
_AXIS_STYLE = dict(showgrid=True, gridcolor="#e8e8e8", zeroline=False)
_HOUR_AXIS = dict(**_AXIS_STYLE, range=[0, 24], dtick=4, title_text="Hour of day")


def plot_overview(world, household_agents, ev_agents, out_path, *, include_plotlyjs):
    """Two-panel interactive figure: EV SoC (left) and household net power (right)."""
    soc_data = world.data_agent_collections["soc"]
    imp_data = world.data_agent_collections["import"]
    ex_data = world.data_agent_collections["export"]

    hours = list(range(25))

    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=[
            "EV Battery State of Charge",
            "Household Net Power (PV − Load)",
        ],
        horizontal_spacing=0.10,
    )

    # ── Daylight shading ──────────────────────────────────────────────────────
    for col in (1, 2):
        fig.add_vrect(
            x0=6, x1=18, fillcolor="gold", opacity=0.12,
            layer="below", line_width=0, row=1, col=col,
        )

    # ── Battery full reference line ───────────────────────────────────────────
    fig.add_hline(
        y=40, line_dash="dash", line_color="lightgray",
        annotation_text="Full (40 kWh)", annotation_position="top right",
        row=1, col=1,
    )

    # ── EV SoC traces ─────────────────────────────────────────────────────────
    for ev, color, label in zip(ev_agents, EV_COLORS, EV_LABELS):
        soc = soc_data.timeseries[ev.aid]
        fig.add_trace(
            go.Scatter(
                x=hours[:len(soc)], y=soc,
                name=label, legendgroup="EVs", legendgrouptitle_text="EVs",
                line=dict(color=color, width=2.2),
                hovertemplate="%{y:.1f} kWh at hour %{x}<extra>" + label + "</extra>",
            ),
            row=1, col=1,
        )

    # ── Household net power traces ────────────────────────────────────────────
    fig.add_hline(y=0, line_color="black", line_width=0.8, row=1, col=2)

    for h, color, label in zip(household_agents, H_COLORS, H_LABELS):
        imp = imp_data.timeseries[h.aid]
        ex = ex_data.timeseries[h.aid]
        net = [(ex[t] - ex[t - 1]) - (imp[t] - imp[t - 1]) for t in range(1, len(imp))]
        fig.add_trace(
            go.Scatter(
                x=list(range(1, len(net) + 1)), y=net,
                name=label, legendgroup="Households",
                legendgrouptitle_text="Households",
                line=dict(color=color, width=1.8),
                hovertemplate="%{y:.2f} kW at hour %{x}<extra>" + label + "</extra>",
            ),
            row=1, col=2,
        )

    # ── Layout ────────────────────────────────────────────────────────────────
    fig.update_layout(
        title=dict(
            text="EV Coordination Simulation — 24-Hour Overview",
            font=dict(size=15),
            x=0.5,
        ),
        height=440,
        legend=dict(groupclick="toggleitem"),
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    fig.update_xaxes(**_HOUR_AXIS)
    fig.update_yaxes(title_text="SoC (kWh)", range=[0, 44], row=1, col=1, **_AXIS_STYLE)
    fig.update_yaxes(title_text="Net power (kW)", row=1, col=2, **_AXIS_STYLE)

    html = fig.to_html(
        full_html=False,
        include_plotlyjs=include_plotlyjs,
        config={"responsive": True},
        div_id="ev-overview",
    )
    out_path.write_text(html, encoding="utf-8")
    print(f"  saved {out_path}")


def plot_trajectories(world, household_agents, ev_agents, household_positions, out_path,
                      *, include_plotlyjs):
    """Interactive EV trajectory chart in the 2D grid."""
    pos_data = position_history(world)

    fig = go.Figure()

    # ── Household markers ─────────────────────────────────────────────────────
    hx = [pos.x for _, pos in household_positions]
    hy = [pos.y for _, pos in household_positions]
    fig.add_trace(go.Scatter(
        x=hx, y=hy,
        mode="markers+text",
        name="Household",
        marker=dict(symbol="square", size=18, color="dimgray", line=dict(color="white", width=2)),
        text=H_LABELS_SHORT,
        textposition="top center",
        textfont=dict(size=10, color="black"),
        hovertemplate="%{text}<extra></extra>",
    ))

    # ── EV trajectories ───────────────────────────────────────────────────────
    for ev, color, label in zip(ev_agents, EV_COLORS, EV_LABELS):
        track = pos_data.timeseries[ev.aid]
        xs = [p.x for p in track]
        ys = [p.y for p in track]
        hours = list(range(len(track)))

        # Path
        fig.add_trace(go.Scatter(
            x=xs, y=ys,
            mode="lines",
            name=label, legendgroup=label,
            line=dict(color=color, width=1.8),
            opacity=0.6,
            hovertemplate="Hour %{customdata}: (%{x:.2f}, %{y:.2f})<extra>" + label + "</extra>",
            customdata=hours,
        ))
        # Start marker
        fig.add_trace(go.Scatter(
            x=[xs[0]], y=[ys[0]],
            mode="markers",
            name=label + " start",
            legendgroup=label, showlegend=False,
            marker=dict(symbol="circle", size=9, color=color,
                        line=dict(color="white", width=1.5)),
            hovertemplate="Start: (%{x:.2f}, %{y:.2f})<extra>" + label + "</extra>",
        ))
        # End marker
        fig.add_trace(go.Scatter(
            x=[xs[-1]], y=[ys[-1]],
            mode="markers",
            name=label + " end",
            legendgroup=label, showlegend=False,
            marker=dict(symbol="star", size=14, color=color),
            hovertemplate="End: (%{x:.2f}, %{y:.2f})<extra>" + label + "</extra>",
        ))

    fig.update_layout(
        title=dict(text="EV Trajectories over 24 Hours", font=dict(size=14), x=0.5),
        height=520,
        xaxis=dict(title_text="x (grid units)", range=[-0.5, 10.5],
                   dtick=2, **_AXIS_STYLE),
        yaxis=dict(title_text="y (grid units)", range=[-0.5, 10.5],
                   dtick=2, scaleanchor="x", scaleratio=1, **_AXIS_STYLE),
        legend=dict(groupclick="toggleitem"),
        annotations=[dict(
            text="● start  ★ end",
            xref="paper", yref="paper",
            x=0.99, y=0.01, showarrow=False,
            font=dict(size=10, color="gray"),
        )],
        plot_bgcolor="white",
        paper_bgcolor="white",
    )

    html = fig.to_html(
        full_html=False,
        include_plotlyjs=include_plotlyjs,
        config={"responsive": True},
        div_id="ev-trajectories",
    )
    out_path.write_text(html, encoding="utf-8")
    print(f"  saved {out_path}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running EV coordination simulation …")
    world, h_agents, ev_agents, h_positions = asyncio.run(run_simulation())

    print("Generating interactive Plotly charts …")
    # First figure loads Plotly.js from CDN; subsequent figures reuse it.
    plot_overview(
        world, h_agents, ev_agents,
        OUT_DIR / "ev_soc_netpower.html",
        include_plotlyjs="cdn",
    )
    plot_trajectories(
        world, h_agents, ev_agents, h_positions,
        OUT_DIR / "ev_trajectories.html",
        include_plotlyjs=False,
    )
    print("Done.")
