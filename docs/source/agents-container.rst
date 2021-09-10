========
Agents and container in general
========
You can think of agents as independent pieces of software running in parallel. Agents are able to perceive their environment, receive some input and create some output. In order to speed up message exchange between agents that run on the same physical hardware, agents usually live in ``container``. This structure allows agents from the same container to excahnge messages without having to send it thorugh the network. A container is responsible for everything regarding the message distribution between agents.

========
mango container
========
In mango container are created using the classmethod ``mango.core.container.Container.factory``:

.. code-block:: python3

    @classmethod
    async def factory(cls, *, connection_type: str = 'tcp', codec: str = 'json',
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

A container can be parametrized regarding its connection type ('tcp' or 'MQTT') and regaring its codec that is used to serialize the messages ('json' or 'protobuf'). More information regarding these topics can be found in XXX and YYY.

After a container is created, it is waiting for incoming messages on the given address. As soon as the container has some agents, it will distribute incoming messages to the corresponding agents and allow agents to send messages. 

========
mango agents
========
mango agents can be implemented by inheriting from the abstract class ``mango.core.agent.Agent``. This class provides
basic functionality such as to register the agent at the container or to constantly check the inbox for incoming messages. Every agent lives in exactly one container and therefore an instance of a container has to be provided when instanciating an agent.
Custom agents that inherit from the ``Agent`` class are able to receive messages from other agents via the method ``handle_message``. Hence this method has to be overwirtten. The structure of this method looks like this:

.. code-block:: python3

    @abstractmethod
    def handle_msg(self, content, meta: Dict[str, Any]):

        raise NotImplementedError



In the ``__init__`` function of any custom agent, ``super().__init__(container)__`` has to be called. This will register the agent at the provided instance of a ``container`` and will assign a unique agent id (``self._aid``) to the agent. It will also create the task to check for incoming messages.

A simple agent, that just prints incoming messages could look like this:

.. code-block:: python3

    from mango.core.agent import Agent

    class SimpleAgent(Agent):
        def __init__(self, container):
            super().__init__(container)

        def handle_msg(self, content, meta):
            print(f'{self._aid} received a message with content {content} and'
                f'meta {meta}')


========
receiving messages
========

