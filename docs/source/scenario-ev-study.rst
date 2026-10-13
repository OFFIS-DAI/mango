=======================================
Example: the EV feeder study, 120 runs
=======================================

This example answers one research question with the scenario layer
(:doc:`scenario`): *does proportional EV dispatch beat greedy dispatch on a
12-bus feeder with 30 PV households, across 3 fleet sizes, 2 communication
models and a 2-hour communication outage, with 10 seeds each?*  That is 120
runs.  A follow-up study then asks how sensitive the outcomes are to the PV
share and the battery size.

The example is a small project in ``examples/ev_study`` of the mango
repository.  Every file is shown on this page, and the integration tests
(``tests/integration_tests/test_ev_study_example.py``) run reduced versions
of its studies.

.. code-block:: text

   examples/ev_study/
     ev_study/params.py      typed parameters
     ev_study/kits.py        the Neighbourhood kit: feeder, households, profiles, recorders
     ev_study/scenario.py    the scenario: kit, coordinator, EVs, links, recorders
     ev_study/agents.py      HouseholdAgent, EVAgent, CoordinatorAgent and their messages
     ev_study/grid.py        FeederBehavior: radial feeder with a linear voltage drop
     ev_study/kpi.py         the outcomes
     presets/base.yaml       time, warm-up phase, outage event, communication, profiles
     studies/fleet_comparison.yaml   the 120-run study
     studies/sobol_pv.yaml           the Sobol follow-up
     data/profiles.csv, data/make_profiles.py   synthetic 15-minute profiles
     run.sh, analyze.py, README.md

.. note::

   Install the optional dependencies first:
   ``pip install "mango-agents[scenario-all]"`` (pydantic, PyYAML, pandas,
   SciPy for the Sobol analysis and pyarrow for Parquet files).  Run every
   command from ``examples/ev_study``: the command line tool puts the working
   directory on the Python path, so ``ev_study.scenario:scenario`` imports.

----

Parameters
==========

Every number of the study has a name, a type and a default.  The EV
parameters are a plain pydantic model, and ``Params`` adds them to
:class:`~mango.scenario.ScenarioParams`, which already has ``seed``,
``time``, ``phases``, ``events``, ``comm`` and ``profiles``:

.. literalinclude:: ../../examples/ev_study/ev_study/params.py
   :language: python

``Uniform`` and ``Normal`` say what the numbers mean: ``soc`` is drawn per
EV between 0.2 and 0.8, ``arrival_h`` around 16:00 with a standard deviation
of 1.5 h.  Presets and the command line set values by dotted path
(``--set ev.n=20``).  A wrong key or value fails before any run starts, with
the path and a suggestion (:ref:`scenario-params`).

The neighbourhood as a kit
==========================

The feeder, the households, their profiles and the recorders form a kit
(:ref:`scenario-kits`).  The kit has its own typed parameters, and another
study reuses it with one line:

.. literalinclude:: ../../examples/ev_study/ev_study/kits.py
   :language: python
   :start-at: class Neighbourhood

- ``scenario.use(Neighbourhood(), prefix="grid")`` nests the kit parameters
  at ``grid`` (``--set grid.pv_share=0.8``).  The group is called
  ``grid.hh`` and its world variables have the entity ``grid``.
- ``ctx.set_behavior(...)`` adds the feeder to the default environment.
  ``install=lambda e: {"bus": e.index}`` hands each household its bus.
- ``ctx.profile("h0")`` is the profile named in the preset, loaded once per
  worker process (:ref:`scenario-profiles`).
- ``e.rng`` is the household's own random stream, keyed by its name.  The
  household always makes all three draws (PV or not, PV size, annual load),
  so a changed ``grid.pv_share`` changes only who has PV, not the loads.

The feeder is an ordinary :class:`~mango.Behavior`.  Agents installed with a
``bus`` draw their ``net_kw`` there:

.. literalinclude:: ../../examples/ev_study/ev_study/grid.py
   :language: python
   :pyobject: FeederBehavior

The scenario
============

.. literalinclude:: ../../examples/ev_study/ev_study/scenario.py
   :language: python
   :start-at: scenario = Scenario

``"ev.n"`` as the count reads the fleet size from the parameters.  Each EV
draws ``soc``, ``arrival_h`` and ``departure_h`` from ``e.rng``, a stream
derived from the study seed and the name ``ev_0``, ``ev_1``, ... and from
nothing else.  A 31st household therefore does not move any EV's arrival
(:ref:`scenario-seeds`).  ``scenario.link`` compiles to the mango topologies
``reports`` and ``dispatch`` (:ref:`scenario-links`).

The agents
==========

The agents are plain mango agents.  They do not know about the scenario
layer; they find each other through the links:

.. literalinclude:: ../../examples/ev_study/ev_study/agents.py
   :language: python
   :pyobject: HouseholdAgent

.. literalinclude:: ../../examples/ev_study/ev_study/agents.py
   :language: python
   :pyobject: EVAgent

The coordinator shares the feeder headroom, a 20 kW limit minus the
households' net load, among the plugged-in EVs.  A report sent in a step is
delivered at the end of that step, so the coordinator reads it in the next
step, and the EVs act on its dispatch one step later still
(:ref:`scenario-timing`).  It therefore extrapolates the load and subtracts
what each EV charged since its last status.  *greedy* serves EVs first come, first served, each at up to 11 kW.
*proportional* splits the headroom by the energy each EV still needs.

.. literalinclude:: ../../examples/ev_study/ev_study/agents.py
   :language: python
   :pyobject: CoordinatorAgent

Time, warm-up and the outage
============================

The preset holds the values every run shares.  Time, the warm-up and the
outage are data, not code (:ref:`scenario-events`):

.. literalinclude:: ../../examples/ev_study/presets/base.yaml
   :language: yaml

- The first day is the unrecorded ``warmup`` phase, so outcomes only see
  2024-06-02.
- From 12:00 to 14:00 of that day the ``comm_outage`` event raises the
  message loss to 100 %.  The runner switches the communication simulation
  at both boundaries.  A second outage is one more line.
- The profiles are read from ``data/profiles.csv``.  The file's ``time``
  column puts each sample on the calendar.

``mango-scenario plan ev_study.scenario:scenario -p presets/base.yaml``
shows all of it before anything runs:

.. code-block:: text

   Scenario 'ev_feeder': 41 agents in 3 groups, 2 links, 5 recorders
     time: horizon 2d, step 15min (192 steps), start 2024-06-01T00:00:00
     kits:
       grid             Neighbourhood (ev_study.kits:Neighbourhood)
     behaviors: FeederBehavior (grid)
     groups:
       grid.hh              30  grid.hh_0 .. grid.hh_29
       coordinator           1  coordinator
       ev                   10  ev_0 .. ev_9
     links:
       reports          grid.hh -> coordinator (many_to_one); 30 edges, undirected
       dispatch         ev -> coordinator (many_to_one); 10 edges, undirected
     recorders:
       pv_kw            grid.hh (flow)
       import_kw        grid (world) (flow)
       min_voltage_pu   grid (world) (flow)
       soc_kwh          ev (state)
       ev_power_kw      ev (flow)
     profiles:
       h0               csv data/profiles.csv [h0], times from column 'time'
       pv               csv data/profiles.csv [pv], times from column 'time'
     phases:
       warmup           2024-06-01T00:00 .. 2024-06-02T00:00  (not recorded)
       main             2024-06-02T00:00 .. 2024-06-03T00:00
     events:
       comm_outage      2024-06-02T12:00 .. 2024-06-02T14:00  (comm loss_percent=1)

Outcomes
========

Every run has a tidy table, and ``run.ts(...)`` returns one variable for the
recorded phases.  An outcome is just its formula
(:ref:`scenario-results`).  The recorders declare what their values are
(:ref:`flows and states <scenario-state-flow>`): the powers and the
voltage are *flows*, computed in ``on_step`` for the step, and the state
of charge is a *state*, a level.  The warm-up day is not recorded, so
every flow has 96 rows per entity: the row at 00:15 describes the first
evaluated step, the row at midnight at the end of the day the last one;
``soc_kwh`` also has a row at the start of the day, the level the warm-up
left.  An energy is power times step length over the flow rows, which
``run.integrate("pv_kw")`` computes from the declared kind
(:ref:`scenario-rows`):

.. literalinclude:: ../../examples/ev_study/ev_study/kpi.py
   :language: python
   :start-at: def self_consumption

The study
=========

.. literalinclude:: ../../examples/ev_study/studies/fleet_comparison.yaml
   :language: yaml

The uncertainties describe the world (fleet size, communication), the
levers what is compared (the algorithm).  With common random numbers, both
algorithms of a replication see the same households and EVs.  The study is
run with the command line tool (:ref:`scenario-cli`):

.. literalinclude:: ../../examples/ev_study/run.sh
   :language: bash
   :lines: 2-

.. code-block:: console

   $ mango-scenario run studies/fleet_comparison.yaml --workers 12
   [  1/120] done   run 0 (23c9a6dd45dd) in 1.49 s (cpu 1.41 s)
   ...
   120 runs: 120 done, 0 skipped, 0 failed in 17.5 s (process pool (12 workers), workers reused for up to 11 runs each (auto)) -> results/fleet_comparison
   $ mango-scenario run studies/fleet_comparison.yaml --workers 12
   120 runs: 0 done, 120 skipped, 0 failed in 0.2 s (process pool (12 workers)) -> results/fleet_comparison
   $ mango-scenario rerun results/fleet_comparison/run_0037
   identical: run d6532ea0841a replayed in 1.1 s, checksum 46605d585cac8059 (4037 values)

- Every run gets a folder under ``results/fleet_comparison/runs/<run id>``.
  The run id is a hash of the resolved parameters and the seed.
- The pool measures how long a worker takes to start (about a second of
  imports) and how long a run takes, and reuses each worker for several of
  these short runs (``runs_per_worker="auto"``); every progress line shows
  the run's wall and CPU time, which the manifest and ``status.csv`` keep.
- A second ``run`` skips finished runs before it starts any process.  After
  an edit of the agent code or the profile CSV the finished runs are stale:
  the next ``run`` names the changed files before the first run starts and
  runs them again.  When an edit cannot change the results (a comment, a
  renamed helper), ``mango-scenario certify results/fleet_comparison
  --reason "..."`` keeps the runs and records why
  (:ref:`scenario-resuming`).
- ``--set grid.pv_share=0.8`` changes the parameters, so it gives 120 new run
  ids.  The folders of the 0.6 runs stay, but the study folder's run table
  now describes the 0.8 plan; ``load_study(..., plan="all")`` shows both, and
  ``--out results/fleet_pv08`` keeps the variant in a folder of its own.
- ``rerun`` replays one run from its folder and compares checksums.
- ``--executor slurm --partition short`` writes a Slurm array job, one task
  per pending run, and submits it (:ref:`scenario-slurm`).

The follow-up: Sobol sensitivity
================================

The second study extends the first.  It fixes the fleet size and the
communication model, samples the PV share and the battery size, and swaps
the design for a Saltelli design (:ref:`scenario-designs`):

.. literalinclude:: ../../examples/ev_study/studies/sobol_pv.yaml
   :language: yaml

That is 256 x (2 + 2) points x 3 replications = 3072 runs.  The seed,
folder layout, resume, outcomes and run table come from the first study.
The runs take under a second each, so on a cluster run four per array task
(``--executor slurm --partition short --runs-per-task 4`` gives 768 tasks,
below the usual ``MaxArraySize`` of 1001; larger arrays are split into
several jobs automatically), and on a workstation ``--workers 16`` reuses
each worker process for many such short runs.

Analysis
========

.. literalinclude:: ../../examples/ev_study/analyze.py
   :language: python
   :start-at: df = load_study

``load_study`` returns one row per run: index, run id, replication, status,
the factor columns (with ``comm.loss_percent:label`` for the labelled
levels) and the outcomes.  Both algorithms of a replication run on the same
seed (common random numbers), so ``paired`` subtracts the greedy run from
the proportional run of the same replication, fleet size and communication
model, and gives a Student-t confidence interval over the ten differences
(:ref:`scenario-crn`).  The second half rebuilds one configuration with the
study's seed tree and steps it to the outage, for debugging in a notebook.

What the study finds
====================

These are the means over the 10 replications of each variant, from one run
of all 120 runs in 18 s on 12 worker processes:

.. list-table::
   :header-rows: 2
   :widths: 8 10 12 12 12 12 12 12

   * -
     -
     - self-consumption
     -
     - peak import (kW)
     -
     - lowest voltage (pu)
     -
   * - EVs
     - comm
     - greedy
     - proportional
     - greedy
     - proportional
     - greedy
     - proportional
   * - 5
     - ideal
     - 0.500
     - 0.500
     - 20.48
     - 20.55
     - 0.936
     - 0.936
   * - 5
     - lossy
     - 0.494
     - 0.494
     - 22.51
     - 22.52
     - 0.935
     - 0.936
   * - 10
     - ideal
     - 0.539
     - 0.540
     - 22.80
     - 22.80
     - 0.891
     - 0.902
   * - 10
     - lossy
     - 0.535
     - 0.537
     - 23.77
     - 25.38
     - 0.887
     - 0.898
   * - 20
     - ideal
     - 0.577
     - 0.582
     - 23.15
     - 23.15
     - 0.882
     - 0.893
   * - 20
     - lossy
     - 0.574
     - 0.579
     - 23.58
     - 27.09
     - 0.882
     - 0.885

The paired differences, proportional minus greedy, with 95 % confidence
intervals over the 10 replications (``analyze.py``):

.. list-table::
   :header-rows: 1
   :widths: 8 10 16 22 16 22

   * - EVs
     - comm
     - lowest voltage (pu)
     - 95 % interval
     - peak import (kW)
     - 95 % interval
   * - 10
     - ideal
     - +0.011
     - 0.003 to 0.019
     - 0.00
     - 0.00 to 0.00
   * - 10
     - lossy
     - +0.011
     - -0.003 to 0.025
     - +1.60
     - -0.05 to 3.25
   * - 20
     - ideal
     - +0.011
     - 0.003 to 0.019
     - 0.00
     - 0.00 to 0.00
   * - 20
     - lossy
     - +0.003
     - -0.005 to 0.011
     - +3.51
     - 0.34 to 6.69

- **Voltage.** Proportional dispatch keeps the lowest voltage about 0.01 pu
  higher once 10 or more EVs compete for the headroom; with ideal
  communication the interval excludes zero.  Greedy dispatch concentrates
  11 kW on a few EVs, so the worst moment comes when those EVs sit at the
  far end of the feeder.
- **Peak import.** Both algorithms dispatch the same headroom, so the peaks
  are equal with ideal communication.  The excess over 20 kW comes from the
  evening ramp, when the PV output falls.  With 10 % message loss,
  proportional dispatch overshoots more, by 1.6 kW with 10 EVs and 3.5 kW
  with 20 EVs; ten replications make only the second difference clear.
  Proportional dispatch changes every EV's power on every step, and while
  the headroom falls, a lost dispatch leaves an EV at its previous, higher
  power.  Greedy EVs mostly stop because their battery is full.
- **Self-consumption** grows with the fleet and is slightly higher with
  proportional dispatch.  Greedy dispatch leaves late arrivals uncharged on
  busy evenings.

These numbers come from a small synthetic model.  They show the workflow;
they are not a grid study.  The model is easy to change: the
coordinator's feeder limit, the dispatch rules and the EV behaviour are a few
lines each in ``ev_study/agents.py``.

What a change costs
===================

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Change
     - With the scenario layer
   * - Add a fleet size of 40
     - Add it to ``ev.n`` in the study file.  The 120 finished runs are
       skipped, and only the 40 new runs execute.
   * - Change the PV share for every run
     - Set ``grid.pv_share`` in the preset.  All runs get new ids and
       recompute.  The old folders stay untouched.
   * - Add one household
     - ``grid.n_households=31``.  Every EV keeps its draws, because streams
       are keyed by name.
   * - Move the outage or add a second one
     - Edit ``events:`` in the preset.
   * - Sensitivity on PV share and battery size
     - ``studies/sobol_pv.yaml`` (11 lines) plus ``sobol_indices()``.
   * - Run on Slurm
     - ``--executor slurm`` (``--runs-per-task 4`` for the 3072-run Sobol
       study).
   * - Reuse the neighbourhood in another study
     - ``scenario.use(Neighbourhood(), prefix="grid")``.
   * - Reproduce run 37 for a reviewer
     - ``mango-scenario rerun results/fleet_comparison/run_0037``.
