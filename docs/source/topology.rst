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

.. grid:: 1 2 2 3
   :gutter: 3

   .. grid-item-card:: Build & assign
      :shadow: sm

      Construct a graph, then place agents on its nodes — one by one, in
      round-robin, or by a predicate.

   .. grid-item-card:: Query neighbours
      :shadow: sm

      From any agent or role, list direct neighbours and filter them by link
      state, characteristic, or a custom predicate.

   .. grid-item-card:: Connect topologies
      :shadow: sm

      Bridge two independent topologies through *connector* agents for
      hierarchical and multi-overlay designs.


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

The ``with`` block is a convenience: :func:`~mango.create_topology` calls
:meth:`~mango.Topology.inject` for you when the block exits, pushing the
neighbourhood of every node into each agent's
:class:`~mango.TopologyService`.  Build the graph first; **inject before you
start sending** (register the agents in a container after, as above).


Ready-made graph shapes
=======================

For common structures you do not need to wire edges by hand.  Each constructor
returns a :class:`~mango.Topology` you can populate with :func:`~mango.per_node`
(see `Assigning agents to nodes`_):

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Constructor
     - Shape
   * - :func:`~mango.complete_topology` ``(n)``
     - Fully connected — every node linked to every other.
   * - :func:`~mango.star_topology` ``(n)``
     - One hub (node ``0``) connected to ``n - 1`` leaves.
   * - :func:`~mango.cycle_topology` ``(n)``
     - A ring — each node linked to its two neighbours.
   * - :func:`~mango.graph_topology` ``(graph)``
     - Any existing ``networkx`` graph (alias: :func:`~mango.custom_topology`).

.. code-block:: python

    from mango import star_topology, cycle_topology, graph_topology
    import networkx as nx

    hub_and_spokes = star_topology(5)      # node 0 is the hub
    ring           = cycle_topology(6)
    from_graph     = graph_topology(nx.wheel_graph(7))


Assigning agents to nodes
=========================

A freshly-built graph has empty nodes.  There are three ways to place agents,
each ending in an automatic :meth:`~mango.Topology.inject`:

**One agent per node** — iterate the nodes with :func:`~mango.per_node` and
call :meth:`AgentNode.add` on each:

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

.. warning::

   :func:`~mango.per_node` injects **after** the loop finishes.  Breaking out
   of the loop early skips the injection — iterate to the end (or call
   :meth:`~mango.Topology.inject` yourself).

**Round-robin** — spread a list of agents across the nodes with
:func:`~mango.auto_assign` (wraps around when there are more agents than
nodes):

.. code-block:: python

    from mango import complete_topology, auto_assign

    topology = complete_topology(3)
    auto_assign(topology, my_agents)   # agent i → node i % 3

**By predicate** — :func:`~mango.assign_agents` places every agent for which
``condition(agent, node)`` is true.  The node is passed as the second argument;
a matching agent is added to *every* node whose predicate returns true, so the
predicate usually tests the agent's type or attributes:

.. code-block:: python

    from mango import assign_agents

    assign_agents(
        lambda agent, node: isinstance(agent, SensorAgent),
        topology,
        all_agents,
    )

.. note::

   More than one agent may live on a single node.  Agents sharing a node are
   automatically each other's neighbours (a ``NORMAL`` link), so a node acts
   like a fully-connected local cluster.


Node characteristics
====================

A *characteristic* is a short string label attached to an agent within a node
(for example ``"leader"`` or ``"aggregator"``).  Neighbours can then filter by
role in the graph instead of by identity.

Assign a characteristic while building the topology with
:meth:`~mango.Topology.set_characteristic`:

.. code-block:: python

    with create_topology() as topology:
        hub = topology.add_node(hub_agent)
        leaf = topology.add_node(leaf_agent)
        topology.add_edge(hub, leaf)
        topology.set_characteristic(hub, hub_agent, "leader")

At runtime, filter neighbours by characteristic and read your own label:

.. code-block:: python

    # only neighbours tagged "leader"
    leaders = agent.neighbors(has_characteristic="leader")

    # your own role in the graph
    from mango import topology_characteristic
    my_role = topology_characteristic(agent)   # "" if none was set


Querying neighbours from agents and roles
=========================================

:meth:`~mango.Agent.neighbors` is the direct method on an agent.  When you work
with the :doc:`role system <role-api>`, use the matching free functions —
they accept **either an agent or a role** and read the same
:class:`~mango.TopologyService`:

.. list-table::
   :widths: 40 60
   :header-rows: 1

   * - Function
     - Returns
   * - :func:`~mango.topology_neighbors` ``(agent_or_role)``
     - neighbour :class:`~mango.AgentAddress` list (with the same filters as
       :meth:`~mango.Agent.neighbors`).
   * - :func:`~mango.topology_node_id` ``(agent_or_role)``
     - the integer node ID the agent occupies.
   * - :func:`~mango.topology_characteristic` ``(agent_or_role)``
     - the agent's characteristic label (``""`` if unset).
   * - :func:`~mango.topology_connectors` ``(agent_or_role)``
     - connector addresses reachable across a topology link.
   * - :func:`~mango.topology_connection_types` ``(agent_or_role)``
     - the connection-type labels available to the agent.

All of them take a ``tid`` keyword so an agent that belongs to several
topologies can pick which neighbour set it means:

.. code-block:: python

    from mango import Role, topology_neighbors

    class GossipRole(Role):
        async def broadcast(self, payload):
            for addr in topology_neighbors(self, tid="overlay"):
                await self.context.send_message(payload, addr)

The full filter set is shared by :meth:`~mango.Agent.neighbors` and
:func:`~mango.topology_neighbors`:

.. code-block:: python

    topology_neighbors(
        agent_or_role,
        state=State.NORMAL,               # by link state
        tid="default",                    # which topology
        has_characteristic="leader",      # by neighbour label
        include_connectors=("uplink",),   # also reach connectors (see below)
        match_func=lambda desc: ...,      # arbitrary predicate on AgentDescription
    )


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

To change the graph *after* it has been injected — flip a link to
``BROKEN`` when a peer goes silent, add or remove nodes — wrap the edits in
:func:`~mango.modify_topology`.  It re-injects the updated neighbourhoods when
the block exits:

.. code-block:: python

    from mango import modify_topology, State

    with modify_topology(topology) as t:
        t.set_edge_state(0, 1, State.BROKEN)
        t.remove_node(2)
    # every affected agent now sees the updated neighbour set


Connecting multiple topologies
==============================

Large systems are often built from several independent topologies — one per
region, per voltage level, per organisation — that must still exchange a few
messages across the boundary.  Rather than merging them into one graph, mango
links them through *connector* agents.

A connector is an agent nominated to represent its topology to the outside.
Nominate connectors either while building the topology with
:meth:`~mango.Topology.set_as_connector`, or ahead of time with
:func:`~mango.mark_as_connector` (the mark is picked up on the next inject):

.. code-block:: python

    from mango import (
        complete_topology, per_node, connect_topologies, topology_connectors,
    )

    region_a = complete_topology(3, tid="a")
    region_b = complete_topology(3, tid="b")
    for node in per_node(region_a):
        node.add(RegionAgent())
    for node in per_node(region_b):
        node.add(RegionAgent())

    # nominate one bridge agent on each side
    region_a.set_as_connector(region_a.agents[0], connector_type="uplink")
    region_b.set_as_connector(region_b.agents[0], connector_type="uplink")

    # link the two topologies via matching connector types
    connect_topologies(region_a, region_b, connection_type="uplink")

After :func:`~mango.connect_topologies`, each side's connector can see the
other side's connectors — without any node in ``region_a`` becoming a graph
neighbour of a node in ``region_b``:

.. code-block:: python

    bridge = region_a.agents[0]
    for addr in topology_connectors(bridge, tid="a"):
        await bridge.send_message("cross-region hello", addr)

Connectors are intentionally kept separate from ordinary neighbours so
intra-topology algorithms are unaffected.  When you *do* want a single call to
reach both, pass ``include_connectors`` to
:meth:`~mango.Agent.neighbors` / :func:`~mango.topology_neighbors`:

.. code-block:: python

    everyone = topology_neighbors(bridge, tid="a", include_connectors=("uplink",))

Pass ``directed=True`` to :func:`~mango.connect_topologies` for a one-way link
(``region_a`` reaches ``region_b`` but not vice versa).


Exporting to an agent-level graph
=================================

A topology node may hold several agents, so the topology graph is not always
one-agent-per-node.  :func:`~mango.topology_to_aid_graph` expands it into a
flat ``networkx`` graph whose nodes are agent IDs and whose edges carry the
link :class:`~mango.State`.  This is the natural input for distance-based
communication delays in a simulation:

.. code-block:: python

    from mango import topology_to_aid_graph
    from mango.simulation import create_distribution_based_com_sim

    aid_graph = topology_to_aid_graph(topology)
    com_sim = create_distribution_based_com_sim(
        aid_graph, default_delay_per_edge_ms=20.0
    )

.. seealso::

    :doc:`simulation` — use :func:`~mango.run_with_simulation` to run
    topology-based agents in a simulation world, and feed
    :func:`~mango.topology_to_aid_graph` into the communication simulation for
    delays that grow with graph distance.
