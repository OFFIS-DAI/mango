import pytest
from mango.core.container import Container
from mango.role.core import RoleAgent
from mango.cohda.coalition import *

@pytest.mark.asyncio
@pytest.mark.parametrize("num_part", [1, 2, 3, 4, 5])
async def test_build_coalition(num_part):
    # create containers
    
    c = await Container.factory(addr=('127.0.0.2', 5555))
    
    # create agents
    agents = []
    addrs = []
    for _ in range(num_part):
        a = RoleAgent(c)
        a.add_role(CoalitionParticipantRole())
        agents.append(a)
        addrs.append((c.addr, a._aid))

    controller_agent = RoleAgent(c)
    controller_agent.add_role(CoalitionInitiatorRole(addrs, 'cohda', 'cohda-negotiation'))
    agents.append(controller_agent)

    # all agents send ping request to all agents (including themselves)

    for a in agents:
        if a._check_inbox_task.done():
            if a._check_inbox_task.exception() is not None:
                raise a._check_inbox_task.exception()
            else:
                assert False, f'check_inbox terminated unexpectedly.'
    
    for a in agents:
        await a.tasks_complete()

    await asyncio.wait_for(wait_for_coalition_built(agents[0:num_part]), timeout=5)

    # gracefully shutdown
    for a in agents:
        await a.shutdown()
    await c.shutdown()

    assert len(asyncio.all_tasks()) == 1
    for a in agents[0:num_part]:
        assignments = a.roles[0].context.get_or_create_model(CoalitionModel).assignments
        assert list(assignments.values())[0].coalition_id is not None
        assert list(assignments.values())[0].controller_agent_id == controller_agent.aid
        assert list(assignments.values())[0].controller_agent_addr == c.addr
        assert len(list(assignments.values())[0].neighbors) == num_part-1

async def wait_for_coalition_built(agents):
    for agent in agents:
        while len(agent.roles[0].context.get_or_create_model(CoalitionModel).assignments) == 0:
            await asyncio.sleep(1)
