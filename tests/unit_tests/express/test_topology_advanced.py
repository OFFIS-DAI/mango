"""Tests for the extended topology API."""

import asyncio
from typing import Any

import pytest

from mango import (
    Agent,
    RoleAgent,
    State,
    assign_agents,
    auto_assign,
    complete_topology,
    connect_topologies,
    create_topology,
    cycle_topology,
    graph_topology,
    modify_topology,
    run_with_tcp,
    star_topology,
    topology_characteristic,
    topology_connection_types,
    topology_connectors,
    topology_neighbors,
    topology_node_id,
    topology_to_aid_graph,
)
from mango.agent.role import Role
from mango.express.topology import broadcast_to_neighbors


class TopAgent(Agent):
    def __init__(self):
        super().__init__()
        self.received: list[Any] = []

    def handle_message(self, content, meta):
        self.received.append(content)


class TopAgent2(Agent):
    def handle_message(self, content, meta):
        pass


def _make_agents(n, cls=TopAgent):
    return [cls() for _ in range(n)]


def test_star_topology_structure():
    t = star_topology(4)
    assert t.graph.number_of_nodes() == 4
    hub = 0
    assert t.graph.degree(hub) == 3


def test_cycle_topology_structure():
    t = cycle_topology(5)
    assert t.graph.number_of_nodes() == 5
    for node in t.graph.nodes:
        assert t.graph.degree(node) == 2


def test_graph_topology_uses_provided_graph():
    import networkx as nx

    g = nx.path_graph(4)
    t = graph_topology(g)
    assert t.graph.number_of_nodes() == 4


def test_remove_edge():
    t = complete_topology(3)
    t.remove_edge(0, 1)
    assert not t.graph.has_edge(0, 1)
    assert t.graph.has_edge(0, 2)


def test_remove_node():
    t = complete_topology(3)
    t.remove_node(2)
    assert 2 not in t.graph.nodes
    assert t.graph.number_of_nodes() == 2


def test_add_node_after_remove_no_collision():
    t = complete_topology(3)
    t.remove_node(0)
    # nodes are now [1, 2]; new node must not reuse id 2
    new_id = t.add_node()
    assert new_id not in [1, 2]
    assert t.graph.number_of_nodes() == 3


def test_set_edge_state_both_directions():
    t = complete_topology(3)
    t.set_edge_state(0, 1, State.BROKEN)
    from mango.express.topology import STATE_EDGE_KEY

    assert t.graph.edges[0, 1][STATE_EDGE_KEY] == State.BROKEN
    assert t.graph.edges[1, 0][STATE_EDGE_KEY] == State.BROKEN


def test_set_edge_state_one_direction():
    import networkx as nx

    from mango.express.topology import STATE_EDGE_KEY

    g = nx.DiGraph()
    g.add_edge(0, 1)
    g.add_edge(1, 0)
    t = graph_topology(g)
    t.set_edge_state(0, 1, State.INACTIVE, both_directions=False)
    assert t.graph.edges[0, 1][STATE_EDGE_KEY] == State.INACTIVE
    assert t.graph.edges[1, 0][STATE_EDGE_KEY] == State.NORMAL


def test_auto_assign_distributes_agents():
    t = complete_topology(3)
    agents = _make_agents(3)
    auto_assign(t, agents)
    assert set(t.agents) == set(agents)
    assert len(t.agents) == 3


def test_auto_assign_wraps_around():
    t = complete_topology(2)
    agents = _make_agents(4)
    auto_assign(t, agents)
    assert len(t.agents) == 4


def test_assign_agents_condition():
    t = complete_topology(2)
    agents = _make_agents(2) + [TopAgent2(), TopAgent2()]
    assign_agents(lambda a, node: isinstance(a, TopAgent2), t, agents)
    for a in t.agents:
        assert isinstance(a, TopAgent2)


def test_tid_default():
    t = complete_topology(2)
    assert t.tid == "default"


def test_custom_tid():
    t = complete_topology(2, tid="overlay")
    assert t.tid == "overlay"


def test_set_characteristic_and_query():
    agents = _make_agents(3)
    with create_topology() as t:
        n0 = t.add_node(agents[0], agents[1])
        n1 = t.add_node(agents[2])
        t.add_edge(n0, n1)
        t.set_characteristic(n0, agents[0], "leader")

    assert topology_characteristic(agents[0]) == "leader"
    assert topology_characteristic(agents[1]) == ""
    assert topology_characteristic(agents[2]) == ""


@pytest.mark.asyncio
async def test_topology_neighbors_returns_addresses():
    agents = _make_agents(3)
    with create_topology() as t:
        n0 = t.add_node(agents[0])
        n1 = t.add_node(agents[1])
        n2 = t.add_node(agents[2])
        t.add_edge(n0, n1)
        t.add_edge(n0, n2)

    async with run_with_tcp(1, *agents):
        addrs = topology_neighbors(agents[0])
        assert len(addrs) == 2
        all_addrs = {a.addr for a in agents[1:]}
        assert set(addrs) == all_addrs


@pytest.mark.asyncio
async def test_topology_neighbors_state_filter():
    agents = _make_agents(3)
    with create_topology() as t:
        n0 = t.add_node(agents[0])
        n1 = t.add_node(agents[1])
        n2 = t.add_node(agents[2])
        t.add_edge(n0, n1)
        t.add_edge(n0, n2, State.BROKEN)

    async with run_with_tcp(1, *agents):
        normal = topology_neighbors(agents[0], state=State.NORMAL)
        broken = topology_neighbors(agents[0], state=State.BROKEN)
        assert len(normal) == 1
        assert len(broken) == 1


@pytest.mark.asyncio
async def test_topology_neighbors_has_characteristic():
    agents = _make_agents(3)
    with create_topology() as t:
        n0 = t.add_node(agents[0])
        n1 = t.add_node(agents[1])
        n2 = t.add_node(agents[2])
        t.add_edge(n0, n1)
        t.add_edge(n0, n2)
        t.set_characteristic(n1, agents[1], "leader")

    async with run_with_tcp(1, *agents):
        leaders = topology_neighbors(agents[0], has_characteristic="leader")
        assert len(leaders) == 1
        assert leaders[0] == agents[1].addr


@pytest.mark.asyncio
async def test_topology_neighbors_match_func():
    agents = _make_agents(2) + [TopAgent2()]
    with create_topology() as t:
        n0 = t.add_node(agents[0])
        n1 = t.add_node(agents[1])
        n2 = t.add_node(agents[2])
        t.add_edge(n0, n1)
        t.add_edge(n0, n2)

    async with run_with_tcp(1, *agents):
        filtered = topology_neighbors(
            agents[0],
            match_func=lambda desc: desc.category == "agent",
        )
        assert len(filtered) == 2


@pytest.mark.asyncio
async def test_topology_node_id():
    agents = _make_agents(2)
    with create_topology() as t:
        n0 = t.add_node(agents[0])
        n1 = t.add_node(agents[1])
        t.add_edge(n0, n1)

    async with run_with_tcp(1, *agents):
        assert topology_node_id(agents[0]) == n0
        assert topology_node_id(agents[1]) == n1


@pytest.mark.asyncio
async def test_modify_topology_reinjects():
    agents = _make_agents(3)
    with create_topology() as t:
        n0 = t.add_node(agents[0])
        n1 = t.add_node(agents[1])
        n2 = t.add_node(agents[2])
        t.add_edge(n0, n1)
        t.add_edge(n0, n2)

    async with run_with_tcp(1, *agents):
        assert len(topology_neighbors(agents[0])) == 2
        with modify_topology(t):
            t.remove_edge(n0, n2)
        assert len(topology_neighbors(agents[0])) == 1


@pytest.mark.asyncio
async def test_connect_topologies():
    agents_a = _make_agents(2)
    agents_b = _make_agents(2)

    with create_topology(tid="a") as ta:
        na0 = ta.add_node(agents_a[0])
        na1 = ta.add_node(agents_a[1])
        ta.add_edge(na0, na1)
        ta.set_as_connector(agents_a[0])

    with create_topology(tid="b") as tb:
        nb0 = tb.add_node(agents_b[0])
        nb1 = tb.add_node(agents_b[1])
        tb.add_edge(nb0, nb1)
        tb.set_as_connector(agents_b[0])

    connect_topologies(ta, tb)

    async with run_with_tcp(1, *agents_a, *agents_b):
        conn_a = topology_connectors(agents_a[0], tid="a")
        assert agents_b[0].addr in conn_a
        types_a = topology_connection_types(agents_a[0], tid="a")
        assert "default" in types_a


@pytest.mark.asyncio
async def test_broadcast_to_neighbors():
    agents = _make_agents(3)
    with create_topology() as t:
        n0 = t.add_node(agents[0])
        n1 = t.add_node(agents[1])
        n2 = t.add_node(agents[2])
        t.add_edge(n0, n1)
        t.add_edge(n0, n2)

    async with run_with_tcp(1, *agents):
        await broadcast_to_neighbors(agents[0], "hello")
        await asyncio.sleep(0.1)

    assert "hello" in agents[1].received
    assert "hello" in agents[2].received
    assert agents[0].received == []


@pytest.mark.asyncio
async def test_topology_to_aid_graph():
    agents = _make_agents(3)
    with create_topology() as t:
        n0 = t.add_node(agents[0], agents[1])
        n1 = t.add_node(agents[2])
        t.add_edge(n0, n1)

    async with run_with_tcp(1, *agents):
        g = topology_to_aid_graph(t)
        assert agents[0].aid in g.nodes
        assert agents[1].aid in g.nodes
        assert agents[2].aid in g.nodes
        assert g.has_edge(agents[0].aid, agents[1].aid)
        assert g.has_edge(agents[0].aid, agents[2].aid)
        assert g.has_edge(agents[1].aid, agents[2].aid)


def test_state_enum_values():
    assert State.NORMAL.value == 0
    assert State.INACTIVE.value == 1
    assert State.BROKEN.value == 2
    assert State.UNKNOWN.value == 3
    assert State.EXT_CONNECTION.value == 4


@pytest.mark.asyncio
async def test_multi_tid_neighbors():
    """An agent in two topologies can query each independently."""
    agents = _make_agents(3)
    with create_topology(tid="net1") as t1:
        n0 = t1.add_node(agents[0])
        n1 = t1.add_node(agents[1])
        t1.add_edge(n0, n1)

    with create_topology(tid="net2") as t2:
        m0 = t2.add_node(agents[0])
        m1 = t2.add_node(agents[2])
        t2.add_edge(m0, m1)

    async with run_with_tcp(1, *agents):
        net1_neighbors = topology_neighbors(agents[0], tid="net1")
        net2_neighbors = topology_neighbors(agents[0], tid="net2")
        assert agents[1].addr in net1_neighbors
        assert agents[2].addr not in net1_neighbors
        assert agents[2].addr in net2_neighbors
        assert agents[1].addr not in net2_neighbors


@pytest.mark.asyncio
async def test_topology_queries_from_role():
    """topology_neighbors and topology_node_id work when called with a Role."""

    class QueryRole(Role):
        def setup(self):
            pass

    agents = [RoleAgent(), TopAgent(), TopAgent()]
    role = QueryRole()
    agents[0].add_role(role)

    with create_topology() as t:
        n0 = t.add_node(agents[0])
        n1 = t.add_node(agents[1])
        n2 = t.add_node(agents[2])
        t.add_edge(n0, n1)
        t.add_edge(n0, n2)

    async with run_with_tcp(1, *agents):
        addrs = topology_neighbors(role)
        assert len(addrs) == 2
        assert agents[1].addr in addrs
        assert agents[2].addr in addrs
        assert topology_node_id(role) == n0


@pytest.mark.asyncio
async def test_broadcast_to_neighbors_from_role():
    """broadcast_to_neighbors sends messages when called from a Role."""

    class BroadcastRole(Role):
        def setup(self):
            pass

    sender_agent = RoleAgent()
    receivers = _make_agents(2)
    role = BroadcastRole()
    sender_agent.add_role(role)

    with create_topology() as t:
        n0 = t.add_node(sender_agent)
        n1 = t.add_node(receivers[0])
        n2 = t.add_node(receivers[1])
        t.add_edge(n0, n1)
        t.add_edge(n0, n2)

    async with run_with_tcp(1, sender_agent, *receivers):
        await broadcast_to_neighbors(role, "hello-from-role")
        await asyncio.sleep(0.1)

    assert "hello-from-role" in receivers[0].received
    assert "hello-from-role" in receivers[1].received
