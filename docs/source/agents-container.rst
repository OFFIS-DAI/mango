====================
Agents and Container
====================

.. _container-docs:

Container
=========

In mango, every agent lives inside a *container*.  The container owns the
network layer: it sends and receives messages, routes them to the correct
agent, and handles serialisation/deserialisation with the chosen codec.
When two agents share the same container their messages stay in-process
(no network round-trip), which speeds up local communication significantly.

Container types
---------------

mango ships three container types, each suited to a different deployment
scenario:

.. list-table::
   :widths: 20 30 50
   :header-rows: 1

   * - Type
     - Factory
     - When to use
   * - **TCP**
     - :meth:`~mango.create_tcp_container`
     - Default choice.  Fast, point-to-point TCP sockets for local and
       distributed simulations.
   * - **MQTT**
     - :meth:`~mango.create_mqtt_container`
     - When a message broker is already present (e.g. IoT deployments) or
       when you need topic-based pub/sub routing.
   * - **External Coupling**
     - :meth:`~mango.create_ec_container`
     - Co-simulation scenarios where an external tool (e.g. a power-flow
       solver) drives the time loop and injects messages.

All factory methods are *synchronous*: you can create containers before
starting the asyncio event loop.  The default codec is JSON (see
:doc:`codecs` for details).  You can supply a custom :class:`~mango.ExternalClock`
to decouple simulation time from wall time (see :doc:`scheduling`).

.. testcode::

    import asyncio
    from mango import create_tcp_container

    container = create_tcp_container(addr=('127.0.0.1', 5555))
    print(container.addr)

.. testoutput::

    ('127.0.0.1', 5555)

Starting and stopping
---------------------

Container creation is separate from container *starting*.  Before a container
can exchange messages its network server must be started.  Use the
:meth:`~mango.activate` context manager; it starts all containers, runs your
code, and shuts everything down on exit (even on exceptions):

.. testcode::

    import asyncio
    from mango import create_tcp_container, activate

    async def start_container():
        container = create_tcp_container(addr=('127.0.0.1', 5555))

        async with activate(container) as c:
            print("Container is running!")
            await asyncio.sleep(0)
        print("Container shut down automatically.")

    asyncio.run(start_container())

.. testoutput::

    Container is running!
    Container shut down automatically.

.. note::
    Shutdown also cancels all running agent tasks and calls
    :meth:`~mango.Agent.on_stop` on every registered agent.


.. _agent-docs:

Agents
======

Agents are created by subclassing :class:`~mango.Agent`.  Every subclass
**must** call ``super().__init__()`` in its constructor.

An agent is registered with a container via :meth:`~mango.Container.register`.
Registration assigns the agent its *agent ID* (``aid``) and enables
scheduling.  You can suggest a preferred AID with ``suggested_aid``; if the
name conflicts with an existing agent or with the default ``agentN`` pattern
the framework generates one automatically.

.. testcode::

    from mango import Agent, create_tcp_container
    import asyncio

    class MyAgent(Agent):
        pass

    async def create_and_register():
        container = create_tcp_container(addr=('127.0.0.1', 5555))
        agent = container.register(MyAgent(), suggested_aid="my_agent")
        return agent

    print(asyncio.run(create_and_register()).aid)

.. testoutput::

    my_agent

Lifecycle callbacks
-------------------

Implement these methods to hook into the agent's lifecycle:

.. list-table::
   :widths: 25 75
   :header-rows: 1

   * - Method
     - When it is called
   * - :meth:`~mango.Agent.on_register`
     - Immediately after the agent is registered.  The scheduler and agent
       address are available; no messages can be sent yet.
   * - :meth:`~mango.Agent.on_start`
     - When the container is started (inside :meth:`~mango.activate`).
       Internal messages are possible; external messages depend on setup.
   * - :meth:`~mango.Agent.on_ready`
     - After **all** containers passed to :meth:`~mango.activate` have
       started.  This is the right place to send the first messages.
   * - :meth:`~mango.Agent.on_stop`
     - When the container shuts down or the agent is deregistered.
       Use it for cleanup and final messages.

.. _agent-handlers:

Handling messages
-----------------

An agent declares which messages it handles.  :func:`~mango.on_message`
subscribes a method to a message type, and every message whose content is an
instance of that type is delivered to it:

.. testcode::

    import asyncio
    from mango import Agent, activate, create_tcp_container, on_message

    class Ping:
        pass

    class PingAgent(Agent):
        @on_message(Ping)
        def handle_ping(self, content, meta):
            print("Ping received!")

    async def run_ping_agent():
        container = create_tcp_container(addr=('127.0.0.1', 5557))
        agent = container.register(PingAgent(), suggested_aid="pinger")
        async with activate(container):
            await container.send_message(Ping(), agent.addr)
            await asyncio.sleep(0.01)

    asyncio.run(run_ping_agent())

.. testoutput::

    Ping received!

Declare one handler per message type instead of a chain of ``isinstance``
checks.  An agent whose handlers cover every message it expects needs no
:meth:`~mango.Agent.handle_message` at all.

Two options refine a subscription, as in :ref:`the role API
<role-decorators>`.  ``where(self, content, meta)`` narrows it beyond the
type check, and ``priority`` orders the decorated handlers, lowest first.

Time-driven behaviour is declared the same way: :func:`~mango.periodic` turns
a coroutine method into a recurring task that starts once all containers are
ready (see :doc:`scheduling`).

See :doc:`message exchange` for the full messaging API, and :doc:`role-api`
for splitting a growing agent into roles.

.. note::

   :func:`~mango.on_event` is the one decorator a plain agent cannot use.
   Its events travel on the bus an agent's roles emit on, so it needs a
   :class:`~mango.RoleAgent`; on a plain agent it raises ``TypeError``.


Alternative: ``handle_message``
-------------------------------

:meth:`~mango.Agent.handle_message` receives **every** message the agent
gets, unfiltered, after the decorated handlers have run.  Override it when
the message type cannot carry the distinction:

* the meaning sits in ``meta`` rather than in the content, as with a FIPA
  performative or an MQTT topic;
* the agent has to see all traffic, for example to log it or count arrivals;
* a message no handler claimed should be reported instead of ignored.

.. testcode::

    from mango import Agent, Performatives

    class MeterAgent(Agent):
        def handle_message(self, content, meta):
            if meta.get("performative") == Performatives.request:
                print(f"reading requested: {content}")
            else:
                print(f"unexpected message: {content}")

The two styles mix freely: decorated handlers take the typed messages,
``handle_message`` takes the rest.  It also sees the messages the handlers
already took, so check for that where it matters.


.. _express-setup:

Express setup
=============

The :func:`~mango.run_with_tcp` (and :func:`~mango.run_with_mqtt`) helpers
wrap container creation, agent registration, activation, and shutdown into a
single context manager.  Agents are distributed evenly across the requested
number of containers.

Pass plain agent instances or ``(agent, {"aid": "preferred_id"})`` tuples:

.. testcode::

    import asyncio
    from mango import PrintingAgent, run_with_tcp

    async def run_with_tcp_example():
        agent_tuple = (PrintingAgent(), dict(aid="MyAgent"))
        single_agent = PrintingAgent()

        async with run_with_tcp(2, agent_tuple, single_agent) as cl:
            await agent_tuple[0].send_message("Hello, print me!", single_agent.addr)
            await asyncio.sleep(0.1)

    asyncio.run(run_with_tcp_example())

.. testoutput::

    Received: Hello, print me! with {'sender_id': 'MyAgent', 'sender_addr': ['127.0.0.1', 5555], 'receiver_id': 'agent0', 'network_protocol': 'tcp', 'priority': 0}

.. seealso::

    :func:`~mango.run_with_simulation` for the equivalent helper for the
    :doc:`simulation world <simulation>`.


.. _agent-process:

Agent processes
===============

Python's GIL limits true parallelism within a single process.  For
CPU-intensive tasks mango lets you run individual agents in a dedicated
subprocess, coordinated automatically through a *mirror container*.

.. code-block:: python

    # Register an agent in a new subprocess
    process_handle = await main_container.as_agent_process(
        agent_creator=lambda sub_container: sub_container.register(
            MyAgent(), suggested_aid="process_agent"
        )
    )

    # Wait until the subprocess is ready
    await process_handle
    print(f"Agent running in PID {process_handle.pid}")

The agent in the subprocess communicates with other agents exactly like any
other mango agent, through the normal messaging API.

.. note::
    Once an agent is running in a subprocess you cannot access it directly
    from the main process.  Use
    :meth:`~mango.container.core.Container.dispatch_to_agent_process` to
    schedule a function inside the subprocess:

    .. code-block:: python

        main_container.dispatch_to_agent_process(
            process_handle.pid,
            my_function,   # called as my_function(sub_container, *args)
            *args,
        )

    ``my_function`` has to be importable in the subprocess, so define it at
    module level rather than as a lambda or a nested function.

If you need to set up process agents before an asyncio loop is available,
use :meth:`~mango.container.core.Container.as_agent_process_lazy` (no
process handle is returned; the subprocess is created when
:meth:`~mango.activate` is called).

.. seealso::

    :doc:`scheduling`: clock types and the scheduling API
