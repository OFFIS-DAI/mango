
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