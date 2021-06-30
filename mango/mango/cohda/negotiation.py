import uuid
import asyncio

from abc import ABC, abstractmethod
from ..role.api import ProactiveRole, SimpleReactiveRole
from ..util.scheduling import InstantScheduledTask
from mango.cohda.coalition import CoalitionAssignment, CoalitionModel
from typing import *

class Negotiation:

    def __init__(self, coalition_id: uuid.UUID, negotiation_id: int, active: bool = True) -> None:
        self._negotiation_id = negotiation_id
        self._coalition_id = coalition_id
        self._active = active

    @property
    def negotiation_id(self):
        return self._negotiation_id
        
    @property
    def coalition_id(self):
        return self._coalition_id

class NegotiationModel:

    def __init__(self, negotiations: Dict[uuid.UUID, Negotiation] = {}) -> None:
        self._negotiations = negotiations

    def by_id(self, negotiation_id: uuid.UUID) -> Negotiation:
        return self._negotiations[negotiation_id]

    def exists(self, negotiation_id: uuid.UUID) -> bool:
        return negotiation_id in self._negotiations

    def add(self, id: uuid.UUID, assignment: Negotiation):
        self._negotiations[id] = assignment


class NegotiationMessage:

    def __init__(self, coalition_id: uuid.UUID, negotiation_id: int, message) -> None:
        self._negotiation_id = negotiation_id
        self._coalition_id = coalition_id
        self._message = message

    @property
    def negotiation_id(self):
        return self._negotiation_id

    @property
    def coalition_id(self):
        return self._coalition_id

    @property
    def messsage(self):
        return self._message

class NegotiationStarterRole(ProactiveRole):

    def __init__(self, message_creator) -> None:
        self._message_creator = message_creator

    def setup(self):
        super().setup()

        self.context.schedule_task(InstantScheduledTask(self.start()))

    async def start(self):
        coalition_model = self.context.get_or_create_model(CoalitionModel)

        # Assume there is a exactly one coalition
        first_assignment = list(coalition_model.assignments.values())[0]
        for neighbor in first_assignment.neighbors:
            await self.context.send_message(
                content=NegotiationMessage(first_assignment.coalition_id, uuid.uuid1(), self._message_creator(first_assignment)), 
                receiver_addr=neighbor[1], 
                receiver_id=neighbor[2],
                acl_metadata={'sender_addr': self.context.addr, 'sender_id': self.context.aid},
                create_acl=True)


class NegotiationParticipant(SimpleReactiveRole, ABC):

    def __init__(self, schedules_provider, weights, local_acceptable_func):
        self._schedules_provider = schedules_provider
        self._is_local_acceptable = local_acceptable_func
        self._weights = weights
        self._cohda = {}

    def handle_msg(self, content: NegotiationMessage, meta: Dict[str, Any]):
        if not self.context.get_or_create_model(CoalitionModel).exists(content.coalition_id):
            return

        assignment = self.context.get_or_create_model(CoalitionModel).by_id(content.coalition_id)
        negotiation_model = self.context.get_or_create_model(NegotiationModel)

        if not negotiation_model.exists(content.negotiation_id):
            negotiation_model.add(content.negotiation_id, Negotiation(content.coalition_id, content.negotiation_id))

        self.handle(content.messsage, assignment, negotiation_model.by_id(content.negotiation_id), meta)

    @abstractmethod    
    def handle(self, message, assignment, negotiation, meta):
        pass

    def send_to_neighbors(self, assignment: CoalitionAssignment, negotation: NegotiationMessage, message):
        for neighbor in assignment.neighbors:
            self.send(negotation, message, neighbor)

    def send(self, negotation: Negotiation, message, neighbor) -> None:
        asyncio.create_task(self.context.send_message(
            content=NegotiationMessage(negotation.coalition_id, negotation.negotiation_id, message), receiver_addr=neighbor[1], receiver_id=neighbor[2],
            acl_metadata={'sender_addr': self.context.addr, 'sender_id': self.context.aid},
            create_acl=True))

    def is_applicable(self, content, meta):
        return type(content) == NegotiationMessage 
