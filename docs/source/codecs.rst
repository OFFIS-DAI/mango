=======
Codecs
=======

Most of the codec related code is taken and adapted from aiomas:
https://gitlab.com/sscherfke/aiomas/

Codecs enable the container to encode and decode known data types to send them as messages.
Mango already contains two codecs: A json serializer that can (recursively) handle any json serializable object and a protobuf codec
that will wrap an object into a generic protobuf message. Other codecs can be implemented by inheriting
from the ``Codec`` base class and implementing its ``encode`` and ``decode`` methods.
Codecs will only handle types explicitely known to them.
New known types can be added to a codec with the ``add_serializer`` method.
This method expects a type together with a serialization method and a deserialization method that translate the object into a format
the codec can handle (for example a json-serializable string for the json codec).

.. warning::
    When using the json codec certain types can not be exactly serialized and deserialized between containers.
    One example are ``tuple`` and classes derived from it like ``namedtuple``. The core of the json codec uses
    pythons json encoder [1] for any type that this encoder can handle by itself. Tuples are translated to
    json arrays without any further information by this encoder. Consequently, a receiving container will only
    see a json array and deserialize it to a python list.

    [1]: https://docs.python.org/3/library/json.html#json.JSONEncoder

Quickstart
###########

**general use**

Consider a simple example class we wish to encode as json:

.. testcode::

    class MyClass:
        def __init__(self, x, y):
            self.x = x
            self._y = y

        @property
        def y(self):
            return self._y

        def __asdict__(self):
            return {"x": self.x, "y": self.y}

        @classmethod
        def __fromdict__(cls, attrs):
            return cls(**attrs)

        @classmethod
        def __serializer__(cls):
            return (cls, cls.__asdict__, cls.__fromdict__)


If we try to encode an object of ``MyClass`` without adding a serializer we get an SerializationError:

.. testcode::

    from mango import JSON, SerializationError

    codec = JSON()

    my_object = MyClass("abc", 123)
    try:
        encoded = codec.encode(my_object)
    except SerializationError as e:
        print(e)

.. testoutput::

    No serializer found for type "<class 'MyClass'>"

We have to make the type known to the codec to use it:

.. testcode::

    codec = JSON()
    codec.add_serializer(*MyClass.__serializer__())

    my_object = MyClass("abc", 123)
    encoded = codec.encode(my_object)
    decoded = codec.decode(encoded)

    print(my_object.x, my_object.y)
    print(decoded.x, decoded.y)

.. testoutput::

    abc 123
    abc 123

All that is left to do now is to pass our codec to the container. This is done during container creation in the ``create_container`` method.

.. testcode::

    from mango import Agent, create_tcp_container, activate
    import asyncio

    class SimpleReceivingAgent(Agent):
        def __init__(self):
            super().__init__()

        def handle_message(self, content, meta):
            if isinstance(content, MyClass):
                print(content.x)
                print(content.y)


    async def main():
        codec = JSON()
        codec.add_serializer(*MyClass.__serializer__())

        # codecs can be passed directly to the container
        # if no codec is passed a new instance of JSON() is created
        sending_container = create_tcp_container(addr=("localhost", 5556), codec=codec)
        receiving_container = create_tcp_container(addr=("localhost", 5555), codec=codec)
        receiving_agent = receiving_container.register(SimpleReceivingAgent())

        async with activate(sending_container, receiving_container):
            # agents can now directly pass content of type MyClass to each other
            my_object = MyClass("abc", 123)
            await sending_container.send_message(
                content=my_object, receiver_addr=receiving_agent.addr
            )
            await asyncio.sleep(0.1)

    asyncio.run(main())

.. testoutput::

    abc
    123

**@json_serializable decorator**

In the above example we explicitely defined methods to (de)serialize our class. For simple classes, especially data classes,
we can achieve the same result (for json codecs) via the :meth:`mango.json_serializable`` decorator. This creates the ``__asdict__``,
``__fromdict__`` and ``__serializer__`` functions in the class:

.. testcode::

    from mango import json_serializable, JSON

    @json_serializable
    class DecoratorData:
        def __init__(self, x, y, z):
            self.x = x
            self.y = y
            self.z = z

    codec = JSON()
    codec.add_serializer(*DecoratorData.__serializer__())

    my_data = DecoratorData(1,2,3)
    encoded = codec.encode(my_data)
    decoded = codec.decode(encoded)

    print(my_data.x, my_data.y, my_data.z)
    print(decoded.x, decoded.y, decoded.z)

.. testoutput::

    1 2 3
    1 2 3


fast json
##########
Besides the normal full features json codec, which is able to serialize and deserialize messages under preservation of the type information, mango
provides the `codecs.FastJson` codec. This codec usese `msgspec` and does not provide any type safety. Therefore are also no custom serializer.


proto codec and ACLMessage
##########################

Serialization methods for the proto codec are expected to encode the object into a protobuf message object with the ``SerializeToString``
method.
The codec then wraps the message into a generic message wrapper, containing the serialized
protobuf message object and a type id.
This is necessary because in general the original type of a protobuf message can not be infered
from its serialized form.

The ``ACLMessage`` class is encouraged to be used for fipa compliant agent communication. For ease of use it gets specially handled in
the protobuf codec: Its content field may contain any proto object known to the codec and gets encoded with the associated type id just
like a non-ACL message would be encoded into the generic message wrapper.
