=====================
Scenarios and studies
=====================

A simulation study is more than its agents.  Someone has to decide which
agents exist and how many, with which parameters, how they are wired, how
long the simulation runs and what is recorded.  For a study, someone has to
run many variants with several seeds, in parallel, store every result with
the parameters that produced it, and resume after a crash.  Without help,
each project writes this as a hand-made ``async def run()`` plus a loop, a
CSV dump and a multiprocessing wrapper, and copies it into the next project.

The scenario layer, :mod:`mango.scenario`, takes over that part.  It is a
thin layer on top of the :doc:`simulation`: it builds an ordinary
:class:`~mango.SimulationWorld` with ordinary agents, environments,
communication simulations and topologies, so the agents themselves do not
change.  It has three core parts:

* a **scenario** declares the structure once: groups of agents, links
  between them, recorders, environment and communication;
* a **parameter model** holds every number, validated, with presets and
  overrides;
* a **study** runs a scenario over factor combinations and seeds, on a
  process pool or a Slurm cluster, and stores one self-describing folder per
  run.

Every run derives all its random numbers from one seed, and every result is
a tidy table keyed by entity names such as ``ev_0``.  Around the core,
:ref:`kits <scenario-kits>` bundle reusable parts of a scenario (a battery
fleet, a grid) with their own parameters; :ref:`profiles
<scenario-profiles>` and :ref:`events and phases <scenario-events>` bring
time series, warm-up periods and outages into the parameters;
:ref:`sampling designs <scenario-designs>` add Latin hypercube and Sobol
studies with sensitivity indices; and :ref:`scenario and study files
<scenario-files>` with the :ref:`mango-scenario command line
<scenario-cli>` let you check, plan, run and replay studies without writing
a run script.

.. note::

   The scenario layer needs optional dependencies (pydantic, PyYAML and
   pandas): ``pip install "mango-agents[scenario]"``.  The extra
   ``scenario-all`` adds pyarrow (Parquet results; without it, run results
   are stored as CSV) and scipy (Latin hypercube and Sobol designs, Sobol
   indices).  Second-order Sobol indices also need SALib (``pip install
   SALib``).

.. seealso::

   :doc:`scenario-tutorial` walks through a complete example, the EV
   neighbourhood of the :doc:`simulation-ev-tutorial`.

.. _scenario-reading-path:

**Where to start.**  This page is a reference; nobody needs all of it.  For
a first model, read :ref:`scenario-first`, the `Building blocks of a
scenario`_ up to :ref:`scenario-timing`, `Parameter models`_ and `Presets,
overrides and precedence`_, :ref:`scenario-results` up to `The run result`_,
and :ref:`scenario-studies` up to `Loading a study`_ (about a third of the
page), then the :doc:`scenario-tutorial`.  ``mango-scenario new <folder>``
(see `New projects`_) writes a runnable project to start from.  Come back
for the rest through the table below when a question comes up.

.. _scenario-cheatsheet:

Which feature for which question
================================

Each row names the feature and where this page explains it;
:doc:`scenario-recipes` shows complete studies, one per research type,
that combine them.

.. list-table::
   :header-rows: 1
   :widths: 36 40 24

   * - I want to ...
     - Use
     - Read
   * - run a model once and look at the numbers
     - ``scenario.run(seed=1)``, ``result.wide("x")``, ``result.ts("x")``
     - :ref:`scenario-first`, `The run result`_
   * - try a variant without editing code
     - a preset file or ``overrides=["battery.n=20"]`` (``--set`` on the
       command line)
     - :ref:`scenario-params`
   * - declare parameters without a Python model, with bounds, allowed
       values and help texts
     - ``parameters:`` in a scenario file (``{default: 0.2, min: 0, max: 1,
       choices: [...], help: ...}``)
     - :ref:`Parameters without Python <scenario-file-parameters>`
   * - know exactly when things happen inside a step (message latency, lead
       times, when an event shows in the results)
     - the step cycle and the latency rule
     - :ref:`scenario-timing`
   * - sum energy, cost or other flows over time correctly; time-average a
       queue length or another state
     - ``record(..., kind="flow")`` / ``kind="state"``,
       ``result.integrate("power_kw")``, ``result.time_average("queue")``
     - :ref:`flows and states <scenario-state-flow>`
   * - daily or hourly totals and means (calendar bins)
     - ``result.bins("load_kw", "1D", how="integrate")``
     - :ref:`flows and states <scenario-state-flow>`
   * - count agents per state (compartments), sum or average over a group
     - ``record(..., aggregate="count", categories=[...])`` / ``"sum"`` /
       ``"mean"``
     - :ref:`scenario-aggregates`
   * - record the peak or mean per interval instead of snapshots
     - ``record(..., every="1h", window="max")``
     - :ref:`scenario-aggregates`
   * - keep a log of what happened (trades, infections, deliveries)
     - ``ctx.log(name, **fields)``, ``result.log(name)``
     - :ref:`scenario-logs`
   * - store one number per run (a convergence time)
     - ``ctx.set_metric(...)``, ``scenario.metric(...)``, study outcomes
     - :ref:`scenario-logs`, :ref:`scenario-studies`
   * - stop a run when a condition holds (convergence, extinction)
     - ``scenario.stop_when(fn)``
     - :ref:`scenario-running`
   * - react to a state inside the run (a lockdown above a threshold)
     - a behavior in a kit, or an agent's ``on_step``
     - :ref:`scenario-events`
   * - wire agents: rings, small worlds, scale-free graphs, lattices, an
       edge list from a file
     - ``scenario.link(name, a, b, pattern=...)``
     - :ref:`scenario-links`
   * - make the topology, a link delay or an event time a study factor
     - ``pattern="net.topology"``, ``"$path"`` arguments,
       ``link(..., delay="$lead.s")``, ``event(at="$shock.at")``
     - :ref:`scenario-structure-factor`
   * - change who talks to whom during a run
     - ``ctx.relink(name, pairs)``
     - :ref:`scenario-comm`
   * - build a population with random structure (households, communities)
     - a count from the parameters, ``install=`` attributes,
       ``patterns.by_attr`` or a custom pattern drawing from ``rng``
     - :ref:`scenario-groups`, :ref:`scenario-links`
   * - let agents be born and die
     - ``ctx.spawn(group)``, ``ctx.retire(entity)``
     - :ref:`scenario-dynamic`
   * - place agents in space and find neighbours by distance
     - ``Area2D``, ``space.neighbors_within(agent, r)``,
       ``space.agents_within(Position2D(x, y), r)``
     - :ref:`scenario-space`
   * - model message delays, loss, per-link lead times, partitions
     - ``comm.delay_s``, ``comm.loss_percent``, ``link(..., delay=,
       loss=)`` (lead time *L* steps: ``delay=(L - 1) * dt``),
       ``comm.partition``
     - :ref:`scenario-comm`, :ref:`scenario-timing`
   * - schedule outages, shocks or recurring disturbances
     - ``events:`` with ``at``, ``until``, ``every``, ``comm``, ``emit``
     - :ref:`scenario-events`
   * - leave a warm-up out of the results
     - a first phase with ``record: false`` (later phases record unless
       they say ``record: false``)
     - :ref:`scenario-events`
   * - feed time series (load, weather, prices) to agents
     - ``profiles:`` and ``e.profile(name)``
     - :ref:`scenario-profiles`
   * - give every entity a value from a table (a survey), several columns
       from the same respondent
     - ``FromColumn``, ``e.draw(dist)``, ``e.row(dist)``
     - `Per-entity values from a table`_
   * - count abstract rounds instead of seconds
     - ``time: {step: 1}`` and ``result.wide(x, index="step")``
     - `The run result`_
   * - compare algorithms fairly, with replications
     - ``Study(levers=..., replications=...)``, common random numbers,
       ``frame.paired(...)``
     - :ref:`scenario-studies`, :ref:`scenario-crn`
   * - compare variants that have settings of their own
     - levers on ``algorithm.kind``, ``subexperiments``
     - :ref:`scenario-subexperiments`
   * - explore uncertain inputs, rank parameters by influence
     - ``Lhs``, ``Sobol``, ``frame.sobol_indices()``
     - :ref:`scenario-designs`
   * - report means with confidence intervals
     - ``frame.summary()``, ``frame.paired(...)`` (``missing`` counts runs
       without a value; ``pairs=True`` lists the pairs)
     - `Loading a study`_
   * - plan the number of replications from a pilot
     - ``frame.replications_needed("x", half_width=...)``
     - :ref:`scenario-crn-analysis`
   * - fit my own regression or ANOVA to a study, handle censored runs or a
       warm-up
     - block on ``C(replication)``, one value per run
     - :ref:`scenario-crn-analysis`
   * - run many runs in parallel, or on a cluster; know what a run costs
     - ``workers=`` (short runs share workers: ``runs_per_worker="auto"``),
       ``executor="slurm"``; ``cpu_s`` in manifests and ``status.csv``
     - :ref:`scenario-executors`, :ref:`scenario-slurm`
   * - change the model after some runs finished
     - stale runs (also edits while they ran) are detected and run again;
       ``mango-scenario certify`` keeps them after a harmless change
     - `Resuming`_
   * - test a study quickly before a long run
     - ``validate --build``, ``scenario.run(until="2h")``
     - :ref:`scenario-cli`, :ref:`scenario-running`
   * - replay a run for a reviewer, show which code and environment made it
     - ``mango-scenario rerun``, the run manifest (package list in
       ``environments/``, the diff of a dirty tree, ``--require-clean``)
     - `The results folder`_, :ref:`scenario-cli`
   * - see why a run recorded nothing or what went wrong quietly
     - ``result.warnings`` (also in the manifest and ``status``),
       ``ctx.warn(...)``
     - `The run result`_
   * - reuse a part of the model (a fleet, a grid, a market) in other
       studies
     - a kit (``ScenarioComponent``)
     - :ref:`scenario-kits`
   * - debug one run step by step, inspect the messages it delivered, or
       see it as plain mango code
     - ``scenario.build()`` with ``run.step()``, ``scenario.run(...,
       record_messages=True)``, ``scenario.to_script()``
     - :ref:`scenario-running`, :ref:`scenario-large-runs`,
       :ref:`scenario-tooling`
   * - teach with a form instead of YAML
     - ``mango-scenario form``
     - `Preset forms`_

----

.. _scenario-architecture:

How the parts fit together
==========================

.. code-block:: text

    structure                   numbers                     variants and seeds
    Scenario: Python, kits      parameter model, presets,   Study: factors, design,
    or a scenario file          overrides                   replications, seed
            │                           │                           │
            └─────────────┬─────────────┴───────────────────────────┘
                          ▼
            run specs: plain JSON (scenario reference, resolved parameters, seed)
                          │  executor: in-process | process pool | Slurm array
                          ▼
            worker: imports the scenario, builds a SimulationWorld, runs it
                          ▼
            run folder: spec, resolved config, manifest, tidy time series, outcomes

Three rules keep the parts independent of each other:

* **Structure, numbers and execution are separate objects.**  The
  :class:`~mango.scenario.Scenario` holds the structure, the parameter model
  every number, and the study or executor decides where runs execute.
  Changing ``battery.n`` never touches the scenario; moving from a laptop to
  a cluster never touches either.
* **Everything that crosses a process boundary is plain data.**  A run spec
  holds the scenario's import reference, the fully resolved parameters and
  the seed as JSON, so any worker (a pool process, a Slurm array task, a
  colleague's machine years later) can execute or replay the run; results
  come back as files, never as live objects.
* **The scenario layer only uses public mango API.**  It calls
  ``create_world``, ``world.register``, environments and behaviors,
  communication simulations, ``create_topology`` and the recorders, exactly
  as a hand-written script would; :meth:`Scenario.to_script
  <mango.scenario.Scenario.to_script>` writes one run out as such a script.

.. testsetup:: scenario

    import os
    import tempfile

    _scenario_cwd = os.getcwd()
    _scenario_tmp = tempfile.TemporaryDirectory()
    os.chdir(_scenario_tmp.name)
    os.makedirs("presets")
    with open("presets/base.yaml", "w") as file:
        file.write(
            "time: {horizon: 6h, step: 30min}\n"
            "battery:\n"
            "  n: 5\n"
            "  soc_kwh: {uniform: [1.0, 4.0]}\n"
        )
    with open("presets/large.yaml", "w") as file:
        file.write(
            "extends: presets/base.yaml\n"
            "battery: {n: 50, capacity_kwh: 13.5}\n"
        )
    with open("presets/typo.yaml", "w") as file:
        file.write(
            "battery:\n"
            "  capactiy_kwh: 12\n"
            "  charge_kw: fast\n"
        )

----

.. _scenario-first:

A first scenario
================

The running example of this page is a small fleet of batteries.  Each
battery charges with a constant power and reports its state of charge to an
aggregator.  The agents are plain mango agents; the battery finds the
aggregator through a topology (``self.neighbors(tid="reports")``) instead of
holding its address:

.. testcode:: scenario

    from pydantic import BaseModel

    from mango import Agent
    from mango.scenario import Scenario, ScenarioParams, Uniform


    class Battery(Agent):
        def __init__(self, capacity_kwh: float, soc_kwh: float, charge_kw: float):
            super().__init__()
            if soc_kwh > capacity_kwh:
                raise ValueError(f"soc_kwh {soc_kwh:.2f} exceeds capacity_kwh {capacity_kwh}")
            self.capacity_kwh = capacity_kwh
            self.soc_kwh = soc_kwh
            self.charge_kw = charge_kw

        def on_step(self, env, clock, step_size_s):
            energy_kwh = self.charge_kw * step_size_s / 3600
            self.soc_kwh = min(self.capacity_kwh, self.soc_kwh + energy_kwh)
            for aggregator in self.neighbors(tid="reports"):
                self.schedule_instant_message(self.soc_kwh, aggregator)


    class Aggregator(Agent):
        def __init__(self):
            super().__init__()
            self.reports = {}

        def handle_message(self, content, meta):
            self.reports[meta["sender_id"]] = content

The numbers go into a parameter model, the structure into a scenario:

.. testcode:: scenario

    class BatteryParams(BaseModel, extra="forbid"):
        n: int = 3
        capacity_kwh: float = 10.0
        soc_kwh: Uniform = Uniform(2.0, 6.0)
        charge_kw: float = 1.5


    class Params(ScenarioParams):
        battery: BatteryParams = BatteryParams()


    def make_battery(e):
        p = e.p.battery
        return Battery(
            capacity_kwh=p.capacity_kwh,
            soc_kwh=p.soc_kwh.draw(e.rng),
            charge_kw=p.charge_kw,
        )


    scenario = Scenario("batteries", params=Params)
    scenario.time(horizon="4h", step="1h")
    aggregator = scenario.group("aggregator", 1, lambda e: Aggregator())
    batteries = scenario.group("battery", "battery.n", make_battery)
    scenario.link("reports", batteries, aggregator, pattern="many_to_one")
    scenario.record("soc_kwh", batteries, attr="soc_kwh")
    scenario.record_world(
        "reported_kwh", lambda ctx: sum(ctx.agents("aggregator")[0].reports.values())
    )

    result = scenario.run(seed=42)
    print(result.wide("soc_kwh").round(2))

.. testoutput:: scenario
   :options: +NORMALIZE_WHITESPACE

    entity   battery_0  battery_1  battery_2
    time
    0.0           5.26       3.06       4.75
    3600.0        6.76       4.56       6.25
    7200.0        8.26       6.06       7.75
    10800.0       9.76       7.56       9.25
    14400.0      10.00       9.06      10.00

What happened:

* ``Scenario("batteries", params=Params)`` creates the scenario with its
  parameter model; :meth:`~mango.scenario.Scenario.time` sets the default
  horizon and step.
* :meth:`~mango.scenario.Scenario.group` declares a group: a name, a count
  (here the parameter ``battery.n``) and a factory that builds one agent per
  entity.  The factory gets an :class:`~mango.scenario.EntityContext` ``e``
  with the parameters (``e.p``), the entity's ``name`` and ``index``, and its
  own random stream ``e.rng``.
* :meth:`~mango.scenario.Scenario.link` connects every battery with the
  aggregator in a topology called ``reports``.
* :meth:`~mango.scenario.Scenario.record` samples ``soc_kwh`` of every
  battery, :meth:`~mango.scenario.Scenario.record_world` one world-level value
  per step.
* :meth:`~mango.scenario.Scenario.run` resolves the parameters, builds a
  fresh world, steps it to the horizon and returns a
  :class:`~mango.scenario.RunResult`.

The entities are named ``aggregator`` and ``battery_0`` to ``battery_2``;
the names are the agents' aids and the keys of the results.

.. tip::

   Agents are plain :class:`~mango.Agent` subclasses: call
   ``super().__init__()`` first, and do not name your own attributes
   ``name``, ``color``, ``category``, ``inbox``, ``context``, ``scheduler``
   or the other names :ref:`mango reserves <simulation-reserved-names>`
   (assigning one raises an error that suggests another name).  A study on a
   process pool also needs its script's main guard, see `Executors`_.

----

Building blocks of a scenario
=============================

A scenario only *declares*; nothing is built until a run starts.  Every run
replays the declarations against its own parameters and seed and builds the
world in a fixed order:

1. parameters, seed and time are resolved;
2. the declarations are evaluated (group counts and entity names, links,
   recorders);
3. the communication simulation and the environment are created, then the
   world;
4. the groups are instantiated in declaration order: for each entity
   ``install`` → factory → ``world.register``;
5. positions are applied, links are compiled to topologies, recorders are
   attached and ``on_build`` hooks run;
6. the world starts (``on_ready``, environment initialisation, the first
   samples at time 0) and is stepped to the horizon.

.. _scenario-groups:

Groups
------

``scenario.group(name, count, factory, *, install=None, position=None,
naming=None)`` declares a group and returns a :class:`~mango.scenario.GroupRef`
that links and recorders accept (a group name works as well).

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Argument
     - Meaning
   * - ``count``
     - An int, a dotted parameter path such as ``"battery.n"``, or a function
       of the parameters, e.g. ``lambda p: len(p.households)``.  ``0`` gives
       an empty group.
   * - ``factory(e)``
     - Returns the agent of entity ``e``.  ``e`` offers ``name``, ``group``,
       ``index``, ``count``, ``p`` (the parameters of the declaring scope:
       ``params.<prefix>`` in a kit, else all) and ``params`` (all
       parameters of the run), ``time``,
       ``attrs`` (from ``install``), ``rng`` and ``rng_for(name)`` (the
       entity's streams, see `Seeds and random streams`_) and ``ctx`` (the
       :class:`~mango.scenario.BuildContext`).
   * - ``install(e)``
     - Evaluated before the factory; returns a dict that becomes ``e.attrs``
       and is passed to ``world.register(...)`` as install arguments of the
       environment (e.g. ``{"bus": e.index % 4}``).
   * - ``position(e)``
     - Returns the entity's position, applied with ``space.move`` after
       registration; ``e.agent`` is the agent the factory returned (see
       :ref:`scenario-space`).
   * - ``naming``
     - Entity names: a format string with ``{group}`` and ``{index}``
       (``"{group}_{index:03d}"``) or a function ``naming(e) -> str``.  The
       default is ``"{group}_{index}"``; a group declared with the literal
       count ``1`` is named like the group.  ``{group}`` is the qualified
       group name (``north.battery`` in kit ``north``), and the result of a
       format string or function is used verbatim: in a kit, include
       ``{group}`` (or ``e.group``) to keep the names prefixed;
       ``naming=lambda e: STAGES[e.index]`` gives ``retailer``, not
       ``chain.retailer``.

Entity names must be unique, must not contain ``/`` and must not look like
mango's generated aids (``agent0``); ``plan()`` checks them before anything is
built.  ``group`` also works as a decorator when the
factory is omitted:

.. testcode:: scenario

    from mango import Area2D, DefaultEnvironment, Position2D

    spatial = Scenario("spatial", params=Params)


    @spatial.group("battery", "battery.n", position=lambda e: Position2D(float(e.index), 0.0))
    def battery(e):
        return make_battery(e)


    @spatial.environment
    def environment(ctx):
        return DefaultEnvironment(space=Area2D(width=10.0, height=1.0))


    @spatial.on_build
    def show(ctx):
        space = ctx.world.environment.space
        print([space.location(agent) for agent in ctx.agents("battery")])


    print(battery)
    spatial_result = spatial.run(seed=1)

.. testoutput:: scenario

    battery
    [Position2D(x=0.0, y=0.0), Position2D(x=1.0, y=0.0), Position2D(x=2.0, y=0.0)]

.. _scenario-dynamic:

Dynamic populations
-------------------

Group sizes are fixed when a run is built, but entities can be born and die
during the run: ``ctx.spawn(group, attrs=None)``
(:meth:`~mango.scenario.BuildContext.spawn`) builds the next entity of a
group like the others (``install``, then the factory, then registration and
``position``) and returns its :class:`~mango.scenario.Entity`;
``ctx.retire(entity)`` (:meth:`~mango.scenario.BuildContext.retire`) removes
an entity, its agent or its name.  Agents reach the context through the
factory, e.g. ``lambda e: Rabbit(e.ctx)``.

* **Names and streams.** A spawned entity continues the group's numbering
  (``rabbit_2`` after ``rabbit_0`` and ``rabbit_1``; the group's ``naming``
  applies, names are never reused), so its random stream
  ``agent/<name>`` is reproducible per seed and does not shift other
  streams.
* **Stepping.** A spawned agent is registered at once (``on_ready`` runs)
  and stepped from the next step on; a retired one is deregistered, its
  tasks are cancelled, ``on_stop`` runs, and messages to it are dropped.
  It is not stepped again, not even later in the step in which it was
  retired, whatever the registration order.
* **Recording.** Recorders sample the current members: a spawned entity's
  rows start at the next sample, a retired entity's rows end before it, so
  ``result.wide()`` shows ``NaN`` outside its lifetime and the tidy table
  has no rows there.  ``ctx.agents(group)`` lists the living members.
* **Links.** A retired entity loses its edges in every link.  A spawned
  entity joins no link by itself: ``ctx.relink(name)`` applies the link's
  declared patterns again to the current members, ``ctx.relink(name,
  pairs)`` sets the edges explicitly.
* ``result.stats`` adds ``spawned``, ``retired`` and ``agents_alive`` (per
  group) to runs that used them; ``entity.alive`` is ``False`` once an
  entity was retired.

.. testcode:: scenario

    class Rabbit(Agent):
        def __init__(self, ctx):
            super().__init__()
            self.ctx = ctx
            self.age = 0

        def on_step(self, env, clock, step_size_s):
            self.age += 1
            if self.age == 2:  # every rabbit has one offspring at age 2
                self.ctx.spawn("rabbit")


    class Fox(Agent):
        def __init__(self, ctx):
            super().__init__()
            self.ctx = ctx

        def on_step(self, env, clock, step_size_s):
            rabbits = self.ctx.agents("rabbit")
            if len(rabbits) > 3:  # eats the oldest rabbit
                self.ctx.retire(rabbits[0])


    meadow = Scenario("meadow")
    meadow.time(horizon=4, step=1)
    meadow.group("rabbit", 2, lambda e: Rabbit(e.ctx))
    meadow.group("fox", 1, lambda e: Fox(e.ctx))
    meadow.record("age", "rabbit", attr="age")
    meadow_result = meadow.run(seed=1)
    print(meadow_result.wide("age"))
    print(meadow_result.stats["spawned"], meadow_result.stats["retired"])

.. testoutput:: scenario
   :options: +NORMALIZE_WHITESPACE

    entity  rabbit_0  rabbit_1  rabbit_2  rabbit_3  rabbit_4  rabbit_5
    time
    0.0          0.0       0.0       NaN       NaN       NaN       NaN
    1.0          1.0       1.0       NaN       NaN       NaN       NaN
    2.0          NaN       2.0       0.0       0.0       NaN       NaN
    3.0          NaN       3.0       1.0       1.0       NaN       NaN
    4.0          NaN       4.0       2.0       2.0       0.0       0.0
    4 1

Rabbits step before the fox (registration order), so at ``t = 2`` both
rabbits have had their offspring when the fox retires ``rabbit_0`` (with the
fox first, ``rabbit_0`` would not step at ``t = 2`` at all).  An
alternative that keeps every entity for the whole run is a pre-allocated
pool with an ``alive`` flag and a recorder that returns ``math.nan`` for
inactive entities.

.. _scenario-comm:

Environment, communication and hooks
------------------------------------

``scenario.environment(factory)`` and ``scenario.communication(factory)``
set factories that receive the run's :class:`~mango.scenario.BuildContext`
``ctx`` (both work as decorators).  Without an environment factory every run
gets a fresh ``DefaultEnvironment()``.  Without a communication factory the
communication simulation is built from the parameters ``comm``
(:class:`~mango.scenario.CommConfig`): ``kind`` (``"simple"``, ``"ideal"``
or ``"custom"`` with an import reference ``factory``), ``loss_percent`` (a
probability) and ``delay_s`` (seconds or a duration such as ``"2h"``, or a
distribution drawn per message, durations allowed inside, e.g. ``{uniform:
[1h, 3h]}``):

.. testcode:: scenario

    lossy = scenario.run(
        seed=42, overrides=["comm.loss_percent=0.5", "comm.delay_s={uniform: [0, 1800]}"]
    )
    print(result.stats["messages_delivered"], lossy.stats["messages_delivered"])

.. testoutput:: scenario

    12 4

A custom communication factory must read ``ctx.comm`` (the effective
settings) and should draw from ``ctx.seeds.rng("comm")``, for example
``scenario.communication(lambda ctx: SimpleCommunicationSimulation(
loss_percent=ctx.comm.loss_percent, rng=ctx.seeds.rng("comm")))``.  The
``comm`` stream is one stream drawn in delivery order (by the built-in
models, too): a variant that sends more or fewer messages shifts the loss
and delay draws of every later message, so under common random numbers
message noise is only common up to the first difference in traffic.  The
params-level ``comm: {kind: custom, factory: module:fn}`` calls ``fn(cfg,
rng)`` or, when it takes a third argument, ``fn(cfg, rng, ctx)``.
``comm.kind`` selects a mode of one model: switching it in a preset, an
``extends`` file, an override or an event keeps ``loss_percent``,
``delay_s`` and ``options`` (``ideal`` ignores them).  When phases or events
change the settings, a simulation with a method ``on_comm_change(comm)`` is
kept and told; otherwise the factory builds one simulation per distinct
setting and the run reuses it when the setting returns (the end of an
outage brings back the instance, and its state, from before).  Run folders
name the factories: ``manifest["factories"]`` and a comment at the top of
``config.resolved.yaml``; ``plan()`` prints a ``communication:`` line.

Links can carry their own communication: ``scenario.link(name, ...,
delay="1w", loss=0.05)`` adds the delay (seconds, a duration or a
distribution) to every message along the link and drops it with the extra
probability, drawn from a stream per directed edge; both may read the
parameters (``delay="$corridor.travel_s"``, see `Structure as a study
factor`_).  For a lead time of *L* steps use ``delay = (L - 1) * dt`` (see
the latency rule below): with weekly steps ``delay={"choice": [0, "1w",
"2w"]}`` gives lead times of 1, 2 or 3 weeks.  Durations inside a delay
distribution are converted to seconds, so ``{"uniform": ["1w", "3w"]}`` and
``{"int_uniform": ["1w", "3w"]}`` draw (whole) seconds between one and three
weeks: with weekly steps that is a lead time of 3 or 4 weeks, never 1 or 2.
Only delay fields convert durations; a :data:`~mango.scenario.Dist`
parameter that mixes numbers and durations (``{choice: [0, 1w]}``) is
refused with the values in seconds.  ``comm: {partition: [[north], [south]]}`` (groups or
entities; or ``{by_attr: side}``) drops messages between the blocks, usually
inside an event.  ``ctx.relink(name, pairs)`` replaces a link's edges while
the run is running (event actions, behaviors, agents holding ``ctx``), and
``ctx.link_pairs(name)`` lists them, e.g. to recompute range-based
neighbourhoods every step.

The context offers ``ctx.p`` (the parameters of the declaring scope:
``params.<prefix>`` inside a kit, all parameters otherwise),
``ctx.params`` (all parameters of the run, also inside a kit), ``ctx.time``, ``ctx.comm``, ``ctx.seeds`` (the run's
:class:`~mango.util.seeding.SeedTree`), ``ctx.rng(name)`` (a named world-level
stream), ``ctx.world`` and, once the groups exist, ``ctx.entities(group)``,
``ctx.agents(group)`` and ``ctx.entity(name)``; while the run runs also
``ctx.phase`` (the current phase name) and ``ctx.recording``.
``ctx.warn(message)`` issues a :class:`~mango.scenario.RunWarning` and keeps
it with the result (``result.warnings``, once per message), so a study keeps
it even from worker processes.  ``scenario.on_build(fn)``
registers ``fn(ctx)`` to run after agents, links and recorders exist and
before the world starts: an escape hatch for wiring the builder does not
cover.

Planning
--------

:meth:`~mango.scenario.Scenario.plan` resolves the parameters and evaluates
the declarations without building agents.  It reports counts, entity names,
links with their edge counts (when they do not depend on random draws) and
recorders.  It fails on the same parameter and declaration errors a run would
(invalid parameters, unknown groups, duplicate entity names, link patterns
that do not fit the group sizes):

.. testcode:: scenario

    print(scenario.plan(overrides=["battery.n=20"]))

.. testoutput:: scenario

    Scenario 'batteries': 21 agents in 2 groups, 1 link, 2 recorders
      time: horizon 4h, step 1h (4 steps)
      groups:
        aggregator            1  aggregator
        battery              20  battery_0 .. battery_19
      links:
        reports          battery -> aggregator (many_to_one); 20 edges, undirected
      recorders:
        soc_kwh          battery
        reported_kwh     world

.. _scenario-running:

Running
-------

``scenario.run(params=None, *, seed=None, overrides=None)`` runs to the
horizon and also works inside a running event loop such as Jupyter (it then
runs in a helper thread); ``await scenario.run_async(...)`` is the
coroutine.  For control over the steps, :meth:`~mango.scenario.Scenario.build`
returns a :class:`~mango.scenario.ScenarioRun`, an async context manager that
builds and starts the world on entry:

.. testcode:: scenario

    import asyncio


    async def inspect_midway():
        run = scenario.build(seed=42)
        async with run:
            await run.run_until("2h")
            print(run.elapsed_s, [round(a.soc_kwh, 2) for a in run.ctx.agents("battery")])
            await run.run()
        return run.result()


    stepped = asyncio.run(inspect_midway())
    print(stepped.checksum() == result.checksum())

.. testoutput:: scenario

    7200.0 [8.26, 6.06, 7.75]
    True

``run.step()`` advances one step (or, with ``time.step: event``, to the
next event) and returns ``None`` at the horizon; ``run.run_until(t)`` accepts
an offset (``"12h"``, seconds) or, with a calendar start, a date and time.

**Quick test runs.** ``scenario.run(..., until="2h")`` (also ``run_async``
and ``run.execute(until=...)``) stops early without changing the scenario:
shortening ``time.horizon`` instead fails when a preset has phases or events
after the new horizon, because they are checked against it.  Events after
``until`` never fire, and the result says it was truncated
(``stats["stopped_early"]``, ``stats["stop_reason"] == "until"``).  From the
command line, ``mango-scenario run scenario.yaml -p presets/base.yaml
--until 2h`` does the same for a single run: the run folder is named
``<run id>_until_7200s``, the manifest records ``until`` (so ``rerun``
replays the truncated run) and the summary says where the run stopped.
Studies always run to the horizon.

**Stopping early.** ``scenario.stop_when(fn, every=None)`` (a decorator, or
``stop_when: module:fn`` / ``{fn: module:fn, every: 10s}`` in a scenario
file) ends a run once ``fn(ctx)`` returns something true, checked after
every step or at multiples of ``every``; a string result is the reason.
``ctx.stop(reason)`` and ``run.stop(reason)`` end a run from hooks, event
actions or a manual ``while await run.step() is not None`` loop.  The time
series end at the stop, and ``stats`` (also in the manifest) holds
``stopped_early``, ``stop_time_s`` and ``stop_reason``, so outcomes such as
a time to convergence read ``run.stats["stop_time_s"]``.  Check times are
multiples of ``every`` without float noise (``every=0.1`` stops at 4.1, not
4.1000000000000005; the same holds for fixed steps and recorder times).  A
condition that needs memory across calls keeps it in ``ctx.state`` (a dict
of the run, empty at its start) and reads metrics it set earlier with
``ctx.get_metric(name, default=None)``:

.. code-block:: python

    @scenario.stop_when(every="1s")
    def converged(ctx):
        spread = max(opinions(ctx)) - min(opinions(ctx))
        if spread < 0.1 and ctx.get_metric("t_close") is None:
            ctx.set_metric("t_close", ctx.world.clock.time)  # recorded once
        ctx.state["calm"] = ctx.state.get("calm", 0) + 1 if spread < 1e-3 else 0
        return ctx.state["calm"] >= 5

To average curves over runs that stopped at different times,
``frame.timeseries(variables=[...], pad=True)`` holds each run's last
values up to the later times of the other runs (added rows have
``padded == True``).

.. _scenario-timing:

Timing within a step
--------------------

For lead times, request/reply protocols and detection delays, the order of
calls inside a step is part of the model.  One step takes the run from
``t`` to ``t + dt`` (the fixed step; in event-driven runs the time to the
next message, task wake-up or boundary).  Inside it, always in this order:

1. **Phases and events** due at ``t`` are applied: emits reach
   ``on_global_event``/``on_agent_event``, actions run (scenario events
   first, then parameter events, each in declaration order).
2. **Agents step**: every agent's ``on_step(env, clock, dt)`` (roles
   included) runs with ``clock.time == t``, in registration order: groups
   in declaration order, each in index order, entities spawned during the
   run last.
3. **Behaviors step**, in declaration order; the clock is still at ``t``.
4. **The clock moves** to ``t + dt``: tasks due by then wake up, and the
   communication settings of a phase or event that starts at ``t + dt``
   apply from here on.
5. **Messages are delivered**: every message due at or before ``t + dt``,
   then the ones its handlers and the woken tasks sent that are due too,
   repeatedly until nothing is due.  A reply with delay 0 therefore
   arrives within the same step, and so does a chain of such replies.
6. **The recorders sample**: these are the rows at ``t + dt`` (see
   :ref:`scenario-rows`); then ``stop_when`` conditions are checked.

The rows at ``t = 0`` are taken when the world starts, before the first
step; events at ``t = 0`` are applied after them.  So an event at ``t``
shows first in the row at ``t + dt``.

**Message latency.**  A message carries the clock time at which its send
coroutine runs (its *stamp*) and is delivered in the first step that ends at
or after ``stamp + delay``.  ``schedule_instant_message`` queues the send
(in a simulation world as one loop callback, elsewhere as a task), and it
runs only after the clock moved (step 4), so a send from ``on_step`` at
``t`` is stamped ``t + dt``:

.. list-table::
   :header-rows: 1
   :widths: 40 22 38

   * - Message
     - Delay ``d``
     - Handled by the receiver at
   * - sent in ``on_step`` at ``t``
     - ``0``
     - ``t + dt``, in the same step; its ``on_step`` at ``t + dt`` sees it
   * - sent in ``on_step`` at ``t``
     - ``0 < d <= dt``
     - ``t + 2 dt``
   * - sent in ``on_step`` at ``t``
     - any
     - ``t + dt + ceil(d / dt) * dt``
   * - sent by a handler or task while delivering at ``t + dt`` (a reply)
     - ``0``
     - ``t + dt``, in the same step
   * - any message, in an event-driven run
     - any
     - exactly ``stamp + d`` (the world steps to that time)

For a lead time of *L* steps from the sender's ``on_step`` to the
receiver's ``on_step``, use ``delay = (L - 1) * dt``: delay 0 is a lead time
of one step.  A message also passes the communication settings in force at
its stamp: an outage from ``t1`` to ``t2`` drops what ``on_step`` sends at
``t1 - dt`` up to (not including) ``t2 - dt``, and every reply sent while
messages are delivered at times in ``[t1, t2)``.

**Activation order.**  Agents always step in registration order, so in
every step the first group acts on the state the later groups left one step
earlier, while the later groups already see the first group's changes.
When that would bias a model (two groups competing for the same places),
let the agents only decide in ``on_step`` and apply the decisions in a
behavior, which runs after all agents (or shuffles them with its own
stream, see :ref:`scenario-kits`).

**Event-driven runs.**  With ``time.step: event`` the world steps to the
next due message, task wake-up, phase or event boundary, or multiple of a
recorder's or ``stop_when``'s ``every``.  ``on_step`` and the behaviors run
at every such step, with the size of that step as ``step_size_s``, so code
that integrates over time must use ``step_size_s``, never a fixed step.

This scenario logs every call: ``asker`` sends a question from ``on_step``,
``answerer`` replies with delay 0, and an event fires at ``t = 1``:

.. testcode:: timing

    from mango import Agent, Behavior, DefaultEnvironment, sender_addr
    from mango.scenario import Scenario

    trace = []


    class Asker(Agent):
        def on_step(self, env, clock, step_size_s):
            trace.append(f"t={clock.time:g}  on_step of {self.aid}")
            for peer in self.neighbors(tid="ask"):
                self.schedule_instant_message("question", peer)

        def on_global_event(self, event):
            trace.append(f"t={event.time:g}  event {event.name}")

        def handle_message(self, content, meta):
            trace.append(f"t={self.scheduler.clock.time:g}  {self.aid} handles {content}")


    class Answerer(Agent):
        def handle_message(self, content, meta):
            trace.append(f"t={self.scheduler.clock.time:g}  {self.aid} handles {content}")
            self.schedule_instant_message("answer", sender_addr(meta))


    class Dynamics(Behavior):
        def on_step(self, environment, clock, step_size_s):
            trace.append(f"t={clock.time:g}  behavior")


    timing = Scenario("timing")
    timing.time(horizon=2, step=1)
    asker = timing.group("asker", 1, lambda e: Asker())
    answerer = timing.group("answerer", 1, lambda e: Answerer())
    timing.link("ask", asker, answerer, pattern="one_to_one")
    timing.environment(lambda ctx: DefaultEnvironment(behavior=Dynamics()))
    timing.event(1, name="go", emit={"kind": "go"})
    timing.record_world("rows", lambda ctx: trace.append(f"t={ctx.world.clock.time:g}  record"))
    timing.run(seed=1)
    print("\n".join(trace))

.. testoutput:: timing

    t=0  record
    t=0  on_step of asker
    t=0  behavior
    t=1  answerer handles question
    t=1  asker handles answer
    t=1  record
    t=1  event go
    t=1  on_step of asker
    t=1  behavior
    t=2  answerer handles question
    t=2  asker handles answer
    t=2  record

The question sent at ``t = 0`` and its answer are both handled at ``t =
1``, before the row at ``t = 1``; the event at ``t = 1`` comes after that
row, so its effects show from ``t = 2``.  With ``comm.delay_s`` between 0
and 1 the question would arrive at ``t = 2``.

----

.. _scenario-params:

Parameters
==========

Parameter models
----------------

The parameters of a scenario are a `pydantic <https://docs.pydantic.dev>`_
model that subclasses :class:`~mango.scenario.ScenarioParams`.  The base
class contributes ``seed``, ``time`` (:class:`~mango.scenario.TimeConfig`:
``start``, ``horizon``, ``step``) and ``comm``
(:class:`~mango.scenario.CommConfig`) and forbids unknown keys.
:func:`~mango.scenario.load_params` checks every key of every preset, dict
and override against the model, also inside nested models that use
pydantic's default of ignoring unknown keys, so a typo is an error instead of
a silently ignored key (only models declared with an explicit
``extra="ignore"`` or ``extra="allow"`` are left alone).  Nested models
should still forbid unknown keys (``extra="forbid"``) for code that builds
them directly.  Durations accept numbers (seconds) and strings such as
``"15min"``, ``"1h30min"``, ``"2d"`` or ``"1w"``; ``horizon`` and ``step``
are elapsed time, so ``"3d"`` is 72 hours even across a 23-hour
daylight-saving day, and the run ends at ``time.end``.  A calendar ``start`` adds a
``datetime`` column to all results and makes ``clock.datetime`` available to
agents.  A ``start`` with a time zone follows its daylight saving rules when
the zone is named: write ``"2024-03-31T00:00[Europe/Berlin]"`` or pass a
``zoneinfo.ZoneInfo``; the resolved parameters keep the name
(``"2024-03-31T00:00:00+01:00[Europe/Berlin]"``), so a replay from
``result.config`` uses the same calendar.  A fixed offset such as
``"+01:00"`` stays a fixed offset.

A scenario file can declare simple parameters without a Python model: see
:ref:`Parameters without Python <scenario-file-parameters>`.  Without a
model of its own, a scenario has only the fields of ``ScenarioParams``, and
an unknown parameter is an error that says how to add one.

The algorithm under test can be a tagged union of models, so that a variant
switches its kind and its own settings in one line.  Tuning the default
variant needs no ``kind``; switching the ``kind`` drops the settings of the
previous variant:

.. testcode:: scenario

    from typing import Literal

    from pydantic import Field

    from mango.scenario import load_params


    class Greedy(BaseModel, extra="forbid"):
        kind: Literal["greedy"] = "greedy"
        threshold_kw: float = 0.3


    class Market(BaseModel, extra="forbid"):
        kind: Literal["market"] = "market"
        rounds: int = 5


    class DispatchParams(ScenarioParams):
        algorithm: Greedy | Market = Field(default_factory=Greedy, discriminator="kind")


    preset = {"algorithm": {"threshold_kw": 0.5}}
    print(load_params(DispatchParams, preset).algorithm)
    print(load_params(DispatchParams, preset, overrides=["algorithm.kind=market"]).algorithm)

.. testoutput:: scenario

    kind='greedy' threshold_kw=0.5
    kind='market' rounds=5

A study compares such variants with the lever ``algorithm.kind`` and gives
each variant its own settings in subexperiments (see
:ref:`scenario-subexperiments`).

Distributions
-------------

A distribution-valued parameter says what its numbers mean and is drawn per
entity from the entity's stream, e.g. ``e.p.battery.soc_kwh.draw(e.rng)``.
``draw(rng, size=None)`` works with ``numpy.random.Generator`` and
``random.Random``; ``expected_value`` gives the expectation.

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Class
     - Meaning and YAML shorthands
   * - ``Constant(value)``
     - Always *value*; a plain number where a ``Dist`` is expected.
   * - ``Uniform(low, high)``
     - Uniform on ``[low, high]``; ``{uniform: [3, 8]}``.
   * - ``IntUniform(low, high)``
     - Integers ``low..high`` (inclusive); ``{int_uniform: [1, 6]}``.
   * - ``Normal(mean, std, low=None, high=None)``
     - Normal, clipped to the optional bounds; ``{normal: [16, 1.5]}``.
   * - ``LogNormal(mean, sigma)``
     - Log-normal with the parameters of the underlying normal;
       ``{lognormal: [0, 0.5]}``.
   * - ``Choice(values, p=None)``
     - One of *values*, optionally weighted; ``{choice: [a, b]}``.
   * - ``Bernoulli(p)``
     - ``True`` with probability *p*; a plain number means *p*.

The long form ``{kind: uniform, low: 3, high: 8}`` works everywhere.  A field
typed with one class (``soc_kwh: Uniform``) also accepts a list of its
arguments (``soc_kwh: [1, 4]``); a field typed :data:`~mango.scenario.Dist`
accepts any distribution or a plain number.  As the default of such a field
write ``Constant(1.0)``, not ``1.0``: pydantic does not validate defaults,
so a bare number stays a float and every dump of the parameters warns.

A distribution field accepts every distribution of its type, also ones
whose values make no sense for the parameter (a normal lead time can be
negative).  To restrict it, validate the distribution with plain pydantic;
this check rejects distributions that can draw values below 1:

.. code-block:: python

    from typing import Annotated

    from pydantic import AfterValidator

    from mango.scenario import Dist, Normal, Uniform


    def at_least_one(dist):
        low = getattr(dist, "low", None)
        if isinstance(dist, Normal) and low is None:
            raise ValueError("a normal lead time needs low=1 (Normal(2, 1, low=1))")
        if low is not None and low < 1:
            raise ValueError(f"the lead time must be at least 1, got low={low}")
        return dist


    class Lead(BaseModel, extra="forbid"):
        order_weeks: Annotated[Dist, AfterValidator(at_least_one)] = Uniform(1, 3)

Presets, overrides and precedence
---------------------------------

Presets are YAML (or JSON) files.  A preset can extend another one with
``extends``:

.. code-block:: yaml
   :caption: presets/base.yaml

    time: {horizon: 6h, step: 30min}
    battery:
      n: 5
      soc_kwh: {uniform: [1.0, 4.0]}

.. code-block:: yaml
   :caption: presets/large.yaml

    extends: presets/base.yaml
    battery: {n: 50, capacity_kwh: 13.5}

:func:`~mango.scenario.load_params` merges, from lowest to highest
precedence: the model defaults, the scenario defaults (from
``scenario.time(...)`` and ``scenario.defaults({...})``, one layer per call),
the sources in order, then the overrides.  Nested mappings merge, scalars and
lists replace.  A layer that sets part of a value changes the value below it,
down to the model default: ``battery.soc_kwh.high=6`` keeps the default
``low`` of the distribution, and ``homes.1.kw=5`` changes the second item of
a list (an index past the end is an error).  Overrides are dotted paths; their
values are parsed as YAML with the YAML 1.2 rules for scalars, so ``010`` is
10, ``1:30`` and ``on`` stay text and ``true``/``false`` are booleans; quote a
value that must stay text (``label='2024'``):

.. testcode:: scenario

    params = load_params(scenario, "presets/large.yaml", overrides=["battery.charge_kw=2.0"])
    print(params.battery)
    print(params.time)

.. testoutput:: scenario

    n=50 capacity_kwh=13.5 soc_kwh=Uniform(1.0, 4.0) charge_kw=2.0
    start=None horizon=21600.0 step=1800.0

Every method that runs or plans a scenario takes the same ``params``
argument: ``None``, a params instance, a dict, a preset path or a list of
these (merged in order), plus ``overrides`` as a list of ``"path=value"``
strings or a dict ``{"path": value}``.  Two markers edit lists and keys:
``"+name"`` appends to a list (to the model's default list if no layer set
it), ``"-name"`` removes a key so that the default applies again:

.. testcode:: scenario

    small = scenario.run(
        ["presets/base.yaml", {"battery": {"n": 2}}],
        seed=1,
        overrides={"battery.charge_kw": 0.5, "time.-step": None},
    )
    print(small.wide("soc_kwh").shape, small.step_s)

.. testoutput:: scenario

    (7, 2) 3600.0

Here ``-step`` removes the step of 30 minutes the preset set, so the model
default of one hour applies: 6 hours give 7 samples of 2 batteries.  In the
list or command-line form a removal needs no value: ``overrides=["-events",
"time.-step"]``, ``--set -events`` (``-events=`` works too).

Validation errors
-----------------

Parameters are validated before anything is built.  Every problem is
reported at once, with its dotted path, the file or layer it came from and,
for unknown keys, a suggestion:

.. testcode:: scenario

    from mango.scenario import ParamsError

    try:
        load_params(scenario, "presets/typo.yaml")
    except ParamsError as error:
        print(error)

.. testoutput:: scenario

    Invalid parameters for Params (2 errors):
      battery.charge_kw: Input should be a valid number, unable to parse string as a number (got 'fast') [from presets/typo.yaml]
      battery.capactiy_kwh: unknown parameter; BatteryParams has no field 'capactiy_kwh'; did you mean 'battery.capacity_kwh'? [from presets/typo.yaml]

:class:`~mango.scenario.ParamsError` is a ``ValueError`` with the attributes
``path``, ``source`` and ``errors`` (one dict per problem).  Overrides are
checked even before the files are read.

Inspecting parameters
---------------------

:func:`~mango.scenario.dump_params` returns the resolved parameters as plain
data and :func:`~mango.scenario.params_to_yaml` as a preset that reproduces
them; :func:`~mango.scenario.params_schema` returns the JSON Schema, which
editors use for autocompletion in preset files:

.. testcode:: scenario

    from mango.scenario import params_to_yaml

    print(params_to_yaml(load_params(scenario, "presets/base.yaml")), end="")

.. testoutput:: scenario

    seed: null
    time:
      start: null
      horizon: 6h
      step: 30min
    comm:
      kind: simple
      loss_percent: 0.0
      delay_s: 0.0
      factory: null
      options: {}
    profiles: {}
    phases: []
    events: []
    battery:
      n: 5
      capacity_kwh: 10.0
      soc_kwh:
        kind: uniform
        low: 1.0
        high: 4.0
      charge_kw: 1.5

----

.. _scenario-seeds:

Seeds and random streams
========================

A run has one seed.  It comes from the ``seed`` argument of
``run``/``build`` (an int, a :class:`~mango.util.seeding.SeedTree` or a seed
dict such as ``result.seed``), else from the parameter ``seed``; without
both, fresh entropy is drawn and recorded in the result, so every run can be
replayed.  The seed is the root of a :class:`~mango.util.seeding.SeedTree`
that the world shares, and every random draw comes from a named stream of
that tree:

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Stream
     - Used by
   * - ``agent/<entity name>``
     - ``e.rng`` in the factory and ``self.rng`` in the agent: the *same*
       generator, so construction draws and runtime draws continue one
       stream.  ``e.rng_for(name)`` and ``self.rng_for(name)`` give named
       sub-streams.
   * - ``comm``
     - Message loss and delays of the communication simulation.
   * - ``link/<link name>``
     - Random link patterns.
   * - ``space`` and other names
     - Shared components of the world (e.g. random initial positions in an
       ``Area2D``) and ``ctx.rng(name)`` in factories and hooks.

Because entity streams are keyed by name, not by registration order, adding
entities never changes what existing entities draw:

.. testcode:: scenario

    three = scenario.run(seed=7)
    five = scenario.run(seed=7, overrides=["battery.n=5"])
    print(three.wide("soc_kwh").iloc[0].round(2).tolist())
    print(five.wide("soc_kwh").iloc[0].round(2).tolist())

.. testoutput:: scenario

    [5.17, 5.4, 4.12]
    [5.17, 5.4, 4.12, 5.97, 2.45]

Within one entity, the draws follow each other on its stream, so make the
same draws whatever the parameters are.  ``p.pv_kw.draw(e.rng) if
e.rng.random() < p.pv_share else 0.0`` skips a draw for households without
PV, and a changed ``pv_share`` then shifts every later draw of the
households that gain or lose PV; ``(e.rng.random() < p.pv_share) *
p.pv_kw.draw(e.rng)`` always draws, so ``pv_share`` changes only who has PV.
Inputs that must stay the same across the variants of a study (the initial
state, the demand of each household: what :ref:`common random numbers
<scenario-crn>` compare on) are safest on a named sub-stream of their own,
``p.soc_kwh.draw(e.rng_for("initial"))``: draws a variant adds or skips on
``e.rng`` or another sub-stream then cannot shift them.

``result.seed`` and ``result.config`` are plain data; together they replay a
run exactly, also from a saved run folder:

.. testcode:: scenario

    print(three.seed)
    replay = scenario.run(three.config, seed=three.seed)
    print(replay.checksum() == three.checksum())

.. testoutput:: scenario

    {'entropy': 7, 'spawn_key': []}
    True

.. tip::

   Agents should draw from ``self.rng`` (or ``self.rng_for(...)``), never
   from the global ``random`` or ``numpy.random`` functions: the global
   generators are shared by everything in the process, so their draws depend
   on what else runs.

----

.. _scenario-links:

Links and patterns
==================

``scenario.link(name, source, target=None, *, pattern="all_to_all",
where=None, directed=False)`` connects two groups, or the entities of one
group when *target* is omitted.  Every link name becomes one mango topology
(see :doc:`topology`) whose id is the name, so agents ask
``self.neighbors(tid=name)``.  Several declarations with the same name merge
into one topology.  A pattern returns ``(source, target)`` pairs; links are
undirected by default (both ends see each other), with ``directed=True`` only
the source of each pair sees its target.  ``where(a, b)`` filters the pairs
(both are :class:`~mango.scenario.Entity` objects with ``name``, ``index``,
``attrs`` and ``agent``).

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Pattern
     - Pairs (``a -> b``: with ``directed=True`` only ``a`` sees ``b``)
   * - ``"one_to_one"`` (``"zip"``)
     - the *i*-th source -> the *i*-th target (equal sizes)
   * - ``"many_to_one"`` / ``"one_to_many"``
     - every source -> the single target / the single source -> every
       target
   * - ``"all_to_all"``
     - every source -> every target (the default)
   * - ``"complete"``
     - within one group: all pairs, both orders
   * - ``"ring"``
     - within one group: ``e_i -> e_(i+1)`` in index order and ``e_(n-1) ->
       e_0``
   * - ``"line"``
     - within one group: a chain ``e_i -> e_(i+1)`` in index order (the last
       entity sees nobody when directed)
   * - ``patterns.reverse(pattern)``
     - every pair of *pattern* turned around: ``reverse("line")`` is the
       upstream chain ``e_(i+1) -> e_i``
   * - ``patterns.random(p=None, *, per_target=None, per_source=None,
       connected=False)``
     - random edges: each pair with probability *p* (alias
       ``erdos_renyi(p)``; within one group lower -> higher index), or *k*
       random sources per target (``per_target=(1, 2)`` draws *k* from
       1..2); ``connected=True`` redraws until the graph is connected
   * - ``patterns.by_attr(name)``
     - source and target share the install attribute (or agent attribute)
       *name*
   * - ``patterns.small_world(k, p=0.1)`` (``watts_strogatz``)
     - within one group: a Watts–Strogatz graph, *k* ring neighbours (even),
       rewired with probability *p*
   * - ``patterns.scale_free(m)`` (``barabasi_albert``)
     - within one group: a Barabási–Albert graph, each entity attaching to
       *m* earlier ones
   * - ``patterns.grid(rows=None, cols=None, *, periodic=False,
       diagonal=False)`` (``lattice``)
     - within one group: a 2-d lattice in row-major order (``periodic``: a
       torus, ``diagonal``: Moore neighbourhood)
   * - ``patterns.networkx(generator, **kwargs)``
     - within one group: any networkx generator (``"random_regular_graph",
       d=3``), node *i* is entity *i*
   * - ``patterns.edges(file=None, *, pairs=None, source="source",
       target="target")``
     - explicit edges: an edge-list CSV or a list of pairs, by entity name or
       index (empirical networks)
   * - ``fn(src, dst, rng)``
     - a custom pattern (see below)

The graph generators draw from the link's own stream
``seeds.rng("link/<name>")`` like ``random``, so a topology is reproducible
per seed and independent of every other draw; their undirected edges point
from the lower to the higher index.

**Custom patterns.** ``fn(src, dst, rng)`` receives the entities of the
source and the target group as lists of :class:`~mango.scenario.Entity` in
index order, and ``rng``, the link's ``numpy.random.Generator``.  For a link
within one group (no target) ``dst`` *is* ``src`` (the same list).  It returns
``(source_entity, target_entity)`` pairs; self-loops are dropped and
``where`` is applied afterwards.  A function that returns such a pattern is
a *pattern factory*; scenario files call one with arguments as
``{"module:factory": {...}}``:

.. code-block:: python

    def upstream(src, dst, rng):           # what patterns.reverse("line") does
        return [(b, a) for a, b in patterns.line(src, dst, rng)]

    def nearby(radius):                    # a pattern factory
        def pattern(src, dst, rng):
            return [(a, b) for a in src for b in dst
                    if abs(a.attrs["x"] - b.attrs["x"]) <= radius]
        return pattern

.. testcode:: scenario

    from mango.scenario import patterns

    feeder = Scenario("feeder", params=Params)
    lines = feeder.group(
        "line", 2, lambda e: Aggregator(), install=lambda e: {"bus": e.index}
    )
    homes = feeder.group(
        "home", "battery.n", make_battery, install=lambda e: {"bus": e.index % 2}
    )
    feeder.link("feeder", homes, lines, pattern=patterns.by_attr("bus"))
    feeder.link("street", homes, pattern="line")
    feeder.link("gossip", homes, pattern=patterns.random(per_target=(1, 2)), directed=True)
    feeder.link("near", homes, pattern="complete", where=lambda a, b: abs(a.index - b.index) <= 1)
    print(feeder.plan(overrides=["battery.n=6"]))
    print(feeder.run(seed=3, overrides=["battery.n=6"]).stats["link_edges"])

.. testoutput:: scenario

    Scenario 'feeder': 8 agents in 2 groups, 4 links, 0 recorders
      time: horizon 1d, step 1h (24 steps)
      groups:
        line                  2  line_0 .. line_1
        home                  6  home_0 .. home_5
      links:
        feeder           home -> line (by_attr('bus')); ? edges, undirected
        street           home -> home (line); 5 edges, undirected
        gossip           home -> home (random(per_target=(1, 2))); ? edges, directed
        near             home -> home (complete, filtered by where=); 5 edges, undirected
    {'feeder': 6, 'street': 5, 'gossip': 9, 'near': 5}

``plan()`` counts the edges of a link when they follow from the group sizes
alone: it evaluates the patterns and ``where`` filters on placeholder
entities and prints the exact count every run gets.  It prints ``? edges``
when a pattern or filter draws from ``rng`` or reads install attributes
(``random``, ``by_attr``, ``nearby`` above): those counts differ per run or
need the built entities, and ``run.stats["link_edges"]`` reports them.  A
custom pattern must therefore draw from the ``rng`` it receives, never from
a global generator.  ``plan()`` names a custom pattern by its function
name, a closure by its factory (``nearby.pattern``); set
``fn.pattern_name = "nearby(2.0)"`` for another display name.

.. _scenario-structure-factor:

Structure as a study factor
---------------------------

Like a group's ``count``, a pattern may depend on the run's parameters, so a
topology, its density or its rewiring probability can be a study factor
without a kit.  The spec is resolved per run (inside a kit against the
kit's parameters ``ctx.p``):

* a parameter path whose value is a pattern spec: ``pattern="net.topology"``
  (``{param: net.topology}`` in files), where the parameter holds ``"ring"``
  or ``{"small_world": {"k": 4, "p": 0.1}}``;
* ``"$path"`` values inside factory arguments: ``{"small_world": {"k":
  "$net.k", "p": "$net.rewire"}}`` (``"$$"`` writes a literal ``$``);
* a one-argument function ``fn(p)`` returning a pattern spec.

Patterns can therefore always be given as data (``"ring"``, ``{"random":
{"per_target": [1, 2]}}``, ``{"by_attr": "bus"}``, ``{"reverse": "line"}``),
in the scenario and in parameter files alike.  The same holds for other
structural numbers: ``record(..., every=...)``, ``record_world`` and
``stop_when`` take a parameter path (``every="market.period_s"``) or
``fn(p)``; link ``delay`` and ``loss`` take ``"$path"`` (or the bare path),
``$path`` values inside a distribution (``{"uniform": ["$lead.min_s",
"$lead.max_s"]}``) or ``fn(p)`` (``link("platoon", lanes, pattern="line",
delay="$corridor.travel_s")``); event ``at``, ``until`` and ``every`` take
the same (``scenario.event(0, every="market.period_s", ...)``, ``at:
$market.open`` in files); and ``scenario.time(horizon=..., step=...)`` derives the time grid
from the parameters (``horizon=lambda p: p.market.periods *
p.market.ticks``); a preset or override that sets a derived ``time.horizon``
to a different value is an error instead of being ignored.

.. testcode:: scenario

    class NetParams(BaseModel, extra="forbid"):
        n: int = 8
        topology: str | dict = "ring"
        k: int = 2
        rewire: float = 0.1


    class OverlayParams(ScenarioParams):
        net: NetParams = NetParams()


    overlay = Scenario("overlay", params=OverlayParams)
    overlay.time(horizon="1h", step="1h")
    nodes = overlay.group("node", "net.n", lambda e: Aggregator())
    overlay.link("overlay", nodes, pattern="net.topology")
    overlay.link("friends", nodes, pattern={"small_world": {"k": "$net.k", "p": "$net.rewire"}})
    overlay.link("upstream", nodes, pattern=patterns.reverse("line"), directed=True)
    for topology in ["ring", "line", "{grid: {cols: 4}}", "{scale_free: {m: 2}}"]:
        stats = overlay.run(seed=1, overrides=[f"net.topology={topology}", "net.k=4"]).stats
        print(f"{topology:22} {stats['link_edges']}")

.. testoutput:: scenario

    ring                   {'overlay': 8, 'friends': 16, 'upstream': 7}
    line                   {'overlay': 7, 'friends': 16, 'upstream': 7}
    {grid: {cols: 4}}      {'overlay': 10, 'friends': 16, 'upstream': 7}
    {scale_free: {m: 2}}   {'overlay': 12, 'friends': 16, 'upstream': 7}

A study then varies the topology like any other factor, e.g. ``levers:
{net.topology: [ring, {small_world: {k: 4, p: 0.1}}]}``.  For a topology
that changes *during* a run see :meth:`ctx.relink
<mango.scenario.BuildContext.relink>`.

----

.. _scenario-results:

Recording and results
=====================

Recorders
---------

``scenario.record(name, group, fn=None, *, attr=None, every=None,
aggregate=None, window=None)`` samples
one value per agent of *group*: ``fn(agent)``, or a (dotted) attribute
``attr="battery.soc_kwh"`` (positions in a space: see
:ref:`scenario-space`).  The same name on different groups forms one
variable; a list of groups declares it at once
(``scenario.record("order_qty", [retailer, wholesaler, factory],
attr="order_qty")``, in files ``group: [retailer, wholesaler, factory]``).
``scenario.record_world(name, fn, *, every=None, entity="world",
window=None)`` samples ``fn()`` or ``fn(ctx)`` once per step; a world variable is declared
once per entity.  Recorders sample at time 0
and after every step; ``every="1h"`` samples only at multiples of one hour
(``every`` may also be a parameter path such as ``"market.period_s"`` or
``fn(p)``, see `Structure as a study factor`_).
A row at ``t > 0`` is the state *after* the step that ended at ``t``, the
row at ``t = 0`` the initial state (see :ref:`scenario-rows`).
With a fixed step, ``every`` must be a multiple of the step (samples are taken
at steps; ``every="90min"`` with an hourly step is an error that suggests
``"1h"`` or ``"2h"``); an event-driven run also stops at every multiple of
``every``, so the samples are there even when no event happens.

.. _scenario-rows:

What a recorded row means
-------------------------

Recorders sample at the end of a step (see :ref:`scenario-timing`), so a
row stands for the step *before* it:

.. code-block:: text

                 step [0, dt)        step [dt, 2dt)      step [2dt, 3dt)
    on_step at:  clock = 0           clock = dt          clock = 2dt
    rows:        t = 0               t = dt              t = 2dt             t = 3dt
                 initial state       after [0, dt)       after [dt, 2dt)     after [2dt, 3dt)

* **A row at t > 0 is the state after the step that ended at t**, i.e. it
  describes the interval from the previous step to ``t``.  A value an agent
  computes in ``on_step`` with ``clock.time == t - dt`` (a power, a demand,
  an order) appears in the row at ``t``.
* **The row at t = 0 is the initial state**, before any step: flows are
  usually 0 there, so leave it out of statistics over flows
  (``ts[ts.index > 0]``).
* **Phases are attributed by interval**: a row's ``phase`` is the phase
  active during its step, and ``record: false`` drops the rows of such
  steps.  After a warm-up until ``2h`` with hourly steps, the row at
  ``2h`` belongs to the warm-up (it is the state after ``[1h, 2h)``) and is
  dropped; the first row of the next phase is at ``3h``.  A day evaluated
  after a warm-up at 15 min steps has 96 rows per entity, one per interval.
* **World recorders see the end of the step.**  ``ctx.world.clock`` in a
  ``record_world`` function reads ``t``, while the agents computed their
  state at ``t - dt``: a world recorder that reads a profile at the clock is
  one step ahead of agents that read it in ``on_step``.  Record what the
  agents saw (an attribute set in ``on_step``), or read the profile at the
  step start, ``ctx.profile("pv").at(max(0.0, ctx.world.clock.time -
  ctx.time.step_s))`` (``mango-scenario new --template full`` does this).
* **Event logs use the clock at the call**: ``ctx.log(...)`` in ``on_step``
  stamps ``t - dt``, the start of the step whose recorder row is ``t``.

.. _scenario-state-flow:

**Flows and states.**  Which interval a row stands for depends on what the
value is, and a recorder says it with ``kind=``:

- ``kind="flow"``: the value describes the interval that just ended. It is
  computed in ``on_step`` for the step (a power, a demand, a cost per
  week), so the row at ``t`` covers the time since the entity's previous
  row (or since the start of its phase), and the ``t = 0`` row counts for
  nothing.  Everything above describes flows.
- ``kind="state"``: a level that holds until it changes. It is changed in
  message handlers, tasks or events (a queue length, an inventory, an
  opinion), so the row at ``t`` holds from ``t`` until the entity's next
  row.  A state recorder writes a row at the start of every recorded phase
  (the level at the end of a warm-up), its rows belong to the phase that
  starts at their time, and the last row holds until a ``record: false``
  phase, a stop or the horizon.

For quantities per interval (energy from power, cost from a price, vehicle
minutes from a queue length), :meth:`RunResult.integrate
<mango.scenario.RunResult.integrate>` sums ``value x interval length`` over
these intervals, :meth:`RunResult.time_average
<mango.scenario.RunResult.time_average>` divides that by the recorded time
(:attr:`RunResult.recorded_s <mango.scenario.RunResult.recorded_s>`: a
warm-up, a stop or ``until`` are left out, so do not divide by
``horizon_s``), and :meth:`RunResult.bins <mango.scenario.RunResult.bins>`
splits it into calendar bins.  They work with warm-ups, ``every=`` and
event-driven runs, where ``ts(...).sum() * step_h`` does not.  They take the
recorder's ``kind`` (stored with the run) or ``kind=``; when neither says
it, they raise an error rather than guess: integrating a state as a flow
charges every level to the interval *before* it was set, which in an
event-driven queue overstates the mean queue length by 5-20 % however many
replications you run.  Outcome code written before recorders declared their
kind (``run.integrate("cost")`` on an undeclared recorder) therefore fails
with that error: add ``kind="flow"`` to recorders set in ``on_step`` (the
earlier behaviour, same numbers) and ``kind="state"`` to levels.

``unit`` sets the time unit of the result (``"1h"``, the default, gives kWh
from kW), ``per_entity=True`` returns one value per entity, and ``group``,
``entity`` and ``phase`` select rows:

.. testcode:: rows

    from mango import Agent
    from mango.scenario import Scenario


    class Heater(Agent):
        def __init__(self):
            super().__init__()
            self.power_kw = 0.0

        def on_step(self, env, clock, step_size_s):
            self.power_kw = 1.0 + clock.time / 3600  # the power during this step


    heating = Scenario("heating")
    heating.time(horizon="6h", step="1h")
    heater = heating.group("heater", 2, lambda e: Heater())
    heating.record("power_kw", heater, attr="power_kw", kind="flow")
    warmup = {"phases": [{"name": "warmup", "until": "2h", "record": False}]}
    result = heating.run(warmup, seed=1)
    print(result.wide("power_kw", index="h"))
    print(result.integrate("power_kw"), result.integrate("power_kw", per_entity=True).tolist())

.. testoutput:: rows
   :options: +NORMALIZE_WHITESPACE

    entity  heater_0  heater_1
    time_h
    3.0          3.0       3.0
    4.0          4.0       4.0
    5.0          5.0       5.0
    6.0          6.0       6.0
    36.0 [18.0, 18.0]

The heaters draw 1 kW during ``[0, 1h)``, 2 kW during ``[1h, 2h)`` and so
on; the evaluated hours ``[2h, 6h)`` are the four rows from ``3h`` to
``6h``, and each heater uses ``3 + 4 + 5 + 6 = 18`` kWh in them.

A queue whose length changes when customers arrive and leave (tasks and
messages, here an event-driven run) is a state:

.. testcode:: rows

    class Counter(Agent):
        """1 customer from t=1 min to t=10 min (a level changed by tasks)."""

        def __init__(self):
            super().__init__()
            self.waiting = 0

        def on_ready(self):
            self.schedule_timestamp_task(self.set_to(1), 60.0)
            self.schedule_timestamp_task(self.set_to(0), 600.0)

        async def set_to(self, value):
            self.waiting = value


    shop = Scenario("shop")
    shop.time(horizon="20min", step="event")
    counter = shop.group("counter", 1, lambda e: Counter())
    shop.record("waiting", counter, attr="waiting", kind="state")
    result = shop.run(seed=1)
    print(result.ts("waiting").to_dict())
    print(result.integrate("waiting", unit="1min"), result.time_average("waiting"))
    print(result.integrate("waiting", kind="flow", unit="1min"))  # wrong for a state

.. testoutput:: rows

    {0.0: 0.0, 60.0: 1.0, 600.0: 0.0, 1200.0: 0.0}
    9.0 0.45
    1.0

The rows are the changes; the customer waits 9 minutes, 45 % of the time.
Read as a flow, the 1 set at 1 min would be charged to ``[0, 1 min)`` and
the 0 set at 10 min to ``[1 min, 10 min)``: 1 minute instead of 9.  ``window="mean"`` of a state is the
time-weighted mean of the levels held during the window (``"sum"`` is
refused for a state).

**Calendar bins.**  A flow row is labelled by the *end* of its interval, so
``wide(v, index="datetime").resample("1D")`` puts the last interval of each
day (23:45-24:00 at 15 min steps) into the next day and counts the
``t = 0`` row.  :meth:`RunResult.bins <mango.scenario.RunResult.bins>`
splits every row's interval at the bin edges instead: ``bins("load_kw",
"1D", how="integrate")`` is the energy per calendar day (kWh), ``how="mean"``
the time-weighted mean per bin and ``how="last"`` the value at the end of
each bin; with ``time.start`` the bins are pandas frequencies in the run's
time zone (``"1h"``, ``"1D"``, ``"W"``, ``"MS"``), without it durations.

.. _scenario-aggregates:

Aggregates and windows
----------------------

Two options reduce what a recorder writes, declaratively (they work in
scenario files and need no closures):

- ``aggregate=`` reduces across the agents of the group at each sample:
  ``"sum"``, ``"mean"``, ``"min"``, ``"max"`` (missing values skipped) or a
  function ``fn(values)`` give one row per group (``group`` and ``entity``
  are the group name); ``"count"`` gives one row per category with the
  category as ``entity``, so ``result.wide(name)`` is a time x category
  table (a category counts 0 at the samples before it first appears).  A
  category that never occurs in a run has no rows unless it is declared:
  ``categories=["S", "E", "I", "R", "V"]`` writes these columns in this
  order in every run, 0 while absent (other values are appended after
  them), so curves average over runs without a ``reindex``.  Compartment
  counts of an epidemic are one line instead of one world recorder per
  state.
- ``window=`` (with ``every``) evaluates the recorder at *every* step and
  writes, at each multiple of ``every``, the ``"max"``, ``"min"``,
  ``"mean"``, ``"sum"`` or ``"last"`` of the samples in the window
  ``(t - every, t]``: the samples of the steps that ended after ``t -
  every`` and up to ``t``.  ``"mean"`` weights each sample by its interval
  (relevant in event mode).  The row at ``t = 0`` is the initial value;
  samples of ``record: false`` phases are left out; a run that stops
  between two multiples drops the incomplete window.  Without ``window``,
  ``every`` takes snapshots: a queue that peaks mid-minute and is empty at
  the minute marks records as 0.

Both combine (``aggregate`` first, then ``window``), and ``record_world``
takes ``window`` too.  All groups of one variable use the same
``aggregate``; when a category label occurs in several groups (a count over
a list of groups), ``result.wide(name)`` raises a ``KeyError`` and wants
``group=``:

.. testsetup:: recording

    import os
    import tempfile

    from mango import Agent
    from mango.scenario import Scenario

    _recording_cwd = os.getcwd()
    _recording_tmp = tempfile.TemporaryDirectory()
    os.chdir(_recording_tmp.name)


    class Person(Agent):
        """SEIR-like state cycle, a queue that peaks mid-minute, and logged infections."""

        def __init__(self, index, ctx):
            super().__init__()
            self.index, self.ctx = index, ctx
            self.k, self.state, self.queue = 0, "S", 0.0

        def on_step(self, env, clock, step_size_s):
            self.k += 1
            self.state = "SEIR"[(self.k + self.index) % 4] if self.k > 2 else "S"
            self.queue = float((self.k % 12) * (self.k % 12 < 6))
            if self.state == "I":
                self.ctx.log("infections", who=self.aid, k=self.k)

.. testcode:: recording

    scenario = Scenario("outbreak")
    scenario.time(horizon="3min", step="5s")
    person = scenario.group("person", 4, lambda e: Person(e.index, e.ctx))
    scenario.record("state", person, attr="state", aggregate="count")
    scenario.record("infected", person, lambda a: a.state == "I", aggregate="sum")
    scenario.record("queue_snapshot", person, attr="queue", every="1min")
    scenario.record("queue_max", person, attr="queue", every="1min", window="max")
    result = scenario.run(seed=1)
    print(result.wide("state").iloc[:4])
    print(result.ts("queue_snapshot", entity="person_0").tolist())
    print(result.ts("queue_max", entity="person_0").tolist())

.. testoutput:: recording
   :options: +NORMALIZE_WHITESPACE

    entity    S    R    E    I
    time
    0.0     4.0  0.0  0.0  0.0
    5.0     4.0  0.0  0.0  0.0
    10.0    4.0  0.0  0.0  0.0
    15.0    1.0  1.0  1.0  1.0
    [0.0, 0.0, 0.0, 0.0]
    [0.0, 5.0, 5.0, 5.0]

In scenario files the keys are the same: ``{name: state, group: person,
attr: state, aggregate: count}``, ``{name: queue_max, group: person, attr:
queue, every: 1min, window: max}`` (a function aggregate is a
``'module:fn'`` reference).  ``to-script`` writes reductions out: the
generated script installs them with a small ``add_reduced_recorder`` helper
that calls the same :class:`~mango.scenario.reducers.Window` and
:func:`~mango.scenario.reducers.group_reduce` as the run, so it records the
same values (both in the lean and the full form).  Any other reduction remains a pandas one-liner on per-step rows, e.g. category
counts of a text recorder ``frame[frame.variable == "state"].groupby(["time",
"value"], observed=True).size().unstack(fill_value=0)``.

The run result
--------------

A :class:`~mango.scenario.RunResult` holds the parameters (``params``,
``config``), the ``seed``, the ``time`` settings, run statistics (``stats``)
and the recorded data as a tidy table ``frame`` with one row per sample:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Column
     - Content
   * - ``run_id``
     - content hash of scenario, parameters and seed
   * - ``time``
     - seconds since the scenario start
   * - ``datetime``
     - calendar time (only with ``time.start``)
   * - ``phase``
     - the phase the sample belongs to (``"main"`` without declared phases,
       see :ref:`scenario-events`)
   * - ``group`` / ``entity``
     - group and entity name (``"world"`` for world recorders)
   * - ``variable`` / ``value``
     - recorder name and value (a float; non-numeric values are kept as
       text)

``run_id``, ``phase``, ``group``, ``entity`` and ``variable`` are pandas
categoricals (a few bytes per row instead of a string each); comparisons,
``isin``, ``pivot`` and ``merge`` work as with strings.  With pandas 2 pass
``observed=True`` to ``groupby`` on them (the default of pandas 3), or
convert with ``.astype(str)``.

``ts(variable, *, group=None, entity=None)`` returns one variable in long
form (index: time; ``.sum()`` sums over entities and time) and
``wide(variable)`` a time × entity table.  Both take ``index=``, the time
axis: ``"time"`` (seconds since the start, the default), ``"datetime"``
(calendar time of the row, needs ``time.start``; a flow row is labelled by
the *end* of its interval, so bin days or hours with :meth:`RunResult.bins
<mango.scenario.RunResult.bins>`, not ``resample``, see
:ref:`flows and states <scenario-state-flow>`), ``"step"`` (the step number as an
int) or a unit, ``"min"``, ``"h"``, ``"d"`` or ``"w"`` (time since the
start in that unit).  Models that count abstract rounds or ticks use
``step=1`` (one second per round; ``plan()`` prints it as ``1s``) and
``index="step"``.  ``variables()``, ``groups()``, ``agent(name)``
(in-process runs) and ``checksum()`` (identical for identical data,
whatever the file format) complete it.  ``warnings`` lists what the run
warned about as text (a run with recorders that recorded nothing, a survey
table that wrapped around, an event no agent handles, ``ctx.warn(...)``):
each is issued once as a :class:`~mango.scenario.RunWarning` and kept in
the run's manifest, so a study shows them (``status``, ``N runs with
warnings``) even when the run executed in a worker process:

.. testcode:: scenario

    print(result.variables(), result.groups())
    print(result.ts("soc_kwh", entity="battery_1").round(2).tolist())
    print(result.ts("soc_kwh").max(), round(result.agent("battery_1").soc_kwh, 2))
    print(result.wide("soc_kwh", index="h").index.tolist())

.. testoutput:: scenario

    ['reported_kwh', 'soc_kwh'] ['aggregator', 'battery']
    [3.06, 4.56, 6.06, 7.56, 9.06]
    10.0 9.06
    [0.0, 1.0, 2.0, 3.0, 4.0]

``save(path)`` writes a run folder and :func:`~mango.scenario.load_run`
reads it back; pass the scenario to get typed parameters again:

.. testcode:: scenario

    from mango.scenario import load_run

    folder = result.save("results/single")
    print(sorted(path.name for path in folder.iterdir()))
    loaded = load_run("results/single", scenario=scenario)
    print(loaded.checksum() == result.checksum(), loaded.params.battery.n)

.. testoutput:: scenario

    ['config.resolved.yaml', 'manifest.json', 'outcomes.json', 'timeseries...']
    True 3

The time series is ``timeseries.parquet``, or ``timeseries.csv`` when
pyarrow is not installed.

.. _scenario-logs:

Event logs and metrics
----------------------

Recorders sample *state*.  What *happens* (trades, infections, deliveries:
any number per step, several fields each) goes into an **event log**:
``ctx.log(name, **fields)`` appends one row, from an agent (keep the
``e.ctx`` of the factory), a behavior, an event action or a hook.  Each
row gets ``run_id``, ``time`` (the clock now: the step start in
``on_step``, the delivery time in a message handler), ``datetime`` (with a
calendar start) and ``phase`` (the phase active now; rows of ``record:
false`` phases are dropped), then its
fields in order of first appearance (empty where a row lacks one).  Field
values are numbers, bools, strings or ``None``; other values are stored as
JSON text.  ``result.log(name)`` returns the table:

.. testcode:: recording

    infections = result.log("infections")
    print(infections.columns.tolist(), len(infections))
    print(infections.iloc[0].to_dict())

.. testoutput:: recording

    ['run_id', 'time', 'phase', 'who', 'k'] 34
    {'run_id': '...', 'time': 10.0, 'phase': 'main', 'who': 'person_3', 'k': 3}

Values known once per run, e.g. the time a protocol converged, are
**metrics**: ``ctx.set_metric(name, value)`` sets one during the run (the
last call wins), and ``scenario.metric(name, fn)`` declares one computed
by ``fn(ctx)`` when the result is collected (after the last step, with the
agents still in place).  They are ``result.metrics``:

.. testcode:: recording

    @scenario.metric("final_infected")
    def final_infected(ctx):
        return sum(agent.state == "I" for agent in ctx.agents("person"))

    result = scenario.run(seed=1)
    print(result.metrics)
    folder = result.save("results/outbreak")
    print(sorted(path.name for path in folder.iterdir()))

.. testoutput:: recording

    {'final_infected': 1}
    ['config.resolved.yaml', 'logs', 'manifest.json', 'metrics.json', 'outcomes.json', 'timeseries...']

Inside a kit, log and metric names are qualified by the prefix
(``"market.trades"``), like groups.  Logs are saved as
``logs/<name>.parquet`` (or ``.csv``), metrics as ``metrics.json``; both
are part of ``checksum()`` and come back with
:func:`~mango.scenario.load_run`.  In a study, every metric becomes a
column of :func:`~mango.scenario.load_study` (after the outcomes; a
metric named like another column becomes ``"metric:<name>"``, with a
warning) and ``frame.log(name)`` concatenates a log over the runs, with the
run's ``replication``, ``point``, factor and override columns appended like
``timeseries()`` (``with_factors=False`` leaves them out).  Outcome functions
can read ``run.metrics``, so they stay recomputable from disk (outcomes
that read ``run.agent(...)`` cannot be recomputed, see
:ref:`scenario-studies`).  Scenario files declare metrics as ``metrics:
{final_infected: 'outbreak:final_infected'}``.

.. testcleanup:: recording

    os.chdir(_recording_cwd)
    _recording_tmp.cleanup()

.. _scenario-large-runs:

Large runs
----------

A run holds ``entities x variables x samples`` rows.  The finished table
costs about 22 bytes per row (two floats, five categorical labels).  While
a run records, numeric samples take about 18 bytes per row (a float array
per entity and variable; other values such as text or positions take a list
entry and the object); building the table peaks at roughly 45 bytes per
row, and a finished run then releases the raw samples.  So 500 households x
4 variables x 17,280 five-minute steps (35 million rows) need about 1.6 GB
at the peak and 0.8 GB for the result, per study worker.  With a calendar
(``time(start=...)``) the ``datetime`` column adds 8 bytes per row: about
30 bytes per row finished and 55 at the peak.  On disk Parquet compresses
to a few bytes per row.  Delivered
messages are counted (``stats["messages_delivered"]``) but not kept, so
message traffic does not add to the memory of a run (to keep them, e.g. for
:func:`~mango.show_communication_data`: ``scenario.run(...,
record_messages=True)`` or ``scenario.build(..., record_messages=True)``;
``result.world.recorded_messages`` then holds them).  To shrink a run:

- record only the variables an analysis needs, and the others at
  ``every="1h"`` (with ``window="mean"`` or ``"max"`` when the extremes
  matter);
- reduce across groups with ``aggregate=`` instead of one row per agent;
- keep events in an event log rather than as one sample per agent and
  step.

A study writes one Parquet file per run; ``frame.timeseries(variables=[...])``
reads only the rows of those variables, and ``frame.run(i)`` loads one run.

Run time follows the agents that act: the simulation's own cost of a step
grows with the agents that receive messages or run tasks in it, not with the
population, so thousands of idle agents with sleeping periodic tasks are
cheap.  What grows with the population is the model's own work per step
(``on_step`` of every agent, recorders that read every agent; record those at
``every=`` when a step is short).  Measurements and sizing advice are in
:ref:`simulation-performance`.

----

.. _scenario-studies:

Studies
=======

A :class:`~mango.scenario.Study` turns a scenario, a base configuration and
factors into an explicit table of runs, executes it and stores one folder per
run.  Outcomes are functions of a :class:`~mango.scenario.RunResult`:

.. testcode:: scenario

    from mango.scenario import Study


    def final_soc(run):
        return float(run.wide("soc_kwh").iloc[-1].mean())


    study = Study(
        scenario,
        uncertainties={
            "battery.soc_kwh": {
                "empty": {"uniform": [0.0, 2.0]},
                "half": {"uniform": [4.0, 6.0]},
            },
        },
        levers={"battery.charge_kw": [1.0, 2.0]},
        replications=2,
        seed=2026,
        outcomes=[final_soc],
        out="results/batteries",
    )
    plan = study.plan()
    print(len(plan))
    print(plan.to_frame().drop(columns=["run_id", "battery.soc_kwh"]))

.. testoutput:: scenario
   :options: +NORMALIZE_WHITESPACE

    8
       index  replication  point battery.soc_kwh:label  battery.charge_kw
    0      0            0      0                 empty                1.0
    1      1            1      0                 empty                1.0
    2      2            0      1                 empty                2.0
    3      3            1      1                 empty                2.0
    4      4            0      2                  half                1.0
    5      5            1      2                  half                1.0
    6      6            0      3                  half                2.0
    7      7            1      3                  half                2.0

``print(plan)`` summarises the study; ``plan[i]`` is the *run spec* of run
*i*: plain JSON data with the run id, the scenario reference, the fully
resolved parameters, the seed, the factor values and labels, the replication,
the outcome references and the run folder.  A run spec is all a worker needs
to execute a run.

Factors
-------

*Uncertainties* describe the world (fleet size, weather, communication
quality), *levers* what is compared (the algorithm, a threshold).  Both are
factors ``{dotted path: spec}``; the role is kept as metadata.  Paths are
checked against the parameter model when the study is planned, and every
design point is validated, so a typo fails before the first run.

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - Factor spec
     - Meaning
   * - ``[5, 10, 20]``
     - levels
   * - ``{"ideal": 0.0, "lossy": 0.1}``
     - labeled levels (the label becomes a ``"<path>:label"`` column)
   * - ``{"values": [...]}`` / ``{"levels": {...}}``
     - levels / labeled levels, explicitly (e.g. when a level is a dict)
   * - ``{"value": x}`` or a plain value
     - fixed for every run
   * - ``{"range": [lo, hi]}``, ``{"int_range": ...}``, ``{"log_range": ...}``
     - ranges for :ref:`sampling designs <scenario-designs>` (floats,
       integers, log-uniform floats)

A distribution as the one value of a factor needs ``value``:
``{"value": {"uniform": [2, 4]}}``.  Written bare, ``{"uniform": [2, 4]}``
would be one labeled level named ``uniform``, so it is an error.  Labels that
are the fields of a distribution (``{"low": 2, "high": 6}``) are still
labeled levels, each a fixed value.  On a parameter that accepts a
distribution they warn, because a distribution may have been meant; write
``{"levels": {...}}`` to keep them without the warning.  On any other
parameter (a plain ``float`` such as ``homes.hp_share``) they are quiet.

Designs
-------

The design generates the factor combinations.
:class:`~mango.scenario.FullFactorial` (the default) crosses all levels, the
last factor varying fastest.  :class:`~mango.scenario.Explicit` takes rows of
values and :class:`~mango.scenario.FromCsv` reads them from a CSV file whose
columns are factor paths.  Declared factors a row does not set are crossed
with the row (the sampling designs :class:`~mango.scenario.Lhs`,
:class:`~mango.scenario.Sobol` and :class:`~mango.scenario.RandomSample`
are described in :ref:`scenario-designs`):

.. testcode:: scenario

    from mango.scenario import Explicit

    explicit = Study(
        scenario,
        levers={"battery.charge_kw": [1.0, 2.0]},
        design=Explicit([{"battery.n": 2}, {"battery.n": 4, "battery.capacity_kwh": 13.5}]),
        seed=1,
    )
    print(explicit.plan().to_frame().drop(columns=["run_id", "index"]))

.. testoutput:: scenario
   :options: +NORMALIZE_WHITESPACE

       replication  point  battery.n  battery.charge_kw  battery.capacity_kwh
    0            0      0          2                1.0                   NaN
    1            0      1          2                2.0                   NaN
    2            0      2          4                1.0                  13.5
    3            0      3          4                2.0                  13.5

.. _scenario-subexperiments:

Variants with settings of their own
-----------------------------------

When the compared variants are a :ref:`tagged union <scenario-params>`
such as ``algorithm: Greedy | Market``, the lever is the tag:
``levers={"algorithm.kind": ["greedy", "market"]}``.  (``levers=
{"algorithm": [...]}`` works only for a plain string field; for a union it
fails when the study is planned, with a message that names the ``.kind``
path.)  Settings that only one variant has (the greedy threshold, the number
of market rounds) cannot be crossed with every variant.  They go into
*subexperiments*: a list of factor dicts, each merged over the study's own
factors, that form one block of the run table each.  Only the blocks run;
add ``{}`` to run the study factors alone as well:

.. testcode:: scenario

    dispatch = Scenario("dispatch", params=DispatchParams)
    dispatch.group("aggregator", 1, lambda e: Aggregator())
    tuned = Study(
        dispatch,
        subexperiments=[
            {"algorithm.kind": "greedy", "algorithm.threshold_kw": [0.2, 0.5]},
            {"algorithm.kind": "market", "algorithm.rounds": [3, 10]},
        ],
        seed=1,
    )
    columns = ["subexperiment", "algorithm.kind", "algorithm.threshold_kw", "algorithm.rounds"]
    print(tuned.plan().to_frame()[columns])

.. testoutput:: scenario
   :options: +NORMALIZE_WHITESPACE

       subexperiment algorithm.kind  algorithm.threshold_kw  algorithm.rounds
    0              0         greedy                     0.2               NaN
    1              0         greedy                     0.5               NaN
    2              1         market                     NaN               3.0
    3              1         market                     NaN              10.0

``print(plan)`` lists the factors of every subexperiment, and the
``subexperiment`` column of :func:`~mango.scenario.load_study` tells the
blocks apart.  In a study file the key is ``subexperiments:`` with the same
list.

``frame.paired("algorithm.kind", baseline="greedy")`` compares such
variants on common random numbers: a factor that only some levels set
(``algorithm.rounds`` of ``market``) becomes part of the compared level, so
the result has one row per tuned variant (``market`` with ``rounds`` 3 and
10, each against ``greedy``), paired on the other factors and the
replication.  When the baseline has several tuned points (the two greedy
thresholds above), name one: ``baseline={"algorithm.kind": "greedy",
"algorithm.threshold_kw": 0.2}``.

.. _scenario-crn:

Replications and common random numbers
--------------------------------------

Every design point runs ``replications`` times, and every run's seed derives
from the study ``seed``.  ``common_random_numbers`` decides which runs share
a seed, i.e. see the same random draws:

.. list-table::
   :header-rows: 1
   :widths: 22 48 30

   * - ``common_random_numbers``
     - Runs that share a seed
     - Seed of replication *r*
   * - ``True``
     - all runs of replication *r*, in every design point: every comparison
       (of levers *and* of uncertainty levels) is paired
     - ``SeedTree(seed).child("replication", r)``
   * - ``"levers"``
     - runs of replication *r* that differ only in lever values; every
       combination of the other factors (each sample of a sampling design)
       gets its own seed
     - ``child("sample", hash(non-lever values), r)``
   * - ``False``
     - none: every run has its own seed
     - ``child("variant", hash(all values), r)``

The default (``None``) is ``"levers"`` for :class:`~mango.scenario.Lhs` and
:class:`~mango.scenario.RandomSample`, whose samples stand for independent
draws of the uncertain inputs, and ``True`` for every other design (full
factorial, explicit rows, CSV designs and :class:`~mango.scenario.Sobol`,
whose estimators compare matched points); ``print(plan)`` shows the mode in
its ``seeds`` line and ``study.yaml`` records it.  Under ``"levers"``, paths
a design sets without declaring them as levers (columns of an
:class:`~mango.scenario.Explicit` or CSV design) count as uncertainties.

Runs that share a seed make a paired comparison: the noise both runs have in
common cancels in their difference, which then needs far fewer replications
to separate variants (:meth:`StudyFrame.paired
<mango.scenario.StudyFrame.paired>` computes it).  How well the noise
cancels depends on the model drawing the same numbers in both runs: see
:ref:`scenario-seeds` for named sub-streams (``e.rng_for``) of inputs that
must stay common, and note that the shared ``comm`` stream drifts apart once
the variants send different messages.

.. warning::

   Runs that share a seed are **not independent replicates**.  With
   ``common_random_numbers=True`` and an ``Lhs(16)`` design with three
   replications, all 16 samples of replication *r* use one seed: the study
   has three noise realisations, not 48, and a regression or a confidence
   interval that treats the samples as independent is pseudo-replicated.
   ``plan()`` warns in that case; use ``"levers"`` (the default for these
   designs).  :meth:`StudyFrame.summary <mango.scenario.StudyFrame.summary>`
   and ``paired`` count runs that share a seed as one unit, so their
   intervals stay honest under every mode.

``print(plan)`` counts the seeds: ``seeds  seed 1, common random numbers:
40 runs on 10 seeds``.  A :class:`~mango.scenario.FromCsv` design with more
than 8 points over columns that are not levers (an exported Latin
hypercube, say) warns under ``True``: unlike :class:`~mango.scenario.Lhs`
it does not default to ``"levers"``.  Identical rows of an
:class:`~mango.scenario.Explicit` or CSV design get the same seeds and run
once, with a warning.  For more runs of chosen points (an unequal
allocation), give those rows a ``replications`` value (a column of the CSV
file, a key of the row, or of a subexperiment): ``Explicit([{"servers": 2},
{"servers": 3, "replications": 20}])`` runs 2 replications (the study's)
of the first point and 20 of the second, and ``print(plan)`` says ``2
design points x 2 to 20 replications``.  Replications keep the indices
``0..n-1``, so the first runs of a point are the same as in a study with
fewer, and a later study with more replications reuses them.

.. _scenario-crn-analysis:

Analysing studies with shared seeds
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``summary`` and ``paired`` handle shared seeds themselves.  For your own
models of the frame (OLS, ANOVA, a regression metamodel over the design
points), remember that under ``common_random_numbers=True`` every row of
replication *r* shares that replication's noise:

- **Block on the replication.**  ``smf.ols("wq ~ C(replication) + servers +
  arrival_rate", data=frame)`` gives the right standard errors for the
  slopes and effects: the shared noise of a replication cancels out of
  every comparison within it (unblocked, the slopes are about right but
  their standard errors are inflated).  The fixed block does not repair the
  standard errors of the overall mean or of predictions: it conditions on
  the replications that happened to run (and with treatment coding the
  intercept is the mean of replication 0, not the grand mean).  For those,
  model the replication as a random effect
  (``smf.mixedlm("wq ~ servers + arrival_rate", data=frame,
  groups="replication")``) or use the replication means as independent units
  (``frame.groupby("replication")["wq"].mean()``; ``summary`` per design
  point has one run per replication).  Unblocked OLS understates these
  standard errors whenever the replications differ, because the rows are
  not independent.
- **Many contrasts**: correct the intervals of many paired comparisons for
  multiplicity (Holm or Bonferroni), or pre-register the few that matter.
- **One value per run.**  The rows of one run's time series or event log
  (``frame.timeseries()``, ``frame.log()``) are autocorrelated: a mean
  ``± 1.96 s / sqrt(n)`` over them is far too narrow.  Reduce each run to
  one number (an outcome) and compare those, or use batch means within a
  long run.
- **Warm-up (initial transient).**  A steady-state outcome must not
  include the start-up from an empty or arbitrary initial state.  The
  framework does not detect the warm-up for you: in a pilot, average the
  series over the replications per time
  (``frame.timeseries(variables="queue").groupby("time")["value"].mean()``,
  smoothed with a moving average: Welch's method), pick the time after
  which it stays flat, and leave that time out with a ``record: false``
  phase.  Outcomes from :meth:`~mango.scenario.RunResult.time_average`
  then divide by the recorded time only, and a state recorder writes the
  level at the end of the warm-up as its first row
  (:ref:`flows and states <scenario-state-flow>`).  Prefer several
  replications of moderate length to one long run, so that every run is
  one independent unit.
- **Censored outcomes.**  ``summary`` and ``paired`` leave out runs whose
  outcome is NaN/None and count them in ``missing``, with a warning.  A time
  to an event that did not happen before the horizon is censored, not
  missing at random: report the censored share (an outcome
  ``run.stats["stopped_early"]``), or record the horizon plus a flag.
- **Antithetic variates**: a boolean uncertainty (``antithetic: [false,
  true]``) under ``common_random_numbers=True``, with the model using
  ``1 - u`` instead of ``u`` on its own ``rng_for`` stream when it is set,
  pairs each run with its mirrored twin; average the two as one unit.
- **How many replications.**  Run a pilot (e.g. 10 replications), then
  ``frame.replications_needed("wq", half_width=0.1)`` (or ``relative=0.05``,
  or ``lever=...`` for paired differences, which need far fewer) gives the
  replications for that interval; raise ``replications`` and run the study
  again in the same folder (the done runs are reused).  Plan once and run
  that number: adding runs until an interval looks narrow enough makes it
  too optimistic.
- **Run order.**  ``Study(..., run_order="replication")`` (study file
  ``run_order: replication``) runs replication 0 of every design point
  first, then replication 1 and so on, so a study that is interrupted or
  stopped early is balanced over the points (run ids do not change).
- **Sobol indices** are estimated from replication averages; their
  bootstrap intervals cover the sampling of the factor space, not the
  noise (see `Sobol indices`_).

A study without ``seed`` uses the ``seed`` of its base parameters (e.g.
``seed: 42`` in a preset) or draws one, and records it in ``study.yaml``,
where a resumed study finds it again.  Inside a study ``params.seed`` is
``null`` in every run spec and resolved configuration (the run seed is the
spec's ``seed``), and ``seed`` cannot be a factor: more seeds per design
point are ``replications``, another set of seeds is another study ``seed``.

Running a study
---------------

:meth:`~mango.scenario.Study.run` plans the study, writes the study folder
and executes the runs that are not done yet:

.. testcode:: scenario

    report = study.run(progress=False)
    print(report.total, report.done, report.skipped, report.failed)

.. testoutput:: scenario

    8 8 0 0

With ``progress=True`` (the default) every finished run prints a line such
as ``[3/8] done   run 2 (97eff9b76c8b) in 0.01 s`` to stderr, and
``print(report)`` summarises the call.  ``report.ok`` is true when every run
of the plan is done; ``report.failures`` lists the failed runs with their
error, and ``report.not_started`` the ``report.pending`` runs that a dying
worker or a broken executor kept from starting (the next ``study.run()``
executes them).

.. _scenario-executors:

Executors
~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - ``study.run(...)``
     - Executes
   * - ``executor=None`` (default), ``"sequential"``
     - in this process, one run after the other, each with a fresh event loop
   * - ``workers=4`` or ``executor="process"``
     - on a managed process pool (``"process"`` alone uses all cores); by
       default (``runs_per_worker="auto"``) short runs share worker
       processes, ``runs_per_worker=1`` gives every run a fresh one
   * - ``executor=<concurrent.futures.Executor>``
     - on your executor, e.g. a ``ProcessPoolExecutor`` you configured or a
       cluster executor with the same interface; it is not shut down.  Its
       workers are shared: one that dies breaks a ``ProcessPoolExecutor``,
       so all runs in flight are recorded as failed and the rest are not
       started (``report.pending``)
   * - ``executor="slurm"`` or ``executor=SlurmExecutor(...)``
     - as a Slurm job array: the call writes the pending runs and a batch
       script, submits it and returns at once (see :ref:`scenario-slurm`)

Runs are independent, so a study parallelises across runs, not inside a run.
The managed pool follows rules that keep parallel runs reproducible:

* **Worker processes sized to the runs.**  Runs execute in ``spawn``
  worker processes (at most ``workers`` at a time), each in its own
  single-process pool, so a process that dies (a crash in native code,
  running out of memory) takes down only its own run, which is recorded as
  failed while all others continue.  Starting a worker costs the
  interpreter, the imports of mango, pandas and your project and the first
  read of the profile files: about 1.5-3 s of CPU time, more with heavy
  project imports.  By default (``runs_per_worker="auto"``) the pool
  measures that start-up and the run time (without the import) and lets a
  worker execute up to K = ceil(10 x start-up / run time) runs (at most
  200) before a fresh one replaces it, so short runs pay the start-up once
  per K runs and runs longer than about ten start-ups get a fresh process
  each.  The report says when workers were reused (``process pool (2
  workers), workers reused for up to 40 runs each (auto)``).  Global state
  of your code (module-level caches) can carry over between the runs of a
  worker (mango's own state does not); ``runs_per_worker=1``
  (``--runs-per-worker 1``, or ``executor: {runs_per_worker: 1}`` in the
  study file) gives every run a fresh process, the strictest isolation, and
  an int K fixes the number.  The report prints a hint when a fixed K
  spends a third or more of the worker time starting workers (it gives the
  measured share), and when a sequential study
  would have been faster (a few short runs: run them without ``workers``).
  Every run records its wall time, CPU time (``cpu_s``) and import time
  (``load_s``) in its manifest; ``cpu_s`` (the CPU time of the thread that
  ran the simulation, so idle helper threads of numerical libraries do not
  inflate it) is the figure for sizing, since wall time grows with the load
  of the machine.
* **Ctrl+C finishes cleanly.**  Workers ignore Ctrl+C; the first press stops
  starting runs and lets the running ones finish and commit, a second press
  terminates them.
* **No hidden threads.**  While the pool runs, ``OMP_NUM_THREADS``,
  ``OPENBLAS_NUM_THREADS``, ``MKL_NUM_THREADS`` and ``NUMEXPR_NUM_THREADS``
  are set to ``1`` and ``PYTHONHASHSEED`` to ``0`` (only where they are not
  set already), so that workers do not oversubscribe the cores.
* **Plain data in, files out.**  A worker receives a run spec, imports the
  scenario and the outcomes by reference, runs and writes its own folder.

A process-based study therefore needs a scenario and outcomes that worker
processes can import: define them at module level of an importable module (a
scenario created as ``scenario = Scenario(...)`` in ``studies/batteries.py``
is found as ``"studies.batteries:scenario"``), or pass the reference as a
string.  Start the study from a script with a main guard, because ``spawn``
imports the main module in every worker:

.. code-block:: python

    # run_study.py, next to the package "studies"
    from mango.scenario import Study, load_study
    from studies.batteries import final_soc, scenario

    if __name__ == "__main__":
        study = Study(
            scenario,
            levers={"battery.charge_kw": [1.0, 2.0]},
            replications=3,
            seed=2026,
            outcomes=[final_soc],
            out="results/pool",
        )
        print(study.run(workers=2))

.. code-block:: text

    [1/6] done   run 0 (66464ba0bb8f) in 0.35 s
    ...
    [6/6] done   run 5 (23aa990430a1) in 0.35 s
    6 runs: 6 done, 0 skipped, 0 failed in 3.9 s (process pool (2 workers)) -> /home/me/project/results/pool

Without the guard ``study.run(workers=...)`` raises a ``StudyError`` before it
starts a worker and names the line: "study.run() at line 14 of run_study.py runs at the top
level of the script, outside an 'if __name__ == "__main__":' guard".  A worker
that still reaches a ``study.run()`` while it imports the script (for example
one inside some other ``if``) exits with one line saying so, and its runs are
reported as not started.

Equivalently, ``Study("studies.batteries:scenario",
outcomes=["studies.batteries:final_soc"], ...)`` imports both by reference.
A scenario or outcome that cannot be imported fails before anything is
written, with a hint, and so does one defined inside the ``if __name__ ==
"__main__":`` block of the script (workers import the script without running
that block).  The project folder (the current directory) is put on the
workers' ``sys.path``.  Because workers import the scenario anew, every
declaration belongs in its module: a scenario that the running script changes
after importing it (say, an extra ``scenario.record(...)``) would differ in
the workers, so each such run fails with an explanation instead of silently
missing the change.  A scenario defined in the script itself has the same run
ids whether the study is started as ``python run_study.py`` or from code that
imports ``run_study``.

.. _scenario-resuming:

Resuming
~~~~~~~~

Run folders are named by the run id, a hash of the scenario reference, the
resolved parameters and the seed.  Before anything is dispatched, the study
checks every run folder: done runs are skipped without starting a process,
interrupted runs (a folder without a manifest) are deleted and run again, and
failed runs are retried unless ``retry_failed=False``.  Running a finished
study again is therefore cheap, and a changed parameter produces new run ids
and new folders next to the old ones instead of silently reusing them:

.. testcode:: scenario

    again = study.run(progress=False)
    print(again.done, again.skipped)

.. testoutput:: scenario

    0 8

The run id does not cover your code or your data files, so a done run is
also checked against them.  Its manifest lists the project files it used
(``code_files``: the modules of the scenario, its factories and kits and the
outcomes, and every project module they import; ``data_files``: every file
a string in its parameters names, such as profile and
:class:`~mango.scenario.FromColumn` tables) with a hash of their content.
When one of these files changed since, or the scenario definition did, the
run is **stale**: ``study.run()`` runs it again and warns once, naming the
files (``2 finished runs are stale (code changed in model/agents.py);
running them again``); the old manifest is kept as ``manifest.prev.json``.
``study.run(stale="keep")`` (``--keep-stale``) keeps stale runs instead,
with a warning; ``load_study`` marks them in its ``stale`` column and
``mango-scenario status`` counts them.  "Project files" are the modules
below the project root (the working directory of the study), not installed
packages and not mango itself, whose version the manifest records.  A study
run in-process (sequentially, in a notebook) refuses to start when a project
module changed after the process imported it, because the runs would execute
the old code: restart the interpreter or the kernel first.  Editing a file
the study does not use (an analysis script) never makes runs stale; editing
a design CSV changes the factor values and so the run ids.  Code is compared
by meaning (:func:`~mango.scenario.freshness.code_digest`): adding or
changing comments, blank lines, line breaks inside brackets or the
indentation width of a module, and comments or layout of a YAML scenario
file, keep its runs fresh, so you can document a model after a long study;
any other edit, a docstring included, makes them stale.  Data files are
compared byte for byte.

The stale summary comes before the first run starts (``mango-scenario run``
prints warnings as they arise), so you can stop a study that would repeat
hours of work.  A run records the code it executed: a module as the worker
process imported it, a scenario or data file as it was when the run
started.  Editing a file while a study runs therefore makes the runs that
used the old version stale (``manifest["changed_during_run"]`` lists the
files; the report says ``2 runs used code or data that changed while the
study ran``), and the next ``study.run()`` repeats them.  When a change
does not affect the results (a refactoring, an unused helper; ``mango-scenario
rerun`` replays a run identically, and says which files changed since),
**certify** the runs instead of repeating them: ``study.certify(reason="...")``
or ``mango-scenario certify results/<study> [RUN ...] --reason "..."``
(``rerun ... --certify`` certifies a run whose replay is identical).  The
manifest then records the current file digests plus the reason and time
(``manifest["certified"]``), and ``manifest.prev.json`` keeps the old one.

Run ids hash the resolved parameters, *defaults included*.  Adding,
renaming or removing a field of the params model (also of a kit) or changing
a default therefore gives every run a new id: the old runs are not stale
runs of the new plan but an earlier plan, kept in ``runs/`` and
``plans/<plan id>/`` (``--keep-stale`` cannot keep them).  ``study.run()``
says so (``the resolved parameters of its runs changed (new field
battery.kind) ... the study now plans 4 runs, 4 of them with new ids``),
and ``mango-scenario status`` plans the stored ``study.yaml`` with today's
code and reports how many of the planned runs are on disk.

Outcomes added to the study after some runs finished are computed for those
runs from their stored time series.  While ``study.run()`` works on a folder
it holds the lock file ``.lock``; a second ``study.run()`` on the same folder
fails with the process and host that hold it (a lock left behind by a
process that no longer runs on this host is taken over).

``Ctrl+C`` starts no further runs, waits for the running ones to finish and
commit (a second ``Ctrl+C`` aborts them) and keeps the finished ones; the
next ``study.run()`` continues where it stopped.  Runs start in run order:
by default all replications of a design point, then the next point, so an
interrupted study may have no runs of its last levels.
``Study(..., run_order="replication")`` (``run_order: replication`` in a
study file) runs replication 0 of every point first, then replication 1, so
the levels stay balanced for ``paired``; the run ids do not change.  With
several workers this orders the dispatch, not the completion.

Failures are data
~~~~~~~~~~~~~~~~~

A run that raises does not stop the study.  Its folder gets the traceback in
``error.txt`` and a manifest with ``status: failed``; the report lists it.
This includes exceptions inside the agents while the simulation runs: a
message handler that raises, and an exception in a scheduled, periodic or
instant task or a role coroutine (for example asynchronous reply code) fail
the run with :class:`~mango.AgentTaskError` naming the agent, the task and
the simulated time, with the first exception chained in ``error.txt`` and
the number of further errors of that step (see :ref:`simulation-agent-errors`).
An exception in ``on_step`` (of an agent or a role) carries the note
``while stepping agent 'a_0' (role R) in on_step at t=1 s``.  The CLI's
one-line error (``run``, ``validate --build``) adds these notes and the
innermost frame of your code (``at model.py:9 in on_step``), so it points at
the bug without ``--debug``; a send to something that is not an
``AgentAddress`` fails at once, in the calling code.
Here a small capacity makes some batteries start above their capacity:

.. testcode:: scenario

    from mango.scenario import load_study

    capacity = Study(
        scenario,
        levers={"battery.capacity_kwh": [3.0, 10.0]},
        replications=2,
        seed=1,
        outcomes=[final_soc],
        out="results/capacity",
    )
    print(capacity.run(progress=False))

.. testoutput:: scenario

    4 runs: 2 done, 0 skipped, 2 failed in ... s (sequential) -> .../results/capacity
      failed run 0 (...): ValueError: soc_kwh 3.69 exceeds capacity_kwh 3.0
      failed run 1 (...): ValueError: soc_kwh 5.13 exceeds capacity_kwh 3.0

An outcome that raises does not fail its run: the outcome is ``None`` and
the error is recorded in the manifest (``outcome_errors``).  The report
counts such runs (``120 runs: 120 done, 0 skipped, 0 failed (3 with outcome
errors)``) and lists their errors, ``mango-scenario status`` does the same
and ``load_study`` has an ``outcome_errors`` column.  Outcomes can be
recomputed later from the stored time series (see below).

The results folder
------------------

.. code-block:: text

    results/batteries/
      study.yaml                 the resolved study definition: scenario, base, factors,
                                 design, replications, seed, outcomes, mango version
      runs.jsonl                 the run table: one run spec per line, in run order
      status.csv                 index, run_id, status (done | failed | pending), wall_s,
                                 cpu_s, warnings, error: a snapshot that study.run() and
                                 mango-scenario status rewrite (Slurm tasks do not update
                                 it; status is the live view)
      plans/<plan id>/           earlier plans of the study (study.yaml, runs.jsonl), kept when
                                 another plan replaced them (a --set variant, a changed
                                 parameter model)
      environments/<hash>.txt    the installed packages (name==version) of each environment
                                 the runs used; the hash is provenance["environment"]
      git/<commit>-<sha>.diff    the uncommitted changes (git diff HEAD) of runs made from a
                                 dirty work tree; the manifest names it (git["diff"])
      runs/<run_id>/
        spec.json                the run spec this run executed
        config.resolved.yaml     the resolved parameters with seed: null; they replay the
                                 run together with the run seed of manifest.json:
                                 run = load_run(folder)
                                 scenario.run(run.config, seed=run.seed)
        timeseries.parquet       the tidy time series (timeseries.csv without pyarrow)
        outcomes.json            the outcome values
        manifest.json            written last; marks the run as done or failed
        manifest.prev.json       the manifest before the run was run again (stale or --force)
                                 or certified
        error.txt                the traceback of a failed run
      slurm/                     only after a run with the Slurm executor:
        pending.jsonl            the run specs of the array, one per line
        array.sbatch             the batch script
        submit.sh                the sbatch commands of an array split into several jobs
        job.json                 the submitted job ids
        logs/                    one log file per array task
      .lock                      only while a study.run() uses the folder

The manifest is the provenance record of a run: run id, study, index,
status, start and end time, wall time, CPU time (``cpu_s``) and the part of
the wall time spent importing the scenario (``load_s``), the distinct
warnings the run issued (``warnings``, also shown by ``mango-scenario
status`` and the study report), seed, scenario reference, factor
values and labels, replication, the checksum of the time series, run
statistics, the entities and their groups, outcome errors, the project
files and data files the run used with their content hashes (``code_files``,
``data_files`` and the fingerprints ``code_fingerprint``,
``data_fingerprint`` over them, see `Resuming`_), the git commit of the
project and whether the working tree had uncommitted changes of tracked
files (``git``, taken by the process that executed the run, so a Slurm task
that starts hours later records the code it imported; ``planned_git`` is the
state when the study was planned; untracked files do not count; for a dirty
tree ``git["diff"]`` names the stored diff), the project root,
``PYTHONHASHSEED`` and under ``provenance`` the versions of mango, Python,
numpy, pandas, pydantic, pyarrow, scipy, networkx and PyYAML, a hash of all
installed packages (``environment``; the list itself is in
``environments/<hash>.txt``), the
version of the installed package that provides the scenario (``package``,
when it is one), platform, host name and, inside a Slurm job, the job,
array task and node ids (``slurm``):

.. testcode:: scenario

    import json

    run_id = plan[0]["run_id"]
    manifest = json.loads(open(f"results/batteries/runs/{run_id}/manifest.json").read())
    print(manifest["status"], manifest["factors"], manifest["labels"])
    print(sorted(manifest["provenance"]["versions"]))

.. testoutput:: scenario

    done {'battery.soc_kwh': {'uniform': [0.0, 2.0]}, 'battery.charge_kw': 1.0} {'battery.soc_kwh': 'empty'}
    ['mango-agents', 'networkx', 'numpy', 'pandas', 'pyarrow', 'pydantic', 'python', 'pyyaml', 'scipy']

A study run from a work tree whose tracked files the runs use (project
code, base presets, data files) have uncommitted changes warns before it
starts; ``study.run(require_clean=True)`` (``mango-scenario run
--require-clean``) refuses to start while any tracked file has uncommitted
changes.

Loading a study
---------------

:func:`~mango.scenario.load_study` returns a ``StudyFrame``, a pandas
``DataFrame`` with one row per planned run: ``index, run_id, replication``,
``point`` (the design point, numbered in run order and shared by its
replications), ``status, wall_s, cpu_s``, ``stale`` (a done run whose code or data
files changed since), ``outcome_errors`` (``{outcome: error}`` of a run
whose outcomes raised), ``git_commit`` and ``git_dirty`` (the git state the
run executed with), ``subexperiment`` and ``sample`` (only when the design
has them), the factors (with ``"<path>:label"`` columns for labeled factors;
dict and list values as canonical JSON text, so every factor column can be
grouped), one column per override of the study (the value the run used, so
a frame of a ``--set`` variant says which variant it is; removals such as
``-phases`` have no column and are listed in ``frame.removed_overrides``)
and the outcomes.  ``StudyPlan.to_frame()`` starts with the same run, design and factor
columns.  These column names (and ``plan``) cannot be outcome names.  It works for finished, partial and failed studies:

.. testcode:: scenario

    frame = load_study("results/batteries")
    print(frame.groupby(["battery.soc_kwh:label", "battery.charge_kw"])["final_soc"].mean())

.. testoutput:: scenario
   :options: +NORMALIZE_WHITESPACE

    battery.soc_kwh:label  battery.charge_kw
    empty                  1.0                   4.911065
                           2.0                   8.911065
    half                   1.0                   8.911065
                           2.0                  10.000000
    Name: final_soc, dtype: float64

Common random numbers at work: replication *r* draws the same uniform
numbers in both ``soc_kwh`` variants, so "empty, 2 kW" and "half, 1 kW" end
at exactly the same charge.

``frame.summary()`` gives the mean and a Student-t confidence interval over
the replications of every design point (or of the groups ``by=[...]``), and
``frame.paired(lever, ...)`` the mean difference of every lever level to a
baseline over the paired runs (same other factors, same replication; the
lever may be given by its ``":label"`` column, a run is never paired with
itself, runs without a value of the lever are left out with a warning, and
runs without a counterpart are left out with a warning that names the
column keeping them apart):

.. testcode:: scenario

    columns = ["battery.soc_kwh:label", "battery.charge_kw", "n", "mean", "ci_low", "ci_high"]
    print(frame.summary("final_soc")[columns].round(2))
    paired = frame.paired("battery.charge_kw", "final_soc", by="battery.soc_kwh:label")
    print(paired[["battery.soc_kwh:label", "battery.charge_kw", "n", "mean", "std"]].round(2))

.. testoutput:: scenario
   :options: +NORMALIZE_WHITESPACE

      battery.soc_kwh:label  battery.charge_kw  n   mean  ci_low  ci_high
    0                 empty                1.0  2   4.91    1.47     8.35
    1                 empty                2.0  2   8.91    5.47    12.35
    2                  half                1.0  2   8.91    5.47    12.35
    3                  half                2.0  2  10.00   10.00    10.00
      battery.soc_kwh:label  battery.charge_kw  n  mean   std
    0                 empty                2.0  2  4.00  0.00
    1                  half                2.0  2  1.09  0.38

``n`` counts *independent units*: runs that share a seed are averaged into
one unit first, so a summary over several design points under
``common_random_numbers=True`` has one unit per replication (see
:ref:`scenario-crn`).  Two replications give wide intervals, but the
paired differences of the "empty" batteries do not vary at all: both charge
rates start from the same draws, so their difference is exactly the extra
charge, while the "half" batteries differ by less because some reach their
capacity.  Both tables also have ``missing``: runs (or pairs) whose outcome
is NaN or None, left out with a warning (see
:ref:`scenario-crn-analysis` for censored outcomes).
``frame.replications_needed(outcome, half_width=...)`` turns such a pilot
into the replications a target interval needs.  ``paired(..., pairs=True)``
returns the pairs themselves, ``frame.expand("per_battery")`` turns a
dict-valued outcome into one column per key (``per_battery.battery_0``, ...)
that ``summary``, ``paired`` and ``sobol_indices`` read like any outcome.

``frame.failed()`` selects the failed runs and adds their ``error`` line,
``frame.run(i)`` (by index or run id) loads the
:class:`~mango.scenario.RunResult` of a done run,
``frame.timeseries(run_ids=None, variables=None)`` concatenates the tidy
tables of done runs, with the run's ``replication``, ``point``, factor and
override columns appended (``with_factors=False`` leaves them out), so
``ts.groupby(["battery.charge_kw", "time"], observed=True)["value"].mean()``
needs no merge, and ``frame.sobol_indices()`` analyses a Sobol study
(see :ref:`scenario-designs`).  The frame stays a ``StudyFrame`` through filtering,
sorting, ``merge`` (with the study frame on the left) and ``concat`` of frames
of one study.  ``load_study(path, outcomes=...)`` computes new or
corrected outcomes from the stored time series, without running anything
again.  A recomputation sees what a loaded run offers (time series, config,
params, manifest); ``run.agent()`` and ``run.world`` exist only while the
study runs, so record a quantity with a recorder if an outcome must be
recomputable.  A recomputation that raises never destroys a stored value:
the stored value stays, and one warning per outcome says for how many runs
the recomputation failed:

.. testcode:: scenario

    def peak_soc(run):
        return float(run.ts("soc_kwh").max())


    frame = load_study("results/batteries", outcomes=[peak_soc])
    print(frame[["battery.charge_kw", "final_soc", "peak_soc"]].head(2).round(2))
    failed = load_study("results/capacity").failed()
    print(failed[["index", "battery.capacity_kwh", "status"]])
    print(failed["error"].iloc[0])
    print(len(frame.timeseries(variables=["soc_kwh"])), frame.run(0).wide("soc_kwh").shape)

.. testoutput:: scenario
   :options: +NORMALIZE_WHITESPACE

       battery.charge_kw  final_soc  peak_soc
    0                1.0       4.64      4.69
    1                1.0       5.18      5.78
       index  battery.capacity_kwh  status
    0      0                   3.0  failed
    1      1                   3.0  failed
    ValueError: soc_kwh 3.69 exceeds capacity_kwh 3.0
    120 (5, 3)

.. testcleanup:: scenario

    os.chdir(_scenario_cwd)
    _scenario_tmp.cleanup()

----

.. _scenario-kits:

Kits: reusable building blocks
==============================

Parts of a scenario come back in the next study: the battery fleet, the
grid with its households, the communication model.  A *kit* bundles such a
part, its typed parameters, groups, links, behaviors and recorders, into one
class that any scenario uses with one line.  A kit subclasses
:class:`~mango.scenario.ScenarioComponent`, declares its parameters as a
nested ``Params`` model and implements any of three hooks:

.. testsetup:: kits

    from mango import Agent


    class Battery(Agent):
        def __init__(self, capacity_kwh: float, soc_kwh: float, charge_kw: float):
            super().__init__()
            if soc_kwh > capacity_kwh:
                raise ValueError(f"soc_kwh {soc_kwh:.2f} exceeds capacity_kwh {capacity_kwh}")
            self.capacity_kwh = capacity_kwh
            self.soc_kwh = soc_kwh
            self.charge_kw = charge_kw

        def on_step(self, env, clock, step_size_s):
            energy_kwh = self.charge_kw * step_size_s / 3600
            self.soc_kwh = min(self.capacity_kwh, self.soc_kwh + energy_kwh)
            for aggregator in self.neighbors(tid="reports"):
                self.schedule_instant_message(self.soc_kwh, aggregator)


    class Aggregator(Agent):
        def __init__(self):
            super().__init__()
            self.reports = {}

        def handle_message(self, content, meta):
            self.reports[meta["sender_id"]] = content

.. testcode:: kits

    from pydantic import BaseModel

    from mango.scenario import Scenario, ScenarioComponent, Uniform


    class BatteryFleet(ScenarioComponent):
        class Params(BaseModel, extra="forbid"):
            n: int = 3
            capacity_kwh: float = 10.0
            soc_kwh: Uniform = Uniform(2.0, 6.0)
            charge_kw: float = 1.5

        def build(self, ctx):
            p = ctx.p
            ctx.group("aggregator", 1, lambda e: Aggregator())
            ctx.group(
                "battery",
                p.n,
                lambda e: Battery(p.capacity_kwh, e.draw(p.soc_kwh), p.charge_kw),
            )

        def wire(self, ctx):
            ctx.link("reports", "battery", "aggregator", pattern="many_to_one")

        def record(self, ctx):
            ctx.record("soc_kwh", "battery", attr="soc_kwh")


    districts = Scenario("districts")
    districts.time(horizon="4h", step="1h")
    districts.use(BatteryFleet(n=2), prefix="north")
    districts.use(BatteryFleet(charge_kw=2.0), prefix="south")
    print(districts.plan(overrides=["south.n=4"]))

.. testoutput:: kits

    Scenario 'districts': 8 agents in 4 groups, 1 link, 2 recorders
      time: horizon 4h, step 1h (4 steps)
      kits:
        north            BatteryFleet (...)
        south            BatteryFleet (...)
      groups:
        north.aggregator      1  north.aggregator
        north.battery         2  north.battery_0 .. north.battery_1
        south.aggregator      1  south.aggregator
        south.battery         4  south.battery_0 .. south.battery_3
      links:
        reports          north.battery -> north.aggregator (many_to_one), south.battery -> south.aggregator (many_to_one); 6 edges, undirected
      recorders:
        soc_kwh          north.battery
        soc_kwh          south.battery

The same kit is used twice; the prefix keeps the two fleets apart.

The kit lifecycle
-----------------

``scenario.use(kit, prefix=None)`` adds the kit under *prefix* (default: the
kit's class attribute ``name``, which defaults to the class name in snake
case, ``BatteryFleet`` → ``"battery_fleet"``).  The prefixes ``agent``,
``behavior``, ``design``, ``link``, ``scenario`` and ``world`` are reserved,
because the kit's random streams ``<prefix>/<name>`` would be those of the
run's entities, links and behaviors.  Every run then declares the scenario
in three passes:

1. ``build(ctx)`` of every kit: groups, behaviors, the space, shared
   resources and kit-local recorders;
2. ``wire(ctx)`` of every kit: links;
3. ``record(ctx)`` of every kit: recorders.

All ``build`` hooks run before any ``wire`` hook, so a link may connect
groups of kits declared later.  Declarations of the scenario itself take
part in these passes in declaration order: ``scenario.group`` in the build
pass, ``scenario.link`` in the wire pass and ``scenario.record`` in the
record pass.  Group factories run later still, once the world exists, so
they see every resource of every kit.  ``plan()`` runs the same passes
without building agents, so a mistake inside a factory (``e.p.north.n``
in kit ``north``, where ``e.p`` already is ``params.north``) shows only
when agents are built: ``mango-scenario validate --build`` builds run 0.

Inside the hooks, ``ctx`` is a :class:`~mango.scenario.ComponentContext`, a
:class:`~mango.scenario.BuildContext` scoped to the kit's prefix:

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - In kit ``north``
     - Means
   * - ``ctx.p``, and ``e.p`` in the kit's factories
     - the kit's parameters of this run, ``params.north`` (``e.p.n``, not
       ``e.p.north.n``)
   * - ``ctx.params``, ``e.params``
     - all parameters of the run: ``ctx.params.north`` is ``ctx.p``,
       scenario-level fields are ``ctx.params.time``,
       ``ctx.params.<field>``
   * - ``ctx.group("battery", ...)``
     - the group ``north.battery`` with entities ``north.battery_0`` ...
       (default naming; a ``naming`` format string or function is used
       verbatim, see the group options)
   * - group names in ``ctx.link``, ``ctx.record``, ``ctx.entities``
     - the kit's own group ``north.<name>`` when it exists, else as given
       (``"coordinator"`` reaches a scenario-level group)
   * - ``ctx.rng("weather")``
     - the stream ``north/weather``
   * - ``ctx.record_world(name, fn)``
     - world rows with ``group`` and ``entity`` ``north`` (the same kit
       under ``south`` records the same variable for entity ``south``)
   * - ``ctx.provide("tariff", obj)`` / ``ctx.resource("tariff")``
     - the resource ``north.tariff``; reading looks in ``north`` first, then
       for the exact name, then for the one kit resource ``*.tariff``
       (``scope="global"`` provides a bare, shared name)
   * - link names
     - global: both fleets report on one topology ``reports``

Kit parameters
--------------

A kit's ``Params`` become the field ``<prefix>`` of the scenario's effective
parameter model, with the constructor arguments as defaults.  Presets and
overrides reach them like any other parameter (``south.charge_kw=3``), and
:func:`~mango.scenario.params_schema` and the preset form include them:

.. testcode:: kits

    print(list(districts.params_model.model_fields))
    params = districts.resolve_params(overrides=["north.charge_kw=3"])
    print(params.north)
    print(params.south)

.. testoutput:: kits

    ['seed', 'time', 'comm', 'profiles', 'phases', 'events', 'north', 'south']
    n=2 capacity_kwh=10.0 soc_kwh=Uniform(2.0, 6.0) charge_kw=3.0
    n=3 capacity_kwh=10.0 soc_kwh=Uniform(2.0, 6.0) charge_kw=2.0

Constructor arguments are validated at once, so a typo fails where it is
written:

.. testcode:: kits

    from mango.scenario import ParamsError

    try:
        BatteryFleet(capactiy_kwh=12)
    except ParamsError as error:
        print(error)

.. testoutput:: kits

    BatteryFleet() got unknown parameters:
      capactiy_kwh: Params has no field 'capactiy_kwh'; did you mean 'capacity_kwh'?
    Parameters of BatteryFleet.Params: n, capacity_kwh, soc_kwh, charge_kw.

Hooks read the run's parameters from ``ctx.p``, never from the kit
instance: the instance only holds the defaults (``kit.params``), while
presets and overrides change ``ctx.p`` per run.  A kit without fields in
``Params`` adds no parameter field.  A kit can also contribute
scenario-level defaults by overriding ``param_defaults()`` (returning, for
example, ``{"comm.delay_s": 0.001}``); they are applied like
:meth:`Scenario.defaults <mango.scenario.Scenario.defaults>`, so presets and
overrides still win.

Shared resources, behaviors and the space
-----------------------------------------

Kits cooperate through named *resources* instead of each other's
internals: ``ctx.provide(name, obj)`` shares an object (a grid model, a
market) and ``ctx.resource(name)`` (or ``e.resource(name)`` in a factory)
reads it.  Resource names are scoped like group names: kit ``grid``
providing ``"transformer"`` stores ``"grid.transformer"``, so a kit used
twice (``agg_n`` and ``agg_s``) provides two resources instead of
colliding.  ``ctx.resource(name)`` looks in the calling kit first
(``"<prefix>.<name>"``), then for the exact name, then for the one resource
of any kit called *name*, so a consumer need not know a single provider's
prefix; when several kits provide the name, the error lists them and the
consumer names one (pass the provider's prefix to it as a kit parameter:
``ctx.resource(f"{ctx.p.feeder}.tariff")``).  ``ctx.provide(name, obj,
scope="global")`` keeps the bare name for a resource that is shared on
purpose (one market for all kits); providing a name twice is an error, and
reading an unknown one lists the provided names.  Resources provided in any
``build`` hook are visible in every ``wire`` and ``record`` hook, in
factories, recorders and ``on_build`` hooks.

Environment dynamics come in as *behaviors*
(:class:`~mango.Behavior`, see :doc:`simulation`):
``ctx.add_behavior(behavior)`` (alias ``ctx.set_behavior``) adds one and
returns it, ``ctx.set_space(space)`` sets the one space of the scenario.
The default environment of a run is ``DefaultEnvironment(space=...,
behavior=...)`` with all added behaviors combined in declaration order by a
:class:`~mango.scenario.CompositeBehavior`.  Here a kit without parameters
models a transformer that sums the charging power of all batteries:

.. testcode:: kits

    from mango import Behavior
    from mango.scenario import CommunicationPreset


    class Transformer(Behavior):
        def __init__(self):
            self.batteries = []
            self.load_kw = 0.0

        def install(self, agent, **attrs):
            if isinstance(agent, Battery):
                self.batteries.append(agent)

        def on_step(self, environment, clock, step_size_s):
            charging = [b for b in self.batteries if b.soc_kwh < b.capacity_kwh]
            self.load_kw = sum(b.charge_kw for b in charging)


    class Grid(ScenarioComponent):
        def build(self, ctx):
            transformer = ctx.add_behavior(Transformer())
            ctx.provide("transformer", transformer)
            ctx.record_world("load_kw", lambda: transformer.load_kw)


    district = Scenario.compose(
        Grid(), BatteryFleet(n=3), CommunicationPreset("lte"), name="district"
    )
    district.time(horizon="6h", step="1h")
    district.record_world(
        "headroom_kw", lambda ctx: 4.0 - ctx.resource("transformer").load_kw
    )
    print(district.plan())
    print(district.run(seed=2).wide("load_kw"))

.. testoutput:: kits
   :options: +NORMALIZE_WHITESPACE

    Scenario 'district': 4 agents in 2 groups, 1 link, 3 recorders
      time: horizon 6h, step 1h (6 steps)
      communication: simple, loss 0, delay Normal(0.04, 0.008, low=0.0)
      kits:
        grid                     Grid (...)
        battery_fleet            BatteryFleet (...)
        communication            CommunicationPreset (mango.scenario.kits:CommunicationPreset)
      behaviors: Transformer (grid)
      resources: grid.transformer (grid)
      groups:
        battery_fleet.aggregator      1  battery_fleet.aggregator
        battery_fleet.battery         3  battery_fleet.battery_0 .. battery_fleet.battery_2
      links:
        reports                  battery_fleet.battery -> battery_fleet.aggregator (many_to_one); 3 edges, undirected
      recorders:
        load_kw                  grid (world)
        soc_kwh                  battery_fleet.battery
        headroom_kw              world
    entity   grid
    time
    0.0       0.0
    3600.0    4.5
    7200.0    4.5
    10800.0   3.0
    14400.0   3.0
    18000.0   1.5
    21600.0   0.0

:meth:`Scenario.compose <mango.scenario.Scenario.compose>` creates a scenario
from kits, each under its default prefix; further declarations can follow.
A behavior that declares ``rng = None`` gets its own stream
``behavior/<prefix>/<i>`` (the *i*-th behavior of that kit), so adding a kit
never changes the draws of another kit's behaviors.  A custom environment
factory (``scenario.environment``) must install ``ctx.behavior`` and
``ctx.space``, for example ``DefaultEnvironment(space=ctx.space,
behavior=ctx.behavior)``; the run checks this.  Profiles are available as
``ctx.profile(name)`` in every hook (see :ref:`scenario-profiles`).

.. _scenario-space:

Agents in space
---------------

An :class:`~mango.Area2D` space keeps the position of every agent, so
agents need no position attribute of their own (a copy would drift from
the space).  A group places its entities with ``position=`` (unplaced
agents get a random position when the world starts); agents read and
change positions through the ``env`` argument of ``on_step``:
``env.space.location(self)``, ``env.space.neighbors_within(self, radius)``
(nearest first), ``env.space.move_toward(self, target, max_step)``.
Behaviors iterate ``environment.agents``; see :ref:`the spatial helpers
<simulation-spatial-environment>` of the simulation guide.

A recorder that takes two arguments, ``fn(agent, ctx)``, gets the run's
context as well; ``ctx.space`` is the run's space (the one a kit set with
``ctx.set_space``, else the space of ``scenario.environment(...)``), so a
plain scenario records positions without copying them onto the agents:

.. testcode:: kits

    from mango import Area2D, DefaultEnvironment, Position2D


    class Walker(Agent):
        def on_step(self, env, clock, step_size_s):
            here = env.space.location(self)
            env.space.move(self, Position2D(here.x + 1.0, here.y))


    walk = Scenario("walk")
    walk.time(horizon=2, step=1)
    walkers = walk.group("walker", 2, lambda e: Walker(), position=lambda e: Position2D(e.index, 0.0))
    walk.environment(lambda ctx: DefaultEnvironment(space=Area2D(width=10.0, height=10.0)))
    walk.record("x", walkers, lambda agent, ctx: ctx.space.location(agent).x)
    print(walk.run(seed=1).wide("x"))

.. testoutput:: kits
   :options: +NORMALIZE_WHITESPACE

    entity  walker_0  walker_1
    time
    0.0          0.0       1.0
    1.0          1.0       2.0
    2.0          2.0       3.0

A kit that sets the space can also close over it:

.. testcode:: kits


    class Rover(Agent):
        def on_step(self, env, clock, step_size_s):
            env.space.move_toward(self, Position2D(0.0, 0.0), max_step=1.0)


    class Field(ScenarioComponent):
        def build(self, ctx):
            space = ctx.set_space(Area2D(width=10.0, height=10.0))
            ctx.group("rover", 3, lambda e: Rover(), position=lambda e: Position2D(2.0 * e.index, 0.0))
            ctx.record("x", "rover", lambda agent: space.location(agent).x)
            ctx.record("y", "rover", lambda agent: space.location(agent).y)


    field = Scenario.compose(Field(), name="field")
    field.time(horizon=2, step=1)
    print(field.run(seed=1).wide("x"))

.. testoutput:: kits
   :options: +NORMALIZE_WHITESPACE

    entity  field.rover_0  field.rover_1  field.rover_2
    time
    0.0               0.0            2.0            4.0
    1.0               0.0            1.0            3.0
    2.0               0.0            0.0            2.0

``build`` runs once per run, so every run gets its own space and the
recorders read that one.

Registering kits
----------------

Scenario files name kits by *kind*.  ``register_component(kind)`` registers
a kit class (as a decorator or a call), ``resolve_component`` looks a kind
up and ``component_kinds()`` lists the known kinds:

.. testcode:: kits

    from mango.scenario import component_kinds, register_component, resolve_component

    register_component("battery_fleet")(BatteryFleet)
    print(resolve_component("battery_fleet") is BatteryFleet)
    print(component_kinds()["communication_preset"])

.. testoutput:: kits

    True
    mango.scenario.kits:CommunicationPreset

A kind is known once the module that registers it is imported.  Packages
publish kinds without being imported first through the entry point group
``mango.scenario.components``:

.. code-block:: toml

    # pyproject.toml of a package that ships kits
    [project.entry-points."mango.scenario.components"]
    battery_fleet = "my_kits.storage:BatteryFleet"

An import reference ``"my_kits.storage:BatteryFleet"`` always works as a
kind, registered or not.

Built-in kits
-------------

Two generic kits ship with mango; domain kits (EV fleets, households,
grids) belong to domain packages.

:class:`~mango.scenario.AgentPopulation` (kind ``agent_population``) is a
group from a factory reference, optionally linked, and is mostly useful in
scenario files.  Its parameters are ``factory`` (``"module:attr"`` of a
function ``fn(e)`` or an ``Agent`` subclass called as ``Cls(**kwargs)``),
``count``, ``group`` (default ``"agent"``), ``kwargs``, ``install``,
``link`` (a link name; none by default), ``pattern`` (default
``"complete"``) and ``target`` (the target group of the link; default: the
population itself).

:class:`~mango.scenario.CommunicationPreset` (kind
``communication_preset``) sets the communication defaults from a named
preset; keyword arguments adjust it (``CommunicationPreset("lossy",
loss_percent=0.2)``), and presets and ``comm.*`` overrides still win:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Preset
     - Communication settings
   * - ``ideal``
     - ``kind: ideal``: no delay, no loss
   * - ``lan``
     - a delay of 1 ms
   * - ``lte``
     - a delay drawn per message from ``Normal(40 ms, 8 ms)``, never negative
   * - ``lossy``
     - 10 % message loss

.. testcode:: kits

    print(district.resolve_params().comm)

.. testoutput:: kits

    kind='simple' loss_percent=0.0 delay_s=Normal(0.04, 0.008, low=0.0) factory=None options={}

.. testcleanup:: kits

    from mango.scenario import components

    components._REGISTRY.pop("battery_fleet", None)

----

.. _scenario-profiles:

Profiles and the calendar
=========================

Many inputs are time series: a household load curve, a PV feed-in shape, a
price.  A *profile* is such a series, declared once in the parameters under
``profiles`` and read by agents through a :class:`~mango.scenario.Profile`
accessor that maps the simulation clock onto the series.  Agents ask for
"the PV output now" instead of computing it from ``clock.time``.

.. testsetup:: timeline

    import os
    import tempfile

    from mango import Agent

    _timeline_cwd = os.getcwd()
    _timeline_tmp = tempfile.TemporaryDirectory()
    os.chdir(_timeline_tmp.name)
    os.makedirs("data")
    os.makedirs("presets")
    with open("data/pv.csv", "w") as file:
        file.write("pv\n0.0\n0.0\n0.0\n0.1\n0.4\n0.8\n1.0\n0.8\n0.4\n0.1\n0.0\n0.0\n")
    with open("data/fleet.csv", "w") as file:
        file.write("capacity_kwh\n8.0\n10.0\n13.5\n")
    with open("data/load.csv", "w") as file:
        file.write("home,shop\n0.5,0.0\n0.75,2.0\n1.0,3.0\n")
    with open("presets/solar.yaml", "w") as file:
        file.write(
            "time: {start: 2024-06-01T00:00, horizon: 1d, step: 2h}\n"
            "profiles:\n"
            "  pv: {path: data/pv.csv, column: pv, resolution: 2h, noise: 0.1}\n"
        )
    with open("presets/outage.yaml", "w") as file:
        file.write(
            "time: {start: 2024-06-01T00:00, horizon: 8h, step: 1h}\n"
            "phases:\n"
            "  - {name: warmup, until: 2024-06-01T02:00, record: false}\n"
            "events:\n"
            "  - {name: outage, at: 2024-06-01T03:00, until: 2024-06-01T05:00,"
            " comm: {loss_percent: 1.0}}\n"
            "  - {name: curtail, at: 2024-06-01T04:00,"
            " emit: {kind: curtail, factor: 0.5}}\n"
        )


    class Battery(Agent):
        def __init__(self, capacity_kwh: float, soc_kwh: float, charge_kw: float):
            super().__init__()
            if soc_kwh > capacity_kwh:
                raise ValueError(f"soc_kwh {soc_kwh:.2f} exceeds capacity_kwh {capacity_kwh}")
            self.capacity_kwh = capacity_kwh
            self.soc_kwh = soc_kwh
            self.charge_kw = charge_kw

        def on_step(self, env, clock, step_size_s):
            energy_kwh = self.charge_kw * step_size_s / 3600
            self.soc_kwh = min(self.capacity_kwh, self.soc_kwh + energy_kwh)
            for aggregator in self.neighbors(tid="reports"):
                self.schedule_instant_message(self.soc_kwh, aggregator)


    class Aggregator(Agent):
        def __init__(self):
            super().__init__()
            self.reports = {}

        def handle_message(self, content, meta):
            self.reports[meta["sender_id"]] = content

Declaring profiles
------------------

A profile is a :class:`~mango.scenario.ProfileSpec` in
``params.profiles``, usually set in a preset.  This one reads the column
``pv`` of a CSV file with one sample every two hours:

.. code-block:: text
   :caption: data/pv.csv

    pv
    0.0
    0.0
    0.0
    0.1
    0.4
    0.8
    1.0
    0.8
    0.4
    0.1
    0.0
    0.0

.. code-block:: yaml
   :caption: presets/solar.yaml

    time: {start: 2024-06-01T00:00, horizon: 1d, step: 2h}
    profiles:
      pv: {path: data/pv.csv, column: pv, resolution: 2h, noise: 0.1}

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Field
     - Meaning
   * - ``source``
     - ``csv``, ``parquet`` or ``inline``; inferred when omitted (``values``
       means inline, a ``.parquet`` path Parquet, any other path CSV)
   * - ``path``, ``column``
     - the table and its value column, or a list of columns (entities pick
       one by index); default: every column except ``time_column``
   * - ``values``
     - the samples of an inline profile, e.g. ``[0.2, 0.5, 0.9]``
   * - ``time_column``
     - a column with sample times: datetimes, or numbers counting seconds
       from ``start``.  UTC or offset-qualified datetimes
       (``2024-03-31T03:00+02:00``) are unambiguous; naive local times are
       placed in the zone of ``time.start`` (``2024-03-31T00:00[Europe/Berlin]``),
       which also orders the repeated hour when clocks go back
   * - ``start``
     - calendar time of the first sample (default: ``time.start``, so the
       profile moves with it, see below; a naive ``start`` is local time of
       the zone of ``time.start``)
   * - ``resolution``
     - spacing of the samples (required without ``time_column``)
   * - ``scale``
     - a factor applied to every value
   * - ``noise``
     - relative standard deviation of multiplicative Gaussian noise per
       entity and sample (``e.profile`` only)
   * - ``interpolation``
     - ``step`` (hold the last sample, the default) or ``linear``
   * - ``wrap``
     - repeat the profile periodically; without it, reading outside the
       profile is an error.  The default depends on the data: a profile
       *dated* by a datetime ``time_column`` does not wrap (a July run on
       January data fails, naming both dates), every other profile wraps

**Alignment checks.** ``plan()`` lists the profiles (source, number of
values, resolution, anchor) and warns when a dated, non-wrapping profile
does not cover the run from ``time.start`` to ``time.end`` (the horizon as
elapsed time, also across daylight-saving changes); such a run is certain
to fail, so ``mango-scenario validate`` reports it as an error (exit code
1).  A profile without ``start`` is
anchored at ``time.start``: moving ``time.start`` earlier to add a warm-up
moves the profile in calendar time, too.  ``plan()`` and the first read
warn when a run whose first phase is not recorded (a warm-up) meets such a
profile; set the profile's ``start`` to pin it to the calendar.

Files are read once per process and shared by every run and entity of a
worker.  Relative paths stay relative in the parameters (so run ids do not
depend on where the project lives) and resolve against the working
directory, then the study's project folder.  A path that a preset writes
relative to its own folder (``../data/pv.csv`` in ``presets/solar.yaml``)
is rewritten relative to the working directory when the preset is loaded,
so workers and replays find it without the preset.

Reading profiles
----------------

``ctx.profile(name)`` in hooks and factories returns the noise-free
profile; ``e.profile(name)`` in a group factory returns the entity's own
variant: its column (by entity index, modulo the number of columns) and
its noise, drawn from the entity's sub-stream
``agent/<entity>/profile/<name>``, so it is reproducible per entity and does
not consume ``e.rng``.  ``profile.at(clock)`` gives the value at the
clock's time:

.. testcode:: timeline

    from mango.scenario import Scenario, ScenarioParams


    class SolarBattery(Battery):
        def __init__(self, capacity_kwh, peak_kw, pv):
            super().__init__(capacity_kwh, soc_kwh=0.0, charge_kw=0.0)
            self.peak_kw = peak_kw
            self.pv = pv

        def on_step(self, env, clock, step_size_s):
            self.charge_kw = self.peak_kw * self.pv.at(clock)
            super().on_step(env, clock, step_size_s)


    class SolarParams(ScenarioParams):
        n: int = 3
        peak_kw: float = 3.0


    solar = Scenario("solar", params=SolarParams)
    battery = solar.group(
        "battery", "n", lambda e: SolarBattery(10.0, e.p.peak_kw, e.profile("pv"))
    )
    solar.record("charge_kw", battery, attr="charge_kw")
    result = solar.run("presets/solar.yaml", seed=3)
    print(result.wide("charge_kw").round(2).iloc[3:9])

.. testoutput:: timeline
   :options: +NORMALIZE_WHITESPACE

    entity   battery_0  battery_1  battery_2
    time
    21600.0       0.00       0.00       0.00
    28800.0       0.29       0.35       0.34
    36000.0       1.17       1.28       1.10
    43200.0       2.35       2.46       2.56
    50400.0       3.48       2.88       3.48
    57600.0       2.17       2.33       2.64

The noise makes every battery's curve slightly different, and the same seed
gives the same curves again.  A :class:`~mango.scenario.Profile` also works
outside scenarios through :func:`~mango.scenario.load_profile`.  ``at(t)``
accepts a clock, seconds since the scenario start, a ``datetime`` or a
string (ISO date and time or a duration such as ``"13h"``), all on one
time axis of elapsed seconds, so they agree across daylight-saving changes;
``window(start, end)`` returns the samples in a time window:

.. testcode:: timeline

    from mango.scenario import load_profile

    pv = load_profile(
        {"path": "data/pv.csv", "resolution": "2h", "start": "2024-06-01T00:00",
         "interpolation": "linear"},
        name="pv",
    )
    print(pv)
    print(pv.at("2024-06-01T13:00"), pv.at("13h"), pv.at(13 * 3600))
    print(pv.at("2024-06-02T13:00"), pv.window("2024-06-01T10:00", "2024-06-01T16:00"))

.. testoutput:: timeline

    <Profile 'pv' column='pv' n=12 resolution=7200s, start=2024-06-01T00:00:00>
    0.9 0.9 0.9
    0.9 [0.8 1.  0.8]

A profile with several columns gives entity *i* column *i* (modulo the
number of columns).  ``e.profile(name, key=...)`` picks a column instead,
by name or by number, for example by the entity's type:

.. code-block:: text
   :caption: data/load.csv

    home,shop
    0.5,0.0
    0.75,2.0
    1.0,3.0

.. testcode:: timeline

    class Meter(Agent):
        def __init__(self, load):
            super().__init__()
            self.load, self.kw = load, 0.0

        def on_step(self, env, clock, step_size_s):
            self.kw = self.load.at(clock)


    sites = Scenario("sites")
    sites.time(horizon="2h", step="1h")
    sites.group(
        "site", 3, lambda e: Meter(e.profile("load", key="shop" if e.index == 0 else "home"))
    )
    sites.record("kw", "site", attr="kw")
    loads = {"profiles": {"load": {"path": "data/load.csv", "resolution": "1h"}}}
    print(sites.run(loads, seed=1).wide("kw"))

.. testoutput:: timeline
   :options: +NORMALIZE_WHITESPACE

    entity  site_0  site_1  site_2
    time
    0.0        0.0    0.00    0.00
    3600.0     0.0    0.50    0.50
    7200.0     2.0    0.75    0.75

The row at ``1h`` shows the load of the first hour, read in ``on_step`` at
``0`` (see :ref:`scenario-rows`).  ``ctx.profile(name, key=...)`` picks a
column the same way in hooks.

Per-entity values from a table
------------------------------

Parameters that differ per entity and come from a table (battery sizes
from a survey, household consumption) use the distribution
:class:`~mango.scenario.FromColumn`.  In the default ``mode="index"`` entity
*i* gets row *i*; ``mode="sample"`` draws a random row per entity, once
per table: every column of one table drawn with ``e.draw`` comes from the
same row (a respondent's opinion and age stay together), and
``e.row(dist)`` returns that row, e.g. ``install=lambda e: {"respondent":
e.row(e.p.opinion)}`` to record which respondent an agent stands for (the
row comes from the entity's sub-stream ``agent/<name>/table/<path>``, not
from ``e.rng``; ``dist.draw(e.rng)`` called directly draws an independent
row per call).  When the group is larger than the table, rows are reused modulo
the number of rows: by default with a ``UserWarning`` (once per table and
column, since a row is usually a distinct unit such as a survey
respondent), silently with ``wrap=True`` (``wrap: true``), and
``wrap=False`` makes it an error.  The warning is stored with each run
(``warnings`` in its manifest, listed by ``mango-scenario status``), and
``mango-scenario validate`` warns before anything runs when a table in
index mode is shorter than a group.  In index mode every replication gets
the same per-entity values (row *i* belongs to entity *i*; only the random
parts of the model vary); ``mode="sample"`` draws the population anew per
replication.  Draw it with ``e.draw(dist)``, which passes the entity index:

.. testcode:: timeline

    from mango.scenario import Dist, FromColumn


    class FleetParams(ScenarioParams):
        n: int = 4
        # 4 batteries, 3 rows: the fourth reuses row 0 on purpose
        capacity_kwh: Dist = FromColumn("data/fleet.csv", "capacity_kwh", wrap=True)


    fleet = Scenario("fleet", params=FleetParams)
    fleet.group("battery", "n", lambda e: Battery(e.draw(e.p.capacity_kwh), 0.0, 1.0))
    fleet.record("capacity_kwh", "battery", attr="capacity_kwh")
    print(fleet.run(seed=1).wide("capacity_kwh").iloc[0].tolist())

.. testoutput:: timeline

    [8.0, 10.0, 13.5, 8.0]

In a preset the same parameter reads ``capacity_kwh: {from_column:
[data/fleet.csv, capacity_kwh]}`` or ``{kind: from_column, path: ...,
column: ..., mode: sample, wrap: true}``.

Calendar time
-------------

``time.start`` puts a run on the calendar.  Agents then read
``clock.datetime``, results get a ``datetime`` column, and profiles, phases,
events and ``run.run_until`` accept absolute times such as
``2024-06-01T12:00`` next to offsets such as ``12h``.  A start in a named
time zone (``2024-03-31T00:00[Europe/Berlin]``) follows its daylight saving
rules.

----

.. _scenario-events:

Events and phases
=================

Studies of resilience and algorithm switches need time-bounded changes: a
communication outage from 12:00 to 14:00, a fault at 18:00, a warm-up day
that should not count.  Phases and events declare them as data in the
parameters, so moving an outage or adding a second one is one line in a
preset instead of a branch in the step loop.

.. code-block:: yaml
   :caption: presets/outage.yaml

    time: {start: 2024-06-01T00:00, horizon: 8h, step: 1h}
    phases:
      - {name: warmup, until: 2024-06-01T02:00, record: false}
    events:
      - {name: outage, at: 2024-06-01T03:00, until: 2024-06-01T05:00, comm: {loss_percent: 1.0}}
      - {name: curtail, at: 2024-06-01T04:00, emit: {kind: curtail, factor: 0.5}}

Phases
------

:class:`~mango.scenario.Phase` entries split a run into consecutive named
periods.  A phase is active from the previous phase's ``until`` (the start
for the first one) up to its own ``until``; after the last ``until`` an
implicit phase ``main`` runs to the horizon (only the last declared phase
may omit ``until`` and then runs to the horizon itself).  ``record: false``
switches every recorder (and ``ctx.log``) off during that phase only: a
phase records unless it says ``record: false``, like the implicit ``main``.
``comm`` overrides the communication settings and cascades: a phase's
``comm`` merges over the previous phase's, so later phases state only what
changes.  The ``phase`` column of the results holds the phase of every
sample, and ``ts`` and ``wide`` take ``phase=`` to select phases.  Agents,
behaviors and event actions read the current phase as ``ctx.phase`` (and
``ctx.recording`` tells whether a sample now is recorded).

A warm-up usually moves ``time.start`` earlier and adds a first phase with
``record: false``; the phases after it record.  When no phase records,
``plan`` and ``validate`` warn, and a run with recorders that records no
values warns and says why (e.g. "the run stopped at ... inside phase
'warmup' (record: false)"), in ``result.warnings``, in the CLI summary and
in the ``KeyError`` of ``result.ts(...)``.  Profiles without a ``start`` are anchored at
``time.start`` and move with it; give them a ``start`` (or a dated
``time_column``) to keep them fixed in calendar time (``plan()`` warns, see
`Profiles and the calendar`_).

Events
------

An :class:`~mango.scenario.Event` happens at a time point ``at`` and does
one or more of:

* ``comm``: communication settings that apply from ``at`` to ``until``
  (default: to the end of the run; with ``emit``, ``until`` also sends the
  payload a second time, see below).  The effective settings at any time are
  ``params.comm``, overridden by the active phase's ``comm``, overridden by
  the ``comm`` of every active event in ``at`` order; the run switches the
  communication simulation the moment the clock reaches a boundary.
* ``emit``: a payload sent to all agents as a
  :class:`~mango.scenario.ScenarioEvent`, always to ``on_global_event`` of
  agents and roles, or only to the entities ``emit_to`` names, always to
  ``on_agent_event``: an entity, a group (its members when the event fires),
  a list of these, or a selector of install attributes such as ``emit_to:
  {group: person, by_attr: {age_group: young}}`` (``group`` optional; a
  list value accepts any of its items).  ``plan`` names the hook, and a run
  warns once per event when none of its receivers implements that hook (or
  when a selector matches nobody).  ``event.kind`` is the payload's ``kind``,
  ``event["factor"]`` reads a payload key, and ``event.name``,
  ``event.time`` and ``event.datetime`` tell when it happened.  With
  ``until`` the payload is sent at ``at`` (``event.stage == "start"``) and
  at ``until`` (``"end"``; ``event.until`` holds the end time), so one event
  describes a lane closure from 08:00 to 08:30.
* ``action``: an import reference ``"module:function"``, called as
  ``fn(ctx)`` with the run's build context.

``at``, ``until`` and ``every`` may also read a parameter: ``at:
$market.open`` or ``every: $market.period_s`` in parameter files, and with
:meth:`Scenario.event <mango.scenario.Scenario.event>` also a bare
parameter path or ``fn(p)``, so a study factor or an override moves the
event (see `Structure as a study factor`_).

Here the batteries halve their charging power on ``curtail``, and the
aggregator counts the reports it receives:

.. testcode:: timeline

    class CurtailableBattery(Battery):
        def on_global_event(self, event):
            if event.kind == "curtail":
                self.charge_kw *= event["factor"]


    class CountingAggregator(Aggregator):
        received = 0

        def handle_message(self, content, meta):
            super().handle_message(content, meta)
            self.received += 1


    grid = Scenario("grid", params=SolarParams)
    hub = grid.group("aggregator", 1, lambda e: CountingAggregator())
    batteries = grid.group("battery", "n", lambda e: CurtailableBattery(20.0, 2.0, 2.0))
    grid.link("reports", batteries, hub, pattern="many_to_one")
    grid.record("soc_kwh", batteries, attr="soc_kwh")
    grid.record_world("received", lambda ctx: ctx.agents("aggregator")[0].received)
    print(grid.plan("presets/outage.yaml"))

.. testoutput:: timeline

    Scenario 'grid': 4 agents in 2 groups, 1 link, 2 recorders
      time: horizon 8h, step 1h (8 steps), start 2024-06-01T00:00:00
      groups:
        aggregator            1  aggregator
        battery               3  battery_0 .. battery_2
      links:
        reports          battery -> aggregator (many_to_one); 3 edges, undirected
      recorders:
        soc_kwh          battery
        received         world
      phases:
        warmup           2024-06-01T00:00 .. 2024-06-01T02:00  (not recorded)
        main             2024-06-01T02:00 .. 2024-06-01T08:00
      events:
        outage           2024-06-01T03:00 .. 2024-06-01T05:00  (comm loss_percent=1)
        curtail          2024-06-01T04:00  (emit {'kind': 'curtail', 'factor': 0.5} to all agents (on_global_event))

.. testcode:: timeline

    outage = grid.run("presets/outage.yaml", seed=1)
    print(outage.frame.pivot_table(
        "value", ["datetime", "phase"], "variable", "mean", observed=True
    ))

.. testoutput:: timeline
   :options: +NORMALIZE_WHITESPACE

    variable                   received  soc_kwh
    datetime            phase
    2024-06-01 03:00:00 main        6.0      8.0
    2024-06-01 04:00:00 main        6.0     10.0
    2024-06-01 05:00:00 main        9.0     11.0
    2024-06-01 06:00:00 main       12.0     12.0
    2024-06-01 07:00:00 main       15.0     13.0
    2024-06-01 08:00:00 main       18.0     14.0

The warm-up is not recorded (the row at 02:00 is the state after the last
warm-up step, so the first recorded row is 03:00), no report arrives during
the outage, and from 04:00 on the batteries charge with half the power.

Events in Python
----------------

``scenario.event(at, *, until=None, name=None, comm=None, emit=None,
emit_to=None, action=None)`` declares an event that belongs to the
scenario's structure rather than to its parameters.  Its ``emit`` may be any
object and its ``action`` any callable, lambdas included; without ``comm``,
``emit`` and ``action`` it is a decorator:

.. testcode:: timeline

    @grid.event("2024-06-01T06:00")
    def reset_reports(ctx):
        ctx.agents("aggregator")[0].reports.clear()
        print("cleared at", ctx.world.clock.datetime)


    after = grid.run("presets/outage.yaml", seed=1)

.. testoutput:: timeline

    cleared at 2024-06-01 06:00:00

Events in the parameters are plain data (``emit`` must be YAML/JSON data,
``action`` an import reference) because they are part of the run spec; they
are factors like any other parameter.  Address named events and phases by
name (``uncertainties={"events.outage.at": ["2024-06-01T03:00",
"2024-06-01T06:00"]}``, ``--set phases.warmup.until=...``), so reordering a
preset cannot move a factor to another event; indices (``events.0.at``)
work as well.  An override may change the keys of an ``emit`` payload but not
add new ones (``events.curtail.emit.factr=0.2`` is an error with a
suggestion; set the payload as a whole to add keys).  ``every: 1d`` (and an
optional ``count``) repeats an event; ``plan()`` lists every occurrence.
Events at the same time apply scenario events first, then parameter events,
each in declaration order.  A trigger that depends on the state (a lockdown
once prevalence exceeds a threshold) is a behavior in a kit that checks the
state in ``on_step`` and acts (``ctx.add_behavior``); to end the run on a
condition use ``stop_when``.

Timing
------

With a fixed step, a time point between two step boundaries applies at the
first boundary at or after it, and :meth:`~mango.scenario.Scenario.plan`
warns about it; event-driven runs (``time.step: event``) stop exactly at
every phase and event boundary.  ``plan()`` checks phases and events against
the time settings: phases must be in order and end by the horizon,
events must start before it, absolute times need ``time.start``, and every
combination of overlapping ``comm`` overrides must be valid.

.. testcleanup:: timeline

    os.chdir(_timeline_cwd)
    _timeline_tmp.cleanup()

----

.. _scenario-designs:

Sampling designs and sensitivity analysis
=========================================

A full factorial design needs levels.  To explore continuous ranges, or to
ask which parameter the outcomes are sensitive to, a study samples its
factors instead.  Give the factors as ranges and choose a sampling design:

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Design
     - Samples
   * - ``Lhs(n, scramble=True)``
     - *n* points of a Latin hypercube (``scipy.stats.qmc``): every factor
       range is cut into *n* strata and each stratum is used once
   * - ``RandomSample(n)``
     - *n* independent uniform points (needs no scipy)
   * - ``Sobol(n, second_order=False, scramble=True)``
     - a Saltelli design from a scrambled Sobol' sequence for Sobol
       sensitivity indices: ``n * (d + 2)`` points for *d* sampled factors

Ranges are ``{"range": [lo, hi]}`` (floats), ``{"int_range": [lo, hi]}``
(integers within the bounds) and ``{"log_range": [lo, hi]}`` (uniform in the
logarithm).  Factors with several levels are crossed with the samples, fixed
factors stay fixed, and every sample is drawn from the study seed, so a
study plans the same points every time:

.. testsetup:: designs

    import os
    import tempfile

    from pydantic import BaseModel

    from mango import Agent
    from mango.scenario import Scenario, ScenarioParams, Uniform

    _designs_cwd = os.getcwd()
    _designs_tmp = tempfile.TemporaryDirectory()
    os.chdir(_designs_tmp.name)


    class Battery(Agent):
        def __init__(self, capacity_kwh: float, soc_kwh: float, charge_kw: float):
            super().__init__()
            self.capacity_kwh = capacity_kwh
            self.soc_kwh = min(soc_kwh, capacity_kwh)
            self.charge_kw = charge_kw

        def on_step(self, env, clock, step_size_s):
            energy_kwh = self.charge_kw * step_size_s / 3600
            self.soc_kwh = min(self.capacity_kwh, self.soc_kwh + energy_kwh)


    class BatteryParams(BaseModel, extra="forbid"):
        n: int = 3
        capacity_kwh: float = 10.0
        soc_kwh: Uniform = Uniform(2.0, 6.0)
        charge_kw: float = 1.5


    class Params(ScenarioParams):
        battery: BatteryParams = BatteryParams()


    def make_battery(e):
        p = e.p.battery
        return Battery(p.capacity_kwh, p.soc_kwh.draw(e.rng), p.charge_kw)


    scenario = Scenario("batteries", params=Params)
    scenario.time(horizon="4h", step="1h")
    scenario.group("battery", "battery.n", make_battery)
    scenario.record("soc_kwh", "battery", attr="soc_kwh")

.. testcode:: designs

    from mango.scenario import Lhs, Study

    lhs = Study(
        scenario,
        uncertainties={
            "battery.charge_kw": {"range": [0.5, 3.0]},
            "battery.n": {"int_range": [2, 6]},
        },
        levers={"battery.capacity_kwh": [8.0, 12.0]},
        design=Lhs(4),
        seed=5,
    )
    plan = lhs.plan()
    print(plan.to_frame().drop(columns=["run_id", "index", "point"]).round(3))
    print(plan[0]["seed"] == plan[1]["seed"], plan[0]["seed"] == plan[2]["seed"])

.. testoutput:: designs
   :options: +NORMALIZE_WHITESPACE

       replication  sample  battery.charge_kw  battery.n  battery.capacity_kwh
    0            0       0              1.866          2                   8.0
    1            0       0              1.866          2                  12.0
    2            0       1              2.448          5                   8.0
    3            0       1              2.448          5                  12.0
    4            0       2              0.639          5                   8.0
    5            0       2              0.639          5                  12.0
    6            0       3              1.538          4                   8.0
    7            0       3              1.538          4                  12.0
    True False

Four Latin hypercube samples of the two ranges, each run with both
capacities.  A sampling design defaults to ``common_random_numbers="levers"``
(see :ref:`scenario-crn`): the two capacities of a sample share their seed,
so they are compared on the same random draws, while every sample gets a
seed of its own and stands for an independent draw of the uncertainties.

Sobol indices
-------------

Sobol indices split the variance of an outcome among the factors: the
first-order index ``S1`` of a factor is the share it explains alone, the
total index ``ST`` includes all its interactions with other factors.  A
study with the :class:`~mango.scenario.Sobol` design runs the Saltelli
points (every run spec records its block, sample and swapped factor in
``design``), and ``StudyFrame.sobol_indices()`` estimates the indices
from the stored outcomes with ``scipy.stats.sobol_indices``:

.. testcode:: designs

    from mango.scenario import Sobol, load_study


    def final_soc(run):
        return float(run.wide("soc_kwh").iloc[-1].mean())


    sobol = Study(
        scenario,
        uncertainties={
            "battery.charge_kw": {"range": [0.5, 3.0]},
            "battery.capacity_kwh": {"range": [6.0, 14.0]},
        },
        design=Sobol(8),
        seed=2026,
        outcomes=[final_soc],
        out="results/sobol",
    )
    plan = sobol.plan()
    print(len(plan), plan[1]["design"])
    report = sobol.run(progress=False)
    print(load_study("results/sobol").sobol_indices().round(2))

.. testoutput:: designs
   :options: +NORMALIZE_WHITESPACE

    32 {'block': 'AB', 'column': 0, 'sample': 0, 'factor': 'battery.charge_kw'}
         outcome                factor    S1  S1_conf    ST  ST_conf
    0  final_soc     battery.charge_kw  ...      ...   ...      ...
    1  final_soc  battery.capacity_kwh  ...      ...   ...      ...

Eight base samples are far too few for reliable indices; real studies use
a few hundred (``n`` is rounded up to a power of two with a warning).
``S1_conf`` and ``ST_conf`` say so: they are the half-widths of 95 %
bootstrap intervals (``S1 ± S1_conf``; percentile intervals from resampling
the base samples with a generator seeded by the study seed, so they are
reproducible), and here they are far wider than the indices.  The outcome
is centred first (as in scipy), so adding a constant to it changes nothing;
the bootstrap treats the base samples as independent draws, so for a
scrambled Sobol' design the intervals are conservative.  A
``UserWarning`` names designs with fewer than 64 base samples, estimates
outside ``[0, 1]`` by more than 0.05 (which only Monte Carlo error
produces) and outcomes that are constant over the design (their indices
are NaN: there is no variance to apportion).  The analysis is
flexible:

* ``sobol_indices(outcomes)`` takes one outcome name, a list, or ``None``
  for every numeric outcome;
* replications are averaged per design point first, and only the rows of the
  frame are used, so ``frame[frame.replication < 2].sobol_indices()``
  analyses the first two replications;
* factors with several levels (crossed with the samples) give one set of
  indices per level combination, in extra level columns;
* ``Sobol(n, second_order=True)`` adds the ``BA`` blocks (``n * (2d + 2)``
  points) and ``sobol_indices(second_order=True)`` returns the second-order
  indices ``S2`` of every factor pair; this needs SALib;
* ``sobol_indices(confidence=0.9, n_resamples=2000)`` changes the intervals,
  ``confidence=None`` skips the bootstrap;
* a Sobol design keeps ``common_random_numbers=True`` by default: every point
  of replication *r* shares one seed, so the estimators compare matched
  points on the same noise.  The indices then describe the replication
  average, and the bootstrap intervals cover the sampling of the factor
  space, not the noise: compare the indices of single replications
  (``frame[frame.replication == r]``) to see how much the noise moves them.

An incomplete design (failed or pending runs, a missing outcome value)
raises a :class:`~mango.scenario.StudyError` instead of returning indices
from a subset.

.. testcleanup:: designs

    os.chdir(_designs_cwd)
    _designs_tmp.cleanup()

----

.. _scenario-files:

Scenario and study files
========================

Scenarios and studies can also be written as YAML files: for archiving and
diffing, for colleagues who would rather not write Python, and for driving
everything from the :ref:`command line <scenario-cli>`.  The files are a
front-end, never a superset: every key of a scenario file compiles to one
call of the Python API above, and functions are import references
``"package.module:name"``.  The agents, factories and outcomes stay Python
code in an importable module.  This section uses the running example as a
module ``batteries.py`` in the project folder:

.. testsetup:: files

    import os
    import sys
    import tempfile

    _files_cwd = os.getcwd()
    _files_tmp = tempfile.TemporaryDirectory()
    os.chdir(_files_tmp.name)
    sys.path.insert(0, _files_tmp.name)
    for folder in ("presets", "scenarios", "studies"):
        os.makedirs(folder)
    _FILES = {
        "batteries.py": '''from pydantic import BaseModel

    from mango import Agent
    from mango.scenario import ScenarioParams, Uniform


    class Battery(Agent):
        def __init__(self, capacity_kwh: float, soc_kwh: float, charge_kw: float):
            super().__init__()
            if soc_kwh > capacity_kwh:
                raise ValueError(f"soc_kwh {soc_kwh:.2f} exceeds capacity_kwh {capacity_kwh}")
            self.capacity_kwh = capacity_kwh
            self.soc_kwh = soc_kwh
            self.charge_kw = charge_kw

        def on_step(self, env, clock, step_size_s):
            energy_kwh = self.charge_kw * step_size_s / 3600
            self.soc_kwh = min(self.capacity_kwh, self.soc_kwh + energy_kwh)
            for aggregator in self.neighbors(tid="reports"):
                self.schedule_instant_message(self.soc_kwh, aggregator)


    class Aggregator(Agent):
        def __init__(self):
            super().__init__()
            self.reports = {}

        def handle_message(self, content, meta):
            self.reports[meta["sender_id"]] = content


    class BatteryParams(BaseModel, extra="forbid"):
        n: int = 3
        capacity_kwh: float = 10.0
        soc_kwh: Uniform = Uniform(2.0, 6.0)
        charge_kw: float = 1.5


    class Params(ScenarioParams):
        battery: BatteryParams = BatteryParams()


    def make_battery(e):
        p = e.p.battery
        return Battery(p.capacity_kwh, p.soc_kwh.draw(e.rng), p.charge_kw)


    def make_aggregator(e):
        return Aggregator()


    def final_soc(run):
        return float(run.wide("soc_kwh").iloc[-1].mean())
    ''',
        "presets/base.yaml": (
            "time: {horizon: 6h, step: 30min}\n"
            "battery:\n"
            "  n: 5\n"
            "  soc_kwh: {uniform: [1.0, 4.0]}\n"
        ),
        "scenarios/batteries.yaml": (
            "params: batteries:Params\n"
            "time: {horizon: 4h, step: 1h}\n"
            "groups:\n"
            "  aggregator: {factory: batteries:make_aggregator, count: 1}\n"
            "  battery: {factory: batteries:make_battery, count: battery.n}\n"
            "links:\n"
            "  - {name: reports, from: battery, to: aggregator, pattern: many_to_one}\n"
            "record:\n"
            "  - {name: soc_kwh, group: battery, attr: soc_kwh}\n"
        ),
        "opinions.py": (
            "from mango import Agent\n"
            "\n"
            "\n"
            "class Person(Agent):\n"
            "    def __init__(self, opinion, epsilon):\n"
            "        super().__init__()\n"
            "        self.opinion, self.epsilon = opinion, epsilon\n"
            "\n"
            "    def on_step(self, env, clock, step_size_s):\n"
            "        self.opinion = min(1.0, self.opinion + self.epsilon)\n"
            "\n"
            "\n"
            "def make_person(e):\n"
            "    return Person(e.draw(e.p.initial), e.p.epsilon)\n"
        ),
        "scenarios/opinions.yaml": (
            "parameters:\n"
            "  n: 100\n"
            "  epsilon: {default: 0.2, min: 0, max: 1, help: confidence bound}\n"
            "  initial: {uniform: [0, 1]}\n"
            "  net: {k: 4, rewire: 0.1}\n"
            "time: {horizon: 50, step: 1}\n"
            "groups:\n"
            "  person: {factory: opinions:make_person, count: n}\n"
            "links:\n"
            "  - {name: friends, from: person, pattern: {small_world: {k: $net.k, p: $net.rewire}}}\n"
            "record:\n"
            "  - {name: opinion, group: person, attr: opinion}\n"
        ),
        "scenarios/lte.yaml": (
            "extends: scenarios/batteries.yaml\n"
            "components:\n"
            "  communication: {kind: communication_preset, preset: lte}\n"
            "  spares: {kind: agent_population, factory: batteries:make_battery, count: 2}\n"
            "battery: {n: 4}\n"
        ),
        "studies/charging.yaml": (
            "scenario: scenarios/batteries.yaml\n"
            "base: presets/base.yaml\n"
            "seed: 2026\n"
            "uncertainties:\n"
            "  battery.soc_kwh:\n"
            "    empty: {uniform: [0.0, 2.0]}\n"
            "    half: {uniform: [4.0, 6.0]}\n"
            "levers:\n"
            "  battery.charge_kw: [1.0, 2.0]\n"
            "replications: 2\n"
            "outcomes: [batteries:final_soc]\n"
            "out: results/charging\n"
        ),
        "studies/sensitivity.yaml": (
            "extends: studies/charging.yaml\n"
            "uncertainties:\n"
            "  battery.soc_kwh: {value: {uniform: [2.0, 4.0]}}\n"
            "  battery.capacity_kwh: {range: [6.0, 14.0]}\n"
            "levers:\n"
            "  battery.charge_kw: {range: [0.5, 3.0]}\n"
            "design: {sobol: {n: 8}}\n"
            "replications: 1\n"
            "out: results/sensitivity\n"
        ),
    }
    for _name, _text in _FILES.items():
        with open(_name, "w") as file:
            file.write(_text)

.. code-block:: python
   :caption: batteries.py

    from pydantic import BaseModel

    from mango import Agent
    from mango.scenario import ScenarioParams, Uniform


    class Battery(Agent):
        def __init__(self, capacity_kwh: float, soc_kwh: float, charge_kw: float):
            super().__init__()
            if soc_kwh > capacity_kwh:
                raise ValueError(f"soc_kwh {soc_kwh:.2f} exceeds capacity_kwh {capacity_kwh}")
            self.capacity_kwh = capacity_kwh
            self.soc_kwh = soc_kwh
            self.charge_kw = charge_kw

        def on_step(self, env, clock, step_size_s):
            energy_kwh = self.charge_kw * step_size_s / 3600
            self.soc_kwh = min(self.capacity_kwh, self.soc_kwh + energy_kwh)
            for aggregator in self.neighbors(tid="reports"):
                self.schedule_instant_message(self.soc_kwh, aggregator)


    class Aggregator(Agent):
        def __init__(self):
            super().__init__()
            self.reports = {}

        def handle_message(self, content, meta):
            self.reports[meta["sender_id"]] = content


    class BatteryParams(BaseModel, extra="forbid"):
        n: int = 3
        capacity_kwh: float = 10.0
        soc_kwh: Uniform = Uniform(2.0, 6.0)
        charge_kw: float = 1.5


    class Params(ScenarioParams):
        battery: BatteryParams = BatteryParams()


    def make_battery(e):
        p = e.p.battery
        return Battery(p.capacity_kwh, p.soc_kwh.draw(e.rng), p.charge_kw)


    def make_aggregator(e):
        return Aggregator()


    def final_soc(run):
        return float(run.wide("soc_kwh").iloc[-1].mean())

Scenario files
--------------

The scenario of the first section, as a file:

.. code-block:: yaml
   :caption: scenarios/batteries.yaml

    params: batteries:Params
    time: {horizon: 4h, step: 1h}
    groups:
      aggregator: {factory: batteries:make_aggregator, count: 1}
      battery: {factory: batteries:make_battery, count: battery.n}
    links:
      - {name: reports, from: battery, to: aggregator, pattern: many_to_one}
    record:
      - {name: soc_kwh, group: battery, attr: soc_kwh}

:func:`~mango.scenario.load_scenario` compiles it to a
:class:`~mango.scenario.Scenario` (a ``FileScenario`` that also knows its
``path`` and its resolved ``document``).  Entity names, random streams and
therefore the numbers are those of the Python version:

.. testcode:: files

    from mango.scenario import load_scenario

    scenario = load_scenario("scenarios/batteries.yaml")
    print(scenario.name, scenario.ref)
    result = scenario.run(seed=42)
    print(result.wide("soc_kwh").round(2))

.. testoutput:: files
   :options: +NORMALIZE_WHITESPACE

    batteries scenarios/batteries.yaml
    entity   battery_0  battery_1  battery_2
    time
    0.0           5.26       3.06       4.75
    3600.0        6.76       4.56       6.25
    7200.0        8.26       6.06       7.75
    10800.0       9.76       7.56       9.25
    14400.0      10.00       9.06      10.00

Every key maps to one Python call:

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Key
     - Python
   * - ``name``
     - ``Scenario(name, ...)``; default: the file name without suffix
   * - ``params: module:Params``
     - ``Scenario(..., params=Params)`` (default ``ScenarioParams``)
   * - ``parameters: {epsilon: 0.2, net: {k: 4}}``
     - declares parameters by their defaults: compiled to a subclass of the
       ``params`` model (see below)
   * - any parameter field, e.g. ``seed``, ``time``, ``comm``, ``profiles``,
       ``phases``, ``events``, ``battery``
     - ``scenario.defaults({...})``: the file's parameter defaults, below
       presets and overrides
   * - ``defaults: {battery.n: 4}``
     - ``scenario.defaults(...)`` with dotted keys (also for parameter fields
       named like a file key)
   * - ``environment``, ``communication``
     - ``scenario.environment(fn)``, ``scenario.communication(fn)``
   * - ``components: {prefix: {kind: ..., **kwargs}}``
     - ``scenario.use(Kit(**kwargs), prefix=prefix)``; ``kind`` is a
       registered kind or ``"module:Class"``
   * - ``groups: {name: {factory, count, install, position, naming}}``
     - ``scenario.group(name, count, factory, install=..., position=...,
       naming=...)``; ``count`` is an int, a parameter path or a function
       reference, ``naming`` a format string or a function reference
   * - ``links: [{name, from, to, pattern, where, directed, delay, loss}]``
     - ``scenario.link(name, from, to, pattern=..., where=...,
       directed=..., delay=..., loss=...)``; ``delay`` in seconds, a
       duration or a distribution (``{choice: [0, 1w, 2w]}``), ``delay``
       and ``loss`` also ``$path``; patterns as data (``{random: {per_target: [1,
       2]}}``, ``{by_attr: bus}``, ``{small_world: {k: $net.k, p: 0.1}}``,
       ``{edges: {file: data/net.csv}}``, ``{reverse: line}``), a parameter
       path or ``{param: net.topology}``, a function reference
       ``module:fn`` or a factory reference with arguments
       ``{"module:factory": {...}}``; an edge-list file is found relative to
       the scenario file, too
   * - ``record: [{name, group, attr}]`` or ``[{name, group, fn}]``
     - ``scenario.record(name, group, fn, attr=..., every=...)``; ``group``
       may be a list of groups, ``every`` a duration or a parameter path;
       also ``kind`` (``state``/``flow``), ``window``, ``aggregate`` and
       ``categories``
   * - ``record: [{name, fn, entity}]``
     - ``scenario.record_world(name, fn, every=..., entity=...)``; also
       ``kind`` and ``window``
   * - ``record: [{name, resource: transformer.load_kw}]``
     - ``scenario.record_world(name, lambda ctx:
       ctx.resource("transformer").load_kw)``; the first part names a
       provided resource, the rest is an attribute path
   * - ``on_build: [module:fn, ...]``
     - ``scenario.on_build(fn)``
   * - ``extends: parent.yaml``
     - merges the parent file first (see below)

.. _scenario-file-parameters:

**Parameters without Python.**  A scenario file sets values of the
parameters its model declares; an unknown top-level key is an error that
says how to declare it.  New parameters are declared in ``parameters`` by
their defaults.  Each type is inferred from the default (``bool``, ``int``,
``float``, ``str``, lists of these, a distribution shorthand such as
``{normal: [0, 0.1]}``); a mapping becomes a nested group of parameters, an
empty mapping a free ``dict``.  The block compiles to a pydantic subclass of
the ``params`` model (``ScenarioParams`` by default) that forbids unknown
keys, so presets, overrides and study factors are type-checked like those of
a Python model.  An ``int`` default accepts only ints (write ``1.0`` for a
float).  A mapping with a ``default`` key is the long form of one
parameter, with the keys ``default``, ``min``/``max`` (or ``gt``/``lt``),
``choices``, ``help`` and ``type``: ``epsilon: {default: 0.25, min: 0, max:
1, help: confidence bound}`` checks the range, ``interaction: {default:
pairwise, choices: [pairwise, messages]}`` rejects a typo such as
``pairwse`` instead of silently running the other branch, ``help`` becomes
the field description (shown by ``validate`` errors and the parameter
form), and ``type: pattern`` declares a link pattern parameter (``str |
dict``) that holds a name or a one-key spec: ``topology: {default: ring,
type: pattern}`` used as ``pattern: {param: topology}`` takes ``-s
'topology={small_world: {k: 4, p: 0.1}}'`` and study levels such as ``[ring,
{random: {p: 0.2}}]`` (one-key specs replace each other across presets,
overrides and levels instead of merging).  Without ``default`` the mapping
stays a group (``{help: ..., min: 0}`` would declare the parameters ``help``
and ``min``; loading warns about it), and a group may hold a long-form field
named ``kind`` (``net: {kind: {default: small_world, choices: [small_world,
random]}, k: 6}``).  Anything beyond this (unions, validators) needs a
Python model:

.. code-block:: yaml
   :caption: scenarios/opinions.yaml

    parameters:
      n: 100
      epsilon: {default: 0.2, min: 0, max: 1, help: confidence bound}
      initial: {uniform: [0, 1]}
      net: {k: 4, rewire: 0.1}
    time: {horizon: 50, step: 1}
    groups:
      person: {factory: opinions:make_person, count: n}
    links:
      - {name: friends, from: person, pattern: {small_world: {k: $net.k, p: $net.rewire}}}
    record:
      - {name: opinion, group: person, attr: opinion}

The factory reads ``e.p.epsilon`` and ``e.draw(e.p.initial)``; overrides
and presets are checked against the declared types:

.. testcode:: files

    from mango.scenario import ParamsError

    opinions = load_scenario("scenarios/opinions.yaml")
    plan = opinions.plan(overrides=["n=20", "epsilon=0.01"])
    print(plan.params.epsilon, plan.groups[0].count, plan.links[0].declarations[0]["pattern"])
    for override in ["epsilon=high", "epsilon=1.5", "epsilonn=0.3"]:
        try:
            opinions.plan(overrides=[override])
        except ParamsError as error:
            print(str(error).splitlines()[-1].strip())

.. testoutput:: files

    0.01 20 small_world(k=4, p=0.1)
    epsilon: Input should be a valid number, unable to parse string as a number (got 'high') [from overrides]
    epsilon: Input should be less than or equal to 1 (got 1.5); epsilon: confidence bound [from overrides]
    epsilonn: unknown parameter; OpinionsParameters has no field 'epsilonn'; did you mean 'epsilon'?

Components (kits) are used before the other declarations.  A factory must
be a function ``fn(e)``; to make a group straight from an agent class, use
the built-in kit ``agent_population``.  ``extends`` merges a parent file
like a preset: nested mappings merge, ``"+key"`` appends to a list and
``"-key"`` deletes a key.  This variant adds communication delays and two
spare batteries:

.. code-block:: yaml
   :caption: scenarios/lte.yaml

    extends: scenarios/batteries.yaml
    components:
      communication: {kind: communication_preset, preset: lte}
      spares: {kind: agent_population, factory: batteries:make_battery, count: 2}
    battery: {n: 4}

.. testcode:: files

    print(load_scenario("scenarios/lte.yaml").plan())

.. testoutput:: files

    Scenario 'lte': 7 agents in 3 groups, 1 link, 1 recorder
      time: horizon 4h, step 1h (4 steps)
      communication: simple, loss 0, delay Normal(0.04, 0.008, low=0.0)
      kits:
        communication    CommunicationPreset (mango.scenario.kits:CommunicationPreset)
        spares           AgentPopulation (mango.scenario.kits:AgentPopulation)
      groups:
        spares.agent          2  spares.agent_0 .. spares.agent_1
        aggregator            1  aggregator
        battery               4  battery_0 .. battery_3
      links:
        reports          battery -> aggregator (many_to_one); 4 edges, undirected
      recorders:
        soc_kwh          battery

Format errors, unknown keys and invalid parameter defaults are reported with
the file (and, through ``extends``, the parent file) they come from, with
suggestions for typos; import errors name the key, e.g.
``scenarios/batteries.yaml: groups.battery.factory: ...``.

Study files
-----------

A study file holds the arguments of :class:`~mango.scenario.Study`; its
``scenario`` is a scenario file or an import reference.  This one is the
study of the :ref:`scenario-studies` section:

.. code-block:: yaml
   :caption: studies/charging.yaml

    scenario: scenarios/batteries.yaml
    base: presets/base.yaml
    seed: 2026
    uncertainties:
      battery.soc_kwh:
        empty: {uniform: [0.0, 2.0]}
        half: {uniform: [4.0, 6.0]}
    levers:
      battery.charge_kw: [1.0, 2.0]
    replications: 2
    outcomes: [batteries:final_soc]
    out: results/charging

The keys are ``name`` (default: the file name), ``scenario``, ``base``,
``overrides``, ``seed``, ``uncertainties``, ``levers``, ``design``
(``full_factorial``, ``{lhs: {n: 64}}``, ``{sobol: {n: 256, second_order:
false}}``, ``{random: {n: 100}}``, ``{csv: designs.csv}`` or ``{explicit:
[...]}``), ``replications``, ``common_random_numbers`` (``true``,
``false`` or ``levers``, see :ref:`scenario-crn`), ``outcomes`` (a list
of references or ``{name: reference}``), ``subexperiments``, ``out``,
``format`` and ``extends``.  :func:`~mango.scenario.load_study_file` (or
``Study.from_yaml``) returns the study; keyword arguments replace values of
the file:

.. testcode:: files

    from mango.scenario import Study

    study = Study.from_yaml("studies/charging.yaml")
    print(study.plan())

.. testoutput:: files

    Study 'charging': 8 runs
      scenario      scenarios/batteries.yaml
      design        full factorial, 4 design points x 2 replications
      seeds         seed 2026, common random numbers: 8 runs on 2 seeds
      uncertainties battery.soc_kwh: empty={"uniform": [0.0, 2.0]} | half={"uniform": [4.0, 6.0]}
      levers        battery.charge_kw: 1.0 | 2.0
      outcomes      final_soc
      out           .../results/charging

Relative paths in study files (``scenario``, ``base``, ``design: {csv:
...}``) are looked up relative to the working directory first, then
relative to the file that sets them; ``out`` is relative to the working
directory.  Run ids name a scenario file by its path, like a module
reference; its content enters the scenario fingerprint, so a resumed study
warns about finished runs that were made with an earlier version of the
file, and a worker refuses to run a file that changed after the study was
planned.

Extending a study
~~~~~~~~~~~~~~~~~

A follow-up study extends the first one and states only what changes.
``uncertainties`` and ``levers`` merge per factor path: a new spec replaces
the parent's spec of that factor, a single value fixes it, and ``"-path"``
drops it.  ``overrides`` merge per dotted path, every other key (including
``design``) is replaced, and ``name`` is never inherited.  Neither is
``out``: without its own, a follow-up writes to a sibling of the parent's
folder named after itself (``results/sensitivity`` next to
``results/charging``), and a study refuses to run in a folder that holds a
study of another name.  This sensitivity study keeps the scenario, base, seed and outcomes, and samples
two factors with a Sobol design:

.. code-block:: yaml
   :caption: studies/sensitivity.yaml

    extends: studies/charging.yaml
    uncertainties:
      battery.soc_kwh: {value: {uniform: [2.0, 4.0]}}
      battery.capacity_kwh: {range: [6.0, 14.0]}
    levers:
      battery.charge_kw: {range: [0.5, 3.0]}
    design: {sobol: {n: 8}}
    replications: 1
    out: results/sensitivity

.. testcode:: files

    from mango.scenario.yaml_scenario import read_study_file

    resolved = read_study_file("studies/sensitivity.yaml")
    print(resolved["name"], resolved["scenario"], resolved["outcomes"])
    print(resolved["uncertainties"])
    print(resolved["levers"], resolved["design"])

.. testoutput:: files

    sensitivity scenarios/batteries.yaml ['batteries:final_soc']
    {'battery.soc_kwh': {'value': {'uniform': [2.0, 4.0]}}, 'battery.capacity_kwh': {'range': [6.0, 14.0]}}
    {'battery.charge_kw': {'range': [0.5, 3.0]}} {'sobol': {'n': 8}}

Editor support
~~~~~~~~~~~~~~

:func:`~mango.scenario.params_schema` (or ``mango-scenario schema``) gives
the JSON Schema of a scenario's parameters for presets;
:func:`~mango.scenario.scenario_file_schema` and
:func:`~mango.scenario.study_file_schema` describe the two file formats.
Editors with a YAML language server use them for completion and checks,
e.g. with a first line ``# yaml-language-server:
$schema=batteries.schema.json`` in a preset:

.. testcode:: files

    from mango.scenario import scenario_file_schema, study_file_schema

    schema = scenario_file_schema(scenario)
    print(sorted(schema["properties"])[:6], schema["additionalProperties"])
    print(sorted(study_file_schema()["properties"])[:5])

.. testoutput:: files

    ['battery', 'comm', 'communication', 'components', 'defaults', 'environment'] False
    ['base', 'common_random_numbers', 'design', 'executor', 'extends']

----

.. _scenario-cli:

The mango-scenario command line
===============================

Installing mango with the ``scenario`` extra installs the command
``mango-scenario`` (also ``python -m mango.scenario``).  Run it from the
project folder: the working directory is put on ``sys.path``, so the import
references of your files resolve.  Targets are study files, scenario files
or import references ``module:attr`` to a scenario.  Errors are printed
without a traceback (``--debug`` shows it); the exit code is 0 on success, 1
on failures (invalid input, failed runs, a replay that differs), 2 on wrong
usage and 130 when interrupted.

``validate`` checks a study, scenario or preset file, or a scenario reference
``module:attr``, before anything runs.  For a study it builds the parameters
of every run and checks that the scenario and outcome references import; a
preset needs the scenario it is meant for.  It also loads every profile and
:class:`~mango.scenario.FromColumn` table the parameters name, once per
distinct table, and reports every missing file, missing column (with a
did-you-mean suggestion), unparsable time column or misplaced local time at
once, with the runs it concerns (exit code 1).  Columns that a factory picks
at run time (``e.profile("load", key=f"zone_{i}")``), recorder attributes
and errors in the factories show only when agents are built: ``validate
--build`` builds run 0, takes one step, records once and evaluates every
recorder once more (also those a ``record: false`` warm-up phase skips),
which is worth it before a cluster submission:

.. code-block:: console

    $ mango-scenario validate studies/charging.yaml
    OK: studies/charging.yaml is a valid study: 8 runs (6 agents each, 1 link), scenario scenarios/batteries.yaml; parameters, declarations and data files of 4 distinct parameter sets checked
    note: agents were not built; --build builds run 0 and takes one step (checks factories, profile columns picked at run time and recorder attributes)
    $ mango-scenario validate scenarios/batteries.yaml --set battery.n=3 --build
    OK: scenarios/batteries.yaml is a valid scenario 'batteries': 4 agents in 2 groups, 1 link, 1 recorder; run built (4 agents), one step taken and ... values recorded; 1 recorder evaluated (... values)
    $ mango-scenario validate presets/base.yaml --scenario scenarios/batteries.yaml
    OK: presets/base.yaml is a valid preset for scenarios/batteries.yaml: 6 agents, 1 link
    $ mango-scenario validate scenarios/batteries.yaml --set battery.capactiy_kwh=12
    error: Invalid overrides for Params:
      battery.capactiy_kwh: unknown parameter; BatteryParams has no field 'capactiy_kwh'; did you mean 'battery.capacity_kwh'?

``plan`` shows what would run: for a study the number of runs and their
size, then the study plan (``--json`` prints the run table as JSON); for a
scenario its plan, with presets (``-p``) and overrides (``-s``):

.. code-block:: console

    $ mango-scenario plan studies/charging.yaml
    8 runs, 6 agents each, 1 link
    Study 'charging': 8 runs
    ...
    $ mango-scenario plan scenarios/batteries.yaml -p presets/base.yaml -s battery.n=2
    Scenario 'batteries': 3 agents in 2 groups, 1 link, 1 recorder
      time: horizon 6h, step 30min (12 steps)
      groups:
        aggregator            1  aggregator
        battery               2  battery_0 .. battery_1
      links:
        reports          battery -> aggregator (many_to_one); 2 edges, undirected
      recorders:
        soc_kwh          battery

``run`` runs a study (``--workers N`` on a process pool, which reuses
workers for short runs unless ``--runs-per-worker 1``, ``--executor slurm``
on a cluster, ``-q`` without progress lines, ``--require-clean`` to refuse
uncommitted changes); finished runs are skipped, stale ones (their code or
data files changed) run again unless ``--keep-stale``, and failed ones are
retried unless ``--no-retry-failed``.  Warnings, such as the stale summary,
are printed as they arise, before the first run starts.  The study file's
``executor:`` block sets defaults for these options (``executor: {workers:
8, runs_per_worker: 20, slurm: {partition: short}}``); flags replace them.
``status`` counts the runs of a study folder, also while a pool or a
cluster works on it, and shows stale runs, runs with outcome errors or
warnings, wall and CPU time, the git commits of the done runs, earlier
plans and, planning the stored ``study.yaml`` with today's code, how many of
the planned runs are on disk; it rewrites the ``status.csv`` snapshot.
``certify`` marks stale runs as current with a reason (``mango-scenario
certify results/charging --reason "renamed a helper"``, see `Resuming`_):

.. code-block:: console

    $ mango-scenario run studies/charging.yaml -q
    8 runs: 8 done, 0 skipped, 0 failed in ... s (sequential) -> .../results/charging
    $ mango-scenario status results/charging
    charging: 8 runs: 8 done, 0 failed, 0 pending
      time: ... s wall, ... s CPU over 8 finished runs (... s CPU per run on average)

``--set`` on a study adds to the study's overrides (and warns when it
changes a factor), so ``mango-scenario run studies/charging.yaml -s
battery.n=10`` runs a variant with new run ids.  Its run folders go next to
the old ones, but ``study.yaml`` and ``runs.jsonl``, and so ``load_study``
and ``status``, describe the latest plan only.  The previous plan is kept in
``plans/<plan id>/`` and a warning says how many finished runs it leaves
out; ``load_study(path, plan="all")`` returns the runs of every plan with a
``plan`` column and the overrides as columns.  To keep a variant apart from
the baseline, give it its own folder (``--out results/charging-n10``) or a
study file of its own that ``extends`` the baseline.  For a scenario,
``run`` makes one run and writes one run folder (default
``results/<scenario>/<run id>``) that ``load_run`` reads:

.. code-block:: console

    $ mango-scenario run scenarios/batteries.yaml -p presets/base.yaml --seed 7
    run ... of scenario 'batteries' done in ... s -> results/batteries/...
      65 values recorded, checksum ...; replay: mango-scenario rerun results/batteries/...

``--until 2h`` stops a single run early for a quick test without touching
the horizon (see :ref:`Running <scenario-running>`).

``rerun`` replays one finished run from its run spec in this process and
compares the checksum of the recorded data, the check a reviewer needs.  It
takes a run folder, ``<study folder>/run_<index>`` or a study folder plus
an index or run-id prefix: ``run_<n>`` is always an index, a bare number
with a leading zero (``0777``; run ids are hex) is read as a run-id prefix
first.  It names the code and data files that changed since the run
(``note: changed since the run: code changed in model.py``), and
``--certify`` marks a stale run whose replay is identical as current.  When the recorded project root does not hold the
scenario any more (a moved or copied project), it uses the nearest folder
above the run folder that does, and it warns when the scenario definition
changed since the run was made:

.. code-block:: console

    $ mango-scenario rerun results/charging/run_3
    identical: run ... replayed in ... s, checksum ... (65 values)

``schema`` writes the JSON Schema of a scenario's parameters (``--kind
scenario-file`` or ``study-file`` for the file formats):

.. code-block:: console

    $ mango-scenario schema scenarios/batteries.yaml -o batteries.schema.json
    wrote the params schema to batteries.schema.json

The remaining commands, ``run-one``, ``to-script``, ``form`` and ``new``, are
described in the next two sections; ``mango-scenario <command> --help``
lists every option.

----

.. _scenario-slurm:

Running on a Slurm cluster
==========================

On a cluster with the `Slurm <https://slurm.schedmd.com>`_ workload manager,
a study runs as one job array with one array task per pending run (or per
``runs_per_task`` runs).  The
:class:`~mango.scenario.SlurmExecutor` writes the run specs of the pending
runs to ``<out>/slurm/pending.jsonl`` and a batch script
``<out>/slurm/array.sbatch``, submits the script with ``sbatch`` and returns
at once:

.. testcode:: files

    from mango.scenario import SlurmExecutor, load_study_file

    cluster = load_study_file("studies/charging.yaml", out="results/cluster")
    executor = SlurmExecutor(partition="short", time="00:10:00", submit=False)
    report = cluster.run(executor=executor)
    print(report.pending, report.submitted, report.job_id)
    print(open(report.script).read(), end="")

.. testoutput:: files

    8 8 None
    #!/bin/bash
    #SBATCH --job-name=charging
    #SBATCH --array=0-7
    #SBATCH --partition=short
    #SBATCH --time=00:10:00
    #SBATCH --cpus-per-task=1
    #SBATCH --output=.../results/cluster/slurm/logs/%A_%a.out

    # Written by mango.scenario: 8 pending runs in 8 array tasks; array task i runs line i of pending.jsonl.
    set -eu
    export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
    export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
    export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
    export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"
    export PYTHONHASHSEED="${PYTHONHASHSEED:-0}"
    TASK=$(( ${MANGO_TASK_OFFSET:-0} + SLURM_ARRAY_TASK_ID ))
    cd ...
    ... -m mango.scenario.cli run-one ...results/cluster/slurm/pending.jsonl --line "$TASK"

Array task *i* runs line *i* of ``pending.jsonl`` with ``mango-scenario
run-one``, which executes exactly what a pool worker executes and writes the
run folder; a run that is already done is skipped, so a requeued task does
not overwrite results (``--force`` runs it again and keeps the old manifest
as ``manifest.prev.json``; a done run whose code or data files changed since
runs again).  With ``submit=False`` (``--no-submit``) the script is only
written, for a look before ``sbatch array.sbatch``.  The options are:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - ``SlurmExecutor(...)`` / ``run`` flag
     - Meaning
   * - ``partition``, ``account``, ``qos`` / ``--partition``, ``--account``,
       ``--qos``
     - where the tasks run and who pays
   * - ``time``, ``mem``, ``cpus_per_task`` / ``--time``, ``--mem``,
       ``--cpus-per-task``
     - limits per array task; with ``runs_per_task`` *K* the time limit must
       cover *K* runs
   * - ``array_parallelism`` / ``--array-parallelism``
     - at most *P* tasks at once (``--array=0-N%P``)
   * - ``runs_per_task`` / ``--runs-per-task``
     - runs per array task (default 1), executed one after another with
       ``run-one --lines``; several runs per task save scheduler overhead and
       the start-up of a Python process (about 1.5-3 s of CPU) for short runs
   * - ``max_array_size`` / ``--max-array-size``
     - the most tasks per job (default 1000).  Clusters limit job arrays
       (``MaxArraySize`` in ``slurm.conf``, 1001 by default, i.e. task ids
       0 to 1000); a larger array is split into several jobs, each submitted
       with its own ``MANGO_TASK_OFFSET``, and ``slurm/submit.sh`` holds their
       ``sbatch`` commands
   * - ``setup`` / ``--setup`` (repeatable)
     - shell lines every task runs first, e.g. ``module load python/3.11`` or
       ``source .venv/bin/activate``; ``sbatch`` passes the submitting
       shell's environment to the tasks (``--export=ALL`` is its default),
       so these are needed only when the tasks need more
   * - ``extra_sbatch`` / ``--extra-sbatch=--constraint=ib`` (repeatable)
     - further ``sbatch`` options, written as ``#SBATCH`` lines (shell
       commands there are refused: they belong in ``setup``)
   * - ``python`` / ``--python``
     - the interpreter of the array tasks: by default ``python`` from
       ``PATH`` when ``setup`` lines are given (so a virtual environment or
       module they activate is used), else the interpreter that submitted
       the study (an absolute path, which bypasses any activation)

A study file can keep these settings in its ``executor:`` block, so every
resubmission uses them; ``run`` flags replace single settings:

.. code-block:: yaml

    executor:
      kind: slurm          # run on Slurm without --executor slurm
      slurm: {partition: short, account: grid, time: "00:30:00", runs_per_task: 4,
              setup: ["module load python/3.11", "source .venv/bin/activate"]}

The same from the command line:

.. code-block:: console

    $ mango-scenario run studies/sensitivity.yaml --executor slurm --partition short --no-submit
    32 runs: 0 done, 0 skipped, 0 failed, 32 pending in .../array.sbatch (not submitted; run: sbatch .../array.sbatch) in ... s (Slurm array, partition short, not submitted) -> .../results/sensitivity
    $ mango-scenario run-one results/sensitivity/slurm/pending.jsonl --line 0
    run 0 (...) done in ... s (cpu ... s)
    $ mango-scenario run-one results/sensitivity/slurm/pending.jsonl --line 0
    run 0 (...) already done; skipped (--force runs it again)
    $ mango-scenario status results/sensitivity
    sensitivity: 32 runs: 1 done, 0 failed, 31 pending
      time: ... s wall, ... s CPU over 1 finished run (... s CPU per run on average)

Submission requires ``sbatch`` on the ``PATH`` (checked before anything is
written).  After submitting, the study folder records the job in
``slurm/job.json`` (``report.job_ids`` lists every job of a split array); ``mango-scenario status`` shows the progress and the
active job, and every ``study.run()`` on the folder refuses to start while
``squeue`` still lists the job, so that no run executes twice.  When the
job has ended, running the study again triages the folders as usual:
finished runs are kept, failed, missing and stale ones run again.  Array
tasks import the scenario and outcomes by reference from the project folder,
the working directory of the submitting process; each manifest records the
git state and the code and data files that its task actually used, and the
Slurm job, array task and node ids.

----

.. _scenario-tooling:

Plain scripts, preset forms and new projects
============================================

Plain mango scripts
-------------------

:meth:`Scenario.to_script <mango.scenario.Scenario.to_script>` writes one
run of a scenario out as a plain mango script: ``create_world`` with the
seed and the calendar, the communication simulation, one registration loop
per group calling the group's factory, every link as explicit topology
edges, the recorders and the step loop.  It uses the same random streams,
so it records the same values; set a breakpoint in the loop to debug a run,
or read it to see what the scenario layer does:

.. testcode:: files

    import runpy
    from pathlib import Path

    script = scenario.to_script(seed=42)
    Path("batteries_script.py").write_text(script)
    rows = runpy.run_path("batteries_script.py")["main"]()
    print(sorted(row["value"] for row in rows) == sorted(result.frame["value"]))
    print(script.placeholders)

.. testoutput:: files

    15 values recorded
    True
    ()

The script comes in two forms.  The **lean** form, written when the
scenario uses no kits, phases, events, communication changes, link
delay/loss, partitions, ``stop_when``/``ctx.stop``, ``ctx.spawn``/
``ctx.retire``/``ctx.relink``, behaviors or resources and steps with a
fixed size, is ``create_world``, the registration loops, the topology, the
recorders and a plain ``for`` loop over the steps, with a small
``ScriptContext`` and ``ScriptEntity`` for what factories and recorders
read (``e.p``, ``e.rng``, ``ctx.agents(...)``).  The **full** form also
writes out phases (``record: false``), events, communication switches,
link delay/loss and partitions (with
:class:`~mango.scenario.LayeredCommunication` and the same per-edge
streams), kit scoping, kit behaviors and resources, ``stop_when``, and what
agents call while the run steps: ``ctx.spawn``, ``ctx.retire``,
``ctx.relink``, ``ctx.link_pairs``, ``ctx.stop``, ``ctx.log``,
``ctx.set_metric`` and ``ctx.phase``.  Recorders keep their ``kind``,
``window``, ``aggregate`` and ``categories``; event logs, metrics and the
stop reason are printed at the end (with a CSV path, each log is written
next to it as ``values.csv.<log>.csv``).  Both forms contain only the
``ctx``/``e`` methods the scenario's modules use: to-script looks for the
calls (``.spawn(``, ``.relink(``, ``.log(``, ...) in the modules of the
scenario, its factories, kits and recorders and the project modules they
import, and a method it left out raises an ``AttributeError`` that names
it.  ``lean=False`` (``--full``) asks for the full form
of a simple scenario; ``lean=True`` (``--lean``) fails with the list of
features that need the full one.  The returned text is a ``str`` with two
attributes, ``script.lean`` and ``script.placeholders``.

What the script imports and what it writes out:

- module-level functions and classes are imported;
- lambdas and local functions are written out when their closure variables
  hold literals (numbers, strings, lists, dicts), parameter models (a kit's
  ``p = ctx.p`` becomes ``p = params.<prefix>``), the kit's ``ctx`` or
  importable objects;
- objects kits create (behaviors, the space, resources) are written out as
  constructor calls when their class is importable and their state is what
  the constructor makes of the stored arguments (``TariffClock(tariff=
  params.agg.tariff)``); an object that is a behavior and a resource stays
  one object;
- recorders with ``aggregate=``/``window=`` and recorders taking ``(agent,
  ctx)`` are written out as well (the script's ``ctx.space`` is the
  environment's space, as in the run);
- everything else (a closure over a random generator, an object changed
  after it was constructed) becomes a clearly marked ``Placeholder``,
  listed in the script's docstring and in ``script.placeholders``, that
  raises when the script reaches it;
- the script imports a few helpers of :mod:`mango.scenario` (the parameter
  models, ``LayeredCommunication``, the reducers of windowed recorders), so
  it needs mango with the ``scenario`` extra and the project's modules;
  ``script.write(path)`` (and ``mango-scenario to-script -o path``) to a
  path outside the project folder adds the lines that put the project
  folder on the import path and make it the working directory (relative
  data paths), so ``python archive/run_16.py values.csv`` runs from
  anywhere; ``script.for_location(path)`` returns that text without
  writing it.

Both usual forms of a kit factory are written out: the lambdas of
``BatteryFleet`` (see :ref:`scenario-kits`), which close over ``p = ctx.p``,
become functions starting with ``p = params.north``, and a module-level
function reading ``e.p`` (inside a kit, ``e.p`` is the kit's params) is
imported.  A closure over anything else, such as the kit instance
(``self.x``) or a random generator, becomes a placeholder; read such values
from ``e.p``, ``e.rng`` or ``ctx`` inside the factory instead.

From the command line, the target is a scenario with presets, or one run of
a study: a run folder, a study folder with ``--run``, or a study file with
``--run`` (run N of its plan, not yet run).  ``--seed`` takes an int or a
seed dict as JSON, as a run's ``spec.json`` stores it:

.. code-block:: console

    $ mango-scenario to-script scenarios/batteries.yaml --seed 42 -o batteries_script.py
    wrote the script to batteries_script.py
    $ python batteries_script.py
    15 values recorded

.. code-block:: text

    mango-scenario to-script results/charging/run_0003 -o run3.py   # replays run 3
    mango-scenario to-script results/charging --run 3
    mango-scenario to-script studies/charging.yaml --run 3          # run 3 of the plan
    mango-scenario to-script scenarios/batteries.yaml --full        # the full form

Without ``--run`` a study file gives the script of its base run (base
presets and overrides, no factor values); the command says so.  In Python,
``scenario.to_script(run.config, seed=run.seed)`` writes the script of a run
loaded with :func:`~mango.scenario.load_study`.

Preset forms
------------

:func:`~mango.scenario.params_form` renders the parameter schema of a
scenario as a self-contained HTML page (no network access needed): nested
models become sections, distributions get a kind selector, and the page
shows and downloads the YAML preset you edit (by default only the values
that differ from the scenario's defaults).  The model's own parameters come
first; the run settings every scenario has (seed, time, communication,
profiles, phases, events) follow in a collapsed section.  The form starts
from the scenario's defaults, or from presets and overrides
(``params=``/``overrides=``, ``--params``/``--set``); for a study file it
starts from the study's base presets and overrides, and the page says that
the factors and replications stay in the study file (``params_form(...,
note=...)`` shows such a remark on any form).  The defaults are compared in
the form the preset is written in, so values a preset only repeats (such
as ``time: {horizon: 50, step: 1}`` for a scenario with
``scenario.time(horizon=50, step=1)``) do not count as changed; optional
fields of list items left unset (``until``, ``comm``, ``emit_to`` of an
event) are left out of the preview, and a mapping of plain values (an
event's ``emit`` payload) is edited as key/value rows:

.. testcode:: files

    from mango.scenario import params_form

    Path("batteries_form.html").write_text(params_form(scenario), encoding="utf-8")
    print(Path("batteries_form.html").read_text(encoding="utf-8")[:15])

.. testoutput:: files

    <!doctype html>

.. code-block:: console

    $ mango-scenario form scenarios/batteries.yaml
    wrote the form to batteries_form.html
    open it in a browser; it needs no network access

.. code-block:: text

    mango-scenario form scenarios/batteries.yaml -p presets/base.yaml --set battery.n=10
    mango-scenario form studies/charging.yaml        # starts from the study's base

Help texts and bounds come from the params model:
``Field(description=...)`` becomes the help line under a field, and
``ge``/``le`` (or ``gt``/``lt``) the bounds of a number input.  Validation
errors quote the description too (``share: Input should be less than or
equal to 1 (got 2); share is the share of informed agents``):

.. code-block:: python

    class TownParams(BaseModel, extra="forbid"):
        share: float = Field(0.5, ge=0, le=1, description="share of informed agents")

The downloaded YAML always writes numbers with a ``.``, whatever the
browser shows in its locale.

New projects
------------

``mango-scenario new <folder>`` creates a runnable project from a template.
``--template minimal`` (the default) writes one ``model.py`` with an agent,
its parameters, a Python scenario and two outcomes, a preset, a study file
and a README: a domain-neutral start (agents on a ring move their values
toward their neighbours') to grow from.  ``--template full`` writes a
sensor-field example with the layout this page uses, meant to be adapted:
packages for agents and kits, outcomes, presets with calendar time, a CSV
profile and a warm-up phase, a scenario file and ``studies/<name>.yaml``.
Both studies run as they are:

.. code-block:: console

    $ mango-scenario new ring
    Created 5 files in ring/ (template 'minimal'):
      README.md
      .gitignore
      model.py
      presets/base.yaml
      study.yaml

    Next steps:
      cd ring
      python model.py                # one run
      mango-scenario validate study.yaml
      mango-scenario plan study.yaml
      mango-scenario run study.yaml
      (sequential is fastest for a few short runs; for many runs use --workers N, which reuses worker processes for short runs; --runs-per-worker 1 gives every run a fresh process)

.. code-block:: console

    $ mango-scenario new sensors --template full
    Created 11 files in sensors/ (template 'full'):
      README.md
      agents/__init__.py
      agents/sensors.py
      data/temperature.csv
      .gitignore
      kits/__init__.py
      kits/field.py
      outcomes.py
      presets/base.yaml
      scenario.yaml
      studies/sensors.yaml

    Next steps:
      cd sensors
      mango-scenario validate studies/sensors.yaml
      mango-scenario plan studies/sensors.yaml
      mango-scenario run studies/sensors.yaml
      (sequential is fastest for a few short runs; for many runs use --workers N, which reuses worker processes for short runs; --runs-per-worker 1 gives every run a fresh process)

.. testcleanup:: files

    os.chdir(_files_cwd)
    sys.path.remove(_files_tmp.name)
    for _module in ("batteries", "batteries_script"):
        sys.modules.pop(_module, None)
    _files_tmp.cleanup()

----

Hand-written run() versus the scenario layer
=============================================

.. list-table::
   :header-rows: 1
   :widths: 22 39 39

   * - Concern
     - Hand-written ``run()``
     - Scenario layer
   * - Parameters
     - Literals and dicts in the script; a typo fails mid-run with a
       ``KeyError``.
     - One typed model with presets and dotted overrides; typos fail before
       the run, with the path, the file and a suggestion.
   * - Entities
     - ``world.register(...)`` loops; generated ids such as ``agent4``.
     - ``scenario.group(name, count, factory)``; names such as ``ev_0`` are
       the aids and the result keys.
   * - Wiring
     - Addresses passed into constructors or patched afterwards; topologies
       built by hand.
     - ``scenario.link(name, a, b, pattern=...)``; agents ask
       ``self.neighbors(tid=name)``.
   * - Build order
     - Positions after the space is initialised, recorders before the first
       step: easy to get wrong.
     - Fixed by the runtime: register, place, link, record, hooks, start.
   * - Reuse
     - Copy the agent setup, the grid and the recorders, with their
       parameters, into the next study's script.
     - A kit with its own typed parameters:
       ``scenario.use(BatteryFleet(), prefix="north")``.
   * - Time
     - A ``for`` loop with a step size and a hand-made calendar.
     - ``time: {start, horizon, step}`` in the parameters; ``step: event``
       for discrete-event runs.
   * - Input time series
     - Read a CSV in the script and index it with ``clock.time``.
     - ``profiles:`` in a preset; ``e.profile("pv").at(clock)`` on the
       calendar, with per-entity noise from the entity's stream.
   * - Warm-up, outages, faults
     - Branches inside the step loop; the warm-up is dropped later in
       another function.
     - ``phases:`` and ``events:`` in a preset; a second outage is one more
       line.
   * - Randomness
     - Global ``random`` / ``numpy.random``, generators seeded with
       ``seed + 1``; adding an agent shifts every later draw.
     - One seed; one named stream per entity, link and component; replay
       from ``result.seed``.
   * - Results
     - Recordings keyed by aid inside the world object; every study converts
       them again.
     - A tidy table with run id, time, calendar time, phase, group, entity,
       variable and value; ``save``/``load_run``.
   * - Variants and seeds
     - Nested loops over module-level lists.
     - ``Study(uncertainties=..., levers=..., design=..., replications=...)``
       with common random numbers, or a study file.
   * - Sensitivity analysis
     - A separate script with its own sample generation, job list and
       result join.
     - ``design: {sobol: {n: 256}}`` in a study file that extends the first
       one, then ``load_study(out).sobol_indices()``.
   * - Parallel runs
     - A hand-written ``ProcessPoolExecutor`` wrapper.
     - ``study.run(workers=8)``: spawn, one process per run, thread limits.
   * - Cluster runs
     - An entry point that runs one job by array index, plus an sbatch
       script.
     - ``mango-scenario run study.yaml --executor slurm``.
   * - Resume
     - Skip by folder name, which reuses stale results after a parameter,
       code or data change.
     - Content-hash run ids, checked before dispatch; changed parameters give
       new runs, and done runs whose code or data files changed since are
       run again (stale runs).
   * - Provenance and replay
     - Whatever the script remembers to write; no single-run entry point.
     - Run spec, resolved config, seed, versions, git commit and checksum per
       run; ``mango-scenario rerun`` replays a run and compares checksums.
   * - Analysis
     - Join the per-run files by hand.
     - ``load_study(out)``: one row per run with factors and outcomes;
       recompute outcomes from stored data.

API reference
=============

See :doc:`api_ref/mango.scenario`.
