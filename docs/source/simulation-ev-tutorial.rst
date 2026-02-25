============================================
Tutorial: Electric Vehicle Coordination
============================================

This tutorial builds an agent-based simulation of electric vehicles (EVs)
acting as mobile energy storages in a small neighbourhood.  Households with
photovoltaic (PV) generators produce surplus energy at midday; the objective
is to have EVs collect that surplus and deliver it to households with a
deficit — maximising local self-consumption and reducing grid exchange.

The scenario combines the major simulation features in one example: the
:class:`~mango.Area2D` space for spatial positioning, :meth:`~mango.Agent.on_step`
for physics-based movement and energy balance, message passing for
decentralised coordination, and data recording for result analysis.

.. note::
    This is a simplified toy example designed to demonstrate the simulation
    capabilities of mango.  Read :doc:`simulation` before starting.

----

Scenario
========

.. code-block:: text

      (0,10) ─────────────────── (10,10)
        │  H2(1,8)     H4(7,8)      │
        │                           │
        │     EV1  EV2  EV3         │
        │                           │
        │  H1(1,3)     H5(9,5)      │
        │         H3(5,2)           │
      (0,0)  ─────────────────── (10,0)

* **5 households** at fixed positions — each has a rooftop PV installation
  and a constant electrical load.
* **3 EVs** that can move freely in the 10×10 grid — each carries a battery
  that can be charged at a surplus household or discharged at a deficit one.
* **1 coordinator** that collects net-power reports from all households every
  step and dispatches EVs to the most urgent locations.

A **1-hour time step** is used.  Simulated time starts at midnight and covers
a full sunny summer day (PV peaks around solar noon).

----

Step 1 — Message types
======================

All coordination messages are plain Python dataclasses:

.. code-block:: python

    from dataclasses import dataclass
    from mango import Position2D

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
        action: str          # "charge" or "discharge"
        power_kw: float

----

Step 2 — Household agent
=========================

Each household computes its hourly PV/load energy balance in ``on_step`` and
reports the current net power to the coordinator.

.. code-block:: python

    import math
    from mango import Agent

    class HouseholdAgent(Agent):
        def __init__(self, pv_peak_kw: float, load_kw: float, coordinator_addr):
            super().__init__()
            self.pv_peak_kw = pv_peak_kw
            self.load_kw = load_kw
            self.coordinator_addr = coordinator_addr
            self.grid_import_kwh = 0.0
            self.grid_export_kwh = 0.0
            self.self_consumed_kwh = 0.0

        # on_step is synchronous — use schedule_instant_message to send
        def on_step(self, env, clock, step_size_s: float) -> None:
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
                NetPowerReport(self.aid, net, pos),
                self.coordinator_addr,
            )

        def handle_message(self, content, meta):
            pass  # households don't receive messages in this example

        def _pv_output_kw(self, time_s: float) -> float:
            """Sinusoidal profile peaking at solar noon (hour 12)."""
            hour = (time_s / 3600.0) % 24.0
            return max(0.0, self.pv_peak_kw * math.sin(math.pi * (hour - 6.0) / 12.0))

.. note::

    :meth:`~mango.Agent.on_step` is synchronous; ``await`` is not available
    inside it.  Use :meth:`~mango.Agent.schedule_instant_message` (or
    :meth:`~mango.Agent.schedule_instant_task` wrapping an ``async`` coroutine)
    to send messages from within ``on_step``.  The messages are dispatched
    during the convergence loop of the same simulation step, so the
    coordinator receives reports in the same step they are sent.

----

Step 3 — EV agent
==================

An EV moves through the space at a fixed speed.  When it reaches its assigned
target it charges or discharges its battery.

.. code-block:: python

    import math
    from mango import Agent, Position2D

    class EVAgent(Agent):
        def __init__(
            self,
            capacity_kwh: float,
            soc_kwh: float,
            max_power_kw: float,
            speed: float,   # grid units per hour
        ):
            super().__init__()
            self.capacity_kwh = capacity_kwh
            self.soc_kwh = soc_kwh
            self.max_power_kw = max_power_kw
            self.speed = speed
            self.target = None
            self.action = "idle"
            self.assigned_power_kw = 0.0

        def on_step(self, env, clock, step_size_s: float) -> None:
            if self.target is None:
                return

            step_h = step_size_s / 3600.0
            current = env.space.location(self)
            dx = self.target.x - current.x
            dy = self.target.y - current.y
            dist = math.sqrt(dx * dx + dy * dy)
            max_travel = self.speed * step_h

            if dist <= max_travel:
                # Arrived — snap to target and exchange energy.
                env.space.move(self, self.target)
                energy_kwh = self.assigned_power_kw * step_h
                if self.action == "charge":
                    self.soc_kwh = min(self.capacity_kwh,
                                       self.soc_kwh + energy_kwh)
                elif self.action == "discharge":
                    self.soc_kwh = max(0.0, self.soc_kwh - energy_kwh)
            else:
                # Still en route — advance toward target.
                ratio = max_travel / dist
                new_pos = Position2D(
                    current.x + dx * ratio,
                    current.y + dy * ratio,
                )
                env.space.move(self, new_pos)

        def handle_message(self, content, meta):
            if isinstance(content, EVAssignment):
                self.target = content.target
                self.action = content.action
                self.assigned_power_kw = content.power_kw

----

Step 4 — Coordinator agent
===========================

The coordinator accumulates net-power reports and dispatches EVs to the most
urgent locations on every step.  Surpluses are served first (EV charges
there), then deficits with remaining EV capacity.

.. code-block:: python

    from mango import Agent

    class CoordinatorAgent(Agent):
        def __init__(self, ev_addresses: list):
            super().__init__()
            self.ev_addresses = ev_addresses   # filled after EV registration
            self._powers: dict[str, float] = {}
            self._positions: dict[str, object] = {}

        def handle_message(self, content, meta):
            if isinstance(content, NetPowerReport):
                self._powers[content.sender_aid] = content.net_power_kw
                self._positions[content.sender_aid] = content.position

        def on_step(self, env, clock, step_size_s: float) -> None:
            if not self._powers:
                return

            surpluses = sorted(
                [(aid, p) for aid, p in self._powers.items() if p > 0.3],
                key=lambda x: -x[1],   # most surplus first
            )
            deficits = sorted(
                [(aid, p) for aid, p in self._powers.items() if p < -0.3],
                key=lambda x: x[1],   # most deficit first (most negative)
            )

            # Share the EV pool across both loops with a single iterator.
            ev_iter = iter(self.ev_addresses)
            for haid, power in surpluses:
                addr = next(ev_iter, None)
                if addr is None:
                    return
                self.schedule_instant_message(
                    EVAssignment(self._positions[haid], "charge", min(power, 3.3)),
                    addr,
                )
            for haid, power in deficits:
                addr = next(ev_iter, None)
                if addr is None:
                    return
                self.schedule_instant_message(
                    EVAssignment(self._positions[haid], "discharge", min(-power, 3.3)),
                    addr,
                )

.. note::

    The coordinator dispatches assignments based on reports from the
    **previous** step (a realistic one-step coordination lag).  Reports sent
    by households in step *N* are processed by ``handle_message`` during the
    convergence loop of step *N*, **after** ``on_step`` for step *N* has
    already run.  The new data is therefore available from step *N+1* onward.

----

Step 5 — World setup and spatial placement
==========================================

Create a :class:`~mango.SimulationWorld` with a 10×10
:class:`~mango.Area2D` space and zero message delay (all reports arrive
in the same step):

.. code-block:: python

    from mango import (
        create_world, step_simulation, SimpleCommunicationSimulation,
        DefaultEnvironment, Area2D, Position2D,
    )

    env = DefaultEnvironment(space=Area2D(width=10.0, height=10.0))
    world = create_world(
        start_time=0.0,
        communication_sim=SimpleCommunicationSimulation(default_delay_s=0.0),
        environment=env,
    )

Register the coordinator first (it is created without EV addresses — they
are added once the EVs are registered):

.. code-block:: python

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

Inside the ``async with world:`` block the space is initialised with random
positions.  Override the household positions to their fixed grid locations
immediately after entering the block:

.. code-block:: python

    household_positions = [
        (h1, Position2D(1.0, 3.0)),
        (h2, Position2D(1.0, 8.0)),
        (h3, Position2D(5.0, 2.0)),
        (h4, Position2D(7.0, 8.0)),
        (h5, Position2D(9.0, 5.0)),
    ]

    async def run():
        async with world:
            space = world.environment.space
            for agent, pos in household_positions:
                space.move(agent, pos)
            # EVs keep their random starting positions.

            # … recording and stepping (see Step 6)

----

Step 6 — Data recording and running
=====================================

Register data collectors **inside** the ``async with`` block (after the space
has been initialised) but **before** the first :func:`~mango.step_simulation`
call:

.. code-block:: python

    from mango import record_agent, record_position

    is_ev = lambda a: isinstance(a, EVAgent)
    is_hh = lambda a: isinstance(a, HouseholdAgent)

    async def run():
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

    import asyncio
    asyncio.run(run())

----

Step 7 — Inspecting the results
=================================

After the simulation finishes, inspect the recordings:

.. code-block:: python

    from mango import position_history

    soc_data = world.data_agent_collections["soc"]
    imp_data = world.data_agent_collections["import"]
    sc_data  = world.data_agent_collections["self_consumed"]
    pos_data = position_history(world)   # EV positions

    print("=== EV state of charge over 24 h (kWh) ===")
    for ev in [ev1, ev2, ev3]:
        values = [round(v, 1) for v in soc_data.timeseries[ev.aid]]
        print(f"  {ev.aid}: {values}")

    print("\n=== Household self-consumption rates ===")
    for h in [h1, h2, h3, h4, h5]:
        total_import = imp_data.timeseries[h.aid][-1]
        total_sc     = sc_data.timeseries[h.aid][-1]
        total        = total_import + total_sc
        rate = round(100 * total_sc / total, 1) if total > 0 else 100.0
        print(f"  {h.aid}: import={total_import:.2f} kWh  "
              f"self-consumption={rate}%")

    print("\n=== EV final positions ===")
    for ev in [ev1, ev2, ev3]:
        final = pos_data.timeseries[ev.aid][-1]
        print(f"  {ev.aid}: ({final.x:.2f}, {final.y:.2f})")

Example output (positions vary due to random start):

.. code-block:: text

    === EV state of charge over 24 h (kWh) ===
      agent4: [20.0, 20.0, ..., 26.5, 27.8, ..., 18.2]
      agent5: [15.0, 15.0, ..., 21.4, 22.7, ..., 14.9]
      agent6: [10.0, 10.0, ..., 15.1, 16.3, ..., 11.0]

    === Household self-consumption rates ===
      agent1: import=2.14 kWh  self-consumption=83.2%
      agent2: import=3.41 kWh  self-consumption=71.5%
      ...

    === EV final positions ===
      agent4: (1.00, 3.00)
      agent5: (5.00, 2.00)
      agent6: (7.00, 8.00)

----

Step 8 — Plotting the results
==============================

Use `Plotly <https://plotly.com/python>`_ to visualise the recordings as
interactive charts.  Two figures match those in the Julia version of this
tutorial.

**Figure 1 — EV state of charge and household net power**

.. code-block:: python

    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    soc_data = world.data_agent_collections["soc"]
    imp_data = world.data_agent_collections["import"]
    ex_data = world.data_agent_collections["export"]

    ev_colors = ["royalblue", "crimson", "darkorange"]
    ev_labels = ["EV 1", "EV 2", "EV 3"]
    h_colors = ["teal", "purple", "sienna", "olivedrab", "steelblue"]
    h_labels = ["H1 (6 kW PV)", "H2 (4 kW PV)", "H3 (7 kW PV)",
                 "H4 (5 kW PV)", "H5 (3 kW PV)"]

    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=["EV Battery State of Charge",
                        "Household Net Power (PV − Load)"],
        horizontal_spacing=0.10,
    )

    for col in (1, 2):
        fig.add_vrect(x0=6, x1=18, fillcolor="gold", opacity=0.12,
                      layer="below", line_width=0, row=1, col=col)
    fig.add_hline(y=40, line_dash="dash", line_color="lightgray",
                  annotation_text="Full (40 kWh)", row=1, col=1)

    hours = list(range(25))
    for ev, color, label in zip([ev1, ev2, ev3], ev_colors, ev_labels):
        soc = soc_data.timeseries[ev.aid]
        fig.add_trace(
            go.Scatter(x=hours[:len(soc)], y=soc, name=label,
                       legendgroup="EVs", legendgrouptitle_text="EVs",
                       line=dict(color=color, width=2.2)),
            row=1, col=1,
        )

    fig.add_hline(y=0, line_color="black", line_width=0.8, row=1, col=2)
    for h, color, label in zip([h1, h2, h3, h4, h5], h_colors, h_labels):
        imp = imp_data.timeseries[h.aid]
        ex = ex_data.timeseries[h.aid]
        net = [(ex[t] - ex[t-1]) - (imp[t] - imp[t-1]) for t in range(1, len(imp))]
        fig.add_trace(
            go.Scatter(x=list(range(1, len(net) + 1)), y=net, name=label,
                       legendgroup="Households", legendgrouptitle_text="Households",
                       line=dict(color=color, width=1.8)),
            row=1, col=2,
        )

    fig.update_layout(
        title=dict(text="EV Coordination Simulation — 24-Hour Overview", x=0.5),
        height=440, plot_bgcolor="white", paper_bgcolor="white",
    )
    fig.update_xaxes(range=[0, 24], dtick=4, title_text="Hour of day")
    fig.update_yaxes(title_text="SoC (kWh)", range=[0, 44], row=1, col=1)
    fig.update_yaxes(title_text="Net power (kW)", row=1, col=2)
    fig.write_html("ev_soc_netpower.html")

.. raw:: html
   :file: _static/ev_soc_netpower.html

The left panel shows each EV's battery level over the day.  All three EVs
discharge into deficit households overnight (before sunrise) and partially
recharge from surplus households during the solar window (shaded, 06:00–18:00).
The right panel confirms the expected sinusoidal PV profile: all households run
a deficit at night and — depending on their PV peak — surplus or marginal
balance around noon.

**Figure 2 — EV trajectories in the 2D grid**

.. code-block:: python

    from mango import position_history

    pos_data = position_history(world)

    fig2 = go.Figure()
    hx = [pos.x for _, pos in household_positions]
    hy = [pos.y for _, pos in household_positions]
    fig2.add_trace(go.Scatter(
        x=hx, y=hy, mode="markers+text", name="Household",
        marker=dict(symbol="square", size=18, color="dimgray",
                    line=dict(color="white", width=2)),
        text=["H1", "H2", "H3", "H4", "H5"],
        textposition="top center",
    ))

    for ev, color, label in zip([ev1, ev2, ev3], ev_colors, ev_labels):
        track = pos_data.timeseries[ev.aid]
        xs = [p.x for p in track]
        ys = [p.y for p in track]
        fig2.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines", name=label, legendgroup=label,
            line=dict(color=color, width=1.8), opacity=0.6,
        ))
        fig2.add_trace(go.Scatter(
            x=[xs[0]], y=[ys[0]], mode="markers",
            legendgroup=label, showlegend=False,
            marker=dict(symbol="circle", size=9, color=color,
                        line=dict(color="white", width=1.5)),
        ))
        fig2.add_trace(go.Scatter(
            x=[xs[-1]], y=[ys[-1]], mode="markers",
            legendgroup=label, showlegend=False,
            marker=dict(symbol="star", size=14, color=color),
        ))

    fig2.update_layout(
        title=dict(text="EV Trajectories over 24 Hours", x=0.5),
        height=520,
        xaxis=dict(title_text="x (grid units)", range=[-0.5, 10.5], dtick=2),
        yaxis=dict(title_text="y (grid units)", range=[-0.5, 10.5], dtick=2,
                   scaleanchor="x", scaleratio=1),
        plot_bgcolor="white", paper_bgcolor="white",
    )
    fig2.write_html("ev_trajectories.html")

.. raw:: html
   :file: _static/ev_trajectories.html

Each coloured path shows where one EV travelled over 24 hours.  The filled
circle marks the random starting position; the star marks the final position.
The EVs cluster around the households with the strongest surplus (H1, H3, H4 —
high PV peaks) during the solar window, then shift toward deficit locations
towards evening.

----

Complete standalone script
===========================

.. code-block:: python

    import asyncio
    import math
    from dataclasses import dataclass

    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    from mango import (
        Agent, Position2D,
        create_world, step_simulation,
        SimpleCommunicationSimulation, DefaultEnvironment, Area2D,
        record_agent, record_position, position_history,
    )

    # ── Message types ────────────────────────────────────────────────────────

    @dataclass
    class NetPowerReport:
        sender_aid: str
        net_power_kw: float
        position: Position2D

    @dataclass
    class EVAssignment:
        target: Position2D
        action: str       # "charge" or "discharge"
        power_kw: float

    # ── Household ────────────────────────────────────────────────────────────

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

    # ── EV ───────────────────────────────────────────────────────────────────

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

    # ── Coordinator ──────────────────────────────────────────────────────────

    class CoordinatorAgent(Agent):
        def __init__(self, ev_addresses):
            super().__init__()
            self.ev_addresses = ev_addresses
            self._powers: dict = {}
            self._positions: dict = {}

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

    # ── Simulation ───────────────────────────────────────────────────────────

    async def run():
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

        # ── Text results ─────────────────────────────────────────────────────

        soc_data = world.data_agent_collections["soc"]
        imp_data = world.data_agent_collections["import"]
        ex_data = world.data_agent_collections["export"]
        sc_data = world.data_agent_collections["self_consumed"]

        print("=== EV state of charge over 24 h (kWh) ===")
        for ev in [ev1, ev2, ev3]:
            values = [round(v, 1) for v in soc_data.timeseries[ev.aid]]
            print(f"  {ev.aid}: {values}")

        print("\n=== Household self-consumption rates ===")
        for h in [h1, h2, h3, h4, h5]:
            total_import = imp_data.timeseries[h.aid][-1]
            total_sc = sc_data.timeseries[h.aid][-1]
            total = total_import + total_sc
            rate = round(100 * total_sc / total, 1) if total > 0 else 100.0
            print(f"  {h.aid}: import={total_import:.2f} kWh  "
                  f"self-consumption={rate}%")

        pos_data = position_history(world)
        print("\n=== EV final positions ===")
        for ev in [ev1, ev2, ev3]:
            final = pos_data.timeseries[ev.aid][-1]
            print(f"  {ev.aid}: ({final.x:.2f}, {final.y:.2f})")

        # ── Plots ────────────────────────────────────────────────────────────

        ev_colors = ["royalblue", "crimson", "darkorange"]
        ev_labels = ["EV 1", "EV 2", "EV 3"]
        h_colors = ["teal", "purple", "sienna", "olivedrab", "steelblue"]
        h_labels = ["H1 (6 kW PV)", "H2 (4 kW PV)", "H3 (7 kW PV)",
                     "H4 (5 kW PV)", "H5 (3 kW PV)"]

        # Figure 1 — EV SoC and household net power
        fig1 = make_subplots(
            rows=1, cols=2,
            subplot_titles=["EV Battery State of Charge",
                            "Household Net Power (PV − Load)"],
            horizontal_spacing=0.10,
        )
        for col in (1, 2):
            fig1.add_vrect(x0=6, x1=18, fillcolor="gold", opacity=0.12,
                           layer="below", line_width=0, row=1, col=col)
        fig1.add_hline(y=40, line_dash="dash", line_color="lightgray",
                       annotation_text="Full (40 kWh)", row=1, col=1)
        hours = list(range(25))
        for ev, color, label in zip([ev1, ev2, ev3], ev_colors, ev_labels):
            soc = soc_data.timeseries[ev.aid]
            fig1.add_trace(
                go.Scatter(x=hours[:len(soc)], y=soc, name=label,
                           legendgroup="EVs", legendgrouptitle_text="EVs",
                           line=dict(color=color, width=2.2)),
                row=1, col=1,
            )
        fig1.add_hline(y=0, line_color="black", line_width=0.8, row=1, col=2)
        for h, color, label in zip([h1, h2, h3, h4, h5], h_colors, h_labels):
            imp = imp_data.timeseries[h.aid]
            ex = ex_data.timeseries[h.aid]
            net = [(ex[t] - ex[t-1]) - (imp[t] - imp[t-1]) for t in range(1, len(imp))]
            fig1.add_trace(
                go.Scatter(x=list(range(1, len(net) + 1)), y=net, name=label,
                           legendgroup="Households", legendgrouptitle_text="Households",
                           line=dict(color=color, width=1.8)),
                row=1, col=2,
            )
        fig1.update_layout(
            title=dict(text="EV Coordination Simulation — 24-Hour Overview",
                       x=0.5),
            height=440, plot_bgcolor="white", paper_bgcolor="white",
        )
        fig1.update_xaxes(range=[0, 24], dtick=4, title_text="Hour of day")
        fig1.update_yaxes(title_text="SoC (kWh)", range=[0, 44], row=1, col=1)
        fig1.update_yaxes(title_text="Net power (kW)", row=1, col=2)
        fig1.write_html("ev_soc_netpower.html")
        fig1.show()

        # Figure 2 — EV trajectories
        fig2 = go.Figure()
        hx = [pos.x for _, pos in household_positions]
        hy = [pos.y for _, pos in household_positions]
        fig2.add_trace(go.Scatter(
            x=hx, y=hy, mode="markers+text", name="Household",
            marker=dict(symbol="square", size=18, color="dimgray",
                        line=dict(color="white", width=2)),
            text=["H1", "H2", "H3", "H4", "H5"],
            textposition="top center",
        ))
        for ev, color, label in zip([ev1, ev2, ev3], ev_colors, ev_labels):
            track = pos_data.timeseries[ev.aid]
            xs = [p.x for p in track]
            ys = [p.y for p in track]
            fig2.add_trace(go.Scatter(
                x=xs, y=ys, mode="lines", name=label, legendgroup=label,
                line=dict(color=color, width=1.8), opacity=0.6,
            ))
            fig2.add_trace(go.Scatter(
                x=[xs[0]], y=[ys[0]], mode="markers",
                legendgroup=label, showlegend=False,
                marker=dict(symbol="circle", size=9, color=color,
                            line=dict(color="white", width=1.5)),
            ))
            fig2.add_trace(go.Scatter(
                x=[xs[-1]], y=[ys[-1]], mode="markers",
                legendgroup=label, showlegend=False,
                marker=dict(symbol="star", size=14, color=color),
            ))
        fig2.update_layout(
            title=dict(text="EV Trajectories over 24 Hours", x=0.5),
            height=520,
            xaxis=dict(title_text="x (grid units)", range=[-0.5, 10.5], dtick=2),
            yaxis=dict(title_text="y (grid units)", range=[-0.5, 10.5], dtick=2,
                       scaleanchor="x", scaleratio=1),
            plot_bgcolor="white", paper_bgcolor="white",
        )
        fig2.write_html("ev_trajectories.html")
        fig2.show()

    asyncio.run(run())

----

What's next?
============

* **Topology-aware coordination** — use :doc:`topology` to limit which
  households an EV can reach from its current position.
* **Stochastic delays** — swap :class:`~mango.SimpleCommunicationSimulation`
  for :class:`~mango.DelayProviderCommunicationSimulation` to model
  unreliable wireless communication between coordinator and EVs.
* **Competing objectives** — add a bidding role so households can auction
  their surplus and the coordinator resolves the market; see :doc:`role-api`.
* **Role-based refactor** — extract the energy-balance logic into a
  ``PVLoadRole`` and attach it to different base agents (household, factory,
  charging station) without code duplication.

.. seealso::

    :doc:`simulation` — full reference for the simulation world API
