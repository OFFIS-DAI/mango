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

All factory methods are *synchronous* — you can create containers before
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
:meth:`~mango.activate` context manager — it starts all containers, runs your
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

For handling incoming messages override :meth:`~mango.Agent.handle_message`.
See :doc:`message exchange` for the full messaging API.


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
other mango agent — through the normal messaging API.

.. note::
    Once an agent is running in a subprocess you cannot access it directly
    from the main process.  Use
    :meth:`~mango.container.core.Container.dispatch_to_agent_process` to
    schedule a function inside the subprocess:

    .. code-block:: python

        await main_container.dispatch_to_agent_process(
            process_handle.pid,
            my_function,   # called as my_function(sub_container, *args)
            *args,
        )

If you need to set up process agents before an asyncio loop is available,
use :meth:`~mango.container.core.Container.as_agent_process_lazy` (no
process handle is returned; the subprocess is created when
:meth:`~mango.activate` is called).

.. seealso::

    :doc:`scheduling` — clock types and the scheduling API
