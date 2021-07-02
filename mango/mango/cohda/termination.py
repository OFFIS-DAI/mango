"""Module, which implements a simple termination detection for negotiations. Here Huangs
detection algorithm is used (10.1109/ICDCS.1989.37933).

It requires the distributed negotiation to have some kind of controller agent. In general
this can often be the initiator.

Roles:
* :class:`NegotiationTerminationRole`: role for the participants, hooks into sending messages,
                                       adding the weight value

Messages:
* :class:`TerminationMessage`: this message will be send to the controller, when an agent
considers itself as inactive.
"""
import asyncio
from typing import Dict, Any, Union, Tuple, Optional
from uuid import UUID

from mango.cohda.coalition import CoalitionModel, CoaltitionResponse
from mango.cohda.negotiation import NegotiationMessage, NegotiationModel
from mango.cohda.planning import CohdaMessage
from mango.role.api import Role, SimpleReactiveRole


class TerminationMessage:
    """Message for sending the remaining weight to the controller
    """
    def __init__(self, weight: float, coalition_id: UUID, negotiation_id: UUID) -> None:
        self._weight = weight
        self._coalition_id = coalition_id
        self._negotiation_id = negotiation_id

    @property
    def weight(self) -> float:
        """Return the remaining weight

        :return: remaining weight
        """
        return self._weight

    @property
    def coalition_id(self) -> UUID:
        """Return the coalition id the negotiation is referring to

        :return: the coalition id
        """
        return self._coalition_id

    @property
    def negotiation_id(self) -> UUID:
        """Return the negotiation id

        :return: the negotiation id
        """
        return self._negotiation_id

class NegotiationTerminationRole(SimpleReactiveRole):
    """Role for negotiation participants. Will add the weight attribute to every
    coalition related message send.
    """

    def __init__(self, is_controller: bool = 0) -> None:
        super().__init__()
        self._weight_map = {}
        self._is_controller = is_controller

    def setup(self):
        super().setup()

        self.context.subscribe_send(self, self.on_send)
        self.context.subscribe_message(self, self.handle_term_msg,
                                            lambda c, _: isinstance(c, TerminationMessage))

    def handle_term_msg(self, content: TerminationMessage, _: Dict[str, Any]) -> None:
        """Handle the termination message.

        :param content: the message
        :param meta: meta data
        """
        self._weight_map[content.negotiation_id] += content.weight

    def on_send(self, content,
                receiver_addr: Union[str, Tuple[str, int]], *,
                receiver_id: Optional[str] = None,
                create_acl: bool = False,
                acl_metadata: Optional[Dict[str, Any]] = None,
                mqtt_kwargs: Dict[str, Any] = None):
        """Add the weight to every coalition related message

        :param content: content of the message
        :param receiver_addr: address
        :param receiver_id: id of the receiver. Defaults to None.
        :param create_acl: If you want to wrap the message in an ACL. Defaults to False.
        :param acl_metadata: ACL meta data. Defaults to None.
        :param mqtt_kwargs: Args for MQTT. Defaults to None.
        """
        if hasattr(content, 'negotiation_id'):
            if not content.negotiation_id in self._weight_map:
                self._weight_map[content.negotiation_id] = 1 if self._is_controller else 0
            content.message_weight = self._weight_map[content.negotiation_id] / 2
            self._weight_map[content.negotiation_id] /= 2

    def handle_msg(self, content, _: Dict[str, Any]) -> None:
        """Check whether a coalition related message has been received and manipulate the internal
        weight accordingly

        :param content: the incoming message
        :param meta: the meta data
        """
        if hasattr(content, 'negotiation_id'):
            if content.negotiation_id in self._weight_map:
                self._weight_map[content.negotiation_id] += content.message_weight
            else:
                self._weight_map[content.negotiation_id] = content.message_weight

            negotiation_model = self.context.get_or_create_model(NegotiationModel)
            if negotiation_model is not None:
                self._check_weight(negotiation_model, content)


    def _check_weight(self, negotiation_model: NegotiationModel, content):
        coalition = self.context.get_or_create_model(CoalitionModel).by_id(content.coalition_id)
        if self.context.inbox_length() == 0 and not negotiation_model.by_id(content.negotiation_id).active \
            and self._weight_map[content.negotiation_id] != 0:
            # Reset weight
            asyncio.create_task(self.context.send_message(
                content=TerminationMessage(self._weight_map[content.negotiation_id],
                                           content.coalition_id,
                                           content.negotiation_id),
                receiver_addr=coalition.controller_agent_addr,
                receiver_id=coalition.controller_agent_id,
                acl_metadata={'sender_addr': self.context.addr, 'sender_id': self.context.aid},
                create_acl=True))
            self._weight_map[content.negotiation_id] = 0
