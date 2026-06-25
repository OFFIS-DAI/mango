import pytest

from mango import Agent, activate, create_tcp_container
from mango.ui import TopologyRegistry


@pytest.mark.asyncio
async def test_registry_backfills_and_clears_subprocess_agents():
    c = create_tcp_container(addr=("127.0.0.1", 15790))
    await c.as_agent_process(
        agent_creator=lambda container: container.register(
            Agent(), suggested_aid="sp_agent"
        )
    )
    c.register(Agent(), suggested_aid="main_agent")

    registry = TopologyRegistry()

    async with activate(c) as c:
        registry.register(c)
        assert f"{c.addr}:sp_agent" in registry._agents
        assert f"{c.addr}:main_agent" in registry._agents

    # container shutdown should deregister subprocess-resident agents too
    assert registry._agents == {}
    assert registry._containers == {}
