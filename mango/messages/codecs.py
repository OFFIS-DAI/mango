"""
This package imports the codecs that can be used for de- and encoding incoming
and outgoing messages:

- :class:`JSON` uses `JSON <http://www.json.org/>`_
- :class:`protobuf' uses protobuf

All codecs should implement the base class :class:`Codec`.

"""

import json
from operator import is_
import pickle
from mango.messages.message import ACLMessage, enum_serializer, Performatives, MType
from ..messages.acl_message_pb2 import ACLMessage as acl_proto
from ..messages.other_proto_msgs_pb2 import GenericMsg as other_proto


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

    def add_serializer(self, type, serialize, deserialize):
        """Add methods to *serialize* and *deserialize* objects typed *type*.

        This can be used to de-/encode objects that the codec otherwise
        couldn't encode.

        *serialize* will receive the unencoded object and needs to return
        an encodable serialization of it.

        *deserialize* will receive an objects representation and should return
        an instance of the original object.

        """
        if type in self._serializers:
            raise ValueError('There is already a serializer for type "{}"'.format(type))
        typeid = len(self._serializers)
        self._serializers[type] = (typeid, serialize)
        self._deserializers[typeid] = deserialize

    def serialize_obj(self, obj):
        """Serialize *obj* to something that the codec can encode."""
        orig_type = otype = type(obj)
        if otype not in self._serializers:
            # Fallback to a generic serializer (if available)
            otype = object

        try:
            typeid, serialize = self._serializers[otype]
        except KeyError:
            raise SerializationError(
                'No serializer found for type "{}"'.format(orig_type)
            ) from None

        try:
            return {"__type__": (typeid, serialize(obj))}
        except Exception as e:
            raise SerializationError(
                'Could not serialize object "{!r}": {}'.format(obj, e)
            ) from e

    def deserialize_obj(self, obj_repr):
        """Deserialize the original object from *obj_repr*."""
        # This method is called for *all* dicts so we have to check if it
        # contains a desrializable type.
        if "__type__" in obj_repr:
            typeid, data = obj_repr["__type__"]
            obj_repr = self._deserializers[typeid](data)
        return obj_repr


class JSON(Codec):
    """A :class:`Codec` that uses *JSON* to encode and decode messages."""

    def __init__(self):
        super().__init__()
        self.add_serializer(*ACLMessage.__json_serializer__())
        self.add_serializer(*enum_serializer(Performatives))
        self.add_serializer(*enum_serializer(MType))

    def encode(self, data):
        return json.dumps(data, default=self.serialize_obj).encode()

    def decode(self, data):
        return json.loads(data.decode(), object_hook=self.deserialize_obj)


class PROTOBUF(Codec):
    ACLMSG_ID = "ACLMessage"

    def __init__(self):
        super().__init__()
        # expected serializers: (obj, to_proto, from_proto)
        # output of to_proto is the already serialized(!) proto object
        # input of from_proto is the string representation of the proto object
        # the codec merely handles the mapping of object types to these methods
        # it does not require any knowledge of the actual proto classes
        self.add_serializer(object, self.to_bytes, self.from_bytes)
        self.add_serializer(*ACLMessage.__proto_serializer__())

        obj_typeid, _ = self._serializers[object]
        # save this for handling of objects with no associated proto file
        self.obj_typeid = obj_typeid

    def to_bytes(self, data):
        return pickle.dumps(data)

    def from_bytes(self, data):
        if not data:
            return None
        return pickle.loads(bytes(data))

    def encode(self, data):
        proto_msg = other_proto()
        typeid, content = self.serialize_obj(data)
        proto_msg.type_id = typeid
        proto_msg.content = content

        return proto_msg.SerializeToString()

    def decode(self, data):
        proto_msg = other_proto()
        try:
            proto_msg.ParseFromString(data)
        except Exception as e:
            raise DecodeError(f"Could not parse data: {data}") from e

        obj_repr = {"__type__": (proto_msg.type_id, proto_msg.content)}
        return self.deserialize_obj(obj_repr)

    def serialize_obj(self, obj):
        serialized = super().serialize_obj(obj)
        return serialized["__type__"]
