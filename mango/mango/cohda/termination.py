from ..role.role import SimpleReactiveRole
from typing import *

import asyncio

class TerminationMessage:
    def __init__(self, weight: int) -> None:
        self._weight = weight
        

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

            if self.context.inbox_length == 0:
                # Rest weight to
                asyncio.create_task( self.context.send_message(
                    content=TerminationMessage(self._weight_map[content.coalition_id]), receiver_addr=content.controller_agent_addr, receiver_id=content.controller_agent_id,
                    acl_metadata={'sender_addr': self.context.addr, 'sender_id': self.context.aid},
                    create_acl=True))
