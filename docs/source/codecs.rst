======
Codecs
======

A *codec* translates Python objects into a wire format that can be sent over
the network and reconstructed on the receiving end.  Every container uses
exactly one codec for all its messages.

mango ships two built-in codecs:

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Codec
     - Notes
   * - :class:`~mango.JSON`
     - Default.  Extends Python's ``json`` module with a type-registry so
       custom classes survive the round-trip.  Zero extra dependencies.
   * - :class:`~mango.PROTOBUF`
     - Uses Google Protocol Buffers.  Requires the ``protobuf`` package.
       Suitable for bandwidth-constrained or schema-first deployments.

You can also write your own codec by subclassing ``Codec`` and implementing
``encode`` and ``decode``.

.. warning::
    The JSON codec cannot round-trip every Python type faithfully.  In
    particular, **tuples** (and ``namedtuple``) are decoded back as ``list``
    because JSON has no distinct array type.  Encode tuples manually or use a
    custom serialiser if you need to preserve the type.


JSON codec
==========

Most of the codec code is adapted from
`aiomas <https://gitlab.com/sscherfke/aiomas/>`_.

The JSON codec can handle any JSON-serialisable primitive (strings, numbers,
booleans, lists, dicts) out of the box.  To send custom class instances you
register a *serialiser* — a pair of (encode, decode) functions — with
:meth:`~mango.JSON.add_serializer`.

Manual serialiser
-----------------

Implement ``__asdict__``, ``__fromdict__``, and ``__serializer__`` on your
class:

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

Without registering a serialiser, encoding an unknown type raises
:class:`~mango.SerializationError`:

.. testcode::

    from mango import JSON, SerializationError

    codec = JSON()
    try:
        codec.encode(MyClass("abc", 123))
    except SerializationError as e:
        print(e)

.. testoutput::

    No serializer found for type "<class 'MyClass'>"

Register the serialiser once per codec instance:

.. testcode::

    codec = JSON()
    codec.add_serializer(*MyClass.__serializer__())

    my_object = MyClass("abc", 123)
    decoded = codec.decode(codec.encode(my_object))

    print(my_object.x, my_object.y)
    print(decoded.x, decoded.y)

.. testoutput::

    abc 123
    abc 123

Type IDs
--------

The codec assigns each registered type a 32-bit integer *type ID* so the
receiver knows how to decode a message.  By default the ID is generated
automatically.  If both ends must agree on an ID (e.g. when ID generation
order may differ), set it explicitly:

.. testcode::

    codec = JSON()
    codec.add_serializer(*MyClass.__serializer__(), type_id=4711)

    decoded = codec.decode(codec.encode(MyClass("abc", 123)))
    print(decoded.x, decoded.y)

.. testoutput::

    abc 123

Pass your configured codec to the container factory:

.. testcode::

    import asyncio
    from mango import Agent, create_tcp_container, activate

    class SimpleReceivingAgent(Agent):
        def handle_message(self, content, meta):
            if isinstance(content, MyClass):
                print(content.x)
                print(content.y)

    async def main():
        codec = JSON()
        codec.add_serializer(*MyClass.__serializer__())

        sending_container   = create_tcp_container(addr=("127.0.0.1", 5556), codec=codec)
        receiving_container = create_tcp_container(addr=("127.0.0.1", 5555), codec=codec)
        receiving_agent = receiving_container.register(SimpleReceivingAgent())

        async with activate(sending_container, receiving_container):
            await sending_container.send_message(
                content=MyClass("abc", 123),
                receiver_addr=receiving_agent.addr,
            )
            await asyncio.sleep(0.1)

    asyncio.run(main())

.. testoutput::

    abc
    123

``@json_serializable`` decorator
---------------------------------

For simple classes (especially dataclasses) the
:func:`~mango.json_serializable` decorator generates the ``__asdict__``,
``__fromdict__``, and ``__serializer__`` methods automatically:

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

    decoded = codec.decode(codec.encode(DecoratorData(1, 2, 3)))
    print(decoded.x, decoded.y, decoded.z)

.. testoutput::

    1 2 3


FastJSON codec
==============

:class:`~mango.messages.codecs.FastJson` is a lightweight alternative to the
full JSON codec.  It uses `msgspec <https://jcristharif.com/msgspec/>`_ for
serialisation and is noticeably faster, but it **does not** support a type
registry.  All messages are encoded and decoded as plain dicts — no custom
class round-trips.  Use it when speed matters and you only pass primitive
values or dicts as message content.

.. code-block:: python

    from mango.messages.codecs import FastJson
    from mango import create_tcp_container

    container = create_tcp_container(addr=("127.0.0.1", 5555), codec=FastJson())


Protobuf codec
==============

The :class:`~mango.PROTOBUF` codec wraps each protobuf message in a generic
envelope that carries the type ID alongside the serialised bytes.  This is
necessary because the original type of a protobuf message cannot be inferred
from its serialised form alone.

Register a serialiser by providing an encode function (returns a protobuf
message with ``SerializeToString``) and a decode function:

.. code-block:: python

    from mango import PROTOBUF
    import my_proto_pb2

    codec = PROTOBUF()
    codec.add_serializer(
        my_proto_pb2.MyMessage,
        lambda obj: obj,                             # already a proto message
        lambda data: my_proto_pb2.MyMessage.FromString(data),
    )

ACLMessage
----------

:class:`~mango.create_acl` creates FIPA-compliant
:class:`~mango.messages.message.ACLMessage` objects.  The protobuf codec
gives special treatment to ``ACLMessage``: the ``content`` field can hold any
protobuf message known to the codec and is encoded with its associated type ID,
just like a top-level message.

.. seealso::

    :doc:`message exchange` — sending and receiving messages
