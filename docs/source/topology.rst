==========
Topologies
==========

A *topology* is a graph that describes which agents are logically connected to
each other.  Once a topology is distributed to the agents, every agent can
iterate over its neighbours and send messages to them without knowing their
addresses upfront.

This is particularly useful for peer-to-peer algorithms (gossip protocols,
consensus, distributed optimisation) where every agent only communicates with
its direct neighbours in a graph.

Under the hood, topologies are backed by
`networkx <https://networkx.org>`_ ``Graph`` objects, so you can use all of
networkx's graph construction helpers.

Building a topology from scratch
=================================

Use :func:`~mango.create_topology` to build a topology incrementally.  Add
agents as nodes, then wire them up with edges:

.. testcode::

    import asyncio
    from typing import Any

    from mango import Agent, run_with_tcp, create_topology

    class TopAgent(Agent):
        def __init__(self):
            super().__init__()
            self.counter = 0

        def handle_message(self, content, meta: dict[str, Any]):
            self.counter += 1

    async def start_example():
        agents = [TopAgent(), TopAgent(), TopAgent()]
        with create_topology() as topology:
            id_1 = topology.add_node(agents[0])
            id_2 = topology.add_node(agents[1])
            id_3 = topology.add_node(agents[2])
            topology.add_edge(id_1, id_2)
            topology.add_edge(id_1, id_3)

        async with run_with_tcp(1, *agents):
            # agents[0] has two neighbours; agents[1] and agents[2] have one
            for neighbour in agents[0].neighbors():
                await agents[0].send_message("hello neighbours", neighbour)
            await asyncio.sleep(0.1)

        print(agents[1].counter)
        print(agents[2].counter)

    asyncio.run(start_example())

.. testoutput::

    1
    1

:meth:`~mango.Agent.neighbors` returns a list of :class:`~mango.AgentAddress`
objects.  By default only *normal* (active) links are returned; you can filter
by link state using the ``state`` parameter (see `Link states`_ below).


Using a pre-built graph
========================

If you already have a networkx graph, use :func:`~mango.per_node` to iterate
over the topology and attach one agent per node.  Convenience constructors
like :func:`~mango.complete_topology` build common graph shapes for you:

.. testcode::

    import asyncio
    from typing import Any

    from mango import Agent, run_with_tcp, per_node, complete_topology

    class TopAgent(Agent):
        def __init__(self):
            super().__init__()
            self.counter = 0

        def handle_message(self, content, meta: dict[str, Any]):
            self.counter += 1

    async def start_example():
        # complete_topology(n) creates a fully-connected graph with n nodes
        topology = complete_topology(3)
        for node in per_node(topology):
            node.add(TopAgent())

        async with run_with_tcp(1, *topology.agents):
            for neighbour in topology.agents[0].neighbors():
                await topology.agents[0].send_message("hello neighbours", neighbour)
            await asyncio.sleep(0.1)

        # In a complete graph of 3 nodes, every agent has 2 neighbours
        print(topology.agents[1].counter)
        print(topology.agents[2].counter)

    asyncio.run(start_example())

.. testoutput::

    1
    1


Link states
============

Every edge in a mango topology has a *state* that lets you model partially
broken or inactive connections:

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - State
     - Meaning
   * - ``State.NORMAL``
     - The link is active (default).  Returned by :meth:`~mango.Agent.neighbors`.
   * - ``State.INACTIVE``
     - The link exists but is currently not used.  Could be reactivated later.
   * - ``State.BROKEN``
     - The link is permanently broken and cannot be used.

Use the ``state`` argument to query neighbours in a specific state:

.. code-block:: python

    from mango import State

    active_neighbours  = agent.neighbors(state=State.NORMAL)
    inactive_links     = agent.neighbors(state=State.INACTIVE)

.. seealso::

    :doc:`simulation` — use :func:`~mango.run_with_simulation` to run
    topology-based agents in a simulation world.
