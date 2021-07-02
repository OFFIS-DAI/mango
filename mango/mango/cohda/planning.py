"""Module for distributed real power planning with COHDA. Contains roles, which
integrate COHDA in the negotiation system and the core COHDA-decider together with its model.
"""
from typing import Dict, Any, Tuple
import copy
import numpy as np

from mango.cohda.negotiation import NegotiationParticipant, NegotiationStarterRole, Negotiation
from mango.cohda.coalition import CoalitionAssignment

class SolutionCandidate:
    """Model for a solution candidate in COHDA.
    """
    def __init__(self, agent_id: int, candidate: Dict[int, np.array]) -> None:
        self._agent_id = agent_id
        self._candidate = candidate

    @property
    def agent_id(self) -> str:
        """Return the agent id

        :return: agent id
        """
        return self._agent_id

    @agent_id.setter
    def agent_id(self, new_id: str):
        """Set the agent id

        :param new_id: agent id
        """
        self._agent_id = new_id

    @property
    def candidate(self) -> Dict[int, np.array]:
        """Return the candidate schedule map (part_id -> schedule)

        :return: map part_id -> schedule
        """
        return self._candidate

    def __eq__(self, o: object) -> bool:
        return isinstance(o, SolutionCandidate) and self._agent_id == o.agent_id \
            and self.candidate == o.candidate


class ScheduleSelection:
    """A selection of a specific schedule
    """
    def __init__(self, schedule: np.array, counter: int) -> None:
        self._schedule = schedule
        self._counter = counter

    @property
    def counter(self) -> int:
        """The counter of the selection

        :return: the counter
        """
        return self._counter

    @property
    def schedule(self) -> np.array:
        """The schedule as np.array

        :return: scheduke
        """
        return self._schedule

    def __eq__(self, o: object) -> bool:
        return isinstance(o, ScheduleSelection) and self.counter == o.counter \
            and self.schedule == o.schedule

class WorkingMemory:
    """Working memory of a COHDA agent
    """
    def __init__(self, target_schedule, system_config: Dict[str, ScheduleSelection],
                 solution_candidate: SolutionCandidate):
        self._target_schedule = target_schedule
        self._system_config = system_config
        self._solution_candidate = solution_candidate

    @property
    def target_schedule(self):
        """Return the target schedule

        :return: the target schedule
        """
        return self._target_schedule

    @target_schedule.setter
    def target_schedule(self, new_target):
        """Set the target schedule

        :param new_target: new target schedule
        """
        self._target_schedule = new_target

    @property
    def system_config(self) -> Dict[str, ScheduleSelection]:
        """Return the system config as map part_id -> selection

        :return: the believed system state
        """
        return self._system_config

    @property
    def solution_candidate(self) -> SolutionCandidate:
        """The current best known solution candidate for the planning

        :return: the solution candidate
        """
        return self._solution_candidate

    @solution_candidate.setter
    def solution_candidate(self, new_solution_candidate: SolutionCandidate):
        """Set the solution candidate

        :param new_solution_candidate: new solution candidate
        """
        self._solution_candidate = new_solution_candidate

    def __eq__(self, o: object) -> bool:
        return isinstance(o, WorkingMemory) and self.solution_candidate == o.solution_candidate \
            and self.system_config == o.system_config and self.target_schedule == o.target_schedule

class CohdaMessage:
    """Message for a COHDa negotiation. Contains the current working memory of an agent.
    """

    def __init__(self, working_memory: WorkingMemory):
        self._working_memory = working_memory

    @property
    def working_memory(self) -> WorkingMemory:
        """Return the working memory of the sender agent

        :return: the working memory of the sender
        """
        return self._working_memory

class CohdaNegotiationStarterRole(NegotiationStarterRole):
    """Convienience role for starting a COHDA negotiation with simply providing a target schedule
    """

    def __init__(self, target_schedule) -> None:
        super().__init__(lambda assignment: CohdaMessage(WorkingMemory(target_schedule, {},
            SolutionCandidate(assignment.part_id, {}))))

class COHDA:
    """COHDA-decider
    """

    def __init__(self, weights, schedule_provider, is_local_acceptable, part_id):
        self._schedule_provider = schedule_provider
        self._weights = weights
        self._is_local_acceptable = is_local_acceptable
        self._memory = WorkingMemory(None, {}, SolutionCandidate(part_id, {}))
        self._counter = 0
        self._part_id = part_id

    def decide(self, content: CohdaMessage) -> Tuple[WorkingMemory, WorkingMemory]:
        """Execute the COHDA decision process.

        :param content: the incoming COHDA message

        :return: old and new working memory
        """
        memory = self._memory
        selection_counter = self._counter

        if memory.target_schedule is None:
            memory.target_schedule = content.working_memory.target_schedule

        old_working_memory = copy.deepcopy(memory)

        if self._part_id not in memory.system_config:
            memory.system_config[self._part_id] = ScheduleSelection(None, self._counter)

        own_schedule_selection_wm = memory.system_config[self._part_id]

        our_solution_cand, objective_our_candidate = self._evalute_message(content, memory)

        possible_schedules = self._schedule_provider()
        our_selected_schedule = our_solution_cand.candidate[self._part_id] \
            if self._part_id in our_solution_cand.candidate else None
        found_new = False
        for schedule in possible_schedules:
            our_solution_cand.candidate[self._part_id] = schedule
            objective_tryout_candidate = self.objective_function(our_solution_cand.candidate,
                                                                 memory.target_schedule)
            if objective_tryout_candidate > objective_our_candidate \
               and self._is_local_acceptable(schedule):
                our_selected_schedule = schedule
                objective_our_candidate = objective_tryout_candidate
                found_new = True

        if not found_new:
            our_solution_cand.candidate[self._part_id] = our_selected_schedule

        if not found_new and our_selected_schedule != own_schedule_selection_wm.schedule:
            our_selected_schedule = own_schedule_selection_wm.schedule
            found_new = True

        if found_new:
            memory.system_config[self._part_id] = ScheduleSelection(our_selected_schedule,
                                                                    selection_counter + 1)
            memory.solution_candidate = our_solution_cand
            our_solution_cand.agent_id = self._part_id
            our_solution_cand.candidate[self._part_id] = our_selected_schedule
            self._counter += 1
        return (old_working_memory, memory)

    def _evalute_message(self,
                        content: CohdaMessage,
                        memory: WorkingMemory):
        """Evalute the incoming message and update our candidate accordingly.

        :param content: the incoming message
        :param memory: our memory

        :return: our new solution candidate and its objective
        """

        msg_solution_cand = content.working_memory.solution_candidate
        our_solution_cand = memory.solution_candidate
        known_part_ids = set(memory.system_config.keys())
        given_part_ids = set(content.working_memory.system_config.keys())

        for agent_id, their_selection in content.working_memory.system_config.items():
            if agent_id in memory.system_config:
                our_selection = memory.system_config[agent_id]
                if their_selection.counter > our_selection.counter:
                    memory.system_config[agent_id] = their_selection
            else:
                memory.system_config[agent_id] = their_selection

        objective_our_candidate = self.objective_function(our_solution_cand.candidate,
                                                          memory.target_schedule)

        if known_part_ids.issubset(given_part_ids):
            our_solution_cand = msg_solution_cand
        elif len(given_part_ids.union(known_part_ids)) > len(known_part_ids):
            missing_ids = given_part_ids.difference(known_part_ids)
            for missing_id in missing_ids:
                our_solution_cand.candidate[missing_id] = msg_solution_cand.candidate[missing_id]
        else:
            objective_message_candidate = self.objective_function(msg_solution_cand.candidate,
                                                                  memory.target_schedule)
            if objective_message_candidate > objective_our_candidate:
                our_solution_cand = msg_solution_cand
            elif objective_message_candidate == objective_our_candidate \
                and msg_solution_cand.agent_id > our_solution_cand.agent_id:
                our_solution_cand = msg_solution_cand

        return our_solution_cand, objective_our_candidate


    def objective_function(self, candidate, target_schedule):
        """Objective function of COHDA. Calculates the negative sum of all deviations of
        the candidate to the target schedule

        :param candidate: candidate schedule
        :param target_schedule: target schedule

        :return: negative sum of all deviations
        """
        # Return the negative(!) sum of all deviations, because bigger scores
        # mean better plans (e.g., -1 is better then -10).
        # print('objective_function: ')
        cluster_schedule = np.array(list(map(lambda item: item[1], candidate.items())))
        sum_cs = cluster_schedule.sum(axis=0)  # sum for each interval
        diff = np.abs(target_schedule - sum_cs)  # deviation to the target schedeule
        w_diff = diff * self._weights  # multiply with weight vector
        result = -np.sum(w_diff)
        return result


class COHDARole(NegotiationParticipant):
    """Negotiation role for COHDA.
    """

    def __init__(self, schedules_provider, weights, local_acceptable_func):
        super().__init__()

        self._schedules_provider = schedules_provider
        self._is_local_acceptable = local_acceptable_func
        self._weights = weights
        self._cohda = {}

    def create_cohda(self, part_id: int):
        """Create an instance of the COHDA-decider.

        :param part_id: participant id

        :return: COHDA
        """
        return COHDA(self._weights, self._schedules_provider, self._is_local_acceptable, part_id)

    def handle(self,
               message,
               assignment: CoalitionAssignment,
               negotiation: Negotiation,
               meta: Dict[str, Any]):

        if negotiation.coalition_id not in self._cohda:
            self._cohda[negotiation.coalition_id] = self.create_cohda(assignment.part_id)

        (old, new) = self._cohda[negotiation.coalition_id].decide(message)

        if old != new:
            self.send_to_neighbors(assignment, negotiation, CohdaMessage(new))

            # set agent as idle
            if self.context.inbox_length() == 0:
                negotiation.active = False
