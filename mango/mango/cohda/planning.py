from mango.cohda.coalition import CoalitionModel
from ..role.role import ProactiveRole, SimpleReactiveRole
from typing import Dict, Any, Tuple
import copy
from uuid import UUID
from ..util.scheduling import InstantScheduledTask
import numpy as np
import asyncio

class SolutionCandidate:
    def __init__(self, agent_id: int, candidate: Dict[int, np.array]) -> None:
        self._agent_id = agent_id
        self._candidate = candidate
    
    @property
    def agent_id(self):
        return self._agent_id

    @agent_id.setter
    def agent_id(self, new_id):
        self._agent_id = new_id

    @property
    def candidate(self):
        return self._candidate

    def __eq__(self, o: object) -> bool:
        return type(o) == SolutionCandidate and self._agent_id == o.agent_id and self.candidate == o.candidate


class ScheduleSelection:
    def __init__(self, schedule, counter) -> None:
        self._schedule = schedule
        self._counter = counter
    
    @property
    def counter(self):
        return self._counter
    
    @property
    def schedule(self):
        return self._schedule

    def __eq__(self, o: object) -> bool:
        return type(o) == ScheduleSelection and self.counter == o.counter and self.schedule == o.schedule

class WorkingMemory:
    def __init__(self, target_schedule, system_config: Dict[str, ScheduleSelection], solution_candidate):
        self._target_schedule = target_schedule
        self._system_config = system_config
        self._solution_candidate = solution_candidate
    
    @property
    def target_schedule(self):
        return self._target_schedule

    @target_schedule.setter
    def target_schedule(self, new_target):
        self._target_schedule = new_target

    @property
    def system_config(self) -> Dict[str, ScheduleSelection]:
        return self._system_config

    @property
    def solution_candidate(self):
        return self._solution_candidate

    @solution_candidate.setter
    def solution_candidate(self, new_solution_candidate):
        self._solution_candidate = new_solution_candidate

    def __eq__(self, o: object) -> bool:
        return type(o) == WorkingMemory and self.solution_candidate == o.solution_candidate and self.system_config == o.system_config and self.target_schedule == o.target_schedule

class CohdaMessage:

    def __init__(self, working_memory: WorkingMemory, coalition_id: UUID):
        self._working_memory = working_memory
        self._coalition_id = coalition_id

    @property
    def working_memory(self):
        return self._working_memory

    @property
    def coalition_id(self):
        return self._coalition_id
        
class CohdaNegotiationStarterRole(ProactiveRole):

    def __init__(self, target_schedule) -> None:
        self._target_schedule = target_schedule

    def setup(self):
        super().setup()

        self.context.schedule_task(InstantScheduledTask(self.start()))

    async def start(self):
        coalition_model = self.context.get_or_create_model(CoalitionModel)

        # Assume there is a exactly one coalition
        first_assignment = list(coalition_model.assignments.values())[0]
        for neighbor in first_assignment.neighbors:
            await self.context.send_message(
                content=CohdaMessage(WorkingMemory(self._target_schedule, {}, SolutionCandidate(first_assignment.part_id, {})), first_assignment.coalition_id), 
                receiver_addr=neighbor[1], 
                receiver_id=neighbor[2],
                acl_metadata={'sender_addr': self.context.addr, 'sender_id': self.context.aid},
                create_acl=True)


class COHDA:

    def __init__(self, weights, schedule_provider, is_local_acceptable, part_id):
        self._schedule_provider = schedule_provider
        self._weights = weights
        self._is_local_acceptable = is_local_acceptable
        self._memory = WorkingMemory(None, {}, SolutionCandidate(part_id, {}))
        self._counter = 0
        self._part_id = part_id

    def decide(self, content: CohdaMessage) -> Tuple[WorkingMemory, WorkingMemory]:
        memory = self._memory
        selection_counter = self._counter

        if memory.target_schedule is None:
            memory.target_schedule = content.working_memory.target_schedule

        message_solution_candidate = content.working_memory.solution_candidate
        our_solution_candidate = memory.solution_candidate

        old_working_memory = copy.deepcopy(memory)

        if self._part_id not in memory.system_config:
            memory.system_config[self._part_id] = ScheduleSelection(None, self._counter)
            
        own_schedule_selection_wm = memory.system_config[self._part_id]

        known_part_ids = set(memory.system_config.keys())
        given_part_ids = set(content.working_memory.system_config.keys())

        for agent_id, their_selection in content.working_memory.system_config.items():
            if agent_id in memory.system_config:
                our_selection = memory.system_config[agent_id]
                if their_selection.counter > our_selection.counter:
                    memory.system_config[agent_id] = their_selection
            else:
                memory.system_config[agent_id] = their_selection

        objective_our_candidate = self.objective_function(our_solution_candidate.candidate, memory.target_schedule)

        if known_part_ids.issubset(given_part_ids):
            our_solution_candidate = message_solution_candidate
        elif len(given_part_ids.union(known_part_ids)) > len(known_part_ids):
            missing_ids = given_part_ids.difference(known_part_ids)
            for missing_id in missing_ids:
                our_solution_candidate.candidate[missing_id] = message_solution_candidate.candidate[missing_id]
        else:
            objective_message_candidate = self.objective_function(message_solution_candidate.candidate, memory.target_schedule)
            if objective_message_candidate > objective_our_candidate:
                our_solution_candidate = message_solution_candidate
            elif objective_message_candidate == objective_our_candidate and message_solution_candidate.agent_id > our_solution_candidate.agent_id:
                our_solution_candidate = message_solution_candidate

        possible_schedules = self._schedule_provider()
        our_selected_schedule_in_solution = our_solution_candidate.candidate[self._part_id] if self._part_id in our_solution_candidate.candidate else None
        found_new = False
        for schedule in possible_schedules:
            our_solution_candidate.candidate[self._part_id] = schedule
            objective_tryout_candidate = self.objective_function(our_solution_candidate.candidate, memory.target_schedule)
            if objective_tryout_candidate > objective_our_candidate and self._is_local_acceptable(schedule):
                our_selected_schedule_in_solution = schedule
                objective_our_candidate = objective_tryout_candidate
                found_new = True

        if not found_new:
            our_solution_candidate.candidate[self._part_id] = our_selected_schedule_in_solution

        if not found_new and our_selected_schedule_in_solution != own_schedule_selection_wm.schedule:
            our_selected_schedule_in_solution = own_schedule_selection_wm.schedule
            found_new = True

        if found_new:
            memory.system_config[self._part_id] = ScheduleSelection(our_selected_schedule_in_solution, selection_counter + 1)
            memory.solution_candidate = our_solution_candidate
            our_solution_candidate.agent_id = self._part_id
            our_solution_candidate.candidate[self._part_id] = our_selected_schedule_in_solution 
            self._counter += 1
        return (old_working_memory, memory)


    def objective_function(self, candidate, target_schedule):
        # Return the negative(!) sum of all deviations, because bigger scores
        # mean better plans (e.g., -1 is better then -10).
        # print('objective_function: ')
        cluster_schedule = np.array(list(map(lambda item: item[1], candidate.items())))
        sum_cs = cluster_schedule.sum(axis=0)  # sum for each interval
        diff = np.abs(target_schedule - sum_cs)  # deviation to the target schedeule
        w_diff = diff * self._weights  # multiply with weight vector
        result = -np.sum(w_diff)
        return result


class COHDARole(SimpleReactiveRole):

    def __init__(self, schedules_provider, weights, local_acceptable_func):
        self._schedules_provider = schedules_provider
        self._is_local_acceptable = local_acceptable_func
        self._weights = weights
        self._cohda = {}

    def create_cohda(self, part_id):
        return COHDA(self._weights, self._schedules_provider, self._is_local_acceptable, part_id)

    def handle_msg(self, content: CohdaMessage, meta: Dict[str, Any]):
        if not self.context.get_or_create_model(CoalitionModel).exists(content.coalition_id):
            return

        assignment = self.context.get_or_create_model(CoalitionModel).by_id(content.coalition_id)

        if content.coalition_id not in self._cohda:
            self._cohda[content.coalition_id] = self.create_cohda(assignment.part_id)

        (old, new) = self._cohda[content.coalition_id].decide(content)

        if old != new:
            for neighbor in assignment.neighbors:
                asyncio.create_task(self.context.send_message(
                    content=CohdaMessage(new, assignment.coalition_id), receiver_addr=neighbor[1], receiver_id=neighbor[2],
                    acl_metadata={'sender_addr': self.context.addr, 'sender_id': self.context.aid},
                    create_acl=True))

    def is_applicable(self, content, meta):
        return type(content) == CohdaMessage 