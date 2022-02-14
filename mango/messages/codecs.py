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
        self.add_serializer(*ACLMessage.__serializer__())
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
        self.add_serializer(*ACLMessage.__serializer__())
        self.add_serializer(*enum_serializer(Performatives))
        self.add_serializer(*enum_serializer(MType))

    def to_bytes(self, data):
        # TODO I dont know what to properly do with this yet
        # generic method to turn any content field into bytes
        # can be overwritten as necessary
        return pickle.dumps(data)

    def from_bytes(self, data):
        if not data:
            return None
        print(f"data to unpickle is: {data}")
        return pickle.loads(bytes(data))

    def encode(self, data):
        # if data is an ACLMessage use that proto file
        if isinstance(data, ACLMessage):
            message = acl_proto()
            for key, value in vars(data).items():
                if value is None:
                    continue

                if key == "content":
                    message.content = self.to_bytes(data.content)
                elif key == "receiver_addr":
                    if isinstance(data.receiver_addr, (tuple, list)):
                        message.receiver_addr = (
                            f"{data.receiver_addr[0]}:{data.receiver_addr[1]}"
                        )
                    else:
                        message.receiver_addr = value
                elif key == "sender_addr":
                    if isinstance(data.sender_addr, (tuple, list)):
                        message.sender_addr = (
                            f"{data.sender_addr[0]}:{data.sender_addr[1]}"
                        )
                    else:
                        message.sender_addr = value

                elif key == "performative":
                    if isinstance(value, Performatives):
                        message.performative = value.value
                    else:
                        message.performative = value
                elif key == "message_type":
                    if isinstance(value, MType):
                        message.message_type = value.value
                    else:
                        message.message_type = value
                else:
                    message.__setattr__(key, value)

            message.content_class = PROTOBUF.ACLMSG_ID
        else:
            message = other_proto()
            message.content = self.to_bytes(data)

        return message.SerializeToString()

    def decode(self, data):
        # try parsing as ACL message
        parsed_msg = None
        is_acl_msg = False
        try:
            parsed_msg = acl_proto()
            parsed_msg.ParseFromString(data)
            if not parsed_msg.content_class == PROTOBUF.ACLMSG_ID:
                raise Exception
            is_acl_msg = True
        except Exception as e:
            parsed_msg = None

        if is_acl_msg:
            msg = ACLMessage()
            msg.content = self.from_bytes(parsed_msg.content)

            for descriptor in parsed_msg.DESCRIPTOR.fields:
                key = descriptor.name
                value = getattr(parsed_msg, key)
                if value == "":
                    value = None

                if key in vars(msg).keys():
                    if key == "content":
                        continue
                    elif key == "performative" and value:
                        msg.performative = Performatives(value)
                    elif key == "message_type" and value:
                        msg.message_type = MType(value)
                    # elif key == "sender_addr" and value:
                    #     host, port = value.split(":")
                    #     msg.sender_addr = [host, int(port)]
                    else:
                        msg.__setattr__(key, value)

        # else parse as generic
        else:
            parsed_msg = other_proto()
            parsed_msg.ParseFromString(data)
            msg = self.from_bytes(parsed_msg.content)

        return msg
