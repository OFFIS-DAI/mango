from mango.cohda.coalition import CoalitionParticipantRole, CoalitionInitiatorRole
import pytest, uuid
from mango.core.container import Container
from mango.role.core import RoleAgent
from mango.cohda.planning import *
from mango.cohda.termination import NegotiationTerminationRole
import asyncio


@pytest.mark.asyncio
async def test_coalition_to_cohda_with_termination():
    # create containers
    
    c = await Container.factory(addr=('127.0.0.2', 5555))
    
    s_array = [[[1, 1, 1, 1, 1],[4,3,3,3,3],[6,6,6,6,6],[9,8,8,8,8],[11,11,11,11,11]]]

    # create agents
    agents = []
    addrs = []
    for i in range(10):
        a = RoleAgent(c)
        cohda_role = COHDARole(lambda: s_array[0], [1,1,1,1,1], lambda s: True)
        a.add_role(cohda_role)
        a.add_role(CoalitionParticipantRole())
        a.add_role(NegotiationTerminationRole(i == 0))
        agents.append(a)
        addrs.append((c.addr, a._aid))

    agents[0].add_role(CoalitionInitiatorRole(addrs, 'cohda', 'cohda-negotiation'))
    
    await asyncio.wait_for(wait_for_coalition_built(agents), timeout=5)

    agents[0].add_role(CohdaNegotiationStarterRole([110, 110, 110, 110, 110]))

    for a in agents:
        if a._check_inbox_task.done():
            if a._check_inbox_task.exception() is not None:
                raise a._check_inbox_task.exception()
            else:
                assert False, f'check_inbox terminated unexpectedly.'
    
    for a in agents:
        await a.tasks_complete()

    await asyncio.wait_for(wait_for_term(agents), timeout=15)

    # gracefully shutdown
    for a in agents:
        await a.shutdown()
    await c.shutdown()

    assert len(asyncio.all_tasks()) == 1
    assert next(iter(agents[0].roles[0]._cohda.values()))._memory.solution_candidate.candidate[1] == [11, 11, 11, 11, 11]        
    assert next(iter(agents[0].roles[2]._weight_map.values())) == 1

async def wait_for_coalition_built(agents):
    for agent in agents:
        while not agent.inbox.empty():
            await asyncio.sleep(5)

async def wait_for_term(agents):
    await asyncio.sleep(1)
    for agent in agents:
        while not agent.inbox.empty() or next(iter(agents[0].roles[2]._weight_map.values())) != 1:
            await asyncio.sleep(5)
