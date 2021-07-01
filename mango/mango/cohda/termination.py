"""Module, which implements a simple termination detection for negotiations. Here Huangs
detection algorithm is used (10.1109/ICDCS.1989.37933).

It requires the distributed negotiation to have some kind of controller agent. In general
this can often be the initiator.

Roles:
* :class:`TerminationControllerRole`: role for the controller agent, handles the rest weight
                                      messages
* :class:`NegotiationTerminationRole`: role for the participants, hooks into sending messages,
                                       adding the weight value

Messages:
* :class:`TerminationMessage`: this message will be send to the controller, when an agent
considers itself as inactive.
"""
import asyncio
from typing import Dict, Any, Union, Tuple, Optional
from uuid import UUID

from mango.cohda.coalition import CoalitionModel
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

        Returns:
            [float]: remaining weight
        """
        return self._weight

    @property
    def coalition_id(self) -> UUID:
        """Return the coalition id the negotiation is referring to

        Returns:
            [UUID]: the coalition id
        """
        return self._negotiation_id

    @property
    def negotiation_id(self) -> UUID:
        """Return the negotiation id

        Returns:
            UUID: the negotiation id
        """
        return self._coalition_id

class TerminationControllerRole(Role):
    """Role for the controller agent. Will handle the TerminationMessage and announce the
    termination.
    """

    def __init__(self):
        super().__init__()
        self._weight = 1

    def setup(self):
        # subscriptions
        self.context.subscribe_message(self, self.handle_term_msg,
                                            lambda c, _: isinstance(c, TerminationMessage))

    def handle_term_msg(self, content: TerminationMessage, _: Dict[str, Any]) -> None:
        """Handle the termination message.

        Args:
            content ([TerminationMessage]): the message
            meta (Dict[str, Any]): meta data
        """
        self._weight += content.weight

class NegotiationTerminationRole(SimpleReactiveRole):
    """Role for negotiation participants. Will add the weight attribute to every
    coalition related message send.
    """

    def __init__(self) -> None:
        super().__init__()
        self._weight_map = {}

    def setup(self):
        super().setup()

        self.context.subscribe_send(self, self.on_send)

    def on_send(self, content,
                _: Union[str, Tuple[str, int]], *,
                __: Optional[str] = None,
                ___: bool = False,
                ____: Optional[Dict[str, Any]] = None,
                _____: Dict[str, Any] = None):
        """Add the weight to every coalition related message

        Args:
            content ([type]): [description]
            _ (Union[str, Tuple[str, int]]): [description]
            __ (Optional[str], optional): [description]. Defaults to None.
            ___ (bool, optional): [description]. Defaults to False.
            ____ (Optional[Dict[str, Any]], optional): [description]. Defaults to None.
            _____ (Dict[str, Any], optional): [description]. Defaults to None.
        """
        if content.negotiation_id is not None:
            content.message_weight = self._weight_map[content.negotiation_id] / 2
            self._weight_map[content.coalition_id] /= 2

    def handle_msg(self, content, _: Dict[str, Any]) -> None:
        """Check whether a coalition related message has been received and manipulate the internal
        weight accordingly

        Args:
            content ([type]): the incoming message
            meta (Dict[str, Any]): the meta data
        """
        if hasattr(content, 'coalition_id') \
            and hasattr(content, 'negotiation_id') and content.negotiation_id in self._weight_map:
            if content.message_weight is not None:
                self._weight_map[content.negotiation_id] += content.message_weight

            coalition_model = self.context.get_or_create_model(CoalitionModel)
            if coalition_model is not None:
                self._check_weight(coalition_model, content)


    def _check_weight(self, coalition_model, content):
        if self.context.inbox_length == 0 and coalition_model.by_id(content.negotiation_id).active:
            # Reset weight
            self._weight_map[content.negotiation_id] = 0
            asyncio.create_task(self.context.send_message(
                content=TerminationMessage(self._weight_map[content.negotiation_id],
                                           content.coalition_id,
                                           content.negotiation_id),
                receiver_addr=content.controller_agent_addr,
                receiver_id=content.controller_agent_id,
                acl_metadata={'sender_addr': self.context.addr, 'sender_id': self.context.aid},
                create_acl=True))
