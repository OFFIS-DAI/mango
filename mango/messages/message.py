"""This module implements the ACLMessage class.
 The class is used to implement messages that are based on the FIPA ACL
 standard.
 http://www.fipa.org/specs/fipa00061/SC00061G.html#_Toc26669715

 It also includes the enum classes for the message Performative and Type

"""
import inspect
import json
import pickle

from typing import Dict, Any
from enum import Enum
from json import JSONDecodeError
from ..messages.acl_message_pb2 import ACLMessage as acl_proto

import networkx as nx


class ACLMessage:
    """The ACL Messages ar used for the communication between agents
     The Messages can have the following parameters parameters
      from the ACL standard:

        conversation-id: conversation unique identity;
        performative: message label;
        sender: message sender;
        receivers: message receivers;
        content: message content;
        protocol: message protocol;
        language: adopted language;
        encoding: encoding message;
        ontology: adopted ontology;
        reply-with: expression used by the answer agent to identify a message;
        in-reply-to: Denotes an expression that references an earlier action
         to which this message is a reply.
        reply-by: Denotes a time and/or date expression which indicates the
        latest time by which the sending agent would like to receive a reply.

    And require the following custom parameters:
        type: MessageType (enum)
    """

    conversation_id = None
    performative = None
    sender_id = None
    sender_addr = None
    receiver_id = None
    receiver_addr = None
    content = None
    protocol = None
    language = None
    encoding = None
    ontology = None
    reply_with = None
    reply_by = None
    in_reply_to = None
    message_type = None

    def __init__(
        self,
        *,
        m_type=None,
        sender_id=None,
        sender_addr=None,
        receiver_id=None,
        content=None,
        receiver_addr=None,
        performative=None,
        conversation_id=None,
        reply_by=None,
        in_reply_to=None,
        protocol=None,
        language=None,
        encoding=None,
        ontology=None,
        reply_with=None,
    ):
        self.message_type = m_type
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

    def __lt__(self, other):
        if self.conversation_id is None:
            return True
        if other.conversation_id is None:
            return False
        return self.conversation_id < other.conversation_id

    def extract_meta(self) -> Dict[str, Any]:
        return {
            "sender_id": self.sender_id,
            "sender_addr": self.sender_addr,
            "receiver_id": self.receiver_id,
            "receiver_addr": self.receiver_addr,
            "performative": self.performative,
            "message_type": self.message_type,
            "conversation_id": self.conversation_id,
            "reply_by": self.reply_by,
            "in_reply_to": self.in_reply_to,
            "protocol": self.protocol,
            "language": self.language,
            "encoding": self.encoding,
            "ontology": self.ontology,
            "reply_with": self.reply_with,
        }

    def __str__(self):
        message_dict = {
            "sender_id": self.sender_id,
            "sender_addr": self.sender_addr,
            "receiver_id": self.receiver_id,
            "receiver_addr": self.receiver_addr,
            "content": self.content,
            "performative": self.performative,
            "message_type": self.message_type,
            "conversation_id": self.conversation_id,
            "reply_by": self.reply_by,
            "in_reply_to": self.in_reply_to,
        }
        return str(message_dict)

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
        return (cls, cls.__asdict__, cls.__fromdict__)

    def __toproto__(self):
        # ACLMessage to serialized proto object
        msg = acl_proto()

        msg.sender_id = self.sender_id if self.sender_id else ""
        msg.receiver_id = self.receiver_id if self.receiver_id else ""
        msg.conversation_id = self.conversation_id if self.conversation_id else ""
        msg.performative = (
            self.performative.value if self.performative is not None else 0
        )
        msg.message_type = (
            self.message_type.value if self.message_type is not None else 0
        )
        msg.protocol = self.protocol if self.protocol else ""
        msg.language = self.language if self.language else ""
        msg.encoding = self.encoding if self.encoding else ""
        msg.ontology = self.ontology if self.ontology else ""
        msg.reply_with = self.reply_with if self.reply_with else ""
        msg.reply_by = self.reply_by if self.reply_by else ""
        msg.in_reply_to = self.in_reply_to if self.in_reply_to else ""
        msg.content_class = ""

        # would be nice to have proper recursive serialization like with json codec
        # but I am not sure how to make that work with proto files for now
        msg.content = pickle.dumps(self.content)

        if isinstance(self.sender_addr, (tuple, list)):
            msg.sender_addr = f"{self.sender_addr[0]}:{self.sender_addr[1]}"
        elif self.sender_addr:
            msg.sender_addr = self.sender_addr

        if isinstance(self.receiver_addr, (tuple, list)):
            msg.receiver_addr = f"{self.receiver_addr[0]}:{self.receiver_addr[1]}"
        elif self.receiver_addr:
            msg.receiver_addr = self.receiver_addr

        return msg.SerializeToString()

    @classmethod
    def __fromproto__(cls, data):
        # serialized proto object to ACLMessage
        msg = acl_proto()
        acl = cls()
        msg.ParseFromString(data)

        acl.sender_id = msg.sender_id if msg.sender_id else None
        acl.receiver_id = msg.receiver_id if msg.receiver_id else None
        acl.conversation_id = msg.conversation_id if msg.conversation_id else None
        acl.performative = Performatives(msg.performative) if msg.performative else None
        acl.message_type = MType(msg.message_type) if msg.message_type else None
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

    @classmethod
    def __proto_serializer__(cls):
        return (cls, cls.__toproto__, cls.__fromproto__)

    def split_content_and_meta(self):
        return (self.content, self.extract_meta())


def enum_serializer(enum_cls):
    def __tostring__(enum_obj):
        return enum_obj.value

    def __fromstring__(enum_repr):
        return enum_cls(enum_repr)

    return (enum_cls, __tostring__, __fromstring__)


class MType(Enum):
    container = 1
    agent = 2


class Performatives(Enum):
    """member values (mus be unique) could be used as priority values
    if not replace by enum.auto"""

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
    inform_about_neighborhood = 23


PUBLIC_ENUMS = {"Performatives": Performatives, "MType": MType}


class EnumEncoder(json.JSONEncoder):
    """
    Helper Class for encoding known Enums and networkx graphs
    """

    def default(self, obj):
        if type(obj) in PUBLIC_ENUMS.values():
            return {"__enum__": str(obj)}
        if isinstance(obj, nx.Graph):
            return {"__graph__": nx.readwrite.json_graph.node_link_data(obj)}
        return json.JSONEncoder.default(self, obj)


def as_enum(data):
    """Helper method for decoding known Enums and networkx graphs"""
    if "__enum__" in data:
        name, member = data["__enum__"].split(".")
        return getattr(PUBLIC_ENUMS[name], member)
    if "__graph__" in data:
        return nx.readwrite.json_graph.node_link_graph(data["__graph__"])
    return data


"""
accept-proposal- The action of accepting a previously submitted propose to
perform an action.
agree- The action of agreeing to perform a requestd action made by another
agent. Agent will carry it out.
cancel- Agent wants to cancel a previous request.
cfp- Agent issues a call for proposals. It contains the actions to be
carried out and any other terms of the agreement.
confirm- The sender confirms to the receiver the truth of the content. The
sender initially believed that the receiver was unsure about it.
disconfirm- The sender confirms to the receiver the falsity of the content.
failure- Tell the other agent that a previously requested action failed.
inform- Tell another agent something. The sender must believe in the truth
of the statement. Most used performative.
inform-if- Used as content of request to ask another agent to tell us is a
statement is true or false.
inform-ref- Like inform-if but asks for the value of the expression.
not-understood- Sent when the agent did not understand the message.
propagate- Asks another agent so forward this same propagate message to others.
propose- Used as a response to a cfp. Agent proposes a deal.
proxy- The sender wants the receiver to select target agents denoted by a
given description and to send an embedded message to them.
query-if- The action of asking another agent whether or not a given
proposition is true.
query-ref- The action of asking another agent for the object referred to by
an referential expression.
refuse- The action of refusing to perform a given action, and explaining the
reason for the refusal.
reject-proposal- The action of rejecting a proposal to perform some action
during a negotiation.
request- The sender requests the receiver to perform some action. Usually to
request the receiver to perform another communicative act.
request-when- The sender wants the receiver to perform some action when some
given proposition becomes true.
request-whenever- The sender wants the receiver to perform some action as
soon as some proposition becomes true and thereafter each time the
proposition becomes true again.
subscribe- The act of requesting a persistent intention to notify the sender
of the value of a reference, and to notify again whenever the object
identified by the reference changes."""
