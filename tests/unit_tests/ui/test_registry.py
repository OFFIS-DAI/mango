import pytest

from mango import Agent, activate, create_tcp_container
from mango.ui import TopologyRegistry


@pytest.mark.asyncio
async def test_register_backfills_visible_agents():
    c = create_tcp_container(addr=("127.0.0.1", 15780))
    c.register(Agent(), suggested_aid="visible_one")

    registry = TopologyRegistry()
    registry.register(c)

    assert f"{c.addr}:visible_one" in registry._agents


@pytest.mark.asyncio
async def test_register_skips_invisible_agents():
    c = create_tcp_container(addr=("127.0.0.1", 15781))
    c.register(Agent(visible=False), suggested_aid="hidden_one")

    registry = TopologyRegistry()
    registry.register(c)

    assert f"{c.addr}:hidden_one" not in registry._agents


@pytest.mark.asyncio
async def test_live_register_and_deregister_agent():
    c = create_tcp_container(addr=("127.0.0.1", 15782))
    registry = TopologyRegistry()
    registry.register(c)

    async with activate(c) as c:
        agent = c.register(Agent(), suggested_aid="live_agent")
        assert f"{c.addr}:live_agent" in registry._agents

        await agent.shutdown()
        assert f"{c.addr}:live_agent" not in registry._agents


@pytest.mark.asyncio
async def test_container_shutdown_clears_registry():
    c = create_tcp_container(addr=("127.0.0.1", 15783))
    registry = TopologyRegistry()

    async with activate(c) as c:
        registry.register(c)
        c.register(Agent(), suggested_aid="agent_one")
        assert registry._agents
        assert registry._containers

    assert registry._agents == {}
    assert registry._containers == {}


@pytest.mark.asyncio
async def test_add_connection_snapshot():
    c1 = create_tcp_container(addr=("127.0.0.1", 15784))
    c2 = create_tcp_container(addr=("127.0.0.1", 15785))
    registry = TopologyRegistry()
    registry.register(c1)
    registry.register(c2)

    a1 = c1.register(Agent(), suggested_aid="a1")
    a2 = c2.register(Agent(), suggested_aid="a2")

    registry.add_connection(a1.addr, a2.addr, conn_type="tcp", direction="uni")

    snapshot = registry._snapshot()
    assert len(snapshot["connections"]) == 1
    assert snapshot["connections"][0]["source_aid"] == "a1"
    assert snapshot["connections"][0]["target_aid"] == "a2"


@pytest.mark.asyncio
async def test_check_health_uses_agent_health_check():
    class HealthyAgent(Agent):
        async def health_check(self):
            return "great"

    c = create_tcp_container(addr=("127.0.0.1", 15786))
    registry = TopologyRegistry()
    registry.register(c)
    c.register(HealthyAgent(), suggested_aid="healthy")

    await registry.check_health()

    assert registry._agents[f"{c.addr}:healthy"].status == "great"
