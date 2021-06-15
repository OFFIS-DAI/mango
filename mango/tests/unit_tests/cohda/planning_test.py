
from mango.cohda.coalition import CoalitionAssignment
import pytest, uuid
from mango.core.container import Container
from mango.role.core import RoleAgent
from mango.cohda.planning import *

def test_cohda_init():
    cohda = COHDA([1, 1, 1], lambda: [[0, 1, 2], [1, 2, 3]], lambda s: True, 1)
    cohda_message = CohdaMessage(WorkingMemory([1, 2, 3], {}, SolutionCandidate(1, {})), '1#2')
    old, new = cohda.decide(cohda_message)
    
    assert new.target_schedule == [1, 2, 3]

def test_cohda_selection_multi():
    cohda = COHDA([1, 1, 1], lambda: [[0, 1, 2], [1, 2, 3], [1, 1, 1], [4, 2, 3]], lambda s: True, 1)
    cohda_message = CohdaMessage(WorkingMemory([1, 2, 1], {}, SolutionCandidate(1, {})), '1#2')
    old, new = cohda.decide(cohda_message)
    
    assert new.solution_candidate.candidate[1] == [1, 1, 1]
    assert new.system_config[1].counter == 1


@pytest.mark.asyncio
async def test_optimize_simple_test_case():
    # create containers
    
    c = await Container.factory(addr=('127.0.0.2', 5555))
    
    s_array = [[[1, 1, 1, 1, 1],[4,3,3,3,3],[6,6,6,6,6],[9,8,8,8,8],[11,11,11,11,11]]]

    # create agents
    agents = []
    addrs = []
    for _ in range(10):
        a = RoleAgent(c)
        cohda_role = COHDARole(lambda: s_array[0], [1,1,1,1,1], lambda s: True)
        a.add_role(cohda_role)
        agents.append(a)
        addrs.append((c.addr, a._aid))


    part_id = 0
    coal_id = uuid.uuid1()
    for a in agents:
        coalition_model = a._agent_context.get_or_create_model(CoalitionModel)
        coalition_model.add(coal_id, CoalitionAssignment(coal_id, list(
            filter(lambda a_t: a_t[0] != part_id, 
            map(lambda ad: (ad[1], c.addr, ad[0].aid), 
            zip(agents, range(10))))), 'cohda', part_id, 1, 1))
        part_id += 1

    agents[0].add_role(CohdaNegotiationStarterRole([542, 528, 519, 511, 509]))

    for a in agents:
        if a._check_inbox_task.done():
            if a._check_inbox_task.exception() is not None:
                raise a._check_inbox_task.exception()
            else:
                assert False, f'check_inbox terminated unexpectedly.'
    
    for a in agents:
        await a.tasks_complete()

    await asyncio.wait_for(wait_for_coalition_built(agents), timeout=5)
    print(agents[0].roles[0]._cohda[coal_id]._memory.solution_candidate.candidate)

    # gracefully shutdown
    for a in agents:
        await a.shutdown()
    await c.shutdown()

    assert len(asyncio.all_tasks()) == 1

async def wait_for_coalition_built(agents):
    for agent in agents:
        while not agent.inbox.empty():
            await asyncio.sleep(1)
