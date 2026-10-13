=============================================
Tutorial: the EV neighbourhood as a scenario
=============================================

The :doc:`simulation-ev-tutorial` builds a neighbourhood of five households
with rooftop PV, three EVs and a coordinator by hand: it creates the world,
registers agents with positional arguments, patches addresses, places agents
inside ``async with world`` and steps a ``for`` loop.  This tutorial builds
the same neighbourhood with the scenario layer (:doc:`scenario`), runs it with
one seed and then runs a small study over two fleet sizes.

The agents stay almost the same.  What changes is everything around them:

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - Hand-written script
     - Scenario
   * - Numbers are positional literals: ``HouseholdAgent(6.0, 2.0, coord.addr)``,
       ``EVAgent(40.0, 20.0, 3.3, 3.0)``.
     - Every number is a field of one parameter model; a variant is an
       override such as ``ev.n=5``, and a typo fails before the run.
   * - The coordinator is created with ``ev_addresses=[]`` and patched after
       the EVs exist.
     - ``scenario.link("dispatch", evs, coordinator, ...)``; the coordinator
       asks ``self.neighbors(tid="dispatch")``.
   * - Positions are set inside ``async with world`` after the space is
       initialised; recorders must come after that but before the first step.
     - Groups declare ``position=``; the scenario builds, places, links and
       records in a fixed order.
   * - Time is ``for _ in range(24): step_simulation(world, 3600.0)``.
     - Start, horizon and step are parameters (``time.horizon=2d``).
   * - Results are keyed by generated ids (``agent4``) and live in the world
       object.
     - Results are a tidy table keyed by ``ev_0`` and ``household_2``, with
       calendar time; they can be saved and loaded again.
   * - EV start positions come from the global ``random`` module.
     - One seed; every entity draws from its own stream, so a sixth household
       does not change any EV.
   * - One run.
     - A study: fleet sizes times seeds, on a process pool, resumable, loaded
       as one table.

.. note::

   The scenario layer needs optional dependencies:
   ``pip install "mango-agents[scenario]"``.  The code below is one module,
   ``ev_tutorial.py``; it is part of mango's test suite
   (``tests/unit_tests/scenario/ev_tutorial.py``), so it is known to run.
   The complete file is shown at the end of the page.

----

Step 1: Messages and agents
===========================

The messages are the same dataclasses as before:

.. literalinclude:: ../../tests/unit_tests/scenario/ev_tutorial.py
   :language: python
   :start-after: # -- messages
   :end-before: # -- agents

A household no longer receives the coordinator's address in its constructor.
It sends its report to every neighbour in the ``reports`` link, which the
scenario wires to the coordinator.  It reads the hour of day from the calendar
clock (``clock.datetime``), because the scenario has a start date:

.. literalinclude:: ../../tests/unit_tests/scenario/ev_tutorial.py
   :language: python
   :pyobject: HouseholdAgent

An EV keeps its own ``position`` (the scenario uses it to place the EV) and
counts the energy it delivered to households with a deficit, so that the
study has something to compare:

.. literalinclude:: ../../tests/unit_tests/scenario/ev_tutorial.py
   :language: python
   :pyobject: EVAgent

The coordinator no longer holds a list of EV addresses.  On every step it
pairs the EVs in its ``dispatch`` link with the most urgent reports:

.. literalinclude:: ../../tests/unit_tests/scenario/ev_tutorial.py
   :language: python
   :pyobject: CoordinatorAgent

----

Step 2: Parameters
==================

All numbers of the old script move into one parameter model.  It subclasses
:class:`~mango.scenario.ScenarioParams`, which adds ``seed``, ``time`` and
``comm`` (communication); nested models use ``extra="forbid"`` so that
unknown keys are errors.  The EVs' starting charge is no longer one literal
per EV but a distribution, drawn per EV, and each household's load is scaled
by a factor drawn per household:

.. literalinclude:: ../../tests/unit_tests/scenario/ev_tutorial.py
   :language: python
   :start-after: # -- parameters
   :end-before: # -- scenario

Variants are overrides with dotted paths (or YAML presets, see
:ref:`scenario-params`).  Values are parsed as YAML, so numbers stay numbers
and ``{uniform: [5, 35]}`` is a distribution:

.. code-block:: python

    from ev_tutorial import scenario

    params = scenario.resolve_params(overrides=["ev.n=5", "ev.soc_kwh={uniform: [5, 35]}"])
    print(params.ev)

.. code-block:: text

    n=5 capacity_kwh=40.0 soc_kwh=Uniform(5.0, 35.0) max_power_kw=3.3 speed=3.0

A typo fails before anything is built, with a suggestion:

.. code-block:: python

    from mango.scenario import ParamsError

    try:
        scenario.resolve_params(overrides=["ev.capactiy_kwh=50"])
    except ParamsError as error:
        print(error)

.. code-block:: text

    Invalid overrides for Params:
      ev.capactiy_kwh: unknown parameter; EVParams has no field 'capactiy_kwh'; did you mean 'ev.capacity_kwh'?

----

Step 3: The scenario
====================

Time and space
--------------

The scenario holds the structure; :meth:`~mango.scenario.Scenario.time` sets
the defaults of ``params.time`` (a preset or an override such as
``time.horizon=2d`` still changes them).  The environment factory receives a
:class:`~mango.scenario.BuildContext` ``ctx`` whose ``ctx.p`` are the run's
parameters:

.. literalinclude:: ../../tests/unit_tests/scenario/ev_tutorial.py
   :language: python
   :start-after: # -- scenario
   :end-before: # -- groups

Groups
------

A group is a name, a count and a factory.  The count can be a literal
(``1``), a parameter path (``"ev.n"``) or a function of the parameters (one
household per entry of ``households``).  The factory receives an
:class:`~mango.scenario.EntityContext` ``e`` with the entity's ``name``,
``index`` and parameters, and ``e.rng``, the entity's own random stream.  It
is the same generator the agent later sees as ``self.rng``, keyed by the
entity name and derived from the run seed.

Both households and EVs draw from their own stream: a household its load
factor, an EV its starting charge and position.

``position=`` places each entity in the space after registration.  The
households come from the parameters, the EVs keep the position they drew in
their factory (``e.agent`` is the agent the factory returned):

.. literalinclude:: ../../tests/unit_tests/scenario/ev_tutorial.py
   :language: python
   :start-after: # -- groups
   :end-before: # -- links

Entities are named after their group: ``coordinator`` (a group with the
literal count ``1`` is named like the group), ``household_0`` to
``household_4`` and ``ev_0`` to ``ev_2``.  These names are the agents' aids
and the keys of all results.

Links and recorders
-------------------

A link becomes a mango topology whose id is the link name, so the agents
find each other with ``self.neighbors(tid="reports")`` and
``self.neighbors(tid="dispatch")``.  ``many_to_one`` connects every source
entity with the single target; links are undirected by default, so the
coordinator sees the EVs and every EV sees the coordinator.  A recorder
samples one value per agent at the start and after every step; the row at
time *t* is the state after the step that ended at *t* (see
:ref:`scenario-rows`).  A recorder whose rows you will integrate or
time-average also says what its value is: ``kind="flow"`` for a power set in
``on_step`` for the step, ``kind="state"`` for a level such as a queue
length (see :ref:`flows and states <scenario-state-flow>`):

.. literalinclude:: ../../tests/unit_tests/scenario/ev_tutorial.py
   :language: python
   :start-after: # -- links
   :end-before: # -- outcomes

:meth:`~mango.scenario.Scenario.plan` shows what a run would build, without
building it:

.. code-block:: python

    print(scenario.plan())

.. code-block:: text

    Scenario 'ev_neighbourhood': 9 agents in 3 groups, 2 links, 7 recorders
      time: horizon 1d, step 1h (24 steps), start 2024-06-21T00:00:00
      environment: factory environment
      groups:
        coordinator            1  coordinator
        household              5  household_0 .. household_4
        ev                     3  ev_0 .. ev_2
      links:
        reports           household -> coordinator (many_to_one); 5 edges, undirected
        dispatch          ev -> coordinator (many_to_one); 3 edges, undirected
      recorders:
        soc_kwh           ev
        delivered_kwh     ev
        x                 ev
        y                 ev
        import_kwh        household
        export_kwh        household
        self_consumed_kwh household

----

Step 4: Run and inspect
=======================

:meth:`~mango.scenario.Scenario.run` builds a fresh world, steps it to the
horizon and returns a :class:`~mango.scenario.RunResult`.  ``wide`` gives one
variable as a time × entity table, ``ts`` one variable in long form:

.. code-block:: python

    from ev_tutorial import delivered_kwh, local_share

    result = scenario.run(seed=7)
    print(result.wide("soc_kwh").iloc[::4].round(1))
    print(f"local share {local_share(result):.1%}, EVs delivered {delivered_kwh(result):.1f} kWh")
    print(result.ts("import_kwh", entity="household_2").iloc[-1].round(2))

.. code-block:: text

    entity   ev_0  ev_1  ev_2
    time
    0.0      18.9  15.2  14.1
    14400.0  18.9  15.2  14.1
    28800.0   9.1   7.0   6.7
    43200.0   9.3   8.5   7.0
    57600.0  15.9  15.1  20.0
    72000.0  19.6  18.4  26.3
    86400.0  14.7  14.3  20.7
    local share 64.7%, EVs delivered 46.3 kWh
    32.91

The EVs discharge into households with a deficit in the early morning and
recharge at the surplus households around noon.  ``result.frame`` is the
tidy table behind these views, one row per sample with the columns
``run_id, time, datetime, phase, group, entity, variable, value``; plotting
is one call away, e.g. ``result.wide("soc_kwh").plot()``.

One seed replays the run exactly.  ``result.config`` and ``result.seed`` are
plain data, so a run can be replayed from a saved run folder as well:

.. code-block:: python

    replay = scenario.run(result.config, seed=result.seed)
    print(replay.checksum() == result.checksum())

.. code-block:: text

    True

Because every entity draws from a stream keyed by its own name, a sixth
household (appended to the default list with ``+households``) draws its own
load factor and changes the outcome, but not a single EV draw:

.. code-block:: python

    extra = {"pv_peak_kw": 8.0, "load_kw": 1.0, "x": 3.0, "y": 6.0}
    bigger = scenario.run(seed=7, overrides={"+households": [extra]})
    print(bigger.wide("import_kwh").columns.tolist())
    print(result.wide("soc_kwh").iloc[0].round(2).tolist())
    print(bigger.wide("soc_kwh").iloc[0].round(2).tolist())

.. code-block:: text

    ['household_0', 'household_1', 'household_2', 'household_3', 'household_4', 'household_5']
    [18.89, 15.22, 14.12]
    [18.89, 15.22, 14.12]

----

Step 5: A study
===============

Does a third EV help?  One run per fleet size is not an answer, because the
EVs' starting charge and position are random.  A
:class:`~mango.scenario.Study` runs every fleet size with several seeds.
Outcomes are plain functions of a run result:

.. literalinclude:: ../../tests/unit_tests/scenario/ev_tutorial.py
   :language: python
   :start-after: # -- outcomes
   :end-before: # -- study

The study varies ``ev.n`` and runs two replications of each fleet size: 4
runs.  With common random numbers (the default of a full factorial design),
replication *r* uses the same seed for both fleet sizes, so ``ev_0`` and
``ev_1`` start identically in both and the comparison is paired
(:ref:`scenario-crn` explains the modes, ``frame.paired(...)`` computes the
paired differences):

.. literalinclude:: ../../tests/unit_tests/scenario/ev_tutorial.py
   :language: python
   :start-after: # -- study

``workers=2`` runs the study on a process pool with two workers.  Each worker
process imports ``ev_tutorial`` to find the scenario and the outcomes, so the
study is started from a script with a main guard.  (For runs as short as
these, starting a worker process costs more than the run itself: the pool
therefore reuses each worker for several short runs, and a handful of short
runs is fastest without ``workers``; see :ref:`scenario-executors`.)

.. code-block:: python

    # run_study.py
    from ev_tutorial import main

    if __name__ == "__main__":
        main()

.. code-block:: text

    Study 'ev_neighbourhood': 4 runs
      scenario      ev_tutorial:scenario
      design        full factorial, 2 design points x 2 replications
      seeds         seed 2026, common random numbers: 4 runs on 2 seeds
      uncertainties ev.n: 2 | 3
      outcomes      local_share, delivered_kwh
      out           /home/me/ev/results/ev_fleet
    [1/4] done   run 0 (9a2ed8ab75c4) in 0.38 s
    [2/4] done   run 1 (4215ef7908f2) in 0.38 s
    [3/4] done   run 2 (3f28b4e6709b) in 0.38 s
    [4/4] done   run 3 (78e94a8d1ba6) in 0.40 s
    4 runs: 4 done, 0 skipped, 0 failed in 2.5 s (process pool (2 workers)) -> /home/me/ev/results/ev_fleet
          local_share  delivered_kwh
    ev.n
    2        0.585217      35.081482
    3        0.677914      55.497516

Each run writes its own folder ``results/ev_fleet/runs/<run_id>/`` with the
resolved configuration, the seed, the time series and a manifest.  The run id
is a hash of the scenario, the parameters and the seed, so running the script
again skips all four runs before any process starts, while a changed
parameter produces new runs next to the old ones:

.. code-block:: text

    4 runs: 0 done, 4 skipped, 0 failed in 0.0 s (process pool (2 workers)) -> /home/me/ev/results/ev_fleet

After an edit of the agent code or of a data file the runs read, the
finished runs are *stale*: the next ``study.run()`` names the changed files
and runs them again (comments and formatting do not count; ``mango-scenario
certify`` keeps them when an edit cannot change the results, see
:ref:`scenario-resuming`).  A changed
parameter makes the study's run table
describe the new plan; the old one is kept in ``plans/``.

:func:`~mango.scenario.load_study` returns one row per run, with the factors
and outcomes as columns, and gives access to every run's stored result:

.. code-block:: python

    from mango.scenario import load_study

    frame = load_study("results/ev_fleet")
    print(frame[["ev.n", "replication", "status", "local_share"]])
    first = frame.run(0)                  # the stored RunResult of run 0
    soc = first.wide("soc_kwh")

.. code-block:: text

       ev.n  replication status  local_share
    0     2            0   done     0.576157
    1     2            1   done     0.594278
    2     3            0   done     0.661525
    3     3            1   done     0.694304

----

The complete module
===================

.. dropdown:: ev_tutorial.py

   .. literalinclude:: ../../tests/unit_tests/scenario/ev_tutorial.py
      :language: python

What's next?
============

* :doc:`scenario` explains every part used here in depth: presets and
  overrides, distributions, seeds and streams, link patterns, results, study
  designs, executors and the results folder.  Its table
  :ref:`scenario-cheatsheet` maps research questions to features.
* Try another variant without touching the code: ``scenario.run(seed=7,
  overrides=["comm.delay_s=600", "comm.loss_percent=0.2"])`` delays and
  drops reports and assignments.
* Add a lever: make the coordinator's threshold a factor of the study,
  ``Study(scenario, uncertainties={"ev.n": [2, 3]}, levers={"threshold_kw":
  [0.3, 1.0]}, ...)``.

.. seealso::

    :doc:`simulation-ev-tutorial`: the same neighbourhood built by hand
