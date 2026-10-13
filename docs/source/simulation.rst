================
Simulation World
================

The ``mango`` simulation module provides a self-contained, clock-driven
simulation environment for multi-agent systems.  Instead of running agents
inside a network container (TCP / MQTT), all agents share a single
:class:`~mango.SimulationWorld` that controls time deterministically.

This is useful for:

* Offline / reproducible experiments without a network stack
* Discrete-event and fixed-step simulations
* Scenarios where you want to model message delays and packet loss
* Collecting agent-level time-series data for analysis


**Key concepts**

* **SimulationWorld**: the container agents register against.  Time is
  controlled by an :class:`~mango.ExternalClock`.
* **step_simulation**: advance the simulation clock by a fixed amount,
  calling all ``on_step`` hooks and delivering pending messages.
* **discrete_step_until**: repeatedly step to the next scheduled event
  (message arrival or task wake-up) until a time limit is reached.
* **CommunicationSimulation**: pluggable model for message delay and loss.
* **DefaultEnvironment**: spatial environment with optional 2-D area and
  custom behaviours.


.. _simulation-fixed-step:

Basic fixed-step simulation
============================

The simplest way to run a simulation is with :func:`~mango.create_world`,
:func:`~mango.step_simulation`, and the ``async with`` context manager:

.. testcode::

    import asyncio
    from mango import Agent, create_world, step_simulation

    class TimedAgent(Agent):
        def on_step(self, env, clock, step_size_s):
            # clock.time is still the *old* time when on_step is called
            print(f"on_step: step_size={step_size_s:.1f}s, clock={clock.time:.1f}s")

    async def run():
        world = create_world(start_time=0.0)
        world.register(TimedAgent())
        async with world:
            await step_simulation(world, step_size_s=1.0)
            await step_simulation(world, step_size_s=2.0)
        print(f"Final time: {world.clock.time:.1f}s")

    asyncio.run(run())

.. testoutput::

    on_step: step_size=1.0s, clock=0.0s
    on_step: step_size=2.0s, clock=1.0s
    Final time: 3.0s

The ``async with world:`` block initialises the world (calling
:meth:`~mango.Agent.on_ready` on every registered agent) and shuts it down on
exit.

.. note::

   :meth:`~mango.Agent.on_step` is called *before* the clock advances to the
   new time.  ``clock.time`` therefore reflects the time at the *beginning* of
   the step, while the messages it sends are stamped with the time at the
   *end* of the step (see :ref:`simulation-step-timing`).


.. _simulation-discrete-event:

Discrete-event simulation
==========================

Pass ``DISCRETE_EVENT`` (the default) to :func:`~mango.step_simulation` to
let the world automatically determine the step size as the time until the next
scheduled event.  :func:`~mango.discrete_step_until` is a convenience wrapper
that keeps stepping until a time limit is reached:

.. testcode::

    import asyncio
    from mango import Agent, create_world, discrete_step_until

    class TickAgent(Agent):
        def __init__(self):
            super().__init__()
            self.tick_count = 0

        def on_ready(self):
            self.schedule_periodic_task(self._tick, delay=5.0)

        async def _tick(self):
            self.tick_count += 1

    async def run():
        world = create_world(start_time=0.0)
        agent = world.register(TickAgent())
        async with world:
            await discrete_step_until(world, max_advance_time_s=20.0)
        print(f"Counted {agent.tick_count} ticks in {world.clock.time:.0f}s")

    asyncio.run(run())

.. testoutput::

    Counted 5 ticks in 20s

The loop stops automatically when there are no more events within the allowed
time window.


.. _simulation-express:

Express API: run_with_simulation
=================================

:func:`~mango.run_with_simulation` mirrors ``run_with_tcp`` / ``run_with_mqtt``
for quick setups:

.. testcode::

    import asyncio
    from mango import Agent, on_message, run_with_simulation, step_simulation, DISCRETE_EVENT

    class EchoAgent(Agent):
        def __init__(self):
            super().__init__()
            self.received = []

        @on_message(str)
        def handle_text(self, content, meta):
            self.received.append(content)

    async def run():
        a1 = EchoAgent()
        a2 = EchoAgent()
        async with run_with_simulation(a1, a2) as world:
            await a1.send_message("ping", a2.addr)
            await step_simulation(world, step_size_s=0.0)
        print(a2.received)

    asyncio.run(run())

.. testoutput::

    ['ping']

You can supply ``(agent, {"aid": "my_id"})`` tuples to request a specific agent
ID, and pass ``start_time``, ``communication_sim``, or ``environment`` keyword
arguments to customise the world.


Message passing and delivery
==============================

Messages sent inside a simulation are *queued* rather than dispatched
immediately.  They are delivered during the next :func:`~mango.step_simulation`
call once the simulation clock has advanced past their scheduled delivery time.

With the default :class:`~mango.SimpleCommunicationSimulation` (zero delay, no
loss), a message stamped at time *t* is delivered in the first step that ends
at or after *t*; a message sent before the first step is handled at the end
of that step:

.. testcode::

    import asyncio
    from mango import Agent, create_world, on_message, step_simulation

    class Relay(Agent):
        def __init__(self):
            super().__init__()
            self.received = []

        @on_message(str)
        def handle_text(self, content, meta):
            self.received.append((content, round(self.context.current_timestamp, 1)))

    async def run():
        world = create_world()
        sender = world.register(Relay())
        receiver = world.register(Relay())
        async with world:
            await sender.send_message("hello", receiver.addr)
            result = await step_simulation(world, step_size_s=1.0)
        print(receiver.received)

    asyncio.run(run())

.. testoutput::

    [('hello', 1.0)]

.. _simulation-step-timing:

Timing within a step
--------------------

:meth:`SimulationWorld.step <mango.simulation.world.SimulationWorld.step>`
from ``t`` to ``t + dt`` always runs in this order:

1. every agent's :meth:`~mango.Agent.on_step` runs with ``clock.time ==
   t`` (roles included), in registration order;
2. the environment steps (its behaviors), the clock still at ``t``;
3. the clock moves to ``t + dt``: tasks due by then wake up;
4. every message due at or before ``t + dt`` is delivered, then the ones
   its handlers and the woken tasks sent that are due too, repeatedly until
   nothing is due;
5. the recorders run: the recording at ``t + dt``.

A message carries the clock time at which its send coroutine runs (its
stamp) and is delivered in the first step that ends at or after ``stamp +
delay``.  ``schedule_instant_message`` queues the send for the next turn of
the event loop (like a task; in a world it is one cheap callback, not a task),
and that turn comes only after the clock moved (step 3), so a send from
``on_step`` at ``t`` is stamped ``t + dt``:

* with delay 0 it is handled in the same step, at ``t + dt``, and so is a
  reply with delay 0;
* with a delay ``0 < d <= dt`` it is handled at ``t + 2 dt``, in general at
  ``t + dt + ceil(d / dt) * dt``; for a lead time of *L* steps between the
  sender's and the receiver's ``on_step`` use ``delay = (L - 1) * dt``;
* a message sent outside a step (``await agent.send_message(...)`` before
  the first step, as above) is stamped with the current time.

In discrete-event mode the world steps to exactly ``stamp + delay`` (or the
next task wake-up), and ``on_step`` and the behaviors run at every such step
with its size.

.. testcode::

    import asyncio
    from mango import Agent, SimpleCommunicationSimulation, create_world, record_world, sender_addr


    class Answerer(Agent):
        def __init__(self, trace):
            super().__init__()
            self.trace = trace

        def handle_message(self, content, meta):
            self.trace.append(f"t={self.scheduler.clock.time:g}  answerer handles {content}")
            self.schedule_instant_message("answer", sender_addr(meta))


    class Asker(Agent):
        def __init__(self, trace, peer):
            super().__init__()
            self.trace, self.peer = trace, peer

        def on_step(self, env, clock, step_size_s):
            self.trace.append(f"t={clock.time:g}  asker on_step")
            self.schedule_instant_message("question", self.peer)

        def handle_message(self, content, meta):
            self.trace.append(f"t={self.scheduler.clock.time:g}  asker handles {content}")


    async def run(delay_s):
        trace = []
        world = create_world(communication_sim=SimpleCommunicationSimulation(default_delay_s=delay_s))
        answerer = world.register(Answerer(trace))
        world.register(Asker(trace, answerer.addr))
        record_world(world, "row", lambda: trace.append(f"t={world.clock.time:g}  record"))
        async with world:
            await world.step(step_size_s=1.0)
            await world.step(step_size_s=1.0)
        return trace


    print("\n".join(asyncio.run(run(0.0))))
    print("--- delay 0.5")
    print("\n".join(asyncio.run(run(0.5))))

.. testoutput::

    t=0  record
    t=0  asker on_step
    t=1  answerer handles question
    t=1  asker handles answer
    t=1  record
    t=1  asker on_step
    t=2  answerer handles question
    t=2  asker handles answer
    t=2  record
    --- delay 0.5
    t=0  record
    t=0  asker on_step
    t=1  record
    t=1  asker on_step
    t=2  answerer handles question
    t=2  record

The recording at ``t + dt`` is therefore the state after the step ``[t, t +
dt)``, and the one at ``t = 0`` the initial state (see
:ref:`simulation-recording`).  The scenario layer adds phases, events and
recorders to this cycle; :ref:`scenario-timing` describes the full order.


.. _simulation-communication:

Communication simulation
=========================

Inject message delays and packet loss by passing a custom
:class:`~mango.SimpleCommunicationSimulation`:

.. testcode::

    import asyncio
    from mango import (
        Agent, create_world, on_message, step_simulation,
        SimpleCommunicationSimulation,
    )

    class Counter(Agent):
        def __init__(self):
            super().__init__()
            self.count = 0

        @on_message(str)
        def handle_text(self, content, meta):
            self.count += 1

    async def run():
        comm = SimpleCommunicationSimulation(default_delay_s=2.0)
        world = create_world(communication_sim=comm)
        sender = world.register(Counter())
        receiver = world.register(Counter())
        async with world:
            await sender.send_message("delayed", receiver.addr)

            # Step to t=1: message arrives at t=2, so not yet delivered
            await step_simulation(world, step_size_s=1.0)
            print(f"After 1s: {receiver.count} message(s)")

            # Step to t=2.5: delivery time (2.0) has now passed
            await step_simulation(world, step_size_s=1.5)
            print(f"After 2.5s: {receiver.count} message(s)")

    asyncio.run(run())

.. testoutput::

    After 1s: 0 message(s)
    After 2.5s: 1 message(s)

Per-link delays can be configured with ``delay_s_directed_edge_dict``:

.. code-block:: python

    from mango import SimpleCommunicationSimulation

    comm = SimpleCommunicationSimulation(
        default_delay_s=0.1,
        loss_percent=0.05,
        delay_s_directed_edge_dict={("agent0", "agent1"): 0.5},
    )

For stochastic delays, use :class:`~mango.DelayProviderCommunicationSimulation`.
A provider that takes one argument receives the simulation's random
generator, which the world binds to its ``"comm"`` stream (see
:ref:`simulation-randomness`); zero-argument providers keep working.
``loss_percent`` adds random message loss:

.. code-block:: python

    from mango import DelayProviderCommunicationSimulation

    comm = DelayProviderCommunicationSimulation(
        default_delay_s_provider=lambda rng: max(0.0, rng.normal(0.1, 0.02)),
        loss_percent=0.01,
    )


Topology-aware communication simulation
-----------------------------------------

When your agents form a graph (e.g. a communication network), use
:func:`~mango.create_distribution_based_com_sim` to derive per-link delays
from shortest-path distances in that graph.  The delay for a
``(sender, receiver)`` pair grows with the number of hops between them:

.. code-block:: python

    import networkx as nx
    from mango import create_distribution_based_com_sim, create_world

    # Linear topology: agent0 -- agent1 -- agent2
    g = nx.path_graph(["agent0", "agent1", "agent2"])

    comm = create_distribution_based_com_sim(
        g,
        default_delay_per_edge_ms=20.0,   # 20 ms per hop
        base_delay_per_message_ms=5.0,    # 5 ms fixed overhead
    )
    world = create_world(communication_sim=comm)

The node labels in *g* must match the agent AIDs registered in the world.
:func:`~mango.create_distribution_based_com_sim` returns a
:class:`~mango.DelayProviderCommunicationSimulation`, so you can combine it
with a custom ``distribution_provider`` to draw delays from any distribution:

.. code-block:: python

    import networkx as nx
    from mango import create_distribution_based_com_sim

    g = nx.grid_2d_graph(4, 4)  # or any NetworkX graph

    # Gaussian jitter around the mean hop-distance delay, drawn from the
    # world's "comm" stream
    sim = create_distribution_based_com_sim(
        g,
        default_delay_per_edge_ms=10.0,
        max_edge_delay_ms=100.0,          # cap at 100 ms regardless of hops
        distribution_provider=lambda mean_ms: (
            lambda rng: max(0.0, rng.normal(mean_ms, mean_ms * 0.1)) / 1000.0
        ),
        loss_percent=0.02,
    )

Both directed and undirected graphs are supported.  For directed graphs, only
the directed edges contribute to the routing; a pair with no path simply
falls back to the default provider (zero delay).


.. _simulation-randomness:

Reproducible randomness
=======================

A world owns one root seed, a :class:`~mango.SeedTree`, that hands out
independent random streams (``numpy.random.Generator``) keyed by stable
names.  A stream depends only on the root seed and its name, so adding,
removing or reordering agents never changes the numbers another agent draws
from its own stream (``self.rng``).  Shared streams such as ``"space"`` and
``"comm"`` are different: they are consumed in registration and message
order (see *Shared streams* below).

.. testcode::

    import asyncio
    from mango import Agent, Area2D, DefaultEnvironment, create_world

    class EV(Agent):
        def on_ready(self):
            # the agent's own stream, keyed by its aid
            self.arrival_h = self.rng.normal(17.5, 1.0)
            # an independent sub-stream for another concern
            self.demand_kwh = self.rng_for("demand").uniform(5.0, 40.0)

        def handle_message(self, content, meta):
            pass

    async def run(seed):
        world = create_world(
            seed=seed, environment=DefaultEnvironment(space=Area2D(10, 10))
        )
        ev = world.register(EV(), suggested_aid="ev_0")
        async with world:
            return ev.arrival_h, ev.demand_kwh, world.environment.space.location(ev)

    first = asyncio.run(run(42))
    second = asyncio.run(run(42))
    print(first == second)

.. testoutput::

    True

**Streams.** ``world.rng(name)`` returns the stream *name*; names are
``/``-separated paths (``world.rng("agent/ev_0")`` is the stream of the
sub-tree ``"agent"`` → ``"ev_0"``; ``world.rng("agent", "ev_0")`` is the
same stream).  Each name maps to a single cached generator, so everyone asking
for the same name shares (and advances) one stream.

**Agents and roles.** ``self.rng`` inside an agent is
``world.rng(f"agent/{aid}")``; ``self.rng_for("demand")`` is the independent
stream ``"agent/<aid>/demand"``.  The aid is always one key segment, even if
it contains ``/`` (then the stream is ``world.rng("agent",
name_key(aid))``, see :func:`~mango.util.seeding.name_key`), so aids ``g``
and ``g/x`` never share a stream.  Roles use their agent's stream via
``self.rng`` and ``self.rng_for(...)`` (also reachable as
``self.context.rng``).  The streams exist from registration on
(``on_register``, ``on_start``, ``on_ready``); accessing them earlier raises
:class:`RuntimeError`.  An agent or role that assigns ``self.rng = ...``
(including ``None``) keeps that value, like a plain attribute;
``del self.rng`` returns to the seeded stream.  Agents
registered with generated aids (``agent0``, ``agent1``, ...) get streams that
depend on registration order, so pass ``suggested_aid`` for streams that stay
stable when the scenario changes.  Networked containers have the same API:
``create_tcp_container(addr, seed=42)``; agent processes started with
``as_agent_process`` share the parent's seed, so an agent's stream does not
depend on the process it runs in.

**Binding protocol.** A component that wants a world stream exposes a public
attribute ``rng`` initialised to ``None``.  When the world is created it binds:

* the communication simulation (also one assigned later to
  ``world.communication_sim``) to ``"comm"``,
* ``DefaultEnvironment.space`` (e.g. :class:`~mango.Area2D`) to ``"space"``,
* ``DefaultEnvironment.behavior`` to ``"behavior"``,
* any other environment with an ``rng`` attribute to ``"environment"``.

Components created with an explicit ``rng=...`` keep it.  A component that a
previous world bound is rebound to the new world's stream, so one
communication simulation or space can be reused across the runs of a sweep:
``create_world(seed=s, communication_sim=shared_sim)`` draws from seed ``s``
every time.  To hand a world stream to a component *before* the world exists,
create the :class:`~mango.SeedTree` first and pass it as ``seed`` (a
``SeedTree`` is used as-is, so both share the cached streams):

.. code-block:: python

    from mango import Area2D, DefaultEnvironment, SeedTree, create_world

    tree = SeedTree(42)
    space = Area2D(10, 10, rng=tree.rng("space"))
    world = create_world(seed=tree, environment=DefaultEnvironment(space=space))
    assert space.rng is world.rng("space")

Write your own behaviors the same way:

.. code-block:: python

    from mango import Behavior

    class Weather(Behavior):
        def __init__(self, rng=None):
            self.rng = rng          # bound to world.rng("behavior")

        def on_step(self, environment, clock, step_size_s):
            self.cloudiness = self.rng.uniform(0.0, 1.0)

Inside a world nothing draws from the global :mod:`random` module.  Used on
their own (never bound), :class:`~mango.Area2D` and
:class:`~mango.SimpleCommunicationSimulation` fall back to it.

**Shared streams.** Only per-agent streams are independent of the rest of the
scenario.  A shared component stream is consumed in order: :class:`~mango.Area2D`
draws the initial positions of all agents one after the other in registration
order, and the communication simulation draws loss and delay per message in
delivery order.  Registering another agent *before* ``ev_0`` therefore moves
``ev_0``'s random position (registering it after does not).  For positions
that must not depend on the rest of the scenario, set them yourself before the
world starts, drawn from the agent's own streams:

.. code-block:: python

    ev = world.register(EV(), suggested_aid="ev_0")
    x, y = ev.rng_for("position").uniform(0.0, 10.0, size=2)
    world.environment.space.move(ev, Position2D(float(x), float(y)))

**Replaying a run.** ``world.seed`` is the seed identity as plain, JSON-safe
data.  Store it with your results; ``create_world(seed=world.seed)`` rebuilds
every stream.  Without a ``seed`` the root entropy is drawn from the global
:mod:`random` module (``random.seed(x)`` at the top of a script still makes
the run reproducible) and recorded in ``world.seed``:

.. code-block:: python

    import json
    from pathlib import Path

    world = create_world()                       # unseeded run
    Path("seed.json").write_text(json.dumps(world.seed))
    replay = create_world(seed=json.loads(Path("seed.json").read_text()))

:func:`~mango.run_with_simulation` accepts the same ``seed`` keyword.


.. _simulation-calendar:

Calendar time
=============

The simulation clock counts seconds.  Give the world a ``start_datetime`` to
map those seconds onto calendar time (a ``datetime`` or ISO 8601 string;
naive and timezone-aware values are kept as given).  Clock seconds are elapsed
seconds: with a timezone-aware start (e.g. ``ZoneInfo("Europe/Berlin")``)
the conversion is done in UTC, so across a daylight saving switch the
calendar jumps by an hour while the clock keeps counting; a naive start uses
plain wall-clock arithmetic.

.. testcode::

    import asyncio
    from mango import create_world

    async def run():
        world = create_world(start_datetime="2024-06-01T00:00")
        async with world:
            await world.step(step_size_s=3600)
            print(world.datetime)
            print(world.clock.to_time("2024-06-01T12:00"))
            print(world.clock.to_datetime(1800))

    asyncio.run(run())

.. testoutput::

    2024-06-01 01:00:00
    43200.0
    2024-06-01 00:30:00

``clock.has_calendar`` tells whether a calendar is configured; without one,
``clock.datetime`` raises :class:`RuntimeError`.  The same calendar is
available on an :class:`~mango.ExternalClock` created directly
(``ExternalClock(start_datetime=...)``) and through
``run_with_simulation(..., start_datetime=...)``.


Role-based agents in simulation
=================================

:class:`~mango.Role` classes support the same ``on_step``, ``on_global_event``
and ``on_agent_event`` hooks as agents:

.. testcode::

    import asyncio
    from mango import Role, RoleAgent, run_with_simulation, step_simulation

    class LoggingRole(Role):
        def __init__(self):
            super().__init__()
            self.steps = []

        def on_step(self, env, clock, step_size_s):
            self.steps.append(round(step_size_s, 1))

    async def run():
        agent = RoleAgent()
        role = LoggingRole()
        agent.add_role(role)
        async with run_with_simulation(agent) as world:
            await step_simulation(world, step_size_s=1.0)
            await step_simulation(world, step_size_s=2.0)
        print(role.steps)

    asyncio.run(run())

.. testoutput::

    [1.0, 2.0]


.. _simulation-recording:

Data recording
==============

Use :func:`~mango.record_world`, :func:`~mango.record_agent`, and
:func:`~mango.record_agent_having` to collect time-series data automatically
after every step:

.. testcode::

    import asyncio
    from mango import Agent, create_world, step_simulation, record_agent

    class ValueAgent(Agent):
        def __init__(self, value):
            super().__init__()
            self.value = float(value)

        def on_step(self, env, clock, step_size_s):
            self.value += step_size_s

    async def run():
        world = create_world()
        a1 = world.register(ValueAgent(0))
        a2 = world.register(ValueAgent(10))

        record_agent(world, "value", lambda a: a.value)

        async with world:
            await step_simulation(world, step_size_s=1.0)
            await step_simulation(world, step_size_s=1.0)

        rec = world.data_agent_collections["value"]
        print(rec.timeseries["agent0"])
        print(rec.timeseries["agent1"])

    asyncio.run(run())

.. testoutput::

    [0.0, 1.0, 2.0]
    [10.0, 11.0, 12.0]

Recordings capture the state at initialisation (``t=0``) and after each step,
so the list length is always ``number_of_steps + 1``.  The value recorded at
``t`` is the state after the step that ended at ``t``: a value an agent
computes in ``on_step`` with ``clock.time == t - dt`` appears at ``t`` (see
:ref:`simulation-step-timing`).  For quantities per interval such as energy,
sum ``value x step`` over the recordings at ``t > 0``, never the initial
one.

:func:`~mango.record_agent_having` restricts recording to agents that carry a
specific role type:

.. code-block:: python

    from mango import record_agent_having
    from myproject.roles import EnergyRole

    record_agent_having(world, "energy", EnergyRole, lambda a: a.get_role(EnergyRole).level)

Use :func:`~mango.record_position` and :func:`~mango.position_history` to
track agent positions when a spatial environment is active:

.. code-block:: python

    from mango import Area2D, DefaultEnvironment, record_position, position_history

    env = DefaultEnvironment(space=Area2D(width=100.0, height=100.0))
    world = create_world(environment=env)
    # … register agents …
    record_position(world)

    async with world:
        await discrete_step_until(world, max_advance_time_s=60.0)

    history = position_history(world)
    # history.timeseries["agent0"]  →  list of Position2D


.. _simulation-tidy-export:

Exporting recordings as a tidy table
------------------------------------

:func:`~mango.recordings_to_records` (or ``world.to_records()``) flattens all
recordings into one long table with the columns
:data:`~mango.simulation.recording.TIDY_COLUMNS`: ``time``, ``datetime`` (only with a calendar),
``group``, ``entity``, ``variable`` and ``value``.
:func:`~mango.recordings_to_frame` (``world.to_frame()``) returns the same
rows as a ``pandas.DataFrame`` (pandas must be installed).

.. testcode::

    import asyncio
    from mango import Agent, create_world, record_agent, record_world

    class ValueAgent(Agent):
        def __init__(self, value):
            super().__init__()
            self.value = value

        def on_step(self, env, clock, step_size_s):
            self.value += 1

        def handle_message(self, content, meta):
            pass

    async def run():
        world = create_world()
        world.register(ValueAgent(0), suggested_aid="ev_0")
        record_agent(world, "value", lambda a: a.value)
        record_world(world, "load_kw", lambda: 3.5, entity="grid")
        async with world:
            await world.step(step_size_s=1.0)
        for row in world.to_records(groups={"ev_0": "ev"}):
            print(row)

    asyncio.run(run())

.. testoutput::

    {'time': 0.0, 'group': 'grid', 'entity': 'grid', 'variable': 'load_kw', 'value': 3.5}
    {'time': 1.0, 'group': 'grid', 'entity': 'grid', 'variable': 'load_kw', 'value': 3.5}
    {'time': 0.0, 'group': 'ev', 'entity': 'ev_0', 'variable': 'value', 'value': 0}
    {'time': 1.0, 'group': 'ev', 'entity': 'ev_0', 'variable': 'value', 'value': 1}

* Agent rows: ``entity`` is the aid; ``group`` comes from the optional
  ``groups`` mapping (aid → group), else from the agent's
  ``description.category``.
* World rows: ``group`` and ``entity`` are the recording's ``entity``
  (``record_world(..., entity="grid")``; default ``"world"``).
* Rows are ordered deterministically: world recordings, then agent
  recordings, each in the order they were registered; aids in recording
  order; times ascending.
* ``keys=[...]`` restricts the export to some recordings;
  ``include_world`` / ``include_agents`` drop one kind.
* Values from :func:`~mango.collect_agent_data` use the shared time axis
  when they were recorded on every step; otherwise append to
  ``recording.agent_time[aid]`` alongside each value.


.. _simulation-spatial-environment:

Spatial environment
====================

A :class:`~mango.DefaultEnvironment` can give agents a *space* in which they
have positions and can move relative to each other.  The default space is
:class:`~mango.NoSpace`, which has no positioning at all; pass an
:class:`~mango.Area2D` explicitly to get a rectangular 2-D arena.

With an :class:`~mango.Area2D`, during world initialisation every agent without
a pre-assigned position receives a random location within the arena.  You can
override this by calling :meth:`~mango.Area2D.move` before the world starts:

.. testcode::

    import asyncio
    from mango import Agent, Area2D, DefaultEnvironment, Position2D, create_world, step_simulation

    class Rover(Agent):
        def on_step(self, env, clock, step_size_s):
            # Move 1 unit toward the origin on every step
            env.space.move_toward(self, Position2D(0.0, 0.0), max_step=1.0)

    async def run():
        space = Area2D(width=10.0, height=10.0)
        env = DefaultEnvironment(space=space)
        world = create_world(environment=env)
        rover = world.register(Rover())

        # Place the rover at a known position before the simulation starts
        space.move(rover, Position2D(3.0, 4.0))

        async with world:
            await step_simulation(world, step_size_s=1.0)

        pos = space.location(rover)
        dist = space.distance(rover, rover)    # trivially 0; illustrates the API
        print(f"Position after one step: ({pos.x:.2f}, {pos.y:.2f})")

    asyncio.run(run())

.. testoutput::

    Position after one step: (2.40, 3.20)


Spatial helper methods
-----------------------

:class:`~mango.Area2D` keeps every agent's position (agents do not need to
mirror it in an attribute). Agents reach the space through the ``env``
argument of :meth:`~mango.Agent.on_step` (``env.space``), behaviors through
their ``environment`` argument; ``environment.agents`` lists every installed
agent:

.. list-table::
   :widths: 35 65
   :header-rows: 1

   * - Method
     - Description
   * - ``space.location(agent)``
     - The agent's current :class:`~mango.Position2D`.
   * - ``space.positions()``
     - ``{aid: Position2D}`` of every placed agent (a copy).
   * - ``space.agents``
     - All agents that have a position, in placement order.
   * - ``space.neighbors_within(agent, radius)``
     - Agents within *radius* of *agent*, nearest first (excluding *agent*).
       *agent* may also be a :class:`~mango.Position2D` (agents near a point).
   * - ``space.agents_within(center, radius, agents=None)``
     - Agents within *radius* of *center*, in candidate order. *center* is an
       agent (excluded from the result) or a :class:`~mango.Position2D`, such as
       a waypoint; *agents* restricts the candidates (default: all placed agents).
   * - ``space.distance(a, b)``
     - Euclidean distance between two agents or :class:`~mango.Position2D` points.
   * - ``space.move(agent, position)`` / ``space.move_toward(agent, target, max_step)``
     - Place *agent*, or move it toward *target* (another agent or a
       :class:`~mango.Position2D`) by at most *max_step* units.

The neighbour queries scan all placed agents, so their cost is linear in the
population per call.

.. testcode::

    import asyncio

    from mango import Agent, Area2D, DefaultEnvironment, Position2D, create_world

    class Bird(Agent):
        def handle_message(self, content, meta):
            pass

        def on_step(self, env, clock, step_size_s):
            flock = env.space.neighbors_within(self, radius=5.0)
            if flock:
                env.space.move_toward(self, flock[0], max_step=1.0)

    async def main():
        world = create_world(environment=DefaultEnvironment(space=Area2D(20, 20)))
        birds = [world.register(Bird(), suggested_aid=f"bird{i}") for i in range(3)]
        for bird, x in zip(birds, (0.0, 3.0, 15.0)):
            world.environment.space.move(bird, Position2D(x, 0.0))
        async with world:
            await world.step(step_size_s=1.0)
        print({aid: p.x for aid, p in world.environment.space.positions().items()})

    asyncio.run(main())

.. testoutput::

    {'bird0': 1.0, 'bird1': 2.0, 'bird2': 15.0}

The standalone :func:`~mango.distance` function works directly on two
:class:`~mango.Position2D` objects without needing a space:

.. testcode::

    from mango import Position2D, distance

    pa = Position2D(0.0, 0.0)
    pb = Position2D(3.0, 4.0)
    print(distance(pa, pb))

.. testoutput::

    5.0

A typical movement loop inside a :class:`~mango.Behavior`:

.. code-block:: python

    from mango import Behavior

    class SwarmBehavior(Behavior):
        """Move each agent toward the nearest neighbour."""

        def on_step(self, environment, clock, step_size_s):
            space = environment.space
            for agent in environment.agents:
                nearby = space.neighbors_within(agent, radius=5.0)
                if nearby:
                    space.move_toward(agent, nearby[0], max_step=step_size_s * 2.0)


Environment events
-------------------

The environment can broadcast events to all agents or target a single one:

.. code-block:: python

    # Broadcast to every agent (and all their roles)
    world.environment.emit_global_event(WeatherChangeEvent(wind=15.0))

    # Deliver to one specific agent by the ID used during install()
    world.environment.emit_agent_event(TaskAssigned(task_id=42), agent_id="agent0")

Agents receive these events via ``on_global_event`` and ``on_agent_event``
hooks.  For roles the same hooks are called on every role attached to the
targeted agent:

.. code-block:: python

    from mango import Agent

    class SensorAgent(Agent):
        def on_global_event(self, event):
            print(f"Broadcast event: {event}")

        def on_agent_event(self, event):
            print(f"Targeted event: {event}")

To use ``emit_agent_event`` you must first install the agent in the
environment so it can be looked up by ID:

.. code-block:: python

    world.environment.install(my_agent, agent_id=my_agent.aid)
    world.environment.emit_agent_event("ping", my_agent.aid)


Accessing recorded messages
============================

Every delivered message is logged in ``world.recorded_messages``, and
counted in ``world.messages_delivered``:

.. testcode::

    import asyncio
    from mango import Agent, create_world, on_message, step_simulation, MessageTransaction

    class Ping(Agent):
        @on_message(str)
        def handle_text(self, content, meta):
            pass

    async def run():
        world = create_world()
        a1 = world.register(Ping())
        a2 = world.register(Ping())
        async with world:
            await a1.send_message("hi", a2.addr)
            await step_simulation(world, step_size_s=1.0)

        tx = world.recorded_messages[0]
        print(isinstance(tx, MessageTransaction))
        print(tx.sender_id, "->", tx.receiver_id)

    asyncio.run(run())

.. testoutput::

    True
    agent0 -> agent1

The log keeps every message with its content for the whole run, so its memory
grows with the number of messages (a small dict payload costs about 1-2 KB per
message). For long runs with much traffic create the world with
``create_world(record_messages=False)``: messages are then only counted
(``world.messages_delivered``), and reading ``world.recorded_messages`` raises
an error that says so. Scenario runs do this by default; a run keeps its
messages with ``scenario.run(..., record_messages=True)`` (or
``scenario.build(..., record_messages=True)``).


.. _simulation-agent-description:

Agent description and metadata
================================

Every agent has an :class:`~mango.AgentDescription` that can be used to attach
human-readable metadata:

.. testcode::

    import asyncio
    from mango import Agent, create_world

    class NamedAgent(Agent):
        def on_register(self):
            self.update_description(name="Alice", color="blue", category="sensor")

    async def run():
        world = create_world()
        agent = world.register(NamedAgent())
        print(agent.name, agent.color, agent.category)

    asyncio.run(run())

.. testoutput::

    Alice blue sensor


.. _simulation-agent-errors:

Agent errors
============

An exception that escapes an agent's scheduled task fails the step in which it
happened. Scheduled tasks are periodic and timestamp tasks, coroutines started
with ``schedule_instant_task`` (for example async work that ``handle_message``
spawns) and role coroutines. The world finishes the step (the clock advances
and the recorders run) and then raises :class:`~mango.AgentTaskError`; its
``errors`` list holds every :class:`~mango.AgentError` of that step and the
first exception is chained as ``__cause__``. Run through the scenario layer,
such a run is marked failed. An exception raised synchronously by
``handle_message`` stops the agent's message processing; the world then raises
a ``RuntimeError`` at the next settling point. An exception in ``on_step`` (of
an agent or a role) propagates from ``world.step`` at once, with the note
``while stepping agent 'a_0' (role R) in on_step at t=2 s``
(``exception.__notes__``).

.. testcode::

    import asyncio
    from mango import Agent, AgentTaskError, create_world

    class Flaky(Agent):
        def on_ready(self):
            self.schedule_periodic_task(self.tick, delay=1.0)

        async def tick(self):
            if self.scheduler.clock.time >= 2.0:
                raise ValueError("sensor offline")

    async def run():
        world = create_world()
        world.register(Flaky(), "flaky")
        async with world:
            try:
                await world.step_until(10.0)
            except AgentTaskError as error:
                first = error.errors[0]
                print(first.aid, first.time, repr(first.exception))

    asyncio.run(run())

.. testoutput::

    flaky 2.0 ValueError('sensor offline')

``create_world(..., raise_agent_errors=False)`` keeps the old behaviour: task
errors are only logged and collected in
:attr:`SimulationWorld.agent_errors <mango.SimulationWorld.agent_errors>`.

The world learns about task errors through the scheduler hook
:attr:`Scheduler.on_task_error <mango.util.scheduling.Scheduler.on_task_error>`,
``fn(task, exception)``, called whenever a scheduled task raises (never on
cancellation); outside a simulation the error is logged with its traceback
(``logger.error``) unless the hook returns ``True``.

.. _simulation-reserved-names:

**Agent attributes reserved by mango.** :class:`~mango.Agent` uses the
attribute names ``aid``, ``addr``, ``name``, ``color``, ``category``,
``uid``, ``description``, ``inbox``, ``context`` and ``scheduler`` itself.
Assigning one of them in a subclass (``self.name = "alice"``,
``self.inbox = []``) raises an ``AttributeError`` that names the fix (for
example :meth:`~mango.Agent.update_description` for ``name``); ``rng`` may be
replaced.  A subclass whose ``__init__`` forgets ``super().__init__()``
gets an error that says so on its first use, instead of a missing
attribute deep inside mango.  In a :class:`~mango.Role` the name
``context`` (the role context) is reserved the same way.

mango calls ``handle_message(content, meta)`` with two positional arguments,
so the parameter names of an override are free.  When message handlers fail,
the world raises one error for the first failing agent, says how many other
agents failed the same way and chains the traceback, instead of logging one
traceback per agent.


.. _simulation-performance:

Performance and sizing
======================

Before time advances and after it, the world *settles* its agents: every inbox
is processed and every scheduled task is sleeping or done
(:func:`~mango.util.termination_detection.tasks_complete_or_sleeping` with
``drain_loop=True``, which also waits until the event loop has nothing left to
run; containers next to real-time traffic settle without it, see
:func:`~mango.util.termination_detection.settle`). A settle looks only at the
agents that have work: an agent is marked when a message reaches its inbox or
one of its scheduled tasks starts running (it was scheduled, or the clock or a
message woke it), and dropped again once it is idle (see
:class:`~mango.util.termination_detection.AgentActivity`). The clock keeps its
sleepers in a heap and the world dispatches ``on_step`` only to agents and
roles that override it (the list is rebuilt when agents are registered or
deregistered or roles are added or removed). So the framework's cost of a step
grows with the agents that act in it, not with the population: idle agents
with sleeping periodic tasks cost nothing per step. What does grow with the
population is the work the model itself does per step, such as ``on_step``
hooks of every agent or recorders that read every agent.

Measured on one core of a laptop (Python 3.13):

.. list-table::
   :widths: 60 40
   :header-rows: 1

   * - Setup
     - Cost
   * - 1000 or 10,000 agents, each with a sleeping periodic task; one agent ticks
     - about 0.02 to 0.05 ms per step
   * - gossip, 128 or 1024 agents, one message per tick, event-driven
     - about 0.13 ms per step (about 7,500 messages/s) for both sizes
   * - SEIR contact model, 1000 agents with ``on_step``, 120 daily steps,
       about 41,000 exposure messages
     - about 2.3 s per run
   * - one agent sending to 40 or 400 others with ``schedule_instant_message``
       in ``on_step``, zero delay
     - about 20 to 25 µs per message (send, delivery and handling); a direct
       method call costs about 0.5 µs

For large populations:

* model many simple entities inside one agent, or as a vectorised
  :class:`~mango.Behavior` of the environment (for example the compartments of
  a population model), and keep agents for the actors that decide and talk;
* prefer fixed steps with ``on_step`` for synchronous models; event-driven runs
  take one step per distinct event time;
* keep messages for real communication; for dense or market-wide contact
  processes (every infectious agent touching all its contacts every step)
  let one agent or a :class:`~mango.Behavior` work on shared state in the
  environment, or call methods directly;
* spread replications and parameter variants over processes with the study
  layer instead of making a single run bigger.


.. _simulation-visualization:

Visualization
==============

The :mod:`mango.simulation.visualization` module turns world recordings and
message logs into matplotlib figures.  It requires ``matplotlib``::

    pip install matplotlib

.. note::

    All visualization functions accept a *write_to* keyword argument.
    When supplied, the figure is saved to that path instead of being
    returned.  If not supplied, the figure object is returned and you can
    show it with ``fig.show()`` or ``plt.show()``.


plot_world
----------

:func:`~mango.plot_world` draws a world-level recording as a line chart:

.. code-block:: python

    import asyncio
    from mango import (
        Agent, create_world, on_message, step_simulation, record_world, plot_world,
    )

    class TrafficAgent(Agent):
        @on_message(str)
        def handle_text(self, content, meta): pass

    async def run():
        world = create_world()
        sender = world.register(TrafficAgent())
        receiver = world.register(TrafficAgent())

        record_world(world, "messages", lambda: len(world.recorded_messages))

        async with world:
            for _ in range(5):
                await sender.send_message("ping", receiver.addr)
                await step_simulation(world, step_size_s=1.0)

        fig = plot_world(world, "messages",
                         title="Cumulative messages over time",
                         ylabel="# delivered messages")
        fig.savefig("traffic.png")

    asyncio.run(run())


plot_agents
-----------

:func:`~mango.plot_agents` plots a per-agent recording with one labelled line
per agent.  Agent names (set via :meth:`~mango.Agent.update_description`) are
used as legend labels when available:

.. code-block:: python

    from mango import record_agent, plot_agents

    record_agent(world, "value", lambda a: a.value)

    async with world:
        await discrete_step_until(world, max_advance_time_s=60.0)

    plot_agents(world, "value",
                ylabel="Agent value",
                write_to="values.png")


plot_recordings
---------------

:func:`~mango.plot_recordings` renders **all** recordings (both world-level
and per-agent) in a single grid figure, ideal for a quick experiment
overview:

.. code-block:: python

    from mango import record_world, record_agent, plot_recordings

    record_world(world, "clock", lambda: world.clock.time)
    record_agent(world, "soc", lambda a: a.soc_kwh)

    async with world:
        await discrete_step_until(world, max_advance_time_s=3600.0)

    # Three recordings side by side, saved to a file
    plot_recordings(world, write_to="overview.png")

    # With a custom colormap for agent lines
    plot_recordings(world, colormap="tab10")


show_communication_data
-----------------------

:func:`~mango.show_communication_data` produces a *message-flow timeline*:
each agent appears as a horizontal lane, and every delivered message is drawn
as an annotated arrow from the sender lane at send-time to the receiver lane
at delivery-time.

This is especially useful for debugging delayed or lost messages:

.. code-block:: python

    from mango import show_communication_data

    async with world:
        await discrete_step_until(world, max_advance_time_s=10.0)

    fig = show_communication_data(
        world,
        aid_to_name={"agent0": "Producer", "agent1": "Consumer"},
        aid_to_color={"agent0": "steelblue", "agent1": "tomato"},
        write_to="comms.png",
    )

When *aid_to_name* is omitted, agent AIDs are used as labels.  When
*aid_to_color* is omitted, matplotlib's default colour cycle is applied.


.. _simulation-behavior-in:

Attaching behavior with behavior_in
====================================

An agent normally declares its handlers on the class with
:func:`~mango.on_message` (see :ref:`Handling messages <agent-handlers>`).
:func:`~mango.behavior_in` attaches message handlers and event subscriptions
to a matched set of agents **without modifying their class definitions**,
which is what you want for instrumenting third-party agents or for varying
behaviour from one experiment run to the next.  It is simulation-only and
requires a :class:`~mango.simulation.world.SimulationWorld`.

.. code-block:: python

    from mango import behavior_in

    # All agents: handler fires for every TypedMessage received
    behavior_in(
        world,
        lambda agent, content, meta: print(agent.aid, content),
        on_message=TypedMessage,
    )

    # Only SensorAgent instances: react to a global AlarmEvent
    behavior_in(
        world,
        lambda agent, event: print(f"{agent.aid} triggered"),
        on_global_event=AlarmEvent,
        agent_types=SensorAgent,
    )

    # Only agents whose CoordRole matched: handle an agent event on the role
    behavior_in(
        world,
        lambda role, event: setattr(role, "count", role.count + 1),
        on_agent_event=UpdateEvent,
        role_types=CoordRole,
    )

Matching criteria (all optional, combined with OR logic for agent-level
filters):

+------------------+-------------------------------------------------------------+
| Keyword          | Matches agents that …                                       |
+==================+=============================================================+
| ``agent_types``  | are instances of one of the given types                     |
+------------------+-------------------------------------------------------------+
| ``has_roles``    | have at least one role of the given type(s)                 |
+------------------+-------------------------------------------------------------+
| ``role_types``   | have a role of one of these types; handler receives the     |
|                  | role as its first argument                                  |
+------------------+-------------------------------------------------------------+
| ``match_names``  | have a matching ``name`` in their                           |
|                  | :class:`~mango.AgentDescription`                            |
+------------------+-------------------------------------------------------------+
| ``match_colors`` | have a matching ``color`` in their description              |
+------------------+-------------------------------------------------------------+

If no matching criteria are provided, **all agents** in the world are matched.

Handler signatures
------------------

.. code-block:: python

    # on_message
    def handler(agent, content, meta): ...

    # on_global_event / on_agent_event
    def handler(agent, event): ...

    # When role_types is used the first arg is the matched role
    def handler(role, content_or_event, ...): ...

behavior_in fires **in addition to** the agent's own handlers, never instead
of them.  Its handlers run after the ``@on_message`` ones and before
``handle_message``, and role subscriptions are untouched.

Optional *preprocessor*
------------------------

Pass a :class:`~mango.MessagePreprocessor` (or
:class:`~mango.WaitingMessagePreprocessor`) via the *preprocessor* keyword to
wrap message delivery for the registered handlers:

.. code-block:: python

    behavior_in(
        world,
        my_handler,
        on_message=MyMsg,
        preprocessor=WaitingMessagePreprocessor(),
    )


API reference
=============

See :doc:`api_ref/mango.simulation` for the full API documentation.
