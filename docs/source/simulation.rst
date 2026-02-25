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


API reference
=============

See :doc:`api_ref/mango.simulation` for the full API documentation.
