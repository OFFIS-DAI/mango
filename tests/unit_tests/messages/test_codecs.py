from multiprocessing.sharedctypes import Value
import pytest
import pickle
from mango.messages.codecs import Codec, JSON, PROTOBUF, SerializationError
from mango.messages.message import ACLMessage, Performatives, MType
from dataclasses import dataclass

testcodecs = [JSON, PROTOBUF]


@dataclass
class SomeDataClass:
    name: str = ""
    unit_price: float = 0
    quantity_on_hand: int = 0

    def __asdict__(self):
        return vars(self)

    @classmethod
    def __fromdict__(cls, attrs):
        msg = SomeDataClass()
        for key, value in attrs.items():
            setattr(msg, key, value)
        return msg

    @classmethod
    def __serializer__(cls):
        return (cls, cls.__asdict__, cls.__fromdict__)


class SomeOtherClass:
    def __init__(self) -> None:
        self.x = 1
        self.y = 2
        self.z = "abc123"
        self.d = {1: "test", 2: "data", 3: 123}

    def __tostr__(self):
        return {"data": list(pickle.dumps(self))}

    @classmethod
    def __fromstr__(cls, obj_repr):
        return pickle.loads(bytes(obj_repr["data"]))

    @classmethod
    def __serializer__(cls):
        return (cls, cls.__tostr__, cls.__fromstr__)

    def __eq__(self, other):
        return (
            self.x == other.x
            and self.y == other.y
            and self.z == other.z
            and self.d == other.d
        )


# ------------------
# base class tests
# ------------------
def test_add_serializer_add_new():
    my_codec = Codec()
    my_codec.add_serializer(*SomeOtherClass.__serializer__())

    assert True


def test_add_serializer_add_existing():
    my_codec = Codec()
    my_codec.add_serializer(*SomeOtherClass.__serializer__())

    with pytest.raises(ValueError):
        my_codec.add_serializer(*SomeOtherClass.__serializer__())


def test_serialize_obj_ok():
    my_codec = Codec()
    my_codec.add_serializer(*SomeOtherClass.__serializer__())
    my_obj = SomeOtherClass()
    serialized = my_codec.serialize_obj(my_obj)
    deserialized = my_codec.deserialize_obj(serialized)

    assert my_obj == deserialized


def test_serialize_obj_fail():
    my_codec = Codec()
    my_obj = SomeOtherClass()

    with pytest.raises(SerializationError):
        my_codec.serialize_obj(my_obj)


# ------------------
# concrete class tests
# ------------------
@pytest.mark.parametrize("codec", testcodecs)
def test_codec_basic(codec):
    x = 4
    y = 1.5
    z = "abc123"
    b = True

    my_codec = codec()
    x_new = my_codec.decode(my_codec.encode(x))
    y_new = my_codec.decode(my_codec.encode(y))
    z_new = my_codec.decode(my_codec.encode(z))
    b_new = my_codec.decode(my_codec.encode(b))

    assert x == x_new
    assert y == y_new
    assert z == z_new
    assert b == b_new


@pytest.mark.parametrize("codec", testcodecs)
def test_codec_known(codec):
    # known == (Performatives, MType, ACLMessage)
    p = Performatives.inform
    m = MType.agent
    msg = ACLMessage(m_type=m, performative=p, sender_addr="localhost:1883")

    my_codec = codec()
    p_new = my_codec.decode(my_codec.encode(p))
    m_new = my_codec.decode(my_codec.encode(m))
    msg_new = my_codec.decode(my_codec.encode(msg))

    assert p == p_new
    assert m == m_new
    assert vars(msg_new) == vars(msg)


@pytest.mark.parametrize("codec", testcodecs)
def test_codec_data_class(codec):
    my_codec = codec()
    my_codec.add_serializer(*SomeDataClass.__serializer__())
    my_obj = SomeDataClass("test", 1.5, 30)
    decoded = my_codec.decode(my_codec.encode(my_obj))

    assert my_obj == decoded


@pytest.mark.parametrize("codec", testcodecs)
def test_codec_other_class(codec):
    my_codec = codec()
    my_codec.add_serializer(*SomeOtherClass.__serializer__())
    my_obj = SomeOtherClass()
    encoded_obj = my_codec.encode(my_obj)
    decoded_obj = my_codec.decode(encoded_obj)

    assert decoded_obj == my_obj
