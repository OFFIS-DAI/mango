from contextlib import contextmanager

import networkx as nx

from mango.agent.core import Agent, AgentAddress, State, TopologyService

AGENT_NODE_KEY = "node"
STATE_EDGE_KEY = "state"


class AgentNode:
    def __init__(self) -> None:
        self.agents: list[Agent] = []

    def add(self, agent: Agent):
        self.agents.append(agent)


class Topology:
    def __init__(self, graph) -> None:
        self.graph = graph

        for node in self.graph.nodes:
            self.graph.nodes[node][AGENT_NODE_KEY] = AgentNode()
        for edge in self.graph.edges:
            self.graph.edges[edge][STATE_EDGE_KEY] = State.NORMAL

    def set_edge_state(self, node_id_from: int, node_id_to: int, state: State):
        self.graph[node_id_from, node_id_to] = state

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


@contextmanager
def create_topology(directed: bool = False):
    topology = Topology(nx.DiGraph() if directed else nx.Graph())
    try:
        yield topology
    finally:
        topology.inject()


def per_node(topology: Topology):
    for node in topology.graph.nodes:
        agent_node = topology.graph.nodes[node][AGENT_NODE_KEY]
        yield agent_node
    topology.inject()
