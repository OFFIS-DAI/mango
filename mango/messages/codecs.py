"""
This package imports the codecs that can be used for de- and encoding incoming
and outgoing messages:

- :class:`JSON` uses `JSON <http://www.json.org/>`_
- :class:`protobuf` uses protobuf

All codecs should implement the base class :class:`Codec`.

Most of this code is taken and adapted from Stefan Scherfkes aiomas:
https://gitlab.com/sscherfke/aiomas/
"""

import inspect
import json
from ctypes import c_int32
from hashlib import sha1

from mango.messages.message import (
    ACLMessage,
    AgentAddress,
    MangoMessage,
    Performatives,
    enum_serializer,
)

from ..messages.acl_message_pb2 import ACLMessage as ACLProto
from ..messages.other_proto_msgs_pb2 import GenericMsg as GenericProtoMsg


def json_serializable(cls=None, repr=True):
    """
    This is a direct copy from aiomas:
    https://gitlab.com/sscherfke/aiomas/-/blob/master/src/aiomas/codecs.py

    Class decorator that makes the decorated class serializable by the json
    codec (or any codec that can handle python dictionaries).

    The decorator tries to extract all arguments to the class’ ``__init__()``.
    That means, the arguments must be available as attributes with the same
    name.

    The decorator adds the following methods to the decorated class:

    - ``__asdict__()``: Returns a dict with all __init__ parameters
    - ``__fromdict__(dict)``: Creates a new class instance from *dict*
    - ``__serializer__()``: Returns a tuple with args for
      :meth:`Codec.add_serializer()`
    - ``__repr__()``: Returns a generic instance representation.  Adding this
      method can be deactivated by passing ``repr=False`` to the decorator.
    """

    def wrap(cls):
        attrs = [a for a in inspect.signature(cls).parameters]

        def __asdict__(self):
            return {a: getattr(self, a) for a in attrs}

        @classmethod
        def __fromdict__(cls, attrs):
            return cls(**attrs)

        def __repr__(self):
            args = (f"{a}={getattr(self, a)!r}" for a in attrs)
            return "{}({})".format(self.__class__.__name__, ", ".join(args))

        @classmethod
        def __serializer__(cls):
            return cls, cls.__asdict__, cls.__fromdict__

        cls.__asdict__ = __asdict__
        cls.__fromdict__ = __fromdict__
        cls.__serializer__ = __serializer__
        if repr:
            cls.__repr__ = __repr__

        return cls

    # The type of "cls" depends on the usage of the decorator.  It's a class if
    # it's used as `@serializable` but ``None`` if used as `@serializable()`.
    if cls is None:
        return wrap
    else:
        return wrap(cls)


class SerializationError(Exception):
    """Raised when an object cannot be serialized."""


class DecodeError(Exception):
    """Raised when an object representation can not be decoded."""


class Codec:
    """Base class for all Codecs.

    Subclasses must implement :meth:`encode()` and :meth:`decode()`.

    """

    def __init__(self):
        self._serializers = {}
        self._deserializers = {}

    def __str__(self):
        return "{}[{}]".format(
            self.__class__.__name__,
            ", ".join(s.__name__ for s in self._serializers),
        )

    def encode(self, data):
        """Encode the given *data* and return a :class:`bytes` object."""
        raise NotImplementedError

    def decode(self, data):
        """Decode *data* from :class:`bytes` to the original data structure."""
        raise NotImplementedError

    def make_type_id(self, otype):
        """Create a type id for *otype* using:
        - type name
        - function names in the class
        - signature of the class
        and return a 32 bit integer type id."""
        class_funcs = sorted(inspect.getmembers(otype, predicate=inspect.isfunction))

        data = otype.__name__
        for d in class_funcs:
            data += d[0]

        try:
            attrs = [a for a in inspect.signature(otype).parameters]
            for d in attrs:
                data += d
        except ValueError:
            # object type has no inspectable signature
            pass

        int_hash = int(sha1(data.encode("utf-8")).hexdigest(), 16)
        # truncate to 32 bit for protobuf wrapper
        type_id = c_int32(int_hash).value
        return type_id

    def add_serializer(self, otype, serialize, deserialize, type_id=None):
        """Add methods to *serialize* and *deserialize* objects typed *otype*.

        This can be used to de-/encode objects that the codec otherwise
        couldn't encode.

        *serialize* will receive the unencoded object and needs to return
        an encodable serialization of it.

        *deserialize* will receive an objects representation and should return
        an instance of the original object.
        """
        if otype in self._serializers:
            raise ValueError(f'There is already a serializer for type "{otype}"')

        if type_id is None:
            type_id = self.make_type_id(otype)

        if type_id in self._deserializers.keys():
            raise ValueError(f'There is already a serializer with type id "{type_id}"')

        # type_id = len(self._serializers)
        self._serializers[otype] = (type_id, serialize)
        self._deserializers[type_id] = deserialize

    def serialize_obj(self, obj):
        """Serialize *obj* to something that the codec can encode."""
        orig_type = otype = type(obj)
        if otype not in self._serializers:
            # Fallback to a generic serializer (if available)
            otype = object

        try:
            type_id, serialize = self._serializers[otype]
        except KeyError:
            raise SerializationError(
                f'No serializer found for type "{orig_type}"'
            ) from None

        try:
            return {"__type__": (type_id, serialize(obj))}
        except Exception as e:
            raise SerializationError(
                f'Could not serialize object "{obj!r}": {e}'
            ) from e

    def deserialize_obj(self, obj_repr):
        """Deserialize the original object from *obj_repr*."""
        # This method is called for *all* dicts so we have to check if it
        # contains a desrializable type.
        if "__type__" in obj_repr:
            type_id, data = obj_repr["__type__"]
            obj_repr = self._deserializers[type_id](data)
        return obj_repr


class JSON(Codec):
    """A :class:`Codec` that uses *JSON* to encode and decode messages."""

    def __init__(self):
        super().__init__()
        self.add_serializer(*ACLMessage.__json_serializer__())
        self.add_serializer(*MangoMessage.__json_serializer__())
        self.add_serializer(*AgentAddress.__serializer__())
        self.add_serializer(*enum_serializer(Performatives))

    def encode(self, data):
        return json.dumps(data, default=self.serialize_obj).encode()

    def decode(self, data):
        return json.loads(data.decode(), object_hook=self.deserialize_obj)


class PROTOBUF(Codec):
    def __init__(self):
        super().__init__()
        # expected serializers: (obj, to_proto, from_proto)
        # output of to_proto is the already serialized(!) proto object
        # input of from_proto is the string representation of the proto object
        # the codec merely handles the mapping of object types to these methods
        # it does not require any knowledge of the actual proto classes
        self.add_serializer(ACLMessage, self._acl_to_proto, self._proto_to_acl)
        self.add_serializer(*MangoMessage.__protoserializer__())

    def encode(self, data):
        # All known proto messages are wrapped in this generic proto msg.
        # This is to have the type_id available to decoding later.
        # Otherwise, we can not infer the original proto type from the serialized message.
        proto_msg = GenericProtoMsg()
        type_id, content = self.serialize_obj(data)
        proto_msg.type_id = type_id
        proto_msg.content = content.SerializeToString()
        return proto_msg.SerializeToString()

    def decode(self, data):
        proto_msg = GenericProtoMsg()
        try:
            proto_msg.ParseFromString(bytes(data))
        except Exception as e:
            raise DecodeError(f"Could not parse data: {data}") from e

        obj_repr = {"__type__": (proto_msg.type_id, proto_msg.content)}
        return self.deserialize_obj(obj_repr)

    def serialize_obj(self, obj):
        serialized = super().serialize_obj(obj)
        return serialized["__type__"]

    def register_proto_type(self, proto_class):
        def deserialize(data):
            proto_obj = proto_class()
            proto_obj.ParseFromString(data)
            return proto_obj

        self.add_serializer(proto_class, lambda x: x, deserialize)

    def _acl_to_proto(self, acl_message):
        # ACLMessage to serialized proto object
        msg = ACLProto()

        msg.sender_id = acl_message.sender_id if acl_message.sender_id else ""
        msg.receiver_id = acl_message.receiver_id if acl_message.receiver_id else ""
        msg.conversation_id = (
            acl_message.conversation_id if acl_message.conversation_id else ""
        )
        msg.performative = (
            acl_message.performative.value
            if acl_message.performative is not None
            else 0
        )
        msg.protocol = acl_message.protocol if acl_message.protocol else ""
        msg.language = acl_message.language if acl_message.language else ""
        msg.encoding = acl_message.encoding if acl_message.encoding else ""
        msg.ontology = acl_message.ontology if acl_message.ontology else ""
        msg.reply_with = acl_message.reply_with if acl_message.reply_with else ""
        msg.reply_by = acl_message.reply_by if acl_message.reply_by else ""
        msg.in_reply_to = acl_message.in_reply_to if acl_message.in_reply_to else ""

        if isinstance(acl_message.sender_addr, tuple | list):
            msg.sender_addr = (
                f"{acl_message.sender_addr[0]}:{acl_message.sender_addr[1]}"
            )
        elif acl_message.sender_addr:
            msg.sender_addr = acl_message.sender_addr

        if isinstance(acl_message.receiver_addr, tuple | list):
            msg.receiver_addr = (
                f"{acl_message.receiver_addr[0]}:{acl_message.receiver_addr[1]}"
            )
        elif acl_message.receiver_addr:
            msg.receiver_addr = acl_message.receiver_addr

        # content is only allowed to be a proto message known to the codec here
        if acl_message.content is not None:
            type_id, content = self.serialize_obj(acl_message.content)
            msg.content = content.SerializeToString()
            msg.content_type = type_id

        return msg

    def _proto_to_acl(self, data):
        # serialized proto object to ACLMessage
        msg = ACLProto()
        acl = ACLMessage()
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

        if msg.content and msg.content_type:
            obj_repr = {"__type__": (msg.content_type, msg.content)}
            acl.content = self.deserialize_obj(obj_repr)

        return acl
