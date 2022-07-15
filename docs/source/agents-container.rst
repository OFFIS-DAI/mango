========
Agents and container
========
In order to speed up message exchange between agents that run on the same physical hardware,
agents live in a ``container``.
Agents living in one container can exchange messages without having to send it through the network.
A container is responsible for the message distribution (sending and receiving) of its agents.

***************
mango container
***************

In mango, a container is created using the classmethod ``mango.core.container.Container.factory``:

.. code-block:: python3

    @classmethod
    async def factory(cls, *, connection_type: str = 'tcp', codec: Codec = None, clock: Clock = None,
                      addr: Optional[Union[str, Tuple[str, int]]] = None,
                      proto_msgs_module=None,
                      mqtt_kwargs: Dict[str, Any] = None):

The factory method is a coroutine, so it has to be scheduled within a running asyncio loop.
A simple container, that uses plain tcp for message exchange can be created as follows:

.. code-block:: python3

    import asyncio
    from mango.container import Container

    async def get_simple_container():
        container = await Container.factory(addr=('localhost', 5555))
        return container

    simple_container = asyncio.run(get_simple_container()))

A container can be parametrized regarding its connection type ('tcp' or 'MQTT') and
regarding the codec that is used for message serialization.
The default codec is JSON (see section codecs for more information). It is also possible to
define the clock that an agents scheduler should use (see section scheduling).

After a container is created, it is waiting for incoming messages on the given address.
As soon as the container has some agents, it will distribute incoming messages
to the corresponding agents and allow agents to send messages to other agents.

At the end of its lifetime, a ``container`` should be shutdown by using the method ``shutdown()``.
It will then shutdown all agents that are still running
in this container and cancel running tasks.

***************
mango agents
***************
mango agents can be implemented by inheriting from the abstract class ``mango.core.agent.Agent``.
This class provides basic functionality such as to register the agent at the container or
to constantly check the inbox for incoming messages.
Every agent lives in exactly one container and therefore an instance of a container has to be
provided when :py:meth:`__init__()` of an agent is called.
Custom agents that inherit from the ``Agent`` class have to call ``super().__init__(container)__``
on initialization.
This will register the agent at the provided container instance and will assign a unique agent id
(``self._aid``) to the agent.
It will also create the task to check for incoming messages.
