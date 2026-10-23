.. _getting-started:

===============
Getting started
===============

mango is a Python framework for multi-agent systems, built on asyncio. A mango
application has two halves. The **agent system** is what you write: agents that
exchange messages, can be composed of roles and schedule their own tasks. A
**runtime** executes it, either in real time, as containers that talk over TCP
or MQTT, or as a simulation world on simulated time. The same agents run in
both.

.. raw:: html
   :file: _static/getting-started/overview.svg
   :class: mango-feature-map-frame

.. only:: html

   .. div:: sd-text-center mango-feature-map-caption

      Each part of the picture links to the step that introduces it.

.. only:: latex

   The agent system consists of agents that exchange messages
   (:ref:`step 1 <getting-started-agents>`), can be composed of roles
   (:ref:`step 2 <getting-started-roles>`) and schedule their own tasks
   (:ref:`step 3 <getting-started-scheduling>`). A runtime executes it
   (:ref:`step 4 <getting-started-run>`): containers in real time, or a
   simulation world on simulated time.

Step 1 introduces agents and messages with two greeters. Steps 2 and 3 build a
weather station that reports the temperature to a collector every hour, and
step 4 runs it in real time and in a simulation.

Install mango with pip (Python 3.10 or newer, see :doc:`installation`):

.. code-block:: console

   $ pip install mango-agents


.. _getting-started-agents:

1. Agents and messages
======================

An agent is an autonomous entity: subclass :class:`~mango.Agent` and register
it in a runtime. The runtime gives it an **address** and drives its
**lifecycle**: :meth:`~mango.Agent.on_register` once it has an address,
:meth:`~mango.Agent.on_ready` once the runtime has started, and
:meth:`~mango.Agent.on_stop` (``async``) at shutdown. Every agent has an inbox
for incoming messages and a scheduler for its own tasks.

.. testcode::

    import asyncio
    from mango import Agent, on_message, run_with_tcp, sender_addr

    class Greeter(Agent):
        def on_ready(self):
            print(f"ready: {self.addr}")

        @on_message(str)
        async def greet_back(self, text, meta):
            print(f"{self.aid} got {text!r} from {sender_addr(meta).aid}")
            if text == "hello":
                await self.reply_to("hello back", meta)

    async def main():
        alice, bob = Greeter(), Greeter()
        async with run_with_tcp(1, alice, bob):
            await alice.send_message("hello", bob.addr)
            await asyncio.sleep(0.1)  # let the reply arrive before the container stops

    asyncio.run(main())

.. testoutput::

    ready: AgentAddress(protocol_addr=('127.0.0.1', 5555), aid='agent0')
    ready: AgentAddress(protocol_addr=('127.0.0.1', 5555), aid='agent1')
    agent1 got 'hello' from agent0
    agent0 got 'hello back' from agent1

.. _getting-started-messages:

**Messages** go to an address: the protocol address of the agent's container
plus its agent id (``aid``). :meth:`~mango.Agent.send_message` sends any Python
object; across containers, the codec must be able to encode it (step 2 shows
how). :meth:`~mango.Agent.reply_to` answers the sender of a received message.
:func:`~mango.on_message` routes each incoming message by the type of its
content, here ``str``, to a handler that receives the content and the message's
``meta`` data, such as the sender.

A **container** hosts agents in your Python process. :func:`~mango.run_with_tcp`
starts one TCP container on ``127.0.0.1:5555``, registers both agents in it and
shuts it down at the end of the ``async with`` block. Its agents share one
asyncio event loop, so a handler must never block.


.. _getting-started-roles:

2. Roles
========

Instead of putting all behaviour into one agent class, you can compose an agent
of **roles**: reusable building blocks, each covering one responsibility. A role
uses the same handlers as an agent (``@on_message`` and the lifecycle hooks, but
:meth:`~mango.Role.setup` instead of ``on_register``). Through ``self.context``
it sends messages and emits in-process events to the other roles of its agent.

The station is one agent composed of two roles with
:func:`~mango.agent_composed_of`: a sensor that measures (step 3), and
``Report``, which sends every measurement to the collector. The collector is a
plain agent.

.. testcode::

    from mango import Agent, Role, json_serializable, on_event, on_message

    @json_serializable
    class Reading:
        def __init__(self, celsius):
            self.celsius = celsius

    class Collector(Agent):
        @on_message(Reading)
        def collect(self, reading, meta):
            print(f"collector got {reading.celsius} °C")

    class Report(Role):
        def __init__(self, collector):
            super().__init__()
            self.collector = collector  # the collector's address

        @on_event(Reading)
        def send(self, reading, source):
            self.context.schedule_instant_message(reading, self.collector)

A ``Reading`` is both an event and a message. The sensor role emits it with
``emit_event``, and :func:`~mango.on_event` hands it to ``Report.send``, which
sends it on to the collector. Event handlers run synchronously, so ``send``
schedules the message with :meth:`~mango.RoleContext.schedule_instant_message`
instead of awaiting ``send_message``. :func:`~mango.json_serializable` lets the
JSON codec encode a ``Reading`` when it travels over TCP or MQTT (step 4
registers it with the codec).


.. _getting-started-scheduling:

3. Scheduling
=============

Agents do not only react to messages, they also act on their own.
:func:`@periodic(every=seconds) <mango.periodic>` on an async method of an agent
or a role starts a task once the agent is ready and repeats it. The scheduler
also runs tasks once (:meth:`~mango.Agent.schedule_instant_task`), at a given
time (:meth:`~mango.Agent.schedule_timestamp_task`) or from any task object
(:meth:`~mango.Agent.schedule_task`).

Tasks run on the runtime's clock: wall-clock time in real time, simulated time
in a simulation. ``current_timestamp`` on the agent or role context reads it. A
task that waits through the scheduler and reads time this way, never with
``asyncio.sleep`` or ``time.time``, works in both runtimes:

.. testcode::

    from mango import periodic

    class Sensor(Role):
        @periodic(every=3600)
        async def measure(self):
            self.context.emit_event(Reading(await self.read()), event_source=self)

        async def read(self):
            raise NotImplementedError

``measure`` runs once when the station is ready and then every hour. ``read``
is the one part that depends on the runtime, because that is where the station
meets its environment.


.. _getting-started-run:

4. Run it: real time or simulation
==================================

Both tabs register the same collector and station in a runtime; only the
sensor subclass and the runtime differ. To run a tab, put the code of steps 2
and 3 and of the tab into one file, in this order.

.. tab-set::
   :sync-group: runtime

   .. tab-item:: Real time
      :sync: realtime

      Time is wall-clock time: the station measures at once, and ``sleep(0.1)``
      ends the run long before the next hour. Two TCP containers host the
      collector and the station, so the reading travels over TCP, encoded by
      the codec, which has to know the ``Reading`` type. The environment lives
      **outside mango**, and your code connects the agents to it:
      ``ExternalSensor`` reads from whatever provides the temperature.

      .. testcode::

          import asyncio
          from mango import JSON, activate, agent_composed_of, create_tcp_container

          class ExternalSensor(Sensor):
              async def read(self):
                  return 21.5  # stand-in: await your device, service or model here

          async def main():
              codec = JSON()
              codec.add_serializer(*Reading.__serializer__())
              hub = create_tcp_container(("127.0.0.1", 5555), codec=codec)
              field = create_tcp_container(("127.0.0.1", 5556), codec=codec)

              collector = hub.register(Collector())
              field.register(agent_composed_of(ExternalSensor(), Report(collector.addr)))

              async with activate(hub, field):
                  await asyncio.sleep(0.1)  # a real-time system runs until it is shut down

          asyncio.run(main())

      .. testoutput::

          collector got 21.5 °C

   .. tab-item:: Simulation
      :sync: simulation

      Time is simulated: :func:`~mango.discrete_step_until` runs a
      discrete-event simulation, moving the world's clock from event to event
      (a task waking up, a message arriving), so four simulated hours take
      milliseconds and every run gives the same result. Each step first
      advances the environment, then moves the clock and runs what is due: the
      readings at hours 0 to 4 show 10 to 14 °C. One
      :class:`~mango.SimulationWorld` hosts both agents. The environment is an
      **entity in mango**: ``Weather``, a :class:`~mango.Behavior` of the
      world's :class:`~mango.DefaultEnvironment`, which ``SimSensor`` reads
      like a device.

      .. testcode::

          import asyncio
          from mango import (
              Behavior, DefaultEnvironment, agent_composed_of, create_world, discrete_step_until,
          )

          class Weather(Behavior):
              celsius = 10.0

              def on_step(self, environment, clock, step_size_s):
                  self.celsius += step_size_s / 3600  # warms by 1 °C per hour

          class SimSensor(Sensor):
              def __init__(self, weather):
                  super().__init__()
                  self.weather = weather

              async def read(self):
                  return self.weather.celsius

          async def main():
              weather = Weather()
              world = create_world(environment=DefaultEnvironment(behavior=weather))

              collector = world.register(Collector())
              world.register(agent_composed_of(SimSensor(weather), Report(collector.addr)))

              async with world:
                  await discrete_step_until(world, max_advance_time_s=4 * 3600)
              print(f"simulated {world.clock.time / 3600:.0f} h")

          asyncio.run(main())

      .. testoutput::

          collector got 10.0 °C
          collector got 11.0 °C
          collector got 12.0 °C
          collector got 13.0 °C
          collector got 14.0 °C
          simulated 4 h

``Reading``, ``Collector``, ``Report`` and the ``Sensor`` base class are the
same in both tabs, and ``register`` works on a simulation world just as on a
container. That is the usual way to work with mango: develop and test the agent
system in a simulation, then run the same agents in real time, in containers
that exchange real messages and connect to their environment.

.. list-table::
   :widths: 16 42 42
   :header-rows: 1

   * -
     - Real time
     - Simulation
   * - Start with
     - :func:`~mango.create_tcp_container` and :func:`~mango.activate`, or
       :func:`~mango.run_with_tcp` (:func:`~mango.run_with_mqtt` for MQTT, with
       a broker)
     - :func:`~mango.create_world`, or :func:`~mango.run_with_simulation`
   * - Time
     - wall clock (Unix time): tasks wait in real seconds
     - simulated, from 0: :func:`~mango.step_simulation` in fixed steps,
       :func:`~mango.discrete_step_until` from event to event
   * - Network
     - TCP or MQTT, content encoded by a codec
     - in process, not encoded; instant unless you add delay and loss
       (:class:`~mango.SimpleCommunicationSimulation`)
   * - Environment
     - outside mango, integrated by your own code
     - an :class:`~mango.Environment` entity inside the world, stepped with it
       and read in ``on_step`` or, like ``SimSensor``, through a reference


Next steps
==========

.. grid:: 1 2 2 3
   :gutter: 3

   .. grid-item-card:: Feature overview
      :link: features
      :link-type: doc
      :shadow: sm

      A map of every part of mango, and where to read about it.

   .. grid-item-card:: Real-time tutorial
      :link: tutorial
      :link-type: doc
      :shadow: sm

      Solar power plants and a controller, step by step in TCP containers.

   .. grid-item-card:: Simulation tutorial
      :link: simulation-ev-tutorial
      :link-type: doc
      :shadow: sm

      Electric vehicles in a 2-D space, with recording and plots.
