
from mango.cohda.coalition import CoalitionAssignment
import pytest, uuid
from mango.core.container import Container
from mango.role.core import RoleAgent
from mango.cohda.planning import *

def test_cohda_init():
    cohda = COHDA([1, 1, 1], lambda: [[0, 1, 2], [1, 2, 3]], lambda s: True, 1)
    cohda_message = CohdaMessage(WorkingMemory([1, 2, 3], {}, SolutionCandidate(1, {})))
    old, new = cohda.decide(cohda_message)
    
    assert new.target_schedule == [1, 2, 3]

def test_cohda_selection_multi():
    cohda = COHDA([1, 1, 1], lambda: [[0, 1, 2], [1, 2, 3], [1, 1, 1], [4, 2, 3]], lambda s: True, 1)
    cohda_message = CohdaMessage(WorkingMemory([1, 2, 1], {}, SolutionCandidate(1, {})))
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

    agents[0].add_role(CohdaNegotiationStarterRole([110, 110, 110, 110, 110]))

    for a in agents:
        if a._check_inbox_task.done():
            if a._check_inbox_task.exception() is not None:
                raise a._check_inbox_task.exception()
            else:
                assert False, f'check_inbox terminated unexpectedly.'
    
    for a in agents:
        await a.tasks_complete()

    await asyncio.wait_for(wait_for_coalition_built(agents), timeout=5)

    # gracefully shutdown
    for a in agents:
        await a.shutdown()
    await c.shutdown()

    assert len(asyncio.all_tasks()) == 1
    assert agents[0].roles[0]._cohda[coal_id]._memory.solution_candidate.candidate[0] == [11, 11, 11, 11, 11]

@pytest.mark.asyncio
async def test_optimize_hinrichs_test_case():
    # create containers
    
    c = await Container.factory(addr=('127.0.0.2', 5555))
    
    s_array = [[[1,1,1,1,1],[4,3,3,3,3],[6,6,6,6,6],[9,8,8,8,8],[11,11,11,11,11]],[[13,12,12,12,12],[15,15,15,14,14],[18,17,17,17,17],[20,20,20,19,19],[23,22,22,22,22]],[[25,24,23,23,23],[27,26,26,25,25],[30,29,28,28,28],[32,31,31,30,30],[35,34,33,33,33]],[[36,35,35,34,34],[39,38,37,36,36],[41,40,40,39,39],[44,43,42,41,41],[46,45,45,44,44]],[[48,47,46,45,45],[50,49,48,48,47],[53,52,51,50,50],[55,54,53,53,52],[58,57,56,55,55]],[[60,58,57,56,56],[62,61,60,59,58],[65,63,62,61,61],[67,66,65,64,63],[70,68,67,66,66]],[[71,70,68,67,67],[74,72,71,70,69],[76,75,73,72,72],[79,77,76,75,74],[81,80,78,77,77]],[[83,81,80,78,78],[85,83,82,81,80],[88,86,85,83,83],[90,88,87,86,85],[93,91,90,88,88]],[[95,92,91,90,89],[97,95,93,92,91],[100,97,96,95,94],[102,100,98,97,96],[105,102,101,100,99]],[[106,104,102,101,100],[109,106,105,103,102],[111,109,107,106,105],[114,111,110,108,107],[116,114,112,111,110]]]

    # create agents
    agents = []
    addrs = []
    for i in range(10):
        a = RoleAgent(c)
        cohda_role = COHDARole(lambda i=i: s_array[i], [1,1,1,1,1], lambda s: True)
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

    # gracefully shutdown
    for a in agents:
        await a.shutdown()
    await c.shutdown()

    assert len(asyncio.all_tasks()) == 1
    cluster_schedule = np.array(list(map(lambda item: item[1], agents[0].roles[0]._cohda[coal_id]._memory.solution_candidate.candidate.items())))
    assert [543, 529, 520, 512, 510] == cluster_schedule.sum(axis=0).tolist()

async def wait_for_coalition_built(agents):
    for agent in agents:
        while not agent.inbox.empty():
            await asyncio.sleep(1)
