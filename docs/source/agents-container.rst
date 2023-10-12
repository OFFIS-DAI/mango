========
Agents and container
========

***************
mango container
***************

In mango, agents live in a ``container``. The container is responsible for everything network related of the agent.
This includes in particular sending and receiving of messages, but also message distribution to the correct agent or
serialization and deserialization of messages.
Container also help to to speed up message exchange between agents that run on the same physical hardware,
as data that is exchanged between such agents will not have to be sent through the network.

In mango, a container is created using the classmethod ``mango.create_container``:

.. code-block:: python3

    @classmethod
    async def create_container(cls, *, connection_type: str = 'tcp', codec: Codec = None, clock: Clock = None,
                      addr: Optional[Union[str, Tuple[str, int]]] = None,
                      proto_msgs_module=None,
                      **kwargs):

The factory method is a coroutine, so it has to be scheduled within a running asyncio loop.
A simple container, that uses plain tcp for message exchange can be created as follows:

.. code-block:: python3

    import asyncio
    from mango import create_container

    async def get_simple_container():
        container = await create_container(addr=('localhost', 5555))
        return container

    simple_container = asyncio.run(get_simple_container()))

A container can be parametrized regarding its connection type ('tcp' or 'MQTT') and
regarding the codec that is used for message serialization.
The default codec is JSON (see section :doc:`codecs` for more information). It is also possible to
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
mango agents can be implemented by inheriting from the abstract class ``mango.Agent``.
This class provides basic functionality such as to register the agent at the container or
to constantly check the inbox for incoming messages.
Every agent lives in exactly one container and therefore an instance of a container has to be
provided when :py:meth:`__init__()` of an agent is called.
Custom agents that inherit from the ``Agent`` class have to call ``super().__init__(container, suggested_aid: str = None)__``
on initialization.
This will register the agent at the provided container instance and will assign a unique agent id
(``self.aid``) to the agent. However, it is possible to suggest an aid by setting the variable ``suggested_aid`` to your aid wish. 
The aid is granted if there is no other agent with this id, and if the aid doesn't interfere with the default aid pattern, otherwise 
the generated aid will be used. To check if the aid is available beforehand, you can use ``container.is_aid_available``.
It will also create the task to check for incoming messages.

***************
agent process
***************
To improve multicore utilization, mango provides a way to distribute agents to processes. For this, it is necessary to create and 
register the agent in a slightly different way.

.. code-block:: python3
    process_handle = await main_container.as_agent_process(
        agent_creator=lambda sub_container: TestAgent(
            container, aid_main_agent, suggested_aid=f"process_agent1"
        )
    )

The process_handle is awaitable and will finish exactly when the process is fully set up. Further, it contains the pid `process_handle.pid`.

Note that after the creation, the agent lives in a mirror container in another process. Therefore, it is not possible to interact
with the agent directly from the main process. If you want to interact with the agent after the creation, it is possible to
dispatch a task in the agent process using `dispatch_to_agent_process`. 

.. code-block:: python3
    main_container.dispatch_to_agent_process(
        pid,
        your_function, # will be called with the mirror container + x as arguments
        ... # varargs, additional arguments you want to pass to your_function
    )
