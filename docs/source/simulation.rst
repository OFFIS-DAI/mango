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

* **SimulationWorld** – the container agents register against.  Time is
  controlled by an :class:`~mango.ExternalClock`.
* **step_simulation** – advance the simulation clock by a fixed amount,
  calling all ``on_step`` hooks and delivering pending messages.
* **discrete_step_until** – repeatedly step to the next scheduled event
  (message arrival or task wake-up) until a time limit is reached.
* **CommunicationSimulation** – pluggable model for message delay and loss.
* **DefaultEnvironment** – spatial environment with optional 2-D area and
  custom behaviours.


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
   the step.


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


Express API: run_with_simulation
=================================

:func:`~mango.run_with_simulation` mirrors ``run_with_tcp`` / ``run_with_mqtt``
for quick setups:

.. testcode::

    import asyncio
    from mango import Agent, run_with_simulation, step_simulation, DISCRETE_EVENT

    class EchoAgent(Agent):
        def __init__(self):
            super().__init__()
            self.received = []

        def handle_message(self, content, meta):
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
loss), messages sent at time *t* are available for delivery from *t* onward:

.. testcode::

    import asyncio
    from mango import Agent, create_world, step_simulation

    class Relay(Agent):
        def __init__(self):
            super().__init__()
            self.received = []

        def handle_message(self, content, meta):
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


Communication simulation
=========================

Inject message delays and packet loss by passing a custom
:class:`~mango.SimpleCommunicationSimulation`:

.. testcode::

    import asyncio
    from mango import (
        Agent, create_world, step_simulation,
        SimpleCommunicationSimulation,
    )

    class Counter(Agent):
        def __init__(self):
            super().__init__()
            self.count = 0

        def handle_message(self, content, meta):
            self.count += 1

    async def run():
        comm = SimpleCommunicationSimulation(default_delay_s=2.0)
        world = create_world(communication_sim=comm)
        sender = world.register(Counter())
        receiver = world.register(Counter())
        async with world:
            await sender.send_message("delayed", receiver.addr)

            # Step to t=1 – message arrives at t=2, so not yet delivered
            await step_simulation(world, step_size_s=1.0)
            print(f"After 1s: {receiver.count} message(s)")

            # Step to t=2.5 – delivery time (2.0) has now passed
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

For stochastic delays, use :class:`~mango.DelayProviderCommunicationSimulation`:

.. code-block:: python

    import random
    from mango import DelayProviderCommunicationSimulation

    comm = DelayProviderCommunicationSimulation(
        default_delay_s_provider=lambda: random.gauss(0.1, 0.02)
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

    # Linear topology: agent0 — agent1 — agent2
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

    import random, networkx as nx
    from mango import create_distribution_based_com_sim

    g = nx.grid_2d_graph(4, 4)  # or any NetworkX graph

    # Gaussian jitter around the mean hop-distance delay
    sim = create_distribution_based_com_sim(
        g,
        default_delay_per_edge_ms=10.0,
        max_edge_delay_ms=100.0,          # cap at 100 ms regardless of hops
        distribution_provider=lambda mean_ms: (
            lambda: max(0.0, random.gauss(mean_ms, mean_ms * 0.1)) / 1000.0
        ),
    )

Both directed and undirected graphs are supported.  For directed graphs, only
the directed edges contribute to the routing — a pair with no path simply
falls back to the default provider (zero delay).


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
so the list length is always ``number_of_steps + 1``.

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


Spatial environment
====================

A :class:`~mango.DefaultEnvironment` gives agents a *space* in which they have
positions and can move relative to each other.  The default space is
:class:`~mango.Area2D`, a rectangular 2-D arena.

During world initialisation every agent without a pre-assigned position
receives a random location within the arena.  You can override this by calling
:meth:`~mango.Area2D.move` before the world starts:

.. testcode::

    import asyncio
    from mango import Agent, Area2D, DefaultEnvironment, Position2D, create_world, step_simulation

    class Rover(Agent):
        def on_step(self, env, clock, step_size_s):
            # Move 1 unit toward the origin on every step
            env.space.move_toward(self, Position2D(0.0, 0.0), max_step=1.0)

        def handle_message(self, content, meta):
            pass

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

:class:`~mango.Area2D` provides three spatial helpers, all operating on
registered agents:

.. list-table::
   :widths: 35 65
   :header-rows: 1

   * - Method
     - Description
   * - ``space.distance(agent_a, agent_b)``
     - Euclidean distance between two agents' current positions.
   * - ``space.agents_within(center, radius, candidates)``
     - Returns all agents from *candidates* within *radius* of *center*
       (excluding *center* itself and unpositioned agents).
   * - ``space.move_toward(agent, target, max_step)``
     - Moves *agent* toward *target* (another agent or a
       :class:`~mango.Position2D`) by at most *max_step* units.

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
            agents = list(environment._id_to_agent.values())
            space = environment.space
            for agent in agents:
                nearby = space.agents_within(agent, radius=5.0, agents=agents)
                if nearby:
                    nearest = min(nearby, key=lambda n: space.distance(agent, n))
                    space.move_toward(agent, nearest, max_step=step_size_s * 2.0)


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

Every delivered message is logged in ``world.recorded_messages``:

.. testcode::

    import asyncio
    from mango import Agent, create_world, step_simulation, MessageTransaction

    class Ping(Agent):
        def handle_message(self, content, meta):
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
        Agent, create_world, step_simulation, record_world, plot_world,
    )

    class TrafficAgent(Agent):
        def handle_message(self, content, meta): pass

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
and per-agent) in a single grid figure — ideal for a quick experiment
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


Declarative behavior with behavior_in
======================================

:func:`~mango.behavior_in` lets you attach message handlers and event
subscriptions to a matched set of agents **without modifying their class
definitions**.  It is simulation-only — it requires a
:class:`~mango.simulation.world.SimulationWorld`.

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

behavior_in fires **in addition to** the agent's ``handle_message`` override
and any existing role subscriptions — it does not replace them.

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
