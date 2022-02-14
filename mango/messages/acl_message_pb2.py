# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: acl_message.proto

import sys
_b=sys.version_info[0]<3 and (lambda x:x) or (lambda x:x.encode('latin1'))
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf import reflection as _reflection
from google.protobuf import symbol_database as _symbol_database
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()




DESCRIPTOR = _descriptor.FileDescriptor(
  name='acl_message.proto',
  package='',
  syntax='proto3',
  serialized_options=None,
  serialized_pb=_b('\n\x11\x61\x63l_message.proto\"\xf6\x05\n\nACLMessage\x12\x11\n\tsender_id\x18\x01 \x01(\t\x12\x13\n\x0bsender_addr\x18\x02 \x01(\t\x12\x13\n\x0breceiver_id\x18\x03 \x01(\t\x12\x15\n\rreceiver_addr\x18\x04 \x01(\t\x12\x17\n\x0f\x63onversation_id\x18\x05 \x01(\t\x12.\n\x0cperformative\x18\x06 \x01(\x0e\x32\x18.ACLMessage.Performative\x12\x0f\n\x07\x63ontent\x18\x07 \x01(\x0c\x12\'\n\x0cmessage_type\x18\x08 \x01(\x0e\x32\x11.ACLMessage.MType\x12\x10\n\x08protocol\x18\t \x01(\t\x12\x10\n\x08language\x18\n \x01(\t\x12\x10\n\x08\x65ncoding\x18\x0b \x01(\t\x12\x10\n\x08ontology\x18\x0c \x01(\t\x12\x12\n\nreply_with\x18\r \x01(\t\x12\x10\n\x08reply_by\x18\x0e \x01(\t\x12\x13\n\x0bin_reply_to\x18\x0f \x01(\t\x12\x15\n\rcontent_class\x18\x10 \x01(\t\"\xd3\x02\n\x0cPerformative\x12\x13\n\x0f\x61\x63\x63\x65pt_proposal\x10\x00\x12\t\n\x05\x61gree\x10\x01\x12\n\n\x06\x63\x61ncel\x10\x02\x12\x15\n\x11\x63\x61ll_for_proposal\x10\x03\x12\x0b\n\x07\x63onfirm\x10\x04\x12\x0e\n\ndisconfirm\x10\x05\x12\x0b\n\x07\x66\x61ilure\x10\x06\x12\n\n\x06inform\x10\x07\x12\x12\n\x0enot_understood\x10\x08\x12\x0b\n\x07propose\x10\t\x12\x0c\n\x08query_if\x10\n\x12\r\n\tquery_ref\x10\x0b\x12\n\n\x06refuse\x10\x0c\x12\x13\n\x0freject_proposal\x10\r\x12\x0b\n\x07request\x10\x0e\x12\x10\n\x0crequest_when\x10\x0f\x12\x14\n\x10request_whenever\x10\x10\x12\r\n\tsubscribe\x10\x11\x12\r\n\tinform_if\x10\x12\x12\t\n\x05proxy\x10\x13\x12\r\n\tpropagate\x10\x14\"!\n\x05MType\x12\r\n\tcontainer\x10\x00\x12\t\n\x05\x61gent\x10\x01\x62\x06proto3')
)



_ACLMESSAGE_PERFORMATIVE = _descriptor.EnumDescriptor(
  name='Performative',
  full_name='ACLMessage.Performative',
  filename=None,
  file=DESCRIPTOR,
  values=[
    _descriptor.EnumValueDescriptor(
      name='accept_proposal', index=0, number=0,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='agree', index=1, number=1,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='cancel', index=2, number=2,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='call_for_proposal', index=3, number=3,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='confirm', index=4, number=4,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='disconfirm', index=5, number=5,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='failure', index=6, number=6,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='inform', index=7, number=7,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='not_understood', index=8, number=8,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='propose', index=9, number=9,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='query_if', index=10, number=10,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='query_ref', index=11, number=11,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='refuse', index=12, number=12,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='reject_proposal', index=13, number=13,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='request', index=14, number=14,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='request_when', index=15, number=15,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='request_whenever', index=16, number=16,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='subscribe', index=17, number=17,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='inform_if', index=18, number=18,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='proxy', index=19, number=19,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='propagate', index=20, number=20,
      serialized_options=None,
      type=None),
  ],
  containing_type=None,
  serialized_options=None,
  serialized_start=406,
  serialized_end=745,
)
_sym_db.RegisterEnumDescriptor(_ACLMESSAGE_PERFORMATIVE)

_ACLMESSAGE_MTYPE = _descriptor.EnumDescriptor(
  name='MType',
  full_name='ACLMessage.MType',
  filename=None,
  file=DESCRIPTOR,
  values=[
    _descriptor.EnumValueDescriptor(
      name='container', index=0, number=0,
      serialized_options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='agent', index=1, number=1,
      serialized_options=None,
      type=None),
  ],
  containing_type=None,
  serialized_options=None,
  serialized_start=747,
  serialized_end=780,
)
_sym_db.RegisterEnumDescriptor(_ACLMESSAGE_MTYPE)


_ACLMESSAGE = _descriptor.Descriptor(
  name='ACLMessage',
  full_name='ACLMessage',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  fields=[
    _descriptor.FieldDescriptor(
      name='sender_id', full_name='ACLMessage.sender_id', index=0,
      number=1, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=_b("").decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='sender_addr', full_name='ACLMessage.sender_addr', index=1,
      number=2, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=_b("").decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='receiver_id', full_name='ACLMessage.receiver_id', index=2,
      number=3, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=_b("").decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='receiver_addr', full_name='ACLMessage.receiver_addr', index=3,
      number=4, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=_b("").decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='conversation_id', full_name='ACLMessage.conversation_id', index=4,
      number=5, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=_b("").decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='performative', full_name='ACLMessage.performative', index=5,
      number=6, type=14, cpp_type=8, label=1,
      has_default_value=False, default_value=0,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='content', full_name='ACLMessage.content', index=6,
      number=7, type=12, cpp_type=9, label=1,
      has_default_value=False, default_value=_b(""),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='message_type', full_name='ACLMessage.message_type', index=7,
      number=8, type=14, cpp_type=8, label=1,
      has_default_value=False, default_value=0,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='protocol', full_name='ACLMessage.protocol', index=8,
      number=9, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=_b("").decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='language', full_name='ACLMessage.language', index=9,
      number=10, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=_b("").decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='encoding', full_name='ACLMessage.encoding', index=10,
      number=11, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=_b("").decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='ontology', full_name='ACLMessage.ontology', index=11,
      number=12, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=_b("").decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='reply_with', full_name='ACLMessage.reply_with', index=12,
      number=13, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=_b("").decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='reply_by', full_name='ACLMessage.reply_by', index=13,
      number=14, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=_b("").decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='in_reply_to', full_name='ACLMessage.in_reply_to', index=14,
      number=15, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=_b("").decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
    _descriptor.FieldDescriptor(
      name='content_class', full_name='ACLMessage.content_class', index=15,
      number=16, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=_b("").decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
    _ACLMESSAGE_PERFORMATIVE,
    _ACLMESSAGE_MTYPE,
  ],
  serialized_options=None,
  is_extendable=False,
  syntax='proto3',
  extension_ranges=[],
  oneofs=[
  ],
  serialized_start=22,
  serialized_end=780,
)

_ACLMESSAGE.fields_by_name['performative'].enum_type = _ACLMESSAGE_PERFORMATIVE
_ACLMESSAGE.fields_by_name['message_type'].enum_type = _ACLMESSAGE_MTYPE
_ACLMESSAGE_PERFORMATIVE.containing_type = _ACLMESSAGE
_ACLMESSAGE_MTYPE.containing_type = _ACLMESSAGE
DESCRIPTOR.message_types_by_name['ACLMessage'] = _ACLMESSAGE
_sym_db.RegisterFileDescriptor(DESCRIPTOR)

ACLMessage = _reflection.GeneratedProtocolMessageType('ACLMessage', (_message.Message,), dict(
  DESCRIPTOR = _ACLMESSAGE,
  __module__ = 'acl_message_pb2'
  # @@protoc_insertion_point(class_scope:ACLMessage)
  ))
_sym_db.RegisterMessage(ACLMessage)


# @@protoc_insertion_point(module_scope)
