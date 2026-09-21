"""
Topology system for mango multi-agent systems.

Provides a graph-based neighborhood model that injects neighbor addresses
into agents without requiring agents to know the network layout up-front.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import networkx as nx

from mango.agent.core import (
    Agent,
    AgentAddress,
    AgentDescription,
    State,
    TopologyNeighbor,
    TopologyService,
)

if TYPE_CHECKING:
    from mango.express.health import EdgeHealth

AGENT_NODE_KEY = "node"
STATE_EDGE_KEY = "state"


class AgentNode:
    """A single node in a topology graph.  Holds one or more agents."""

    def __init__(self, agents: list[Agent] | None = None) -> None:
        self.agents: list[Agent] = [] if agents is None else list(agents)
        self.characteristics: dict[int, str] = {}

    def add(self, *agents: Agent) -> None:
        """Add one or more agents to this node."""
        self.agents.extend(agents)

    def set_characteristic(self, agent: Agent, characteristic: str) -> None:
        """Assign a *characteristic* label to *agent* within this node."""
        self.characteristics[id(agent)] = characteristic

    def _characteristic_for(self, agent: Agent) -> str:
        return self.characteristics.get(id(agent), "")


class Topology:
    """A graph-based agent neighborhood topology.

    Manages a NetworkX graph where each node holds one or more agents.
    After populating nodes and edges, call :meth:`inject` (or use
    :func:`create_topology` / :func:`per_node`) to push neighbor
    information into each agent's :class:`~mango.TopologyService`.

    :param graph: an undirected or directed NetworkX graph
    :param tid: topology identifier; agents in multiple topologies use
        this to distinguish neighbor sets (default ``"default"``)
    """

    def __init__(
        self,
        graph: nx.Graph,
        *,
        tid: str = "default",
        edge_health: EdgeHealth | None = None,
    ) -> None:
        self._graph: nx.Graph = graph
        self._tid: str = tid
        self._connectors: list[tuple[str, TopologyNeighbor]] = []
        self._connections: list[tuple[str, Topology]] = []
        # Optional per-topology link-health tracking.  When set, a
        # :class:`TopologyHealth` runtime is shared across every agent
        # in this topology and an auto-nudge subscription is installed
        # at inject time.  See :mod:`mango.express.health`.
        from mango.express.health import TopologyHealth

        self._edge_health: EdgeHealth | None = edge_health
        self._health: TopologyHealth | None = (
            TopologyHealth(edge_health) if edge_health is not None else None
        )

        for node in self._graph.nodes:
            self._graph.nodes[node][AGENT_NODE_KEY] = AgentNode()
        for edge in self._graph.edges:
            self._graph.edges[edge][STATE_EDGE_KEY] = State.NORMAL

    @property
    def tid(self) -> str:
        """The topology identifier."""
        return self._tid

    @property
    def graph(self) -> nx.Graph:
        """The underlying NetworkX graph."""
        return self._graph

    @property
    def agents(self) -> list[Agent]:
        """All agents in the topology across all nodes."""
        result: list[Agent] = []
        for node in self._graph.nodes:
            result.extend(self._graph.nodes[node][AGENT_NODE_KEY].agents)
        return result

    def add_node(self, *agents: Agent) -> int:
        """Add a new node containing *agents* and return the node ID.

        :param agents: zero or more agents to place at this node
        :return: integer node ID
        """
        int_labels = [n for n in self._graph.nodes if isinstance(n, int)]
        node_id = (max(int_labels) + 1) if int_labels else 0
        self._graph.add_node(node_id, **{AGENT_NODE_KEY: AgentNode(list(agents))})
        return node_id

    def add_edge(
        self,
        node_from: int,
        node_to: int,
        state: State = State.NORMAL,
    ) -> None:
        """Add an undirected edge between *node_from* and *node_to*.

        :param node_from: source node ID
        :param node_to: destination node ID
        :param state: initial edge state (default :attr:`~mango.State.NORMAL`)
        """
        self._graph.add_edge(node_from, node_to, **{STATE_EDGE_KEY: state})

    def remove_edge(self, node_from: int, node_to: int) -> None:
        """Remove the edge between *node_from* and *node_to*.

        :param node_from: source node ID
        :param node_to: destination node ID
        """
        self._graph.remove_edge(node_from, node_to)

    def remove_node(self, node_id: int) -> None:
        """Remove the node with *node_id* and all its incident edges.

        :param node_id: ID of the node to remove
        """
        self._graph.remove_node(node_id)

    def set_edge_state(
        self,
        node_from: int,
        node_to: int,
        state: State,
        *,
        both_directions: bool = True,
    ) -> None:
        """Set the state of the edge between *node_from* and *node_to*.

        :param node_from: source node ID
        :param node_to: destination node ID
        :param state: new edge state
        :param both_directions: also update the reverse edge if present
            (default ``True``)
        """
        self._graph.edges[node_from, node_to][STATE_EDGE_KEY] = state
        if both_directions and self._graph.has_edge(node_to, node_from):
            self._graph.edges[node_to, node_from][STATE_EDGE_KEY] = state

    def set_characteristic(
        self, node_id: int, agent: Agent, characteristic: str
    ) -> None:
        """Assign a *characteristic* label to *agent* in node *node_id*.

        Characteristics are short string labels (e.g. ``"leader"``) that
        allow neighbors to filter by the agent's role in the graph.

        :param node_id: ID of the node the agent belongs to
        :param agent: the agent to label
        :param characteristic: label string
        """
        self._graph.nodes[node_id][AGENT_NODE_KEY].set_characteristic(
            agent, characteristic
        )

    def set_as_connector(self, *agents: Agent, connector_type: str = "default") -> None:
        """Mark *agents* as connectors for inter-topology links.

        Connectors are agents that bridge two topologies when
        :func:`connect_topologies` is called.

        :param agents: agents to mark as connectors
        :param connector_type: connection type label (default ``"default"``)
        """
        for agent in agents:
            neighbor = TopologyNeighbor(agent=agent, description=agent.description)
            self._connectors.append((connector_type, neighbor))

    def inject(self) -> None:
        """Push neighborhood data into every agent's :class:`~mango.TopologyService`.

        Called automatically by :func:`create_topology`, :func:`per_node`,
        :func:`modify_topology`, and :func:`connect_topologies`.
        """
        self._build_and_inject()

    def _build_and_inject(self, *, update_connected: bool = True) -> None:
        for node_id in self._graph.nodes:
            agent_node: AgentNode = self._graph.nodes[node_id][AGENT_NODE_KEY]
            state_to_neighbors: dict[State, list[TopologyNeighbor]] = {}

            for neighbor_id in self._graph.neighbors(node_id):
                edge_state: State = self._graph.edges[node_id, neighbor_id][
                    STATE_EDGE_KEY
                ]
                n_node: AgentNode = self._graph.nodes[neighbor_id][AGENT_NODE_KEY]
                bucket = state_to_neighbors.setdefault(edge_state, [])
                for a in n_node.agents:
                    bucket.append(
                        TopologyNeighbor(
                            agent=a,
                            description=a.description,
                            characteristic=n_node._characteristic_for(a),
                        )
                    )

            for agent in agent_node.agents:
                # Include same-node agents (excluding self) as NORMAL neighbors
                my_state_map: dict[State, list[TopologyNeighbor]] = {
                    s: list(ns) for s, ns in state_to_neighbors.items()
                }
                same_node_bucket = my_state_map.setdefault(State.NORMAL, [])
                for other in agent_node.agents:
                    if other is not agent:
                        same_node_bucket.append(
                            TopologyNeighbor(
                                agent=other,
                                description=other.description,
                                characteristic=agent_node._characteristic_for(other),
                            )
                        )

                svc = agent.service_of_type(TopologyService)
                svc._tid_to_state_to_neighbors[self._tid] = my_state_map
                svc._tid_to_node_id[self._tid] = node_id
                svc._tid_to_characteristic[self._tid] = agent_node._characteristic_for(
                    agent
                )
                # Bind the shared :class:`TopologyHealth` runtime so the
                # agent's receive hook can nudge per-edge scores.  None
                # when the topology was built without ``edge_health``.
                if self._health is not None:
                    svc._tid_to_health[self._tid] = self._health
                else:
                    svc._tid_to_health.pop(self._tid, None)

                # Transfer any marks from mark_as_connector
                for conn_type in svc._marked_connector_for:
                    self_neighbor = TopologyNeighbor(
                        agent=agent, description=agent.description
                    )
                    existing = {(ct, n.description.uid) for ct, n in self._connectors}
                    if (conn_type, agent.description.uid) not in existing:
                        self._connectors.append((conn_type, self_neighbor))

                # Resolve cross-topology connectors for this agent
                connectors_for_agent: list[tuple[str, TopologyNeighbor]] = []
                for conn_type, other_topo in self._connections:
                    for c_type, c_neighbor in self._connectors:
                        if (
                            c_type == conn_type
                            and c_neighbor.description.uid == agent.description.uid
                        ):
                            for o_c_type, o_neighbor in other_topo._connectors:
                                if conn_type == o_c_type:
                                    connectors_for_agent.append((conn_type, o_neighbor))
                svc._tid_to_connectors[self._tid] = connectors_for_agent

        if update_connected:
            for _, other_topo in self._connections:
                other_topo._build_and_inject(update_connected=False)


def complete_topology(number_of_nodes: int, *, tid: str = "default") -> Topology:
    """Create a fully-connected (complete) topology.

    :param number_of_nodes: number of nodes in the topology
    :param tid: topology identifier (default ``"default"``)
    :return: :class:`Topology`

    Example::

        topology = complete_topology(4)
        for node in per_node(topology):
            node.add(MyAgent())
    """
    return Topology(nx.complete_graph(number_of_nodes), tid=tid)


def star_topology(number_of_nodes: int, *, tid: str = "default") -> Topology:
    """Create a star topology (one hub connected to all leaves).

    :param number_of_nodes: total number of nodes including the hub
    :param tid: topology identifier (default ``"default"``)
    :return: :class:`Topology`

    Example::

        topology = star_topology(5)  # node 0 is the hub
        for node in per_node(topology):
            node.add(MyAgent())
    """
    return Topology(nx.star_graph(number_of_nodes - 1), tid=tid)


def cycle_topology(number_of_nodes: int, *, tid: str = "default") -> Topology:
    """Create a ring (cycle) topology.

    :param number_of_nodes: number of nodes in the ring
    :param tid: topology identifier (default ``"default"``)
    :return: :class:`Topology`

    Example::

        topology = cycle_topology(6)
        for node in per_node(topology):
            node.add(MyAgent())
    """
    return Topology(nx.cycle_graph(number_of_nodes), tid=tid)


def graph_topology(graph: nx.Graph, *, tid: str = "default") -> Topology:
    """Create a topology from an existing NetworkX graph.

    :param graph: the graph to use as the topology structure
    :param tid: topology identifier (default ``"default"``)
    :return: :class:`Topology`
    """
    return Topology(graph, tid=tid)


def custom_topology(graph: nx.Graph) -> Topology:
    """Create a topology from an existing NetworkX graph (alias for :func:`graph_topology`)."""
    return graph_topology(graph)


@contextmanager
def create_topology(
    *,
    directed: bool = False,
    tid: str = "default",
    edge_health: EdgeHealth | None = None,
) -> Iterator[Topology]:
    """Context manager that builds a topology and injects neighborhoods on exit.

    :param directed: use a directed graph (default ``False``)
    :param tid: topology identifier (default ``"default"``)
    :param edge_health: opt-in continuous link-health tracking.  When
        set, every agent in the topology gets an auto-nudge hook that
        recovers per-neighbour scores on incoming messages, and the
        :meth:`mango.RoleContext.live_neighbours` query becomes
        available for this ``tid``.  See :mod:`mango.express.health`.
    :yield: an empty :class:`Topology` to populate

    Example::

        agents = [MyAgent(), MyAgent(), MyAgent()]
        with create_topology() as topology:
            n1 = topology.add_node(agents[0])
            n2 = topology.add_node(agents[1])
            n3 = topology.add_node(agents[2])
            topology.add_edge(n1, n2)
            topology.add_edge(n1, n3)
    """
    graph = nx.DiGraph() if directed else nx.Graph()
    topology = Topology(graph, tid=tid, edge_health=edge_health)
    yield topology
    topology.inject()


@contextmanager
def modify_topology(topology: Topology) -> Iterator[Topology]:
    """Context manager for mutating an existing topology.

    Re-injects neighborhoods after the ``with`` block exits.

    :param topology: the :class:`Topology` to modify
    :yield: the same topology

    Example::

        with modify_topology(my_topology) as t:
            t.remove_edge(0, 1)
            t.set_edge_state(0, 2, State.BROKEN)
    """
    yield topology
    topology.inject()


def per_node(topology: Topology) -> Iterator[AgentNode]:
    """Iterate over topology nodes, yielding each :class:`AgentNode`.

    Neighborhoods are injected after the last iteration.

    :param topology: the :class:`Topology` to iterate
    :yield: each :class:`AgentNode` in graph order

    Example::

        topology = complete_topology(3)
        for node in per_node(topology):
            node.add(MyAgent())
    """
    for node in topology.graph.nodes:
        yield topology.graph.nodes[node][AGENT_NODE_KEY]
    topology.inject()


def auto_assign(topology: Topology, agents: list[Agent]) -> None:
    """Distribute *agents* across nodes in round-robin order.

    Agents are assigned to nodes in the order the graph iterates them.
    If there are more agents than nodes the assignment wraps around.

    :param topology: the target :class:`Topology`
    :param agents: list of agents to distribute

    Example::

        topology = complete_topology(3)
        auto_assign(topology, my_agents)
    """
    node_ids = list(topology.graph.nodes)
    for i, agent in enumerate(agents):
        node_id = node_ids[i % len(node_ids)]
        topology.graph.nodes[node_id][AGENT_NODE_KEY].add(agent)
    topology.inject()


def assign_agents(
    condition: Callable[[Agent, AgentNode], bool],
    topology: Topology,
    agents: list[Agent],
) -> None:
    """Assign agents to nodes based on a predicate.

    *condition* receives each ``(agent, node)`` pair and should return
    ``True`` when that agent should be placed in that node.

    :param condition: ``(agent, node) -> bool``
    :param topology: the target :class:`Topology`
    :param agents: pool of agents to assign

    Example::

        assign_agents(
            lambda agent, node: isinstance(agent, SensorAgent),
            topology,
            all_agents,
        )
    """
    for node_id in topology.graph.nodes:
        agent_node: AgentNode = topology.graph.nodes[node_id][AGENT_NODE_KEY]
        for agent in agents:
            if condition(agent, agent_node):
                agent_node.add(agent)
    topology.inject()


def mark_as_connector(agent: Agent, connector_type: str = "default") -> None:
    """Mark *agent* as a connector for later :func:`connect_topologies` calls.

    Unlike :meth:`Topology.set_as_connector`, this can be called before the
    topology is built.  The mark is picked up during :meth:`Topology.inject`.

    :param agent: the agent to mark
    :param connector_type: connection type label (default ``"default"``)
    """
    svc = agent.service_of_type(TopologyService)
    if connector_type not in svc._marked_connector_for:
        svc._marked_connector_for.append(connector_type)


def connect_topologies(
    topology_one: Topology,
    topology_two: Topology,
    connection_type: str = "default",
    *,
    directed: bool = False,
) -> None:
    """Link two topologies so their connector agents can reach each other.

    Connectors are agents registered via :meth:`Topology.set_as_connector` or
    :func:`mark_as_connector`.  After connecting, connector agents on each side
    will have the opposing side's connectors in their
    :meth:`~mango.TopologyService.connectors` list.

    :param topology_one: first topology
    :param topology_two: second topology
    :param connection_type: the link type label (default ``"default"``)
    :param directed: if ``True`` only ``topology_one → topology_two``
        (default ``False``)
    """
    link_one = (connection_type, topology_two)
    if link_one not in topology_one._connections:
        topology_one._connections.append(link_one)
    topology_one._build_and_inject()
    if not directed:
        link_two = (connection_type, topology_one)
        if link_two not in topology_two._connections:
            topology_two._connections.append(link_two)
        topology_two._build_and_inject()


def topology_neighbors(
    agent_or_role: Any,
    *,
    state: State = State.NORMAL,
    tid: str = "default",
    has_characteristic: str | None = None,
    include_connectors: tuple[str, ...] | list[str] = (),
    match_func: Callable[[AgentDescription], bool] | None = None,
) -> list[AgentAddress]:
    """Return the neighbor addresses of *agent_or_role* in topology *tid*.

    Accepts both :class:`~mango.Agent` instances and
    :class:`~mango.agent.role.Role` instances.

    :param agent_or_role: the agent or role whose neighbors to retrieve
    :param state: filter by edge state (default :attr:`~mango.State.NORMAL`)
    :param tid: topology identifier (default ``"default"``)
    :param has_characteristic: only include neighbors with this characteristic
    :param include_connectors: also include connector agents of these types
    :param match_func: optional ``(AgentDescription) -> bool`` predicate
    :return: list of :class:`~mango.AgentAddress`

    Example::

        addrs = topology_neighbors(agent)
        addrs = topology_neighbors(role, state=State.INACTIVE, tid="overlay")
    """
    svc = _get_service(agent_or_role)
    return svc.neighbors(
        state,
        tid=tid,
        has_characteristic=has_characteristic,
        include_connectors=include_connectors,
        match_func=match_func,
    )


def topology_node_id(agent_or_role: Any, *, tid: str = "default") -> int:
    """Return the node ID *agent_or_role* occupies in topology *tid*.

    :param agent_or_role: the agent or role to query
    :param tid: topology identifier (default ``"default"``)
    :return: integer node ID
    :raises KeyError: if the agent has not been injected into *tid*
    """
    return _get_service(agent_or_role).node_id(tid)


def topology_characteristic(agent_or_role: Any, *, tid: str = "default") -> str:
    """Return the characteristic label of *agent_or_role* in topology *tid*.

    :param agent_or_role: the agent or role to query
    :param tid: topology identifier (default ``"default"``)
    :return: characteristic string, or ``""`` if none was set
    """
    return _get_service(agent_or_role).characteristic(tid)


def topology_connectors(
    agent_or_role: Any,
    *,
    tid: str = "default",
    include_connectors: tuple[str, ...] | list[str] = (),
    match_func: Callable[[AgentDescription], bool] | None = None,
) -> list[AgentAddress]:
    """Return connector addresses visible to *agent_or_role* in topology *tid*.

    :param agent_or_role: the agent or role to query
    :param tid: topology identifier (default ``"default"``)
    :param include_connectors: filter to these connection type labels
    :param match_func: optional ``(AgentDescription) -> bool`` predicate
    :return: list of :class:`~mango.AgentAddress`
    """
    return _get_service(agent_or_role).connectors(
        tid, include_connectors=include_connectors, match_func=match_func
    )


def topology_connection_types(agent_or_role: Any, *, tid: str = "default") -> list[str]:
    """Return connection type labels for connectors in topology *tid*.

    :param agent_or_role: the agent or role to query
    :param tid: topology identifier (default ``"default"``)
    :return: list of connection type strings
    """
    return _get_service(agent_or_role).connection_types(tid)


def topology_to_aid_graph(topology: Topology) -> nx.Graph:
    """Convert a :class:`Topology` to an agent-level NetworkX graph.

    Each agent becomes an individual node (labelled by AID).  Nodes in the
    same topology node are connected with NORMAL edges; nodes in adjacent
    topology nodes inherit the topology edge state.

    :param topology: the source :class:`Topology`
    :return: a :class:`networkx.Graph` with AID-labelled nodes and
        :class:`~mango.State`-labelled edges

    Example::

        aid_graph = topology_to_aid_graph(topology)
        # Use with create_distribution_based_com_sim for topology-aware delays
    """
    g: nx.Graph = nx.Graph()

    # Add one vertex per agent
    for node_id in topology.graph.nodes:
        agent_node: AgentNode = topology.graph.nodes[node_id][AGENT_NODE_KEY]
        for agent in agent_node.agents:
            g.add_node(agent.aid, agent=agent)

    # Same-node agents are fully connected (NORMAL)
    for node_id in topology.graph.nodes:
        agent_node = topology.graph.nodes[node_id][AGENT_NODE_KEY]
        for i, a1 in enumerate(agent_node.agents):
            for a2 in agent_node.agents[i + 1 :]:
                if not g.has_edge(a1.aid, a2.aid):
                    g.add_edge(a1.aid, a2.aid, state=State.NORMAL)

    # Cross-node edges inherit topology edge state
    for src, dst in topology.graph.edges:
        edge_state: State = topology.graph.edges[src, dst][STATE_EDGE_KEY]
        src_node: AgentNode = topology.graph.nodes[src][AGENT_NODE_KEY]
        dst_node: AgentNode = topology.graph.nodes[dst][AGENT_NODE_KEY]
        for a_src in src_node.agents:
            for a_dst in dst_node.agents:
                if not g.has_edge(a_src.aid, a_dst.aid):
                    g.add_edge(a_src.aid, a_dst.aid, state=edge_state)

    return g


async def broadcast_to_neighbors(
    agent_or_role: Any,
    content: Any,
    *,
    state: State = State.NORMAL,
    tid: str = "default",
    has_characteristic: str | None = None,
    include_connectors: tuple[str, ...] | list[str] = (),
    match_func: Callable[[AgentDescription], bool] | None = None,
    **kwargs: Any,
) -> list[AgentAddress]:
    """Send *content* to all topology neighbors of *agent_or_role*.

    Accepts all keyword arguments of :func:`topology_neighbors` for filtering,
    plus any extra kwargs that are forwarded to ``send_message``.

    :param agent_or_role: sender agent or role
    :param content: message content
    :param state: edge state filter (default :attr:`~mango.State.NORMAL`)
    :param tid: topology identifier (default ``"default"``)
    :param has_characteristic: neighbor characteristic filter
    :param include_connectors: also broadcast to these connector types
    :param match_func: optional ``(AgentDescription) -> bool`` filter
    :return: list of addresses that received the message

    Example::

        async def on_ready(self):
            await broadcast_to_neighbors(self, "ping")
    """
    addrs = topology_neighbors(
        agent_or_role,
        state=state,
        tid=tid,
        has_characteristic=has_characteristic,
        include_connectors=include_connectors,
        match_func=match_func,
    )
    # Resolve sender: agent or role
    sender = _get_agent(agent_or_role)
    for addr in addrs:
        await sender.send_message(content, receiver_addr=addr, **kwargs)
    return addrs


def _get_service(agent_or_role: Any) -> TopologyService:
    # For a Role: navigate Role._context (RoleContext) -> .context (AgentContext)
    # Services are stored on AgentContext, so we must reach it from there.
    role_ctx = getattr(agent_or_role, "_context", None)
    if role_ctx is not None:
        agent_ctx = role_ctx.context
        if agent_ctx is not None:
            return agent_ctx.service_of_type(TopologyService)
    return agent_or_role.service_of_type(TopologyService)


def _get_agent(agent_or_role: Any) -> Any:
    # For a Role: return the RoleContext, which has send_message.
    # For an Agent: return it directly.
    role_ctx = getattr(agent_or_role, "_context", None)
    if role_ctx is not None:
        return role_ctx
    return agent_or_role
