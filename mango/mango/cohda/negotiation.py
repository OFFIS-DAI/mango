"""This module implements some reacurring elements of neogtiation processes. Therfore a negotiation
wrapper for some meta data is provided. Furthermore this module implements roles which can handle
this meta data models and store them as central role data.

Role-Models:
* :class:`NegotiationModel`: Stores information for all currently known negotiations

Messages:
* :class:`NeogtationMessage`: Wrapper message for negotiation orientied content

Roles:
* :class:`NegotiationStarterRole`: Starts a negotiation
* :class:`NegotiationParticipant`: (abstract) Participates in a negotiation, stores the meta data
                                   and wraps the real message in the negotiation wrapper message
"""
import uuid
import asyncio
from typing import Dict, Any
from abc import ABC, abstractmethod

from mango.role.api import ProactiveRole, SimpleReactiveRole
from mango.util.scheduling import ConditionalTask
from mango.cohda.coalition import CoalitionAssignment, CoalitionModel


class Negotiation:
    """Modell for storing the data regarding a concrete negotiation
    """

    def __init__(self, coalition_id: uuid.UUID, negotiation_id: uuid.UUID,
                 active: bool = True) -> None:
        self._negotiation_id = negotiation_id
        self._coalition_id = coalition_id
        self._active = active

    @property
    def negotiation_id(self) -> uuid.UUID:
        """Return the negotiation id

        Returns:
            [uuid.UUID]: the UUID
        """
        return self._negotiation_id

    @property
    def coalition_id(self) -> uuid.UUID:
        """Return the coalition id

        Returns:
            [uuid.UUID]: the UUID
        """
        return self._coalition_id

    @property
    def active(self) -> bool:
        """Is seen as active

        Returns:
            bool: if active
        """
        return self._active

    @active.setter
    def active(self, is_active) -> None:
        """Set is active

        Args:
            is_active (bool): active
        """
        self._active = is_active


class NegotiationModel:
    """Model for storing all metadata regarding negotiations
    """

    def __init__(self) -> None:
        self._negotiations = {}

    def by_id(self, negotiation_id: uuid.UUID) -> Negotiation:
        """Get a negotiation by id

        Args:
            negotiation_id (uuid.UUID): id of the negotiation

        Returns:
            Negotiation: the negotiation
        """
        return self._negotiations[negotiation_id]

    def exists(self, negotiation_id: uuid.UUID) -> bool:
        """Checks whether a negotiation exists

        Args:
            negotiation_id (uuid.UUID): id of the negotiation

        Returns:
            bool: True if it exists, False otherwise
        """
        return negotiation_id in self._negotiations

    def add(self, negotiation_id: uuid.UUID, assignment: Negotiation):
        """Add a concrete negotiation

        Args:
            negotiation_id (uuid.UUID): the UUID of the negotiation
            assignment (Negotiation): the assignment for the negotiation
        """
        self._negotiations[negotiation_id] = assignment


class NegotiationMessage:
    """Message wrapper for negotiation messages.
    """

    def __init__(self, coalition_id: uuid.UUID, negotiation_id: uuid.UUID, message) -> None:
        self._negotiation_id = negotiation_id
        self._coalition_id = coalition_id
        self._message = message

    @property
    def negotiation_id(self) -> uuid.UUID:
        """Id of the negotiation

        Returns:
            uuid.UUID: the id
        """
        return self._negotiation_id

    @property
    def coalition_id(self) -> uuid.UUID:
        """Id of the coalition this negotiation belongs to

        Returns:
            [uuid.UUID]: UUID
        """
        return self._coalition_id

    @property
    def messsage(self):
        """Return the wrapped message

        Returns:
            [type]: wrapped message
        """
        return self._message


class NegotiationStarterRole(ProactiveRole):
    """Starting role for a negotiation. Will use a specific negotiation message creator to start
    a negotiation within its coalition.
    """

    def __init__(self, message_creator) -> None:
        super().__init__()
        self._message_creator = message_creator

    def setup(self):
        super().setup()

        self.context.schedule_task(ConditionalTask(self.start(), self.is_startable))

    def is_startable(self):
        coalition_model = self.context.get_or_create_model(CoalitionModel)

        # check there is an assignment
        return len(coalition_model.assignments.values()) > 0

    async def start(self):
        """Start a negotiation. Send all neighbors a starting negotiation message.
        """
        coalition_model = self.context.get_or_create_model(CoalitionModel)

        # Assume there is a exactly one coalition
        first_assignment = list(coalition_model.assignments.values())[0]
        negotiation_uuid = uuid.uuid1()
        for neighbor in first_assignment.neighbors:
            await self.context.send_message(
                content=NegotiationMessage(first_assignment.coalition_id, negotiation_uuid, self._message_creator(first_assignment)),
                receiver_addr=neighbor[1],
                receiver_id=neighbor[2],
                acl_metadata={'sender_addr': self.context.addr,
                              'sender_id': self.context.aid},
                create_acl=True)


class NegotiationParticipant(SimpleReactiveRole, ABC):
    """Abstract role for participating a negotiation. Handles the wrapper message and the internal
    agent model about the meta data of the negotiation.
    """

    def __init__(self):
        super().__init__()

    def handle_msg(self, content: NegotiationMessage, meta: Dict[str, Any]):
        """Handles any NegotiationMessages, updating the internal model of the agent.

        Args:
            content (NegotiationMessage): the message
            meta (Dict[str, Any]): meta
        """
        if not self.context.get_or_create_model(CoalitionModel).exists(content.coalition_id):
            return

        assignment = self.context.get_or_create_model(
            CoalitionModel).by_id(content.coalition_id)
        negotiation_model = self.context.get_or_create_model(NegotiationModel)

        if not negotiation_model.exists(content.negotiation_id):
            negotiation_model.add(content.negotiation_id, Negotiation(
                content.coalition_id, content.negotiation_id))

        self.handle(content.messsage, assignment,
                    negotiation_model.by_id(content.negotiation_id), meta)

    @abstractmethod
    def handle(self, message, assignment: CoalitionAssignment, negotiation: Negotiation, meta: Dict[str, Any]):
        """Handle the message and execute the specific negotiation step.

        Args:
            message ([type]): the message
            assignment ([type]): the assignment the negotiations is in
            negotiation ([Negotiation]): the negotiation model
            meta ([Dict[str, Any]]): meta data
        """

    def send_to_neighbors(self, assignment: CoalitionAssignment, negotation: Negotiation, message):
        """Send a message to all neighbors

        Args:
            assignment (CoalitionAssignment): the coalition you want to use the neighbors of
            negotation (Negotiation): the negotiation message
            message ([type]): the message you want to send
        """
        for neighbor in assignment.neighbors:
            self.send(negotation, message, neighbor)

    def send(self, negotation: Negotiation, message, neighbor) -> None:
        """Send a negotiation message to the specified neighbor

        Args:
            negotation (Negotiation): the negotiation
            message ([type]): the content you want to send
            neighbor ([type]): the neighbor
        """
        asyncio.create_task(self.context.send_message(
            content=NegotiationMessage(negotation.coalition_id, negotation.negotiation_id, message),
            receiver_addr=neighbor[1], receiver_id=neighbor[2],
            acl_metadata={'sender_addr': self.context.addr,
                          'sender_id': self.context.aid},
            create_acl=True))

    def is_applicable(self, content, meta):
        return isinstance(content, NegotiationMessage)
