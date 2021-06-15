from uuid import UUID
import asyncio, uuid
from mango.role.role import ProactiveRole, Role, RoleContext

from typing import Dict, Any, List
from mango.util.scheduling import InstantScheduledTask

class CoalitionAssignment:

    def __init__(self, 
                 coalition_id: UUID, 
                 neighbors: list((int, str, str)), 
                 topic: str, 
                 part_id: int,
                 controller_agent_id: str,
                 controller_agent_addr):
        self._coalition_id = coalition_id
        self._neighbors = neighbors
        self._topic = topic
        self._part_id = part_id
        self._controler_agent_id = controller_agent_id
        self._controler_agent_addr = controller_agent_addr

    @property
    def coalition_id(self):
        return self._coalition_id

    @property
    def neighbors(self):
        return self._neighbors

    @property
    def topic(self):
        return self._topic

    @property
    def part_id(self):
        return self._part_id

    @property
    def controller_agent_id(self):
        return self._controler_agent_id

    @property
    def controller_agent_addr(self):
        return self._controler_agent_addr


class CoalitionModel:

    def __init__(self) -> None:
        self._assignments = {}

    @property
    def assignments(self) -> Dict[UUID, CoalitionAssignment]:
        return self._assignments

    def add(self, id: UUID, assignment: CoalitionAssignment):
        self._assignments[id] = assignment

    def by_id(self, id: UUID) -> CoalitionAssignment:
        return self._assignments[id]

    def exists(self, id: UUID):
        return id in self._assignments


class CoalitionInvite:

    def __init__(self, id: UUID, topic: str, details = None):
        self._id = id
        self._topic = topic
        self._details = details

    @property
    def id(self):
        return self._id

    @property
    def topic(self):
        return self._topic

    @property
    def details(self):
        return self._details

class CoaltitionResponse:

    def __init__(self, accept: bool):
        self._accept = accept

    @property
    def accept(self):
        return self._accept


def clique_creator(participants: List):
    part_to_neighbors = {}
    for part in participants:
        part_to_neighbors[part] = list(filter(lambda p: p != part, participants)) 
    return part_to_neighbors

class CoalitionInitiatorRole(ProactiveRole):

    def __init__(self, participants: List, topic: str, details: str, topology_creator = clique_creator):
        self._participants = participants
        self._topic = topic
        self._details = details
        self._topology_creator = topology_creator
        self._part_to_state = {}
        self._assignments_sent = False

    def setup(self):
        # subscriptions
        self.context.subscribe_message(self, self.handle_msg, lambda c, m: type(c) == CoaltitionResponse)

        # tasks
        self.context.schedule_task(InstantScheduledTask(self.send_invitiations(self.context)))

    async def send_invitiations(self, agent_context : RoleContext):
        self._coal_id = uuid.uuid1()

        for participant in self._participants:
            await agent_context.send_message(
                content=CoalitionInvite(self._coal_id, self._topic), receiver_addr=participant[0], receiver_id=participant[1],
                acl_metadata={'sender_addr': agent_context.addr, 'sender_id': agent_context.aid},
                create_acl=True)

    def handle_msg(self, content : CoaltitionResponse, meta: Dict[str, Any]) -> None:
        self._part_to_state[(meta['sender_addr'], meta['sender_id'])] = content.accept

        if len(self._part_to_state) == len(self._participants) and not self._assignments_sent:
            self._send_assignments(self.context)
            self._assignments_sent = True
            
    def _send_assignments(self, agent_context: RoleContext):
        part_id = 0
        accepted_participants = []
        for part in self._participants:
            if part in self._part_to_state and self._part_to_state[part]:
                part_id += 1
                accepted_participants.append((part_id, part[0], part[1]))

        part_to_neighbors = self._topology_creator(accepted_participants)
        for part in accepted_participants:
            asyncio.create_task(agent_context.send_message(
                content=CoalitionAssignment(self._coal_id, part_to_neighbors[part], self._topic, part[0], agent_context.aid, agent_context.addr), receiver_addr=part[1], receiver_id=part[2],
                acl_metadata={'sender_addr': agent_context.addr, 'sender_id': agent_context.aid},
                create_acl=True))

class CoalitionParticipantRole(Role):

    def __init__(self):
        pass

    def setup(self) -> None:
        # subscriptions
        self.context.subscribe_message(self, self.handle_invite, lambda c, m: type(c) == CoalitionInvite)
        self.context.subscribe_message(self, self.handle_assignment, lambda c, m: type(c) == CoalitionAssignment)

    def handle_invite(self, content, meta: Dict[str, Any]) -> None:
        asyncio.create_task(self.context.send_message(
                content=CoaltitionResponse(True), receiver_addr=meta['sender_addr'], receiver_id=meta['sender_id'],
                acl_metadata={'sender_addr': self.context.addr, 'sender_id': self.context.aid},
                create_acl=True))

    def handle_assignment(self, content: CoalitionAssignment, meta: Dict[str, Any]) -> None:
        assignment = self.context.get_or_create_model(CoalitionModel)
        assignment.add(content.coalition_id, content)
        self.context.update(assignment)
