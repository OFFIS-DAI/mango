"""Simple Agent to show basic functionality"""

import asyncio
import logging

from mango import Agent
from mango.messages import other_proto_msgs_pb2 as other_proto_msg
from mango.messages.acl_message_pb2 import ACLMessage as ACLMessage_proto
from mango.messages.message import ACLMessage as ACLMessage_json
from mango.messages.message import Performatives

logger = logging.getLogger(__name__)


class SimpleAgent(Agent):
    def __init__(self, container, other_aid=None, other_addr=None, codec="json"):
        super().__init__(container)
        self.other_aid = other_aid
        self.other_addr = other_addr
        self.conversations = []
        self.codec = codec
        if isinstance(container.addr, (list, tuple)):
            self.addr_str = f"{container.addr[0]}:{container.addr[1]}"
        else:
            self.addr_str = container.addr

    async def send_greeting(self):
        # if we have the address of another agent we send a greeting
        if self.other_aid is not None:
            if self.codec == "json":
                message_content = "Hi there"
            elif self.codec == "protobuf":
                message_content = other_proto_msg.GenericMsg()
                message_content.text = "Hi there"
            acl_meta = {
                "reply_with": "greeting",
                "conversation_id": f"{self.aid}_1",
                "performative": Performatives.inform.value,
                "sender_id": self.aid,
            }
            await self.send_acl_message(
                message_content,
                self.other_addr,
                receiver_id=self.other_aid,
                acl_metadata=acl_meta,
            )

    def handle_message(self, content, meta):
        """
        decide which actions shall be performed as reaction to message
        :param content: the content of the mssage
        :param meta: meta information
        """
        logger.info(f"Received message: {content} with meta {meta}")

        # so far we only expect and react to greetings
        t = asyncio.create_task(self.react_to_greeting(content, meta))
        t.add_done_callback(self.raise_exceptions)

    async def react_to_greeting(self, msg_in, meta_in):
        conversation_id = meta_in["conversation_id"]
        reply_with = meta_in["reply_with"]
        sender_id = meta_in["sender_id"]
        sender_addr = meta_in["sender_addr"]
        if not reply_with:
            # No reply necessary - remove conversation id
            if conversation_id in self.conversations:
                self.conversations.remove(conversation_id)
        else:
            # prepare a reply
            if conversation_id not in self.conversations:
                self.conversations.append(conversation_id)
            if reply_with == "greeting":
                # greet back
                message_out_content = "Hi there too"
                reply_key = "greeting2"
            elif reply_with == "greeting2":
                # back greeting received, send good bye
                message_out_content = "Good Bye"
                # end conversation
                reply_key = None
                self.conversations.remove(conversation_id)
            else:
                assert False, f"got strange reply_with: {reply_with}"
            if self.codec == "json":
                message = ACLMessage_json(
                    sender_id=self.aid,
                    sender_addr=self.addr_str,
                    receiver_id=sender_id,
                    receiver_addr=sender_addr,
                    content=message_out_content,
                    in_reply_to=reply_with,
                    reply_with=reply_key,
                    conversation_id=conversation_id,
                    performative=Performatives.inform,
                )
            elif self.codec == "protobuf":
                message = ACLMessage_proto()
                message.sender_id = self.aid
                message.sender_addr = self.addr_str
                message.receiver_id = sender_id
                message.in_reply_to = reply_with
                if reply_key:
                    message.reply_with = reply_key
                message.conversation_id = conversation_id
                message.performative = Performatives.inform.value
                sub_msg = other_proto_msg.GenericMsg()
                sub_msg.text = message_out_content
                message.content_class = type(sub_msg).__name__
                message.content = sub_msg.SerializeToString()
            logger.debug(f"Going to send {message}")
            await self.send_message(message, sender_addr)

        # shutdown if no more open conversations
        if len(self.conversations) == 0:
            await self.shutdown()
