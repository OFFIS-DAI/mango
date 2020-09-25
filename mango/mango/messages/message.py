"""This module implements the ACLMessage class.
 The class is used to implement messages that are based on the FIPA ACL
 standard.
 http://www.fipa.org/specs/fipa00061/SC00061G.html#_Toc26669715

 It also includes the enum classes for the message Performative and Type

"""
import inspect
import json

from typing import Dict, Any
from enum import Enum
from json import JSONDecodeError

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

    def __init__(self, *, m_type=None, sender_id=None, sender_addr=None, receiver_id=None,
                 content=None, receiver_addr=None,
                 performative=None, conversation_id=None, reply_by=None,
                 in_reply_to=None):
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

    def __lt__(self, other):
        if self.conversation_id is None:
            return True
        if other.conversation_id is None:
            return False
        return self.conversation_id < other.conversation_id

    def encode(self):
        # message_dict = {
        #     'sender': self.sender, 'receivers': self.receivers,
        #     'content': self.content, 'performative': self.performative,
        #     'type': self.type}
        return json.dumps(vars(self), cls=EnumEncoder).encode()


    def decode(self, data):
        attributes = inspect.getmembers(ACLMessage,
                                        lambda a: not inspect.isroutine(a))
        attributes = [a[0] for a in attributes if
                      not (a[0].startswith('__') and a[0].endswith('__'))]
        try:
            message_dict = json.loads(data.decode(),
                                      object_hook=as_enum)
            for attr in attributes:
                if attr in message_dict:
                    self.__setattr__(attr, message_dict[attr])
            # self.sender = message_dict['sender']
            # self.receivers = message_dict['receivers']
            # self.content = message_dict['content']
            # self.performative = message_dict['performative']
            # self.type = message_dict['type']
        except JSONDecodeError:
            print(
                "Invalid JSON in request: \n%s" % data.decode())


    def extract_meta(self) -> Dict[str, Any]:
        return {
            'sender_id': self.sender_id,
            'sender_addr': self.sender_addr,
            'receiver_id': self.receiver_id,
            'receiver_addr': self.receiver_addr,
            'performative': self.performative,
            'message_type': self.message_type,
            'conversation_id': self.conversation_id,
            'reply_by': self.reply_by,
            'in_reply_to': self.in_reply_to,
        }


    def __str__(self):
        message_dict = {
            'sender_id': self.sender_id,
            'sender_addr': self.sender_addr,
            'receiver_id': self.receiver_id,
            'receiver_addr': self.receiver_addr,
            'content': self.content,
            'performative': self.performative,
            'message_type': self.message_type,
            'conversation_id': self.conversation_id,
            'reply_by': self.reply_by,
            'in_reply_to': self.in_reply_to,
        }
        return str(message_dict)



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


PUBLIC_ENUMS = {
    'Performatives': Performatives,
    'MType': MType
}


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
