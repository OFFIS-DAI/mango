from contextlib import contextmanager

import networkx as nx

from mango.agent.core import Agent, AgentAddress, State, TopologyService

AGENT_NODE_KEY = "node"
STATE_EDGE_KEY = "state"


def _flatten_list(list):
    return [x for xs in list for x in xs]


class AgentNode:
    def __init__(self, agents: list = None) -> None:
        self.agents: list[Agent] = [] if agents is None else agents

    def add(self, agent: Agent):
        self.agents.append(agent)


class Topology:
    def __init__(self, graph) -> None:
        self.graph: nx.Graph = graph

        for node in self.graph.nodes:
            self.graph.nodes[node][AGENT_NODE_KEY] = AgentNode()
        for edge in self.graph.edges:
            self.graph.edges[edge][STATE_EDGE_KEY] = State.NORMAL

    def set_edge_state(self, node_id_from: int, node_id_to: int, state: State):
        self.graph.edges[node_id_from, node_id_to] = state

    def add_node(self, *agents: Agent):
        id = len(self.graph)
        self.graph.add_node(id, node=AgentNode(agents))
        return id

    def add_edge(self, node_from, node_to, state: State = State.NORMAL):
        self.graph.add_edge(node_from, node_to, state=state)

    @property
    def agents(self) -> list[Agent]:
        """Return all agents controlled by the topology after
        the neighborhood were injected.

        :return: the list of agents
        :rtype: list[Agent]
        """
        return _flatten_list(
            [self.graph.nodes[node][AGENT_NODE_KEY].agents for node in self.graph.nodes]
        )

    def inject(self):
        # 2nd pass, build the neighborhoods and add it to agents
        for node in self.graph.nodes:
            agent_node = self.graph.nodes[node][AGENT_NODE_KEY]
            state_to_neighbors: dict[State, list[AgentAddress]] = dict()

            for neighbor in self.graph.neighbors(node):
                state = self.graph.edges[node, neighbor][STATE_EDGE_KEY]
                neighbor_addresses = state_to_neighbors.setdefault(state, [])
                neighbor_addresses.extend(
                    [
                        lambda: agent.addr
                        for agent in self.graph.nodes[neighbor][AGENT_NODE_KEY].agents
                    ]
                )
            for agent in agent_node.agents:
                topology_service = agent.service_of_type(
                    TopologyService, TopologyService()
                )
                topology_service.state_to_neighbors = state_to_neighbors


def complete_topology(number_of_nodes: int) -> Topology:
    """
    Create a fully-connected topology.
    """
    graph = nx.complete_graph(number_of_nodes)
    return Topology(graph)


def custom_topology(graph: nx.Graph) -> Topology:
    """
    Create an already created custom topology base on a nx Graph.
    """
    return Topology(graph)


@contextmanager
def create_topology(directed: bool = False):
    """Create a topology, which will automatically inject the neighbors to
    the participating agents. Example:

    .. code-block:: python

        agents = [TopAgent(), TopAgent(), TopAgent()]
        with create_topology() as topology:
            id_1 = topology.add_node(agents[0])
            id_2 = topology.add_node(agents[1])
            id_3 = topology.add_node(agents[2])
            topology.add_edge(id_1, id_2)
            topology.add_edge(id_1, id_3)

    :param directed: _description_, defaults to False
    :type directed: bool, optional
    :yield: _description_
    :rtype: _type_
    """
    topology = Topology(nx.DiGraph() if directed else nx.Graph())
    try:
        yield topology
    finally:
        topology.inject()


def per_node(topology: Topology):
    """Assign agents per node of the already created topology. This method
    shall be used as iterator in a for in construct. The iterator will return
    nodes, which can be used to add (with node.add()) agents to the node.

    .. code-block:: python

        topology = complete_topology(3)
        for node in per_node(topology):
            node.add(TopAgent())


    :param topology: the topology
    :type topology: Topology
    :yield: AgentNode
    :rtype: _type_
    """
    for node in topology.graph.nodes:
        agent_node = topology.graph.nodes[node][AGENT_NODE_KEY]
        yield agent_node
    topology.inject()
