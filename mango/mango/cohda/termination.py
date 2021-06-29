from mango.cohda.coalition import CoalitionModel
from ..role.role import *
from typing import *

import asyncio

class TerminationMessage:
    def __init__(self, weight: int, coalition_id: int, negotiation_id: int) -> None:
        self._weight = weight
        self._coalition_id = coalition_id
        self._negotiation_id = negotiation_id
        
    @property
    def weight(self):
        return self._weight

    @property
    def coalition_id(self):
        return self._negotiation_id

    @property
    def negotiation_id(self):
        return self._coalition_id

class TerminationControllerRole(Role):

    def __init__(self):
        self._weight = 1

    def setup(self):
        # subscriptions
        self.context.subscribe_message(self, self.handle_term_msg, lambda c, m: type(c) == TerminationMessage)

    def handle_term_msg(self, content, meta: Dict[str, Any]) -> None:
        self._weight += content.weight

class NegotiationTerminationRole(SimpleReactiveRole):

    def __init__(self) -> None:
        super().__init__()
        self._weight_map = {}

    def setup(self):
        super().setup()

        self.context.subscribe_send(self, self.on_send)

    def on_send(self, content,
                receiver_addr: Union[str, Tuple[str, int]], *,
                receiver_id: Optional[str] = None,
                create_acl: bool = False,
                acl_metadata: Optional[Dict[str, Any]] = None,
                mqtt_kwargs: Dict[str, Any] = None):
        if content.coalition_id is not None:
            content.message_weight = self._weight_map[content.coalition_id] / 2
            self._weight_map[content.coalition_id] /= 2

    def handle_msg(self, content, meta: Dict[str, Any]) -> None:
        if content.coalition_id in self._weight_map:
            if content.message_weight is not None:
                self._weight_map[content.coalition_id] += content.message_weight

            coalition_model = self.context.get_or_create_model(CoalitionModel)
            if coalition_model is not None:
                self._check_weight(coalition_model, content)
            

    def _check_weight(self, coalition_model, content):
        if self.context.inbox_length == 0 and coalition_model.by_id(content.coalition_id).active:
            # Reset weight
            self._weight_map[content.coalition_id] = 0
            asyncio.create_task(self.context.send_message(
                content=TerminationMessage(self._weight_map[content.coalition_id]), receiver_addr=content.controller_agent_addr, receiver_id=content.controller_agent_id,
                acl_metadata={'sender_addr': self.context.addr, 'sender_id': self.context.aid},
                create_acl=True))