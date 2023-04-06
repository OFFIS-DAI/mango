"""This module implements the ACLMessage class.
 The class is used to implement messages that are based on the FIPA ACL
 standard.
 http://www.fipa.org/specs/fipa00061/SC00061G.html#_Toc26669715

 It also includes the enum classes for the message Performative and Type

"""
import pickle
from enum import Enum
from typing import Any, Dict

from ..messages.acl_message_pb2 import ACLMessage as ACLProto


class ACLMessage:
    """
    The ACL Message is the standard header used for the communication between  mango agents.
    This class is based on the FIPA ACL standard: http://www.fipa.org/specs/fipa00061/SC00061G.html
    """

    def __init__(
        self,
        *,
        sender_id=None,
        sender_addr=None,
        receiver_id=None,
        receiver_addr=None,
        conversation_id=None,
        performative=None,
        content=None,
        protocol=None,
        language=None,
        encoding=None,
        ontology=None,
        reply_with=None,
        reply_by=None,
        in_reply_to=None,
    ):
        """

        :param sender_id: The agent ID of the message sender (e.g. Agent0)
        :param sender_addr: The address of the message sender
        :param receiver_id: The agent ID of the message receiver
        :param receiver_addr: The address of the message receiver
        :param conversation_id: The conversation ID
        :param performative: The Performative of the message (see http://www.fipa.org/specs/fipa00037/SC00037J.html)
        :param content: The message content
        :param protocol: The interaction protocol in which the ACL message is generated
        :param language: The formal language of content
        :param encoding: Encoding of the content
        :param ontology:  Ontology to support the interpretation of the content
        :param reply_with: This is used to follow a conversation thread
        :param reply_by: Denotes a time which indicates the latest time by which
        the sending agent would like to receive a reply.
        :param in_reply_to: Denotes an expression that references an earlier action to which this message is a reply.
        """

        self.sender_id = sender_id
        self.sender_addr = sender_addr
        self.receiver_id = receiver_id
        self.receiver_addr = receiver_addr
        self.content = content
        self.performative = performative
        self.conversation_id = conversation_id
        self.reply_by = reply_by
        self.in_reply_to = in_reply_to
        self.protocol = protocol
        self.language = language
        self.encoding = encoding
        self.ontology = ontology
        self.reply_with = reply_with

    def __str__(self):
        return str(self.message_dict)

    @property
    def message_dict(self):
        return {
            "sender_id": self.sender_id,
            "sender_addr": self.sender_addr,
            "receiver_id": self.receiver_id,
            "receiver_addr": self.receiver_addr,
            "performative": self.performative,
            "conversation_id": self.conversation_id,
            "content": self.content,
            "reply_by": self.reply_by,
            "in_reply_to": self.in_reply_to,
            "protocol": self.protocol,
            "language": self.language,
            "encoding": self.encoding,
            "ontology": self.ontology,
            "reply_with": self.reply_with,
        }

    def __asdict__(self):
        return vars(self)

    @classmethod
    def __fromdict__(cls, attrs):
        msg = ACLMessage()
        for key, value in attrs.items():
            setattr(msg, key, value)
        return msg

    @classmethod
    def __json_serializer__(cls):
        return cls, cls.__asdict__, cls.__fromdict__

    @classmethod
    def __fromproto__(cls, data):
        # serialized proto object to ACLMessage
        msg = ACLProto()
        acl = cls()
        msg.ParseFromString(data)

        acl.sender_id = msg.sender_id if msg.sender_id else None
        acl.receiver_id = msg.receiver_id if msg.receiver_id else None
        acl.conversation_id = msg.conversation_id if msg.conversation_id else None
        acl.performative = Performatives(msg.performative) if msg.performative else None
        acl.protocol = msg.protocol if msg.protocol else None
        acl.language = msg.language if msg.language else None
        acl.encoding = msg.encoding if msg.encoding else None
        acl.ontology = msg.ontology if msg.ontology else None
        acl.reply_with = msg.reply_with if msg.reply_with else None
        acl.reply_by = msg.reply_by if msg.reply_by else None
        acl.in_reply_to = msg.in_reply_to if msg.in_reply_to else None
        acl.sender_addr = msg.sender_addr if msg.sender_addr else None
        acl.receiver_addr = msg.receiver_addr if msg.receiver_addr else None

        acl.content = pickle.loads(bytes(msg.content)) if msg.content else None

        return acl

    def extract_meta(self) -> Dict[str, Any]:
        meta_dict = self.message_dict
        meta_dict.pop("content")
        return meta_dict

    def split_content_and_meta(self):
        return self.content, self.extract_meta()


def enum_serializer(enum_cls):
    def __tostring__(enum_obj):
        return enum_obj.value

    def __fromstring__(enum_repr):
        return enum_cls(enum_repr)

    return enum_cls, __tostring__, __fromstring__


class Performatives(Enum):
    """
    member values (must be unique) could be used as priority values if not replaced by enum.auto.
    See http://www.fipa.org/specs/fipa00037/SC00037J.html for a description of performatives.
    """

    accept_proposal = 1
    agree = 2
    cancel = 3
    cfp = 4
    call_for_proposal = 5
    confirm = 6
    disconfirm = 7
    failure = 8
    inform = 9
    not_understood = 10
    propose = 11
    query_if = 12
    query_ref = 13
    refuse = 14
    reject_proposal = 15
    request = 16
    request_when = 17
    request_whenever = 18
    subscribe = 19
    inform_if = 20
    proxy = 21
    propagate = 22
