from mango.cohda.coalition import CoalitionModel
from ..role.role import ProactiveRole, ReactiveRole, SimpleReactiveRole
from typing import Dict, Any, List
import copy
from uuid import UUID
from ..util.scheduling import InstantScheduledTask
import numpy as np
import asyncio

class SolutionCandidate:
    def __init__(self, agent_id: str, candidate: Dict[int, np.array]) -> None:
        self._agent_id = agent_id
        self._candidate = candidate
    
    @property
    def agent_id(self):
        return self._agent_id

    @property
    def candidate(self):
        return self._candidate


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

class CohdaMessage:

    def __init__(self, working_memory: WorkingMemory, coalition_id: UUID):
        self.working_memory = working_memory
        self.coalition_id = coalition_id

class CohdaNegotiationStarterRole(ProactiveRole):

    def __init__(self, target_schedule) -> None:
        self._target_schedule

    def setup(self):
        super().setup()

        self.context.schedule_task(InstantScheduledTask(self.start()))

    async def start(self):
        coalition_model = self.context.get_or_create_model(CoalitionModel)

        # Assume there is a exactly one coalition
        for neighbor in coalition_model.assignments[0].neighbors:
            await self.context.send_message(
                content=CohdaMessage(WorkingMemory(self._target_schedule, {}, []), coalition_model.assignments[0].coalition_id), receiver_addr=neighbor[1], receiver_id=neighbor[2],
                acl_metadata={'sender_addr': self.context.addr, 'sender_id': self.context.aid},
                create_acl=True)

class CohdaRole(SimpleReactiveRole):

    def __init__(self, schedules_provider, local_acceptable_func):
        self._schedules_provider = schedules_provider
        self._is_local_acceptable = local_acceptable_func
        self._selection_counter_map = {}
        self._working_memory_map = {}

    def handle_msg(self, content: CohdaMessage, meta: Dict[str, Any]):
        if not self.context.get_or_create_model(CoalitionModel).exists(content.coalition_id):
            return

        assignment = self.context.get_or_create_model(CoalitionModel).by_id(content.coalition_id)
        memory = self._working_memory_map[assignment.coalition_id]
        selection_counter = self._selection_counter_map[assignment.coalition_id]

        if memory.target_schedule is None:
            memory.target_schedule = content.working_memory.target_schedule

        message_system_config = content.working_memory.system_config
        message_solution_candidate = content.working_memory.solution_candidate
        our_solution_candidate = memory.solution_candidate

        old_working_memory = copy.deepcopy(memory)
        own_schedule_selection_wm = memory.system_config[assignment.part_id]

        known_part_ids = memory.system_config.keys()
        given_part_ids = content.working_memory.system_config.keys()

        for agent_id, their_selection in content.working_memory.system_config.iteritems():
            if agent_id in memory.system_config:
                our_selection = memory.system_config[agent_id]
                if their_selection.counter > our_selection.counter:
                    memory.system_config[agent_id] = their_selection

        objective_our_candidate = self.objective_function(our_solution_candidate.candidate, memory.target_schedule)

        if given_part_ids.issubset(known_part_ids):
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

        possible_schedules = self._schedules_provider()
        our_selected_schedule_in_solution = our_solution_candidate.candidate[assignment.part_id]
        found_new = False
        for schedule in possible_schedules:
            our_solution_candidate.candidate[assignment.part_id] = schedule
            objective_tryout_candidate = self.objective_function(our_solution_candidate.candidate, memory.target_schedule)
            if objective_tryout_candidate > objective_our_candidate and self._is_local_acceptable(schedule):
                our_selected_schedule_in_solution = schedule
                objective_our_candidate = objective_tryout_candidate
                found_new = True

        if not found_new and our_selected_schedule_in_solution != own_schedule_selection_wm:
            our_selected_schedule_in_solution = own_schedule_selection_wm
            found_new = True
        
        memory.system_config[assignment.part_id] = our_selected_schedule_in_solution
        if found_new:
            our_solution_candidate.agent_id = assignment.part_id
            our_solution_candidate.counter += 1

        if old_working_memory != memory:
            for neighbor in assignment.neighbors:
                asyncio.create_task(self.context.send_message(
                    content=CohdaMessage(memory, assignment.coalition_id), receiver_addr=neighbor[1], receiver_id=neighbor[2],
                    acl_metadata={'sender_addr': self.context.addr, 'sender_id': self.context.aid},
                    create_acl=True))

    def objective_function(self, cluster_schedule, target_schedule):
        # Return the negative(!) sum of all deviations, because bigger scores
        # mean better plans (e.g., -1 is better then -10).
        # print('objective_function: ')
        sum_cs = cluster_schedule.sum(axis=0)  # sum for each interval
        diff = np.abs(target_schedule - sum_cs)  # deviation to the target schedeule
        w_diff = diff * self.weights  # multiply with weight vector
        result = -np.sum(w_diff)
        return result

    def is_applicable(self, content, meta):
        return type(content) == CohdaMessage 