.. _scenario-recipes:

=================================
Recipes: studies by research type
=================================

Each recipe on this page is a complete study for one kind of research
question: the question, a small model, the study design, the run and the
analysis.  The models are kept short and the studies small, so that every
recipe runs in a few seconds; each recipe says how to scale it.  The test
suite runs every recipe on this page, so the code and the printed numbers are
what the framework does.  :doc:`scenario` is the reference for every feature
used here, and its :ref:`table of features by question <scenario-cheatsheet>`
points to the right section.

.. list-table::
   :header-rows: 1
   :widths: 30 34 36

   * - Recipe
     - Research type
     - Shows
   * - `SEIR epidemic on a contact network`_
     - epidemiology, policy comparison under parameter uncertainty
     - a random population structure, ``by_attr`` and random links,
       ``aggregate="count"``, ``Lhs`` with common random numbers per sample
   * - `Signal corridor with a lane closure`_
     - traffic control
     - calendar time, an event for one agent (``emit_to``) with a start and
       an end, a per-link delay, interval maxima and means (``window=``),
       ``integrate``
   * - `Beer game with stochastic lead times`_
     - operations, supply chains
     - a study file, a preset with a demand step, named entities, directed
       up- and downstream links, the command line
   * - `Drone swarm with range-limited radio`_
     - swarm robotics, spatial models
     - ``Area2D``, a behavior that rewires a link every step
       (``ctx.relink``), a Sobol design and ``sobol_indices``
   * - `Double auction with paired comparisons`_
     - experimental economics
     - private values on their own stream, an event log of trades, a
       run-level metric, ``paired()`` over common random numbers
   * - `Gossip averaging in event-driven time`_
     - distributed systems
     - ``step: event``, timer tasks, delay distributions, a network
       partition, ``stop_when``, the topology as a factor
   * - `Opinion dynamics from files`_
     - computational social science
     - a scenario file with a ``parameters:`` block (bounds, choices),
       survey data with ``FromColumn``, a behavior for simultaneous pair
       updates, a warm-up, recurring media events, a study file
   * - `Schelling segregation for teaching`_
     - teaching agent-based modelling
     - ``mango-scenario new``, one readable file, ``to-script``, ``form``
   * - `Smart building on a Slurm cluster`_
     - reproducible studies on clusters
     - a profile across a daylight-saving switch, an ``executor:`` block,
       ``validate --build``, ``run-one``, ``status``, ``rerun``, manifests

.. note::

   The recipes need the extra ``scenario`` (``pip install
   "mango-agents[scenario]"``); the Latin hypercube and Sobol recipes also
   need scipy (extra ``scenario-all``).  Run the commands of a recipe in its
   project folder.

----

.. _scenario-recipes-design:

A study design in six decisions
===============================

Every recipe below makes the same six decisions explicitly; they are what a
reviewer asks for first.

1. **The outcome is one number per run**, computed from what the run
   recorded (an outcome function of the :class:`~mango.scenario.RunResult`).
   Outcomes recomputed from stored data can be fixed later without running
   anything again (``load_study(..., outcomes=...)``).
2. **Levers and uncertainties are separate.**  Levers are what you compare
   (a policy, an algorithm), uncertainties describe the world you are unsure
   about (a transmission rate, a lead time).  A conclusion about a lever
   should hold across the uncertainties, so cross them (``FullFactorial``)
   or sample them (``Lhs``, ``Sobol``).
3. **Replications, and which runs share random numbers.**  With common
   random numbers (CRN) the lever levels of a replication see the same random
   draws, so their *difference* has far less noise and needs fewer
   replications.  Runs that share a seed are one independent unit, not
   several: :meth:`~mango.scenario.StudyFrame.summary` and
   :meth:`~mango.scenario.StudyFrame.paired` count units, not runs (see
   :ref:`scenario-crn`).  Draw every input that must be common on its own
   stream (``e.rng_for("values")``), so that a variant's extra draws cannot
   shift it.
4. **The design fits the question.**  A full factorial compares a few
   levels, a Latin hypercube spreads few runs over continuous ranges, a Sobol
   design attributes the variance of an outcome to the factors.
5. **Transients are handled on purpose.**  A recorded row at ``t > 0`` is
   the state after the step that ended at ``t``, the row at ``t = 0`` is the
   initial state (:ref:`scenario-timing`).  Leave a warm-up out with a phase
   ``record: false``, start a model in a steady state, or measure the
   transient as the outcome.  Declare what a recorded value is: a flow set
   in ``on_step`` (``kind="flow"``) or a level changed by messages, tasks or
   events (``kind="state"``, e.g. a queue length), so that
   :meth:`~mango.scenario.RunResult.integrate` and
   :meth:`~mango.scenario.RunResult.time_average` weight it correctly
   (:ref:`flows and states <scenario-state-flow>`).
6. **Pilot first, then scale.**  ``mango-scenario validate --build`` and a
   short run (``--until``) catch errors before a long study; a pilot with
   few replications shows the noise, and
   :meth:`frame.replications_needed(outcome, half_width=...)
   <mango.scenario.StudyFrame.replications_needed>` turns it into the
   number of replications.  Report intervals, not just means, and one value
   per run (the rows of one run are not independent; see
   :ref:`scenario-crn-analysis`).

----

SEIR epidemic on a contact network
==================================

*Which vaccination strategy (none, random, by contact degree) lowers the
attack rate most, when the transmission probability and the incubation
period are uncertain?*

- **Model**: persons in households of three (all pairs within a household
  are contacts) plus random community contacts; an infectious person exposes
  every contact once a day; daily steps for 60 days.
- **Lever**: ``seir.vaccination`` (20 % coverage).  **Uncertainties**:
  ``seir.beta`` in [0.02, 0.10] and ``seir.incubation_days`` in [3, 7],
  sampled with a Latin hypercube.
- **Seeds**: an ``Lhs`` design defaults to ``common_random_numbers="levers"``:
  the three strategies of a sample share a seed (a paired comparison), and
  every sample has its own.
- **Outcomes**: attack rate and peak prevalence, from compartment counts.

.. testsetup:: seir

    import os
    import tempfile

    _seir_cwd = os.getcwd()
    _seir_tmp = tempfile.TemporaryDirectory()
    os.chdir(_seir_tmp.name)

The person is an ordinary agent.  Messages sent in ``on_step`` are delivered
before the step ends, so a person exposed during a day is already ``E`` in
the row at the end of that day:

.. testcode:: seir

    from pydantic import BaseModel

    from mango import Agent
    from mango.scenario import Scenario, ScenarioParams, patterns


    class Person(Agent):
        def __init__(self, p):
            super().__init__()
            self.p, self.state = p, "S"

        def on_step(self, env, clock, step_size_s):
            draw = self.rng.random()  # one draw per day, whatever the state
            if self.state == "E" and draw < 1 / self.p.incubation_days:
                self.state = "I"
            elif self.state == "I":
                contacts = self.neighbors(tid="household") + self.neighbors(tid="community")
                for contact in contacts:
                    self.schedule_instant_message("exposure", contact)
                if draw < 1 / self.p.infectious_days:
                    self.state = "R"

        def handle_message(self, content, meta):
            if self.state == "S" and self.rng_for("infection").random() < self.p.beta:
                self.state = "E"


    class Disease(BaseModel, extra="forbid"):
        n: int = 60
        household_size: int = 3
        contacts: float = 0.05  # probability of a community contact per pair
        beta: float = 0.05  # infection probability per contact and day
        incubation_days: float = 5.0
        infectious_days: float = 7.0
        index_cases: int = 2
        vaccination: str = "none"  # none | random | targeted
        coverage: float = 0.2


    class Params(ScenarioParams):
        seir: Disease = Disease()


    scenario = Scenario("seir", params=Params)
    scenario.time(horizon="60d", step="1d")
    person = scenario.group(
        "person", "seir.n", lambda e: Person(e.p.seir),
        install=lambda e: {"household": e.index // e.p.seir.household_size},
    )
    scenario.link("household", person, pattern=patterns.by_attr("household"))
    scenario.link("community", person, pattern={"random": {"p": "$seir.contacts"}})
    scenario.record(
        "state", person, attr="state", aggregate="count",
        categories=["S", "E", "I", "R", "V"],  # every run has every column
    )

The index cases and the vaccination are set once the population and its
links exist, in an ``on_build`` hook.  Both draw from named world streams, so
they do not touch the persons' streams.  The index cases are drawn first,
from all persons, so that every strategy of a replication starts from the
same outbreak; vaccination then picks from the others.  (Drawing them after
vaccination, from those still susceptible, would make the index cases
depend on the lever: common random numbers cancel only the noise the
strategies share, so the paired differences would be noisier.)

.. testcode:: seir

    @scenario.on_build
    def infect_and_vaccinate(ctx):
        p, people = ctx.p.seir, ctx.agents("person")
        cases = ctx.rng("index_cases").choice(len(people), p.index_cases, replace=False)
        for i in cases:  # the same index cases under every strategy
            people[i].state = "I"
        others = [a for a in people if a.state == "S"]
        k = round(p.coverage * len(people))
        if p.vaccination == "random":
            picks = ctx.rng("vaccination").choice(len(others), k, replace=False)
            chosen = [others[i] for i in picks]
        elif p.vaccination == "targeted":  # the most community contacts first
            chosen = sorted(others, key=lambda a: -len(a.neighbors(tid="community")))[:k]
        else:
            chosen = []
        for agent in chosen:
            agent.state = "V"


    result = scenario.run(seed=1, overrides={"seir.beta": 0.08})
    print(result.wide("state")[["S", "E", "I", "R"]].iloc[[0, 10, 20, 60]])

.. testoutput:: seir
   :options: +NORMALIZE_WHITESPACE

   entity        S    E     I     R
   time
   0.0        58.0  0.0   2.0   0.0
   864000.0   47.0  4.0   5.0   4.0
   1728000.0  31.0  4.0  11.0  14.0
   5184000.0  12.0  0.0   1.0  47.0

.. testcode:: seir
   :hide:

   import asyncio


   async def _initial_states(strategy):  # the claim above: same index cases
       run = scenario.build(seed=3, overrides={"seir.vaccination": strategy})
       async with run:
           return [a.state for a in run.ctx.agents("person")]


   _starts = {v: asyncio.run(_initial_states(v)) for v in ["none", "random", "targeted"]}
   _cases = {v: [i for i, x in enumerate(s) if x == "I"] for v, s in _starts.items()}
   assert _cases["none"] == _cases["random"] == _cases["targeted"], _cases
   assert len(_cases["none"]) == 2
   assert _starts["random"].count("V") == _starts["targeted"].count("V") == 12
   assert _starts["random"] != _starts["targeted"]

``aggregate="count"`` writes one row per state and sample, so ``wide`` is
the epidemic curve: a time × compartment table.  ``categories`` lists the
states, so a run without vaccination still has a ``V`` column of zeros
(curves averaged over runs need no ``reindex``).  The study crosses the
three strategies with four Latin hypercube samples and two replications:

.. testcode:: seir

    from mango.scenario import Lhs, Study, load_study


    def attack_rate(run):
        """Share of the population ever infected."""
        last = run.wide("state").iloc[-1]
        return float(last[["E", "I", "R"]].sum() / last.sum())


    def peak_prevalence(run):
        counts = run.wide("state")
        return float((counts["I"] / counts.sum(axis=1)).max())


    study = Study(
        scenario,
        uncertainties={
            "seir.beta": {"range": [0.02, 0.10]},
            "seir.incubation_days": {"range": [3.0, 7.0]},
        },
        levers={"seir.vaccination": ["none", "random", "targeted"]},
        design=Lhs(4),
        replications=2,
        seed=2026,
        outcomes=[attack_rate, peak_prevalence],
        out="results/seir",
    )
    print(study.run(progress=False).done)
    frame = load_study("results/seir")
    paired = frame.paired("seir.vaccination", "attack_rate", baseline="none")
    print(paired[["seir.vaccination", "pairs", "n", "mean", "ci_low", "ci_high"]].round(3))

.. testoutput:: seir
   :options: +NORMALIZE_WHITESPACE

   24
     seir.vaccination  pairs  n   mean  ci_low  ci_high
   0           random      8  8 -0.088  -0.194    0.019
   1         targeted      8  8 -0.181  -0.399    0.037

``paired`` subtracts the ``none`` run from the run of each strategy with the
same sample and replication (eight pairs) and gives a Student-t interval
over them.  Eight pairs are far too few to decide anything; the intervals
say so.  To scale: a population of a few thousand persons, ``Lhs(64)`` and
ten replications, on a process pool (``study.run(workers=8)``, which
reuses each worker for several short runs).

.. testcleanup:: seir

    os.chdir(_seir_cwd)
    _seir_tmp.cleanup()

----

Signal corridor with a lane closure
===================================

*Does a green wave (signals offset by the travel time) reduce the waiting
time on an arterial with four signals, and does it help at the bottleneck
when a lane at the second signal is closed from 08:00 to 08:30?*

- **Model**: four signals in a row; vehicles enter at the first signal and
  from side streets, wait in a queue and are served while the signal is
  green; served vehicles travel to the next signal as a message.  Steps of
  10 s from 07:30 to 09:00.
- **Lever**: ``corridor.strategy`` (``fixed``: all signals switch together,
  ``green_wave``: each signal is offset by the travel time).  **Disturbance**:
  the event ``closure`` reaches only ``signal_1``, at its start and its end.
- **Seeds**: a full factorial design shares the seed of a replication
  between the strategies, so both see the same arrivals.
- **Outcomes**: the total waiting time (vehicle-minutes) and the longest
  queue at ``signal_1`` during the closure, both from five-minute window
  recorders instead of 10-second snapshots.

.. testsetup:: corridor

    import os
    import tempfile

    _corridor_cwd = os.getcwd()
    _corridor_tmp = tempfile.TemporaryDirectory()
    os.chdir(_corridor_tmp.name)

.. testcode:: corridor

    from typing import Literal

    from pydantic import BaseModel

    from mango import Agent
    from mango.scenario import Scenario, ScenarioParams


    class Signal(Agent):
        """One intersection: a queue of eastbound vehicles, green half of each cycle."""

        def __init__(self, index, p):
            super().__init__()
            self.p, self.queue, self.capacity = p, 0, p.saturation
            self.arrivals = p.entry if index == 0 else p.side  # vehicles per step
            self.offset_s = index * p.travel_s if p.strategy == "green_wave" else 0.0

        def on_step(self, env, clock, step_size_s):
            self.queue += int(self.rng.poisson(self.arrivals))
            if (clock.time - self.offset_s) % self.p.cycle_s < self.p.cycle_s / 2:
                served = min(self.queue, self.capacity)
                self.queue -= served
                for downstream in self.neighbors(tid="arterial"):
                    self.schedule_instant_message(served, downstream)

        def handle_message(self, content, meta):  # a platoon from upstream
            self.queue += content

        def on_agent_event(self, event):  # only signal_1 gets the closure
            if event.kind == "lane_closure":
                factor = event["factor"] if event.stage == "start" else 1.0
                self.capacity = round(self.p.saturation * factor)


    class Corridor(BaseModel, extra="forbid"):
        n: int = 4
        strategy: Literal["fixed", "green_wave"] = "fixed"
        cycle_s: float = 60.0
        travel_s: float = 20.0  # served at t, queued at the next signal at t + 20 s
        entry: float = 0.6  # vehicles per step entering at signal_0
        side: float = 0.05  # vehicles per step from each side street
        saturation: int = 2  # vehicles served per green step


    class Params(ScenarioParams):
        corridor: Corridor = Corridor()


    scenario = Scenario("corridor", params=Params)
    scenario.time(start="2024-05-06T07:30", horizon="1h30min", step="10s")
    signal = scenario.group("signal", "corridor.n", lambda e: Signal(e.index, e.p.corridor))
    # a platoon sent in the step [t, t + 10 s) is handled 10 s later, at t + 20 s
    scenario.link("arterial", signal, pattern="line", directed=True, delay="10s")
    scenario.record("queue_max", signal, attr="queue", every="5min", window="max")
    scenario.record(
        "queue_mean", signal, attr="queue", every="5min", window="mean", kind="flow"
    )
    scenario.event(
        "2024-05-06T08:00", until="2024-05-06T08:30", name="closure",
        emit={"kind": "lane_closure", "factor": 0.5}, emit_to="signal_1",
    )
    scenario.defaults({"phases": [
        {"name": "before", "until": "2024-05-06T08:00"},
        {"name": "closure", "until": "2024-05-06T08:30"},
    ]})
    result = scenario.run(seed=1)
    print(result.wide("queue_max", index="datetime").iloc[5:10])

.. testoutput:: corridor
   :options: +NORMALIZE_WHITESPACE

   entity               signal_0  signal_1  signal_2  signal_3
   datetime
   2024-05-06 07:55:00       5.0       5.0       4.0       4.0
   2024-05-06 08:00:00       3.0       3.0       3.0       4.0
   2024-05-06 08:05:00       2.0       4.0       3.0       3.0
   2024-05-06 08:10:00       6.0      15.0       2.0       2.0
   2024-05-06 08:15:00       2.0      21.0       3.0       2.0

A ``window="max"`` recorder evaluates every step and writes the largest
queue of each five-minute window: the row at 08:10 covers the steps from
08:05 to 08:10.  A plain ``every="5min"`` recorder would write the queue at
the window ends only and miss the peaks between the green phases.  The
closure halves ``signal_1``'s capacity from 08:00; the queue builds up from
the next window on.

The waiting time is the integral of the mean queue:
``integrate("queue_mean", unit="1min")`` sums each window's mean queue times
its length (the row at ``t = 0`` is the initial state and does not count).
The queue is set in ``on_step`` for the step, so it is declared a flow; a
queue that only messages or tasks change would be ``kind="state"``, whose
window mean weights each level with the time it held
(:ref:`flows and states <scenario-state-flow>`):

.. testcode:: corridor

    from mango.scenario import Study, load_study


    def waiting_veh_min(run):
        return run.integrate("queue_mean", unit="1min")


    def closure_peak(run):
        return float(run.ts("queue_max", entity="signal_1", phase="closure").max())


    study = Study(
        scenario,
        levers={"corridor.strategy": ["fixed", "green_wave"]},
        replications=5,
        seed=7,
        outcomes=[waiting_veh_min, closure_peak],
        out="results/corridor",
    )
    study.run(progress=False)
    paired = load_study("results/corridor").paired("corridor.strategy", baseline="fixed")
    print(paired[["outcome", "n", "mean", "ci_low", "ci_high"]].round(1))

.. testoutput:: corridor
   :options: +NORMALIZE_WHITESPACE

              outcome  n   mean  ci_low  ci_high
   0  waiting_veh_min  5 -293.0   -381.6   -204.4
   1     closure_peak  5   -1.0     -2.8      0.8

The green wave saves about 290 vehicle-minutes over the 90 minutes, but it
does not shorten the queue at the closed lane: there the capacity, not the
coordination, is the bottleneck.  To scale: real demand from a profile
(``e.profile("demand")``), more strategies (vehicle-actuated control is a
third ``strategy``), and 30 replications.

.. testcleanup:: corridor

    os.chdir(_corridor_cwd)
    _corridor_tmp.cleanup()

----

Beer game with stochastic lead times
====================================

*Does a smoothed order-up-to policy dampen the bullwhip effect of the beer
distribution game after a step in customer demand, with fixed and with random
shipping times?*  The study is a file that students and colleagues edit
without touching Python; the model is one module.

- **Model**: four stages (retailer, wholesaler, distributor, factory) in
  weekly steps.  Orders travel upstream as messages and are seen one week
  later; shipments travel downstream and arrive after a lead time drawn per
  shipment.  Customer demand is Normal(4, 1) and steps to 8 in week 5.
- **Lever**: ``chain.smoothing`` (1: plain order-up-to; 4: the gap to the
  target is closed over four weeks).  **Uncertainty**: ``chain.lead_weeks``,
  fixed at 2 weeks or uniform on 1 to 3 weeks.
- **Seeds**: common random numbers (the full factorial default): every
  design point of a replication sees the same customer demand, drawn on its
  own stream (``rng_for("demand")``), so lead-time draws never shift it.
- **Outcomes**: the bullwhip ratio (variance of the factory's orders over
  the variance of customer demand) and the total cost.

.. testsetup:: beergame

    import os
    import sys
    import tempfile

    _beer_cwd = os.getcwd()
    _beer_tmp = tempfile.TemporaryDirectory()
    os.chdir(_beer_tmp.name)
    sys.path.insert(0, _beer_tmp.name)
    _BEER_FILES = {
        'beer.py': '''"""The beer distribution game: four stages, weekly steps, stochastic shipping times."""

    from pydantic import BaseModel

    from mango import Agent
    from mango.scenario import Dist, IntUniform, Scenario, ScenarioParams

    WEEK = 7 * 24 * 3600
    STAGES = ["retailer", "wholesaler", "distributor", "factory"]


    class Stage(Agent):
        def __init__(self, p):
            super().__init__()
            self.p, self.demand_mean = p, p.demand_mean
            self.on_hand, self.backlog, self.on_order = 8.0, 0.0, 2 * p.demand_mean
            self.forecast, self.orders_in = p.demand_mean, p.demand_mean
            self.pipeline = {1: p.demand_mean, 2: p.demand_mean}  # week -> arrivals
            self.demand = self.order_qty = self.cost = 0.0

        def on_step(self, env, clock, step_size_s):
            p, week = self.p, round(clock.time / WEEK)
            arrived = self.pipeline.pop(week, 0.0)
            self.on_hand, self.on_order = self.on_hand + arrived, self.on_order - arrived
            customers = self.neighbors(tid="downstream")
            if customers:  # what the downstream stage ordered last week
                self.demand, self.orders_in = self.orders_in, 0.0
            else:  # the retailer draws the customer demand
                draw = self.rng_for("demand").normal(self.demand_mean, p.demand_std)
                self.demand = max(0.0, round(draw))
            self.backlog += self.demand
            shipped = min(self.on_hand, self.backlog)
            self.on_hand, self.backlog = self.on_hand - shipped, self.backlog - shipped
            lead = int(p.lead_weeks.draw(self.rng))  # one draw per stage and week
            for customer in customers:
                self.schedule_instant_message(("ship", shipped, week + lead), customer)
            # order up to a target on a smoothed forecast; like most players, the
            # stage counts only part of what is already on its way
            self.forecast += p.alpha * (self.demand - self.forecast)
            position = self.on_hand - self.backlog + p.supply_line_weight * self.on_order
            gap = self.forecast * p.cover_weeks - position
            self.order_qty = max(0.0, self.forecast + gap / p.smoothing)
            self.on_order += self.order_qty
            suppliers = self.neighbors(tid="upstream")
            for supplier in suppliers:
                self.schedule_instant_message(("order", self.order_qty, None), supplier)
            if not suppliers:  # the factory produces, with the same lead times
                due = week + lead
                self.pipeline[due] = self.pipeline.get(due, 0.0) + self.order_qty
            self.cost = p.holding * self.on_hand + p.backlog_cost * self.backlog

        def handle_message(self, content, meta):
            kind, quantity, week = content
            if kind == "order":
                self.orders_in += quantity
            else:
                self.pipeline[week] = self.pipeline.get(week, 0.0) + quantity

        def on_agent_event(self, event):  # the demand step reaches the retailer
            if event.kind == "demand_step":
                self.demand_mean = event["mean"]


    class Chain(BaseModel, extra="forbid"):
        demand_mean: float = 4.0
        demand_std: float = 1.0
        lead_weeks: Dist = IntUniform(1, 3)  # shipping and production time
        alpha: float = 0.3  # forecast smoothing
        cover_weeks: float = 3.0  # target inventory position, in weeks of forecast
        supply_line_weight: float = 0.8  # share of the goods on their way counted
        smoothing: float = 1.0  # 1: order-up-to; 4: close the gap over 4 weeks
        holding: float = 0.5  # cost per unit and week
        backlog_cost: float = 1.0


    class Params(ScenarioParams):
        chain: Chain = Chain()


    scenario = Scenario("beergame", params=Params)
    stage = scenario.group(
        "stage", 4, lambda e: Stage(e.p.chain), naming=lambda e: STAGES[e.index]
    )
    scenario.link("upstream", stage, pattern="line", directed=True)
    scenario.link("downstream", stage, pattern={"reverse": "line"}, directed=True)
    scenario.record("order_qty", stage, attr="order_qty")
    scenario.record("demand", stage, attr="demand")
    scenario.record("cost", stage, attr="cost", kind="flow")  # the week's cost


    def bullwhip(run):
        """Var(factory orders) / Var(customer demand) over the 52 weeks (not t = 0)."""
        orders = run.ts("order_qty", entity="factory").iloc[1:]
        demand = run.ts("demand", entity="retailer").iloc[1:]
        return float(orders.var() / demand.var())


    def total_cost(run):
        """Holding and backlog cost of all stages: cost per week, integrated over weeks."""
        return run.integrate("cost", unit="1w")
    ''',
        'presets/base.yaml': '''# One year in weekly steps; the customer demand steps from 4 to 8 in week 5.
    time: {horizon: 52w, step: 1w}
    events:
      - {name: demand_step, at: 4w, emit: {kind: demand_step, mean: 8.0}, emit_to: retailer}
    ''',
        'studies/policies.yaml': '''# Does a smoothed order-up-to policy tame the bullwhip, with fixed and with
    # random shipping times?  2 x 2 design points x 10 replications = 40 runs.
    scenario: beer:scenario
    base: presets/base.yaml
    seed: 2026
    uncertainties:
      chain.lead_weeks: {fixed: 2, random: {int_uniform: [1, 3]}}
    levers:
      chain.smoothing: {order_up_to: 1, smoothed: 4}
    replications: 10
    outcomes: [beer:bullwhip, beer:total_cost]
    out: results/policies
    ''',
    }
    for _name, _text in _BEER_FILES.items():
        os.makedirs(os.path.dirname(_name) or ".", exist_ok=True)
        with open(_name, "w") as file:
            file.write(_text)

.. testcode:: beergame
   :hide:

   for _name, _text in _BEER_FILES.items():  # the files shown below
       assert open(_name).read() == _text

.. code-block:: python
   :caption: beer.py

   """The beer distribution game: four stages, weekly steps, stochastic shipping times."""

   from pydantic import BaseModel

   from mango import Agent
   from mango.scenario import Dist, IntUniform, Scenario, ScenarioParams

   WEEK = 7 * 24 * 3600
   STAGES = ["retailer", "wholesaler", "distributor", "factory"]


   class Stage(Agent):
       def __init__(self, p):
           super().__init__()
           self.p, self.demand_mean = p, p.demand_mean
           self.on_hand, self.backlog, self.on_order = 8.0, 0.0, 2 * p.demand_mean
           self.forecast, self.orders_in = p.demand_mean, p.demand_mean
           self.pipeline = {1: p.demand_mean, 2: p.demand_mean}  # week -> arrivals
           self.demand = self.order_qty = self.cost = 0.0

       def on_step(self, env, clock, step_size_s):
           p, week = self.p, round(clock.time / WEEK)
           arrived = self.pipeline.pop(week, 0.0)
           self.on_hand, self.on_order = self.on_hand + arrived, self.on_order - arrived
           customers = self.neighbors(tid="downstream")
           if customers:  # what the downstream stage ordered last week
               self.demand, self.orders_in = self.orders_in, 0.0
           else:  # the retailer draws the customer demand
               draw = self.rng_for("demand").normal(self.demand_mean, p.demand_std)
               self.demand = max(0.0, round(draw))
           self.backlog += self.demand
           shipped = min(self.on_hand, self.backlog)
           self.on_hand, self.backlog = self.on_hand - shipped, self.backlog - shipped
           lead = int(p.lead_weeks.draw(self.rng))  # one draw per stage and week
           for customer in customers:
               self.schedule_instant_message(("ship", shipped, week + lead), customer)
           # order up to a target on a smoothed forecast; like most players, the
           # stage counts only part of what is already on its way
           self.forecast += p.alpha * (self.demand - self.forecast)
           position = self.on_hand - self.backlog + p.supply_line_weight * self.on_order
           gap = self.forecast * p.cover_weeks - position
           self.order_qty = max(0.0, self.forecast + gap / p.smoothing)
           self.on_order += self.order_qty
           suppliers = self.neighbors(tid="upstream")
           for supplier in suppliers:
               self.schedule_instant_message(("order", self.order_qty, None), supplier)
           if not suppliers:  # the factory produces, with the same lead times
               due = week + lead
               self.pipeline[due] = self.pipeline.get(due, 0.0) + self.order_qty
           self.cost = p.holding * self.on_hand + p.backlog_cost * self.backlog

       def handle_message(self, content, meta):
           kind, quantity, week = content
           if kind == "order":
               self.orders_in += quantity
           else:
               self.pipeline[week] = self.pipeline.get(week, 0.0) + quantity

       def on_agent_event(self, event):  # the demand step reaches the retailer
           if event.kind == "demand_step":
               self.demand_mean = event["mean"]


   class Chain(BaseModel, extra="forbid"):
       demand_mean: float = 4.0
       demand_std: float = 1.0
       lead_weeks: Dist = IntUniform(1, 3)  # shipping and production time
       alpha: float = 0.3  # forecast smoothing
       cover_weeks: float = 3.0  # target inventory position, in weeks of forecast
       supply_line_weight: float = 0.8  # share of the goods on their way counted
       smoothing: float = 1.0  # 1: order-up-to; 4: close the gap over 4 weeks
       holding: float = 0.5  # cost per unit and week
       backlog_cost: float = 1.0


   class Params(ScenarioParams):
       chain: Chain = Chain()


   scenario = Scenario("beergame", params=Params)
   stage = scenario.group(
       "stage", 4, lambda e: Stage(e.p.chain), naming=lambda e: STAGES[e.index]
   )
   scenario.link("upstream", stage, pattern="line", directed=True)
   scenario.link("downstream", stage, pattern={"reverse": "line"}, directed=True)
   scenario.record("order_qty", stage, attr="order_qty")
   scenario.record("demand", stage, attr="demand")
   scenario.record("cost", stage, attr="cost", kind="flow")  # the week's cost


   def bullwhip(run):
       """Var(factory orders) / Var(customer demand) over the 52 weeks (not t = 0)."""
       orders = run.ts("order_qty", entity="factory").iloc[1:]
       demand = run.ts("demand", entity="retailer").iloc[1:]
       return float(orders.var() / demand.var())


   def total_cost(run):
       """Holding and backlog cost of all stages: cost per week, integrated over weeks."""
       return run.integrate("cost", unit="1w")

The stages are one group whose entities are named by a function
(``retailer`` ... ``factory``); two directed links give every stage its
supplier (``upstream``: ``e_i -> e_(i+1)``) and its customer
(``downstream``, the reversed line).  The demand step is data in the
preset, and the study is a file:

.. code-block:: yaml
   :caption: presets/base.yaml

   # One year in weekly steps; the customer demand steps from 4 to 8 in week 5.
   time: {horizon: 52w, step: 1w}
   events:
     - {name: demand_step, at: 4w, emit: {kind: demand_step, mean: 8.0}, emit_to: retailer}

.. code-block:: yaml
   :caption: studies/policies.yaml

   # Does a smoothed order-up-to policy tame the bullwhip, with fixed and with
   # random shipping times?  2 x 2 design points x 10 replications = 40 runs.
   scenario: beer:scenario
   base: presets/base.yaml
   seed: 2026
   uncertainties:
     chain.lead_weeks: {fixed: 2, random: {int_uniform: [1, 3]}}
   levers:
     chain.smoothing: {order_up_to: 1, smoothed: 4}
   replications: 10
   outcomes: [beer:bullwhip, beer:total_cost]
   out: results/policies

From the project folder, check, plan and run it:

.. code-block:: console

    $ mango-scenario validate studies/policies.yaml
    OK: studies/policies.yaml is a valid study: 40 runs (4 agents each, 2 links), scenario beer:scenario; parameters, declarations and data files of 4 distinct parameter sets checked
    note: agents were not built; --build builds run 0 and takes one step (checks factories, profile columns picked at run time and recorder attributes)
    $ mango-scenario plan beer:scenario -p presets/base.yaml
    Scenario 'beergame': 4 agents in 1 group, 2 links, 3 recorders
      time: horizon 52w, step 1w (52 steps)
      groups:
        stage                 4  retailer .. factory
      links:
        upstream         stage -> stage (line); 3 edges, directed
        downstream       stage -> stage (reverse(line)); 3 edges, directed
      recorders:
        order_qty        stage
        demand           stage
        cost             stage (flow)
      events:
        demand_step      4w  (emit {'kind': 'demand_step', 'mean': 8.0} to retailer (on_agent_event))
    $ mango-scenario run studies/policies.yaml -q
    40 runs: 40 done, 0 skipped, 0 failed in ... s (sequential) -> .../results/policies

The same study file works from Python; a second run skips the finished runs.
``summary`` gives the mean and a confidence interval per design point,
``paired`` the difference the lever makes within each replication:

.. testcode:: beergame

    from mango.scenario import Study, load_study

    Study.from_yaml("studies/policies.yaml").run(progress=False)  # resumes: nothing to do
    frame = load_study("results/policies")
    levels = ["chain.lead_weeks:label", "chain.smoothing:label"]
    summary = frame.summary("bullwhip", by=levels)
    print(summary[[*levels, "n", "mean", "ci_low", "ci_high"]].round(1))
    paired = frame.paired("chain.smoothing", "total_cost", baseline="order_up_to",
                          by="chain.lead_weeks:label")
    print(paired[["chain.lead_weeks:label", "n", "mean", "ci_low", "ci_high"]].round(1))

.. testoutput:: beergame
   :options: +NORMALIZE_WHITESPACE

     chain.lead_weeks:label chain.smoothing:label   n   mean  ci_low  ci_high
   0                  fixed           order_up_to  10  246.0   201.3    290.8
   1                  fixed              smoothed  10   25.2    21.7     28.7
   2                 random           order_up_to  10  216.0   174.1    258.0
   3                 random              smoothed  10   25.5    22.6     28.5
     chain.lead_weeks:label   n    mean  ci_low  ci_high
   0                  fixed  10 -1194.9 -1454.6   -935.3
   1                 random  10  -876.5 -1072.6   -680.4

The bullwhip ratios are large because every stage amplifies the orders it
receives; smoothing the order policy cuts the ratio by an order of
magnitude and the cost by 30 to 40 %, with fixed and with random lead
times.  Students change a number in the preset or on the command line
(``mango-scenario run studies/policies.yaml --set chain.alpha=0.5 --out
results/alpha05``) and the new runs get new run ids.  To scale: 30
replications, more policies as levels of ``chain.smoothing`` or of a policy
``kind`` (see :ref:`scenario-subexperiments`), and ``--workers``.

.. testcleanup:: beergame

    os.chdir(_beer_cwd)
    sys.path.remove(_beer_tmp.name)
    sys.modules.pop("beer", None)
    _beer_tmp.cleanup()

----


Drone swarm with range-limited radio
====================================

*How much do drone speed and radio range each contribute to how much the
swarm knows about the targets in its area?*  Both are continuous factors,
so a Sobol design attributes the variance of the outcome to them.

- **Model**: drones random-walk in a 100 m × 100 m ``Area2D``, find
  targets within 5 m, and send what they know to every drone within radio
  range.  The radio link changes every step: a behavior recomputes it from
  the positions with :meth:`ctx.relink <mango.scenario.BuildContext.relink>`.
- **Uncertainties**: ``swarm.speed`` in [0.5, 4] m/s and ``swarm.radio`` in
  [0, 50] m, Saltelli design ``Sobol(8)`` (8 × (2 + 2) = 32 runs).
- **Seeds**: a Sobol design keeps one seed per replication for all points,
  so the estimators compare points on the same noise (:ref:`scenario-crn`).
- **Outcome**: the share of targets a drone knows, averaged over the drones
  (``aggregate="mean"``) and over the mission (``integrate``).

.. testsetup:: swarm

    import os
    import tempfile

    _swarm_cwd = os.getcwd()
    _swarm_tmp = tempfile.TemporaryDirectory()
    os.chdir(_swarm_tmp.name)

.. testcode:: swarm

    import math

    from pydantic import BaseModel

    from mango import Agent, Area2D, Behavior, DefaultEnvironment, Position2D
    from mango.scenario import Scenario, ScenarioParams


    class Drone(Agent):
        """Random walk; finds targets nearby and shares what it knows within range."""

        def __init__(self, p):
            super().__init__()
            self.p, self.known = p, set()

        def on_step(self, env, clock, step_size_s):
            heading, step = self.rng.uniform(0, 2 * math.pi), self.p.speed * step_size_s
            here = env.space.location(self)
            x = min(max(here.x + step * math.cos(heading), 0.0), self.p.size)
            y = min(max(here.y + step * math.sin(heading), 0.0), self.p.size)
            env.space.move(self, Position2D(x, y))
            for i, target in enumerate(env.behavior.targets):
                if math.dist((x, y), target) <= self.p.sensing:
                    self.known.add(i)
            for peer in self.neighbors(tid="radio"):
                self.schedule_instant_message(frozenset(self.known), peer)

        def handle_message(self, content, meta):
            self.known |= content


    class Field(Behavior):
        """Holds the targets; links the drones within radio range after every step."""

        def __init__(self, ctx):
            p = ctx.p.swarm
            self.ctx = ctx
            spots = ctx.rng("targets").uniform(0, p.size, size=(p.targets, 2))
            self.targets = [tuple(spot) for spot in spots]

        def on_step(self, environment, clock, step_size_s):
            space, radio = environment.space, self.ctx.p.swarm.radio
            drones = self.ctx.agents("drone")
            in_range = [
                (a, b) for i, a in enumerate(drones) for b in drones[i + 1:]
                if space.distance(a, b) <= radio
            ]
            self.ctx.relink("radio", in_range)


    class Swarm(BaseModel, extra="forbid"):
        n: int = 8
        targets: int = 10
        size: float = 100.0  # m, a square area
        speed: float = 2.0  # m/s
        sensing: float = 5.0  # m
        radio: float = 20.0  # m


    class Params(ScenarioParams):
        swarm: Swarm = Swarm()


    def no_edges(src, dst, rng):  # the behavior sets the edges every step
        return []


    scenario = Scenario("swarm", params=Params)
    scenario.time(horizon="5min", step="10s")
    scenario.environment(lambda ctx: DefaultEnvironment(
        space=Area2D(ctx.p.swarm.size, ctx.p.swarm.size), behavior=Field(ctx)))
    drone = scenario.group("drone", "swarm.n", lambda e: Drone(e.p.swarm))
    scenario.link("radio", drone, pattern=no_edges)
    scenario.record(
        "known", drone, lambda a: len(a.known) / a.p.targets, aggregate="mean", kind="flow"
    )
    result = scenario.run(seed=1)
    print(result.ts("known").iloc[::6].round(2).tolist())

.. testoutput:: swarm

    [0.0, 0.01, 0.06, 0.31, 0.5, 0.51]

The drones start at random positions (the world's ``space`` stream).  The
behavior steps after the agents, so a drone talks to the drones that were
in range at the end of the previous step; messages sent in ``on_step``
arrive in the same step.  The study samples the two factors:

.. testcode:: swarm

    import warnings

    from mango.scenario import Sobol, Study, load_study


    def mean_known(run):
        """Share of targets a drone knows, averaged over the drones and the mission."""
        return run.time_average("known")  # divides by the recorded time


    study = Study(
        scenario,
        uncertainties={
            "swarm.speed": {"range": [0.5, 4.0]},
            "swarm.radio": {"range": [0.0, 50.0]},
        },
        design=Sobol(8),
        seed=11,
        outcomes=[mean_known],
        out="results/swarm",
    )
    print(study.run(progress=False).done)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # n = 8 is far too small; it warns
        indices = load_study("results/swarm").sobol_indices()
    print(indices.round(2))

.. testoutput:: swarm
   :options: +NORMALIZE_WHITESPACE

   32
         outcome       factor    S1  S1_conf    ST  ST_conf
   0  mean_known  swarm.speed  0.60     0.35  0.58     0.50
   1  mean_known  swarm.radio  0.59     0.63  0.59     0.54

``S1`` is the share of the variance a factor explains alone, ``ST`` with
its interactions; ``S1_conf`` and ``ST_conf`` are the half-widths of 95 %
bootstrap intervals.  With 8 base samples they are about as wide as the
indices, and ``sobol_indices`` warns: this is a pilot that shows the
pipeline works, not a result.  To scale: ``Sobol(256)`` (1024 runs) with a
few replications, a lever for the exploration algorithm crossed with the
samples (one set of indices per algorithm), and a cluster
(:ref:`scenario-slurm`).  The pairwise range check is linear in the
number of drone pairs; for hundreds of drones use
``space.neighbors_within(drone, radius)`` per drone or a grid index.

.. testcleanup:: swarm

    os.chdir(_swarm_cwd)
    _swarm_tmp.cleanup()

----

Double auction with paired comparisons
======================================

*How much allocative efficiency does the budget constraint of
zero-intelligence traders buy in a continuous double auction?*  ZI-C traders
(Gode and Sunder) never bid above their value or ask below their cost, ZI-U
traders bid at random.

- **Model**: 10 buyers and 10 sellers with one unit each and a private value
  or cost; every step each trader who has not traded sends a random quote
  to the market; the market trades a quote that crosses the best opposite
  quote and clears its book after a trade.
- **Lever**: ``market.strategy``.  No uncertainties: the private values are
  the noise.
- **Seeds**: common random numbers.  The values are drawn on their own
  stream (``e.rng_for("value")``), so both strategies of a replication trade
  with the *same* values, and the comparison is paired.
- **Records**: every trade goes into an event log (``ctx.log``), the maximum
  surplus of the run is a metric (``scenario.metric``), and the outcome
  ``efficiency`` is computed from both.

.. testsetup:: auction

    import os
    import tempfile

    _auction_cwd = os.getcwd()
    _auction_tmp = tempfile.TemporaryDirectory()
    os.chdir(_auction_tmp.name)

.. testcode:: auction

    from typing import Literal

    from pydantic import BaseModel

    from mango import Agent
    from mango.scenario import Scenario, ScenarioParams


    class Trader(Agent):
        """A zero-intelligence trader with one unit: a buyer's value or a seller's cost."""

        def __init__(self, side, limit, p):
            super().__init__()
            self.side, self.limit, self.p, self.done = side, limit, p, False

        def on_step(self, env, clock, step_size_s):
            if self.done:
                return
            if self.p.strategy == "zi_c":  # never trade at a loss
                if self.side == "buy":
                    low, high = 1, self.limit
                else:
                    low, high = self.limit, self.p.max_price
            else:
                low, high = 1, self.p.max_price
            quote = (self.side, float(self.rng.uniform(low, high)))
            for market in self.neighbors(tid="orders"):
                self.schedule_instant_message(quote, market)

        def handle_message(self, content, meta):  # the market's fill
            self.done = True


    class Market(Agent):
        """A quote that crosses the best opposite quote trades at that quote's price."""

        def __init__(self, ctx):
            super().__init__()
            self.ctx, self.best, self.traded = ctx, {"buy": None, "sell": None}, set()

        def handle_message(self, content, meta):
            side, price = content
            trader = self.ctx.entity(meta["sender_id"]).agent
            if trader in self.traded:  # a quote sent before its fill arrived
                return
            other = "sell" if side == "buy" else "buy"
            best = self.best[other]
            if best and (price >= best[0] if side == "buy" else price <= best[0]):
                buyer, seller = (trader, best[1]) if side == "buy" else (best[1], trader)
                self.ctx.log("trades", price=best[0], buyer=buyer.aid, seller=seller.aid,
                             surplus=buyer.limit - seller.limit)
                for agent in (buyer, seller):
                    self.traded.add(agent)
                    self.schedule_instant_message("filled", agent.addr)
                self.best = {"buy": None, "sell": None}
            elif self.best[side] is None or (
                price > self.best[side][0] if side == "buy" else price < self.best[side][0]
            ):
                self.best[side] = (price, trader)


    class Auction(BaseModel, extra="forbid"):
        n: int = 10  # buyers, and as many sellers
        strategy: Literal["zi_c", "zi_u"] = "zi_c"
        max_price: float = 200.0


    class Params(ScenarioParams):
        market: Auction = Auction()


    def make_trader(e):
        value = float(e.rng_for("value").uniform(50, 150))  # equal across strategies
        return Trader("buy" if e.group == "buyer" else "sell", value, e.p.market)


    scenario = Scenario("cda", params=Params)
    scenario.time(horizon=30, step=1)  # 30 rounds
    market = scenario.group("market", 1, lambda e: Market(e.ctx))
    buyers = scenario.group("buyer", "market.n", make_trader)
    sellers = scenario.group("seller", "market.n", make_trader)
    scenario.link("orders", buyers, market, pattern="many_to_one")
    scenario.link("orders", sellers, market, pattern="many_to_one")  # one link


    @scenario.metric("max_surplus")
    def max_surplus(ctx):
        values = sorted((a.limit for a in ctx.agents("buyer")), reverse=True)
        costs = sorted(a.limit for a in ctx.agents("seller"))
        return sum(max(0.0, value - cost) for value, cost in zip(values, costs))


    result = scenario.run(seed=1)
    print(result.log("trades")[["time", "buyer", "seller", "price"]].head(3).round(1))
    print(round(result.metrics["max_surplus"], 1))

.. testoutput:: auction
   :options: +NORMALIZE_WHITESPACE

      time    buyer    seller  price
   0   1.0  buyer_1  seller_2  123.2
   1   2.0  buyer_2  seller_4   87.7
   2   3.0  buyer_3  seller_5   68.6
   269.6

The log has one row per trade, with the time and phase of the trade, and the
metric is one number per run.  Both are stored with the run and can be read
again from disk, so an outcome that uses them can be recomputed later:

.. testcode:: auction

    from mango.scenario import Study, load_study


    def efficiency(run):
        """Realised surplus over the maximum surplus."""
        trades = run.logs.get("trades")
        realised = 0.0 if trades is None else float(trades["surplus"].sum())
        return realised / run.metrics["max_surplus"]


    study = Study(
        scenario,
        levers={"market.strategy": ["zi_c", "zi_u"]},
        replications=8,
        seed=3,
        outcomes=[efficiency],
        out="results/cda",
    )
    study.run(progress=False)
    frame = load_study("results/cda")
    pairs = frame.paired("market.strategy", "efficiency", baseline="zi_u", pairs=True)
    print(pairs[["replication", "baseline_value", "value", "difference"]].head(3).round(2))
    paired = frame.paired("market.strategy", "efficiency", baseline="zi_u")
    print(paired[["market.strategy", "n", "mean", "ci_low", "ci_high"]].round(2))

.. testoutput:: auction
   :options: +NORMALIZE_WHITESPACE

      replication  baseline_value  value  difference
   0            0            0.24   1.00        0.76
   1            1            0.22   1.00        0.78
   2            2           -0.19   0.99        1.17
     market.strategy  n  mean  ci_low  ci_high
   0            zi_c  8  1.33    0.38     2.27

Every pair compares the two strategies on the same traders and values.
ZI-C's efficiency stays close to 1; ZI-U's varies wildly and can be negative
(traders with low values buy from traders with high costs), so the interval
of the difference is wide even though every single pair favours ZI-C.
``frame.log("trades")`` concatenates the trade logs of all runs, with the
run's factor columns appended (``groupby("strategy.kind")`` needs no
merge), e.g. for price convergence across rounds; reduce each run to one
number before computing intervals, as the trades of one run are not
independent.  To scale: several periods with a ``scenario.event`` that resets the
units, more strategies (ZIP as a third level), 40 replications; a paired
t-test or Wilcoxon test on ``pairs["difference"]`` with scipy.

.. testcleanup:: auction

    os.chdir(_auction_cwd)
    _auction_tmp.cleanup()

----

Gossip averaging in event-driven time
=====================================

*How much faster does push-pull gossip averaging converge on a small-world
overlay than on a ring, with and without message loss, when the network is
partitioned for ten seconds?*

- **Model**: every node holds a value and, on an exponential timer, sends it
  to a random neighbour, which replies with its own value; both move to the
  pair's mean.  Time is event-driven (``step: event``): the world jumps from one
  timer or message to the next.  Message delays are log-normal (median
  50 ms).  From 5 s to 15 s an event partitions the network into a west and
  an east half (``comm: {partition: {by_attr: side}}``).
- **Lever**: ``gossip.topology``, a pattern as data (``ring`` or a small
  world).  **Uncertainty**: ``comm.loss_percent``.
- **Outcome**: the time until the spread of the values falls below 1 % of
  its start; ``stop_when`` ends the run there.

.. testsetup:: gossip

    import os
    import tempfile

    _gossip_cwd = os.getcwd()
    _gossip_tmp = tempfile.TemporaryDirectory()
    os.chdir(_gossip_tmp.name)

.. testcode:: gossip

    from pydantic import BaseModel

    from mango import Agent, sender_addr
    from mango.scenario import Scenario, ScenarioParams


    class Node(Agent):
        """Push-pull averaging with a random neighbour, on a random timer."""

        def __init__(self, value, mean_interval_s):
            super().__init__()
            self.value, self.mean_interval_s = value, mean_interval_s

        def on_ready(self):
            self.arm()

        def arm(self):
            wait = self.rng_for("timer").exponential(self.mean_interval_s)
            self.schedule_timestamp_task(self.push(), self.current_timestamp + wait)

        async def push(self):
            peers = self.neighbors(tid="overlay")
            if peers:
                peer = peers[self.rng_for("peer").integers(len(peers))]
                self.schedule_instant_message(("push", self.value), peer)
            self.arm()

        def handle_message(self, content, meta):
            if content[0] == "push":  # reply with the own and the pushed value, average
                pushed = content[1]
                self.schedule_instant_message(("pull", self.value, pushed), sender_addr(meta))
                self.value = (self.value + pushed) / 2
            else:  # move by what the peer gave away, so the pair keeps its sum
                _, theirs, pushed = content
                self.value += (theirs - pushed) / 2


    class Gossip(BaseModel, extra="forbid"):
        n: int = 8
        topology: str | dict = "ring"
        mean_interval_s: float = 1.0
        eps: float = 0.01  # converged below this share of the initial spread


    class Params(ScenarioParams):
        gossip: Gossip = Gossip()


    def spread(ctx):
        values = [agent.value for agent in ctx.agents("node")]
        return max(values) - min(values)


    scenario = Scenario("gossip", params=Params)
    scenario.time(horizon="60s", step="event")
    node = scenario.group(
        "node", "gossip.n",
        lambda e: Node(float(e.index), e.p.gossip.mean_interval_s),
        install=lambda e: {"side": "west" if e.index < e.count // 2 else "east"},
    )
    scenario.link("overlay", node, pattern="gossip.topology")  # a parameter
    scenario.record_world("spread", spread, every="1s")
    scenario.defaults({
        "comm": {"delay_s": {"lognormal": [-3.0, 0.5]}},
        "events": [{"name": "partition", "at": "5s", "until": "15s",
                    "comm": {"partition": {"by_attr": "side"}}}],
    })


    @scenario.stop_when
    def converged(ctx):
        if spread(ctx) < ctx.p.gossip.eps * (ctx.p.gossip.n - 1):
            return "converged"


    result = scenario.run(seed=1)
    print(result.ts("spread").round(2).tolist()[:16])
    print(result.stats["stop_reason"], round(result.stats["stop_time_s"], 2))

.. testoutput:: gossip

    [7.0, 5.12, 3.75, 3.12, 2.8, 2.64, 1.48, 1.44, 1.44, 1.3, 1.07, 1.0, 0.83, 0.83, 0.83, 0.8]
    converged 22.71

.. testcode:: gossip
   :hide:

   import asyncio


   async def _final_mean(seed):  # the claim below: no loss, no partition -> mean kept
       run = scenario.build(seed=seed, overrides={"events": []})
       async with run:
           await run.run()
           return sum(a.value for a in run.ctx.agents("node")) / 8


   _errors = [abs(asyncio.run(_final_mean(seed)) - 3.5) for seed in range(1, 6)]
   assert max(_errors) < 0.01, _errors  # (only replies still in flight at the stop)

A node can be in several exchanges at once, so its value may change
between its push and the pull reply.  The initiator therefore moves by what
the peer gave away (``theirs - pushed``), not towards its current value:
every exchange keeps the pair's sum, and without loss the mean stays at the
initial 3.5 (up to replies still in flight when the run stops).
``every="1s"`` makes the event-driven run also stop at every full second,
so the spread is sampled on a regular grid.  From 5 s to 15 s the halves
average only among themselves, so the spread stalls near the gap between
their means; it falls again once the partition ends.  The study:

.. testcode:: gossip

    import math

    from mango.scenario import Study, load_study


    def time_to_converge(run):
        """Seconds until convergence; NaN when the run reached the horizon."""
        return run.stats["stop_time_s"] if run.stats["stopped_early"] else math.nan


    study = Study(
        scenario,
        levers={"gossip.topology": {
            "ring": "ring",
            "small_world": {"small_world": {"k": 4, "p": 0.2}},
        }},
        uncertainties={"comm.loss_percent": [0.0, 0.05]},
        replications=3,
        seed=5,
        outcomes=[time_to_converge],
        out="results/gossip",
    )
    study.run(progress=False)
    frame = load_study("results/gossip")
    paired = frame.paired("gossip.topology", baseline="ring", by="comm.loss_percent")
    columns = ["comm.loss_percent", "gossip.topology:label", "n", "mean", "ci_low", "ci_high"]
    print(paired[columns].round(2))

.. testoutput:: gossip
   :options: +NORMALIZE_WHITESPACE

      comm.loss_percent gossip.topology:label  n   mean  ci_low  ci_high
   0               0.00           small_world  3  -8.48  -20.20     3.24
   1               0.05           small_world  3 -11.88  -24.47     0.72

The small world converges about 8 to 12 s sooner; with three replications
both intervals still include zero.  A run that never converges has
``NaN``: count them (``frame.time_to_converge.isna().sum()``) before you
average, since leaving them out biases the mean towards fast runs
(``summary()`` and ``paired()`` report them in the ``missing`` column).
A ``pull`` reply that is lost, or dropped by the partition, loses mass:
the peer moved, the initiator did not; so with loss the values converge
to a slightly wrong mean, and the mean error is worth recording as a
second outcome.  To scale: ``n`` in the hundreds, 30 replications, on a
pool (``workers=``); event-driven runs take one world step per
distinct event time, so their cost grows with the number of messages (see
:ref:`simulation-performance`).

.. testcleanup:: gossip

    os.chdir(_gossip_cwd)
    _gossip_tmp.cleanup()

----

Opinion dynamics from files
===========================

*Does a media campaign move an open-minded population further than a
closed-minded one?*  Everything a study varies is in YAML files: the
parameters, the scenario, the events and the study.  Python holds only the
agent, its factory and the outcomes, in one short module.

- **Model**: Deffuant's bounded confidence.  Each day every person picks a
  random friend on a small-world network; the pairs meet one after another
  in random order and, if their opinions differ by less than ``epsilon``,
  both move by ``mu`` towards each other (a meeting keeps the pair's sum, so
  the mean opinion stays).  The initial
  opinions come from a survey table, one respondent per person
  (``FromColumn``).  The first week is a warm-up that is not recorded.
- **Lever**: the campaign strength (``events.campaign.emit.strength``, an
  event addressed by its name).  **Uncertainty**: ``epsilon``.
- **Outcomes**: the mean and the spread of the opinions on the last day.

.. testsetup:: opinions

    import os
    import sys
    import tempfile

    _opinions_cwd = os.getcwd()
    _opinions_tmp = tempfile.TemporaryDirectory()
    os.chdir(_opinions_tmp.name)
    sys.path.insert(0, _opinions_tmp.name)
    _OPINIONS_FILES = {
        'deffuant.py': '''"""Deffuant bounded confidence: people meet a random friend and move closer."""

    from mango import Agent, Behavior, DefaultEnvironment, sender_addr


    class Person(Agent):
        def __init__(self, opinion, p):
            super().__init__()
            self.opinion, self.p, self.partner = opinion, p, None

        def on_step(self, env, clock, step_size_s):
            friends = self.neighbors(tid="friends")
            if friends:  # one meeting per person and day
                friend = friends[self.rng.integers(len(friends))]
                if self.p.interaction == "pairwise":  # Meetings applies it
                    self.partner = friend
                else:
                    self.schedule_instant_message(("meet", self.opinion), friend)

        def handle_message(self, content, meta):  # interaction: messages
            kind, other = content
            if kind == "meet":  # tell the other side the opinion before moving
                self.schedule_instant_message(("reply", self.opinion), sender_addr(meta))
            if abs(other - self.opinion) < self.p.epsilon:
                self.opinion += self.p.mu * (other - self.opinion)

        def on_global_event(self, event):  # a campaign moves those open to it
            reach = abs(event["target"] - self.opinion) < self.p.epsilon
            if event.kind == "media" and reach:
                self.opinion += event["strength"] * (event["target"] - self.opinion)


    class Meetings(Behavior):
        """Textbook Deffuant: the chosen pairs meet one after another, in random order."""

        def __init__(self):
            self.rng, self.people = None, {}  # the world binds its "behavior" stream

        def install(self, agent, **attrs):
            self.people[agent.addr] = agent

        def on_step(self, environment, clock, step_size_s):  # after all agents
            people = list(self.people.values())
            for i in self.rng.permutation(len(people)):
                a = people[i]
                b = self.people.get(a.partner)
                if b is not None and abs(a.opinion - b.opinion) < a.p.epsilon:
                    shift = a.p.mu * (b.opinion - a.opinion)  # both move: the sum stays
                    a.opinion, b.opinion = a.opinion + shift, b.opinion - shift
                a.partner = None


    def environment(ctx):
        return DefaultEnvironment(behavior=Meetings())


    def make_person(e):
        return Person(e.draw(e.p.initial), e.p)


    def spread(run):
        """Standard deviation of the opinions on the last day."""
        return float(run.wide("opinion").iloc[-1].std())


    def mean_opinion(run):
        """Mean opinion on the last day."""
        return float(run.wide("opinion").iloc[-1].mean())
    ''',
        'opinions.yaml': '''# Deffuant opinion dynamics on a small-world network; initial opinions from a survey.
    parameters:
      n: 30
      epsilon: {default: 0.3, min: 0, max: 1, help: people listen to opinions closer than this}
      mu: {default: 0.3, gt: 0, max: 0.5, help: how far they move toward them}
      interaction: {default: pairwise, choices: [pairwise, messages]}
      initial: {from_column: [data/survey.csv, opinion]}
      net: {k: 4, rewire: 0.1}
    time: {start: 2024-03-01, horizon: 6w, step: 1d}
    environment: deffuant:environment
    phases:
      - {name: warmup, until: 2024-03-08, record: false}
    groups:
      person: {factory: deffuant:make_person, count: n}
    links:
      - {name: friends, from: person, pattern: {small_world: {k: $net.k, p: $net.rewire}}}
    record:
      - {name: opinion, group: person, attr: opinion}
    ''',
        'presets/campaigns.yaml': '''# A media campaign toward 0.9 on March 15 and again two weeks later
    events:
      - {name: campaign, at: 2024-03-15, every: 14d, count: 2,
         emit: {kind: media, target: 0.9, strength: 0.2}}
    ''',
        'studies/media.yaml': '''# Does a campaign move an open-minded population further than a closed one?
    # 2 x 2 design points x 3 replications = 12 runs
    scenario: opinions.yaml
    base: presets/campaigns.yaml
    seed: 2026
    levers:
      events.campaign.emit.strength: {none: 0.0, strong: 0.3}
    uncertainties:
      epsilon: [0.2, 0.4]
    replications: 3
    outcomes: [deffuant:spread, deffuant:mean_opinion]
    out: results/media
    ''',
        'data/survey.csv': '''respondent,opinion
    0,0.70
    1,0.36
    2,0.64
    3,0.34
    4,0.81
    5,0.35
    6,0.89
    7,0.19
    8,0.71
    9,0.22
    10,0.61
    11,0.28
    12,0.73
    13,0.35
    14,0.76
    15,0.57
    16,0.80
    17,0.11
    18,0.72
    19,0.23
    20,0.64
    21,0.46
    22,0.67
    23,0.07
    24,0.74
    25,0.27
    26,0.56
    27,0.19
    28,0.62
    29,0.30
    ''',
    }
    for _name, _text in _OPINIONS_FILES.items():
        os.makedirs(os.path.dirname(_name) or ".", exist_ok=True)
        with open(_name, "w") as file:
            file.write(_text)

.. testcode:: opinions
   :hide:

   for _name, _text in _OPINIONS_FILES.items():  # the files shown below
       assert open(_name).read() == _text

.. code-block:: python
   :caption: deffuant.py

   """Deffuant bounded confidence: people meet a random friend and move closer."""

   from mango import Agent, Behavior, DefaultEnvironment, sender_addr


   class Person(Agent):
       def __init__(self, opinion, p):
           super().__init__()
           self.opinion, self.p, self.partner = opinion, p, None

       def on_step(self, env, clock, step_size_s):
           friends = self.neighbors(tid="friends")
           if friends:  # one meeting per person and day
               friend = friends[self.rng.integers(len(friends))]
               if self.p.interaction == "pairwise":  # Meetings applies it
                   self.partner = friend
               else:
                   self.schedule_instant_message(("meet", self.opinion), friend)

       def handle_message(self, content, meta):  # interaction: messages
           kind, other = content
           if kind == "meet":  # tell the other side the opinion before moving
               self.schedule_instant_message(("reply", self.opinion), sender_addr(meta))
           if abs(other - self.opinion) < self.p.epsilon:
               self.opinion += self.p.mu * (other - self.opinion)

       def on_global_event(self, event):  # a campaign moves those open to it
           reach = abs(event["target"] - self.opinion) < self.p.epsilon
           if event.kind == "media" and reach:
               self.opinion += event["strength"] * (event["target"] - self.opinion)


   class Meetings(Behavior):
       """Textbook Deffuant: the chosen pairs meet one after another, in random order."""

       def __init__(self):
           self.rng, self.people = None, {}  # the world binds its "behavior" stream

       def install(self, agent, **attrs):
           self.people[agent.addr] = agent

       def on_step(self, environment, clock, step_size_s):  # after all agents
           people = list(self.people.values())
           for i in self.rng.permutation(len(people)):
               a = people[i]
               b = self.people.get(a.partner)
               if b is not None and abs(a.opinion - b.opinion) < a.p.epsilon:
                   shift = a.p.mu * (b.opinion - a.opinion)  # both move: the sum stays
                   a.opinion, b.opinion = a.opinion + shift, b.opinion - shift
               a.partner = None


   def environment(ctx):
       return DefaultEnvironment(behavior=Meetings())


   def make_person(e):
       return Person(e.draw(e.p.initial), e.p)


   def spread(run):
       """Standard deviation of the opinions on the last day."""
       return float(run.wide("opinion").iloc[-1].std())


   def mean_opinion(run):
       """Mean opinion on the last day."""
       return float(run.wide("opinion").iloc[-1].mean())

The scenario file declares the parameters by their defaults in a
``parameters:`` block (:ref:`Parameters without Python
<scenario-file-parameters>`): their types are
inferred, so presets, ``--set`` and study factors are checked against them;
the long form adds bounds (``epsilon`` in [0, 1]), the allowed values of
``interaction`` (a typo such as ``--set interaction=pairwse`` is an error,
not silently the other branch) and help texts for ``validate`` and
``form``.  ``environment: deffuant:environment`` installs the ``Meetings``
behavior.
``initial`` is a ``FromColumn`` distribution over the survey table
``data/survey.csv`` (columns ``respondent`` and ``opinion``); person *i*
gets row *i*:

.. code-block:: yaml
   :caption: opinions.yaml

   # Deffuant opinion dynamics on a small-world network; initial opinions from a survey.
   parameters:
     n: 30
     epsilon: {default: 0.3, min: 0, max: 1, help: people listen to opinions closer than this}
     mu: {default: 0.3, gt: 0, max: 0.5, help: how far they move toward them}
     interaction: {default: pairwise, choices: [pairwise, messages]}
     initial: {from_column: [data/survey.csv, opinion]}
     net: {k: 4, rewire: 0.1}
   time: {start: 2024-03-01, horizon: 6w, step: 1d}
   environment: deffuant:environment
   phases:
     - {name: warmup, until: 2024-03-08, record: false}
   groups:
     person: {factory: deffuant:make_person, count: n}
   links:
     - {name: friends, from: person, pattern: {small_world: {k: $net.k, p: $net.rewire}}}
   record:
     - {name: opinion, group: person, attr: opinion}

.. code-block:: yaml
   :caption: presets/campaigns.yaml

   # A media campaign toward 0.9 on March 15 and again two weeks later
   events:
     - {name: campaign, at: 2024-03-15, every: 14d, count: 2,
        emit: {kind: media, target: 0.9, strength: 0.2}}

.. code-block:: yaml
   :caption: studies/media.yaml

   # Does a campaign move an open-minded population further than a closed one?
   # 2 x 2 design points x 3 replications = 12 runs
   scenario: opinions.yaml
   base: presets/campaigns.yaml
   seed: 2026
   levers:
     events.campaign.emit.strength: {none: 0.0, strong: 0.3}
   uncertainties:
     epsilon: [0.2, 0.4]
   replications: 3
   outcomes: [deffuant:spread, deffuant:mean_opinion]
   out: results/media

Check everything, look at the plan, try a short run, then run the study:

.. code-block:: console

    $ mango-scenario validate studies/media.yaml
    OK: studies/media.yaml is a valid study: 12 runs (30 agents each, 1 link), scenario opinions.yaml; parameters, declarations and data files of 4 distinct parameter sets checked
    note: agents were not built; --build builds run 0 and takes one step (checks factories, profile columns picked at run time and recorder attributes)
    $ mango-scenario plan opinions.yaml -p presets/campaigns.yaml
    Scenario 'opinions': 30 agents in 1 group, 1 link, 1 recorder
      time: horizon 6w, step 1d (42 steps), start 2024-03-01T00:00:00
      environment: factory environment
      groups:
        person               30  person_0 .. person_29
      links:
        friends          person -> person (small_world(k=4, p=0.1)); ? edges, undirected
      recorders:
        opinion          person
      phases:
        warmup           2024-03-01T00:00 .. 2024-03-08T00:00  (not recorded)
        main             2024-03-08T00:00 .. 2024-04-12T00:00
      events:
        campaign         2024-03-15T00:00  (emit {'kind': 'media', 'target': 0.9, 'strength': 0.2} to all agents (on_global_event))
        campaign         2024-03-29T00:00  (emit {'kind': 'media', 'target': 0.9, 'strength': 0.2} to all agents (on_global_event))
    $ mango-scenario run opinions.yaml -p presets/campaigns.yaml --set epsilon=0.25 --until 10d --seed 1
    run ... of scenario 'opinions' done in ... s -> results/opinions/..._until_864000s
      truncated: stopped at 10d ...
      90 values recorded, checksum ...; replay: mango-scenario rerun results/opinions/..._until_864000s
    $ mango-scenario run studies/media.yaml -q
    12 runs: 12 done, 0 skipped, 0 failed in ... s (sequential) -> .../results/media

``every: 14d`` with ``count: 2`` repeats the campaign, and ``plan`` lists
each occurrence.  A typo in a path (``--set epsilonn=0.3``) or a value of
the wrong type fails before anything runs, with a suggestion.  The analysis
pairs the campaign with no campaign within each replication and ``epsilon``:

.. testcode:: opinions

    from mango.scenario import Study, load_study

    Study.from_yaml("studies/media.yaml").run(progress=False)  # resumes: nothing to do
    frame = load_study("results/media")
    paired = frame.paired("events.campaign.emit.strength", "mean_opinion",
                          baseline="none", by="epsilon")
    print(paired[["epsilon", "n", "mean", "ci_low", "ci_high"]].round(3))

.. testoutput:: opinions
   :options: +NORMALIZE_WHITESPACE

      epsilon  n   mean  ci_low  ci_high
   0      0.2  3  0.025   0.004    0.045
   1      0.4  3  0.162   0.139    0.184

An open-minded population (``epsilon`` 0.4) follows the campaign by about
0.16 on the opinion scale; a closed one hardly moves, because few people are
within reach of the campaign's message.

The meetings are applied by a behavior (``Meetings``, installed by the
``environment:`` factory), which runs after all agents have picked a
friend: each meeting sees both opinions as they are and moves both sides
at once.  ``interaction: messages`` is a message-based variant: the person
sends its opinion, the friend replies with its own and each side moves on
receipt.  That is asynchronous, not the textbook model: each side compares
with the other's opinion at send time, which other meetings of the same day
may already have changed, so the two moves differ and the mean drifts (it
also takes several times longer).  Without a campaign the pairwise model
keeps the mean, the message variant does not:

.. testcode:: opinions

    from mango.scenario import load_scenario

    opinions = load_scenario("opinions.yaml")
    for interaction in ["pairwise", "messages"]:
        run = opinions.run(seed=2, overrides={"interaction": interaction, "epsilon": 0.4})
        means = run.wide("opinion").mean(axis=1)
        print(f"{interaction:8}  first day {means.iloc[0]:.4f}  last day {means.iloc[-1]:.4f}")

.. testoutput:: opinions

    pairwise  first day 0.4963  last day 0.4963
    messages  first day 0.4941  last day 0.4917

.. testcode:: opinions
   :hide:

   import numpy as np

   for _seed in (1, 2, 3):  # the claim above, for every recorded day
       _means = opinions.run(seed=_seed, overrides={"epsilon": 0.4}).wide("opinion").mean(axis=1)
       assert np.allclose(_means, _means.iloc[0], rtol=0, atol=1e-12), _means

Colleagues who prefer a form to
YAML get one with ``mango-scenario form opinions.yaml -p
presets/campaigns.yaml`` (see `Schelling segregation for teaching`_).  To
scale: the full survey with ``mode: sample`` for a larger population, the
network density as a third factor, and 20 replications.

.. testcleanup:: opinions

    os.chdir(_opinions_cwd)
    sys.path.remove(_opinions_tmp.name)
    sys.modules.pop("deffuant", None)
    _opinions_tmp.cleanup()

----


Schelling segregation for teaching
==================================

*How much segregation do mild preferences cause?*  The classic classroom
model, set up the way students meet it: a project from a template, one file
to read from top to bottom, a form to change numbers, and the plain mango
script underneath.  Nothing in the students' file is asynchronous.

- **Model**: red and blue residents at random places in a 10 × 10
  ``Area2D``.  A resident is happy when at least ``threshold`` of its
  neighbours within ``radius`` have its colour; an unhappy one moves to a
  random place.  20 rounds.
- **Lever**: ``town.threshold`` (0.3, 0.5, 0.7), three replications each.
- **Outcomes**: the segregation index (the mean share of same-colour
  neighbours) and the share of happy residents in the last round.

.. testsetup:: schelling

    import os
    import sys
    import tempfile

    _schelling_cwd = os.getcwd()
    _schelling_tmp = tempfile.TemporaryDirectory()
    os.chdir(_schelling_tmp.name)
    _schelling_path = None

``mango-scenario new`` writes a project from the ``minimal`` template: one
``model.py`` in four parts (the agent, the parameters, the scenario, the
outcomes), a preset and a study file:

.. code-block:: console

    $ mango-scenario new schelling
    Created 5 files in schelling/ (template 'minimal'):
      README.md
      .gitignore
      model.py
      presets/base.yaml
      study.yaml

    Next steps:
      cd schelling
      python model.py                # one run
      mango-scenario validate study.yaml
      mango-scenario plan study.yaml
      mango-scenario run study.yaml
    ...

.. testcode:: schelling
   :hide:

   _SCHELLING_FILES = {
       'model.py': '''"""Schelling's segregation model: residents move away when too few neighbours share their colour.

   One run:    python model.py
   The study:  mango-scenario run study.yaml
   """

   from pydantic import BaseModel, Field

   from mango import Agent, Area2D, DefaultEnvironment, Position2D
   from mango.scenario import Scenario, ScenarioParams


   # 1. The agent -------------------------------------------------------------------
   class Resident(Agent):
       """Happy when enough neighbours within the radius have the same colour."""

       def __init__(self, colour, town):
           super().__init__()
           self.colour, self.town = colour, town
           self.similar, self.happy = 1.0, True

       def look_around(self, space):
           neighbours = space.neighbors_within(self, self.town.radius)
           same = sum(other.colour == self.colour for other in neighbours)
           self.similar = same / len(neighbours) if neighbours else 1.0
           self.happy = self.similar >= self.town.threshold

       def on_step(self, env, clock, step_size_s):
           self.look_around(env.space)
           if not self.happy:  # move to a random place, drawn from my own stream
               env.space.move(self, random_place(self.rng, self.town.size))


   def random_place(rng, size):
       x, y = rng.uniform(0, size, size=2)
       return Position2D(float(x), float(y))


   # 2. The parameters --------------------------------------------------------------
   class Town(BaseModel, extra="forbid"):
       red: int = Field(20, ge=0, description="number of red residents")
       blue: int = Field(20, ge=0, description="number of blue residents")
       size: float = Field(10.0, gt=0, description="width and height of the town")
       radius: float = Field(2.0, gt=0, description="how far a resident looks")
       threshold: float = Field(
           0.5, ge=0, le=1, description="share of same-colour neighbours a resident wants"
       )


   class Params(ScenarioParams):
       town: Town = Town()


   # 3. The scenario ----------------------------------------------------------------
   scenario = Scenario("schelling", params=Params)
   scenario.time(horizon=20, step=1)  # 20 rounds


   @scenario.environment
   def town_map(ctx):
       return DefaultEnvironment(space=Area2D(ctx.p.town.size, ctx.p.town.size))


   def make_resident(e):
       return Resident(e.group, e.p.town)


   def home(e):
       return random_place(e.rng, e.p.town.size)


   for colour in ("red", "blue"):
       scenario.group(colour, f"town.{colour}", make_resident, position=home)
   scenario.record("similar", ["red", "blue"], attr="similar")  # one series per resident
   scenario.record("happy", ["red", "blue"], attr="happy")


   @scenario.on_build
   def first_look(ctx):
       for resident in ctx.agents("red") + ctx.agents("blue"):
           resident.look_around(ctx.world.environment.space)


   # 4. The outcomes ----------------------------------------------------------------
   def segregation(run):
       """Mean share of same-colour neighbours in the last round."""
       return float(run.wide("similar").iloc[-1].mean())


   def happy(run):
       """Share of happy residents in the last round."""
       return float(run.wide("happy").iloc[-1].mean())


   if __name__ == "__main__":
       result = scenario.run("presets/base.yaml", seed=1)
       print("similar:", result.wide("similar").mean(axis=1).round(2).tolist()[::5])
       print(f"segregation {segregation(result):.2f}, happy {happy(result):.2f}")
   ''',
       'presets/base.yaml': '''# Change a number here, or with --set town.threshold=0.3
   town: {red: 20, blue: 20, threshold: 0.5}
   ''',
       'study.yaml': '''# How much segregation do mild preferences cause?
   scenario: model:scenario
   base: presets/base.yaml
   levers:
     town.threshold: [0.3, 0.5, 0.7]
   replications: 3
   seed: 2026
   outcomes: [model:segregation, model:happy]
   out: results/study
   ''',
   }
   os.makedirs("schelling/presets", exist_ok=True)
   os.chdir("schelling")
   _schelling_path = os.getcwd()
   sys.path.insert(0, _schelling_path)
   for _name, _text in _SCHELLING_FILES.items():
       with open(_name, "w") as file:
           file.write(_text)

Each part of ``model.py`` is replaced by Schelling's model; the students
read this file and nothing else.  Every resident draws from its own random
stream (``self.rng``), so a seed reproduces a run exactly:

.. code-block:: python
   :caption: model.py

   """Schelling's segregation model: residents move away when too few neighbours share their colour.

   One run:    python model.py
   The study:  mango-scenario run study.yaml
   """

   from pydantic import BaseModel, Field

   from mango import Agent, Area2D, DefaultEnvironment, Position2D
   from mango.scenario import Scenario, ScenarioParams


   # 1. The agent -------------------------------------------------------------------
   class Resident(Agent):
       """Happy when enough neighbours within the radius have the same colour."""

       def __init__(self, colour, town):
           super().__init__()
           self.colour, self.town = colour, town
           self.similar, self.happy = 1.0, True

       def look_around(self, space):
           neighbours = space.neighbors_within(self, self.town.radius)
           same = sum(other.colour == self.colour for other in neighbours)
           self.similar = same / len(neighbours) if neighbours else 1.0
           self.happy = self.similar >= self.town.threshold

       def on_step(self, env, clock, step_size_s):
           self.look_around(env.space)
           if not self.happy:  # move to a random place, drawn from my own stream
               env.space.move(self, random_place(self.rng, self.town.size))


   def random_place(rng, size):
       x, y = rng.uniform(0, size, size=2)
       return Position2D(float(x), float(y))


   # 2. The parameters --------------------------------------------------------------
   class Town(BaseModel, extra="forbid"):
       red: int = Field(20, ge=0, description="number of red residents")
       blue: int = Field(20, ge=0, description="number of blue residents")
       size: float = Field(10.0, gt=0, description="width and height of the town")
       radius: float = Field(2.0, gt=0, description="how far a resident looks")
       threshold: float = Field(
           0.5, ge=0, le=1, description="share of same-colour neighbours a resident wants"
       )


   class Params(ScenarioParams):
       town: Town = Town()


   # 3. The scenario ----------------------------------------------------------------
   scenario = Scenario("schelling", params=Params)
   scenario.time(horizon=20, step=1)  # 20 rounds


   @scenario.environment
   def town_map(ctx):
       return DefaultEnvironment(space=Area2D(ctx.p.town.size, ctx.p.town.size))


   def make_resident(e):
       return Resident(e.group, e.p.town)


   def home(e):
       return random_place(e.rng, e.p.town.size)


   for colour in ("red", "blue"):
       scenario.group(colour, f"town.{colour}", make_resident, position=home)
   scenario.record("similar", ["red", "blue"], attr="similar")  # one series per resident
   scenario.record("happy", ["red", "blue"], attr="happy")


   @scenario.on_build
   def first_look(ctx):
       for resident in ctx.agents("red") + ctx.agents("blue"):
           resident.look_around(ctx.world.environment.space)


   # 4. The outcomes ----------------------------------------------------------------
   def segregation(run):
       """Mean share of same-colour neighbours in the last round."""
       return float(run.wide("similar").iloc[-1].mean())


   def happy(run):
       """Share of happy residents in the last round."""
       return float(run.wide("happy").iloc[-1].mean())


   if __name__ == "__main__":
       result = scenario.run("presets/base.yaml", seed=1)
       print("similar:", result.wide("similar").mean(axis=1).round(2).tolist()[::5])
       print(f"segregation {segregation(result):.2f}, happy {happy(result):.2f}")

.. code-block:: yaml
   :caption: presets/base.yaml

   # Change a number here, or with --set town.threshold=0.3
   town: {red: 20, blue: 20, threshold: 0.5}

.. code-block:: yaml
   :caption: study.yaml

   # How much segregation do mild preferences cause?
   scenario: model:scenario
   base: presets/base.yaml
   levers:
     town.threshold: [0.3, 0.5, 0.7]
   replications: 3
   seed: 2026
   outcomes: [model:segregation, model:happy]
   out: results/study

In the project folder (``cd schelling``), the students run the model, the
study, the plain script of one run and the form:

.. code-block:: console

    $ python model.py
    similar: [0.58, 0.94, 0.94, 0.94, 0.94]
    segregation 0.94, happy 1.00
    $ mango-scenario run study.yaml -q
    9 runs: 9 done, 0 skipped, 0 failed in ... s (sequential) -> .../results/study
    $ mango-scenario to-script model:scenario -p presets/base.yaml --seed 1 -o plain.py
    wrote the script to plain.py
    $ python plain.py
    1680 values recorded
    $ mango-scenario form model:scenario -p presets/base.yaml
    wrote the form to schelling_form.html
    open it in a browser; it needs no network access

``plain.py`` is the same run written out with the mango API:
``create_world``, a registration loop per colour, the positions, the
recorders and a ``for`` loop over the steps, importing ``Resident`` and the
factories from ``model.py``.  Around them sit a few small helpers that
reproduce the run exactly: ``ScriptContext`` and ``ScriptEntity`` with just
the ``ctx``/``e`` methods the model uses, ``register`` and one function per
kind of recorder (an ``aggregate="sum"`` recorder uses ``group_reduce``
from :mod:`mango.scenario`, which also provides the parameter model's
base).  It records the same 1680 values, so students can set a breakpoint
in the loop and compare.  The form shows the
parameters with their descriptions and bounds (``Field(..., ge=0, le=1,
description=...)``) and downloads the edited preset; ``mango-scenario run
model:scenario -p schelling.yaml`` runs it.  The study summary:

.. testcode:: schelling

    from mango.scenario import Study, load_study

    Study.from_yaml("study.yaml").run(progress=False)  # resumes: nothing to do
    summary = load_study("results/study").summary("segregation", by="town.threshold")
    print(summary[["town.threshold", "n", "mean", "ci_low", "ci_high"]].round(2))

.. testoutput:: schelling
   :options: +NORMALIZE_WHITESPACE

      town.threshold  n  mean  ci_low  ci_high
   0             0.3  3  0.83    0.68     0.98
   1             0.5  3  0.97    0.91     1.02
   2             0.7  3  1.00    0.98     1.01

Even residents who are content with 30 % neighbours of their own colour end
up in neighbourhoods with more than 80 % of their own colour: Schelling's
point.  (An interval beyond 1 is an artefact of three replications and the
normal approximation of a share.)  For an
exercise, students add recorders of the positions to draw the final map
(``scenario.record("x", ["red", "blue"], lambda a, ctx:
ctx.space.location(a).x)`` and the same for ``y``; a recorder with two
arguments gets the run's context, see :ref:`scenario-space`), or a
``stop_when`` that ends the run once everybody is happy.

.. testcleanup:: schelling

    os.chdir(_schelling_cwd)
    if _schelling_path in sys.path:
        sys.path.remove(_schelling_path)
    sys.modules.pop("model", None)
    _schelling_tmp.cleanup()

----


Smart building on a Slurm cluster
=================================

*Does occupancy-driven heating save energy over a fixed schedule, and at
what cost in comfort?*  The recipe is about running the study the way a
reviewer can check it: on a Slurm cluster, resumable, with every run
replayable and its code and data on record.

- **Model**: occupancy sensors (four per zone) report to three zone
  controllers, which heat a first-order thermal zone.  The occupancy
  probability is a profile from a building-management export in local time,
  across the switch to summer time on 2024-03-31 (the hour from 02:00 to
  03:00 does not exist).  Steps of 15 min for two days.
- **Lever**: ``hvac.control`` (``occupancy`` or ``schedule``), four
  replications.  **Outcomes**: heating energy and the hours in which an
  occupied zone was below 20 °C.

.. testsetup:: building

    import os
    import sys
    import tempfile

    _building_cwd = os.getcwd()
    _building_tmp = tempfile.TemporaryDirectory()
    os.chdir(_building_tmp.name)
    sys.path.insert(0, _building_tmp.name)
    _BUILDING_FILES = {
        'building.py': '''"""Occupancy sensors report to zone controllers, which heat a first-order zone model."""

    from typing import Literal

    from pydantic import BaseModel

    from mango import Agent
    from mango.scenario import Scenario, ScenarioParams, patterns


    class Sensor(Agent):
        def __init__(self, occupancy):
            super().__init__()
            self.occupancy, self.occupied = occupancy, False

        def on_step(self, env, clock, step_size_s):
            self.occupied = bool(self.rng.random() < self.occupancy.at(clock))
            for zone in self.neighbors(tid="zone"):
                self.schedule_instant_message(self.occupied, zone)


    class Zone(Agent):
        def __init__(self, p):
            super().__init__()
            self.p, self.temp_c, self.power_kw, self.occupied = p, 18.0, 0.0, False
            self.reports = {}

        def handle_message(self, content, meta):
            self.reports[meta["sender_id"]] = content

        def on_step(self, env, clock, step_size_s):
            p = self.p
            self.occupied = any(self.reports.values())
            if p.control == "occupancy":
                comfort = self.occupied
            else:  # a fixed schedule in local time (clock.datetime follows the time zone)
                comfort = 7 <= clock.datetime.hour < 19
            setpoint = p.comfort_c if comfort else p.setback_c
            demand_kw = p.gain_kw_per_k * (setpoint - self.temp_c)
            self.power_kw = min(p.max_kw, max(0.0, demand_kw))
            loss_kw = (self.temp_c - p.outdoor_c) * p.loss_kw_per_k
            hours = step_size_s / 3600
            self.temp_c += (self.power_kw - loss_kw) * hours / p.capacity_kwh_per_k


    class HVAC(BaseModel, extra="forbid"):
        zones: int = 3
        sensors_per_zone: int = 4
        control: Literal["occupancy", "schedule"] = "occupancy"
        comfort_c: float = 21.0
        setback_c: float = 17.0
        outdoor_c: float = 5.0
        loss_kw_per_k: float = 0.1
        capacity_kwh_per_k: float = 2.0
        gain_kw_per_k: float = 4.0
        max_kw: float = 10.0


    class Params(ScenarioParams):
        hvac: HVAC = HVAC()


    def make_sensor(e):
        return Sensor(e.profile("occupancy"))


    def make_zone(e):
        return Zone(e.p.hvac)


    scenario = Scenario("building", params=Params)
    sensors = scenario.group(
        "sensor", lambda p: p.hvac.zones * p.hvac.sensors_per_zone, make_sensor,
        install=lambda e: {"zone": e.index // e.p.hvac.sensors_per_zone},
    )
    zones = scenario.group(
        "zone", "hvac.zones", make_zone, install=lambda e: {"zone": e.index}
    )
    scenario.link("zone", sensors, zones, pattern=patterns.by_attr("zone"))
    scenario.record("temp_c", zones, attr="temp_c")
    scenario.record("power_kw", zones, attr="power_kw", kind="flow")
    scenario.record(
        "cold", zones, lambda zone: zone.occupied and zone.temp_c < 20.0, kind="flow"
    )


    def energy_kwh(run):
        """Heating energy of all zones (power integrated over the steps)."""
        return run.integrate("power_kw")


    def cold_hours(run):
        """Hours in which a zone was occupied and below 20 C, summed over the zones."""
        return run.integrate("cold")
    ''',
        'presets/base.yaml': '''# Two days across the switch to summer time (2024-03-31, 02:00 -> 03:00)
    time: {start: "2024-03-30T00:00[Europe/Berlin]", horizon: 2d, step: 15min}
    profiles:
      occupancy: {path: data/occupancy.csv, column: occupancy, time_column: time}
    ''',
        'studies/heating.yaml': '''# Occupancy-driven or scheduled heating?  2 strategies x 4 replications, on Slurm.
    scenario: building:scenario
    base: presets/base.yaml
    seed: 2026
    levers:
      hvac.control: [occupancy, schedule]
    replications: 4
    outcomes: [building:energy_kwh, building:cold_hours]
    out: results/heating
    executor:
      kind: slurm
      slurm:
        partition: short
        account: building-lab
        time: "00:10:00"
        runs_per_task: 4
        setup: ["module load python/3.11", "source .venv/bin/activate"]
    ''',
    }
    for _name, _text in _BUILDING_FILES.items():
        os.makedirs(os.path.dirname(_name) or ".", exist_ok=True)
        with open(_name, "w") as file:
            file.write(_text)
    import datetime as dt
    import os

    os.makedirs("data", exist_ok=True)

    rows, t = ["time,occupancy"], dt.datetime(2024, 3, 30)
    while t <= dt.datetime(2024, 4, 1):
        if t.date() != dt.date(2024, 3, 31) or t.hour != 2:  # 02:00 to 03:00 does not exist
            busy = 8 <= t.hour < 18
            rows.append(f"{t:%Y-%m-%dT%H:%M},{0.6 if busy else 0.05}")
        t += dt.timedelta(hours=1)
    with open("data/occupancy.csv", "w") as file:
        file.write("\n".join(rows) + "\n")

.. testcode:: building
   :hide:

   for _name, _text in _BUILDING_FILES.items():  # the files shown below
       assert open(_name).read() == _text

.. code-block:: python
   :caption: building.py

   """Occupancy sensors report to zone controllers, which heat a first-order zone model."""

   from typing import Literal

   from pydantic import BaseModel

   from mango import Agent
   from mango.scenario import Scenario, ScenarioParams, patterns


   class Sensor(Agent):
       def __init__(self, occupancy):
           super().__init__()
           self.occupancy, self.occupied = occupancy, False

       def on_step(self, env, clock, step_size_s):
           self.occupied = bool(self.rng.random() < self.occupancy.at(clock))
           for zone in self.neighbors(tid="zone"):
               self.schedule_instant_message(self.occupied, zone)


   class Zone(Agent):
       def __init__(self, p):
           super().__init__()
           self.p, self.temp_c, self.power_kw, self.occupied = p, 18.0, 0.0, False
           self.reports = {}

       def handle_message(self, content, meta):
           self.reports[meta["sender_id"]] = content

       def on_step(self, env, clock, step_size_s):
           p = self.p
           self.occupied = any(self.reports.values())
           if p.control == "occupancy":
               comfort = self.occupied
           else:  # a fixed schedule in local time (clock.datetime follows the time zone)
               comfort = 7 <= clock.datetime.hour < 19
           setpoint = p.comfort_c if comfort else p.setback_c
           demand_kw = p.gain_kw_per_k * (setpoint - self.temp_c)
           self.power_kw = min(p.max_kw, max(0.0, demand_kw))
           loss_kw = (self.temp_c - p.outdoor_c) * p.loss_kw_per_k
           hours = step_size_s / 3600
           self.temp_c += (self.power_kw - loss_kw) * hours / p.capacity_kwh_per_k


   class HVAC(BaseModel, extra="forbid"):
       zones: int = 3
       sensors_per_zone: int = 4
       control: Literal["occupancy", "schedule"] = "occupancy"
       comfort_c: float = 21.0
       setback_c: float = 17.0
       outdoor_c: float = 5.0
       loss_kw_per_k: float = 0.1
       capacity_kwh_per_k: float = 2.0
       gain_kw_per_k: float = 4.0
       max_kw: float = 10.0


   class Params(ScenarioParams):
       hvac: HVAC = HVAC()


   def make_sensor(e):
       return Sensor(e.profile("occupancy"))


   def make_zone(e):
       return Zone(e.p.hvac)


   scenario = Scenario("building", params=Params)
   sensors = scenario.group(
       "sensor", lambda p: p.hvac.zones * p.hvac.sensors_per_zone, make_sensor,
       install=lambda e: {"zone": e.index // e.p.hvac.sensors_per_zone},
   )
   zones = scenario.group(
       "zone", "hvac.zones", make_zone, install=lambda e: {"zone": e.index}
   )
   scenario.link("zone", sensors, zones, pattern=patterns.by_attr("zone"))
   scenario.record("temp_c", zones, attr="temp_c")
   scenario.record("power_kw", zones, attr="power_kw", kind="flow")
   scenario.record(
       "cold", zones, lambda zone: zone.occupied and zone.temp_c < 20.0, kind="flow"
   )


   def energy_kwh(run):
       """Heating energy of all zones (power integrated over the steps)."""
       return run.integrate("power_kw")


   def cold_hours(run):
       """Hours in which a zone was occupied and below 20 C, summed over the zones."""
       return run.integrate("cold")

The preset puts the run on the calendar of Berlin.  A named time zone makes
``clock.datetime`` follow daylight saving time, so the schedule's 07:00 is
local time on both days, and the export's naive local timestamps are placed
in that zone (:ref:`scenario-profiles`).  The profile is dated by its
``time`` column, so it does not wrap: it must cover the whole run (the
elapsed horizon, across the switch), ``validate`` reports an error and
exits 1 when it does not, and ``plan`` warns.

.. code-block:: text

    time,occupancy
    ...
    2024-03-31T01:00,0.05
    2024-03-31T03:00,0.05
    ...

.. code-block:: yaml
   :caption: presets/base.yaml

   # Two days across the switch to summer time (2024-03-31, 02:00 -> 03:00)
   time: {start: "2024-03-30T00:00[Europe/Berlin]", horizon: 2d, step: 15min}
   profiles:
     occupancy: {path: data/occupancy.csv, column: occupancy, time_column: time}

The study file keeps the cluster settings in its ``executor:`` block, so
every resubmission uses the same account, time limit and environment.  The
runs are short, so each array task executes four of them:

.. code-block:: yaml
   :caption: studies/heating.yaml

   # Occupancy-driven or scheduled heating?  2 strategies x 4 replications, on Slurm.
   scenario: building:scenario
   base: presets/base.yaml
   seed: 2026
   levers:
     hvac.control: [occupancy, schedule]
   replications: 4
   outcomes: [building:energy_kwh, building:cold_hours]
   out: results/heating
   executor:
     kind: slurm
     slurm:
       partition: short
       account: building-lab
       time: "00:10:00"
       runs_per_task: 4
       setup: ["module load python/3.11", "source .venv/bin/activate"]

Before a submission, ``validate --build`` builds run 0, takes one step and
evaluates every recorder once (also in a ``record: false`` warm-up), which
catches errors in factories, profile columns and recorders.
``--no-submit`` writes the array job without submitting it:

.. code-block:: console

    $ mango-scenario validate studies/heating.yaml --build
    OK: studies/heating.yaml is a valid study: 8 runs (15 agents each, 1 link), scenario building:scenario; parameters, declarations and data files of 2 distinct parameter sets checked; run built (15 agents), one step taken and 18 values recorded; 3 recorders evaluated (9 values)
    $ mango-scenario run studies/heating.yaml --no-submit
    8 runs: 0 done, 0 skipped, 0 failed, 8 pending in .../array.sbatch (not submitted; run: sbatch .../array.sbatch) in ... s (Slurm array, partition short, 4 runs per task, not submitted) -> .../results/heating

.. testcode:: building
   :skipif: True

   print(open("results/heating/slurm/array.sbatch").read(), end="")

.. testoutput:: building
   :skipif: True
   :options: +NORMALIZE_WHITESPACE

   #!/bin/bash
   #SBATCH --job-name=heating
   #SBATCH --array=0-1
   #SBATCH --partition=short
   #SBATCH --account=building-lab
   #SBATCH --time=00:10:00
   #SBATCH --cpus-per-task=1
   #SBATCH --output=.../results/heating/slurm/logs/%A_%a.out

   # Written by mango.scenario: 8 pending runs in 2 array tasks; array task i runs lines i*4 to i*4+3 of pending.jsonl.
   set -eu
   # setup
   set +u
   module load python/3.11
   source .venv/bin/activate
   set -u
   ...
   TASK=$(( ${MANGO_TASK_OFFSET:-0} + SLURM_ARRAY_TASK_ID ))
   cd ...
   python -m mango.scenario.cli run-one results/heating/slurm/pending.jsonl --lines "$(( TASK * 4 )):$(( TASK * 4 + 4 ))"

With ``setup`` lines the tasks run ``python`` from ``PATH``, so the
interpreter of the virtual environment the setup activates (``executor.slurm.python``
or ``--python`` names another one); the table path is relative to the
project root the task changes to.

(The blocks that read the results of the console session are skipped by
``make doctest``, which does not run console sessions; the test suite runs
them.)  Without a cluster at hand, run an array task locally: it executes
exactly what the task would.  A finished run is skipped, so a requeued task
never overwrites results:

.. code-block:: console

    $ mango-scenario run-one results/heating/slurm/pending.jsonl --lines 0:4
    run 0 (...) done in ... s (cpu ... s)
    run 1 (...) done in ... s (cpu ... s)
    run 2 (...) done in ... s (cpu ... s)
    run 3 (...) done in ... s (cpu ... s)
    $ mango-scenario status results/heating
    heating: 8 runs: 4 done, 0 failed, 4 pending
      time: ... s wall, ... s CPU over 4 finished runs (... s CPU per run on average)
    $ mango-scenario run-one results/heating/slurm/pending.jsonl --lines 0:1
    run 0 (...) already done; skipped (--force runs it again)
    $ mango-scenario run-one results/heating/slurm/pending.jsonl --lines 4:8
    run 4 (...) done in ... s (cpu ... s)
    run 5 (...) done in ... s (cpu ... s)
    run 6 (...) done in ... s (cpu ... s)
    run 7 (...) done in ... s (cpu ... s)

For the reviewer, ``rerun`` replays a run from its folder and compares the
checksum of everything it recorded, and ``to-script`` writes the run as a
plain mango script for an artifact:

.. code-block:: console

    $ mango-scenario rerun results/heating/run_0
    identical: run ... replayed in ... s, checksum ... (1737 values)
    $ mango-scenario to-script results/heating/run_0 -o run_0.py
    note: the script replays run 0 (...) (its parameters and seed)
    wrote the script to run_0.py
    $ python run_0.py
    1737 values recorded

The script imports the project's modules; written into another folder
(``-o artifact/run_0.py``) it puts the project folder on the import path
itself, so ``python artifact/run_0.py`` runs from anywhere.

Every run folder holds a manifest: the seed, the versions of Python and the
main packages, a hash of the installed environment, the git commit of the
project (taken by the process that ran it), the Slurm job and task ids, and
the project files and data files the run used with content hashes:

.. testcode:: building
   :skipif: True

   import json

   from mango.scenario import load_study

   frame = load_study("results/heating")
   run_id = frame.run_id[0]
   manifest = json.loads(open(f"results/heating/runs/{run_id}/manifest.json").read())
   print(manifest["code_files"], manifest["data_files"])
   print(sorted(manifest["provenance"]["versions"]))
   paired = frame.paired("hvac.control", baseline="schedule")
   print(paired[["outcome", "n", "mean", "ci_low", "ci_high"]].round(1))

.. testoutput:: building
   :skipif: True
   :options: +NORMALIZE_WHITESPACE

   {'building.py': '...'} {'data/occupancy.csv': '...'}
   ['mango-agents', 'networkx', 'numpy', 'pandas', 'pyarrow', 'pydantic', 'python', 'pyyaml', 'scipy']
         outcome  n  mean  ci_low  ci_high
   0  energy_kwh  4  25.6    21.4     29.7
   1  cold_hours  4  -5.6    -9.9     -1.2

Occupancy-driven heating keeps the occupied zones warmer (5.6 fewer cold
hours) but uses about 26 kWh more: one of four noisy sensors reports
someone at night now and then, and the zone heats up for nobody.  The
recipe's point is the record: when ``building.py`` or
``data/occupancy.csv`` changes, the finished runs are *stale* (also runs
that were executing while the file changed); ``status`` says which file
changed, and the next ``run`` executes them again and keeps the old
manifest as ``manifest.prev.json`` (``--keep-stale`` keeps the results
instead, and ``mango-scenario certify`` marks them current with a reason
when the change cannot affect them).  To scale: 60 zones, a heating season at one-minute
steps, ``runs_per_task: 1`` with a longer ``time``, and ``squeue`` /
``mango-scenario status`` while the array runs.

.. testcleanup:: building

    os.chdir(_building_cwd)
    sys.path.remove(_building_tmp.name)
    sys.modules.pop("building", None)
    _building_tmp.cleanup()
