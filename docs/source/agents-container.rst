====================
Agents and container
====================

***************
mango container
***************

In mango, agents live in a ``container``. The container is responsible for everything network related of the agent.
This includes in particular sending and receiving of messages, but also message distribution to the correct agent or
serialization and deserialization of messages.
Container also help to to speed up message exchange between agents that run on the same physical hardware,
as data that is exchanged between such agents will not have to be sent through the network.

In mango, a container is created using factory methods:

* :meth:`mango.create_tcp_container`
* :meth:`mango.create_mqtt_container`
* :meth:`mango.create_ec_container`

Most of the time, the tcp container should be the default choice if you wanna create simulations, which run in real time using
a simple but fast network protocol.

.. code-block:: python3

    def create_tcp_container(
        addr: str | tuple[str, int],
        codec: Codec = None,
        clock: Clock = None,
        copy_internal_messages: bool = False,
        **kwargs: dict[str, Any],
    ) -> Container:

The factory methods are asyncio-free, meaning most of the time you can create containers without a running asyncio loop.

A simple container, that uses plain tcp for message exchange can be created as follows:

.. testcode::

    import asyncio
    from mango import create_tcp_container

    def get_simple_container():
        container = create_tcp_container(addr=('localhost', 5555))
        return container

    print(get_simple_container().addr)

.. testoutput::

    ('localhost', 5555)

The container type depends totally on the factory method you invoke. Every supported type has its own class backing
the functionality.

The default codec is JSON (see section :doc:`codecs` for more information). It is also possible to
define the clock that an agents scheduler should use (see page :doc:`scheduling`).

Note, that container creation is different from container starting. Before you can work with a container
you will want to register Agents, and then start (or activate) the container. This shall be done using an
asynchronous context manager, which we provide by invoking :meth:`mango.activate`.

.. testcode::

    import asyncio
    from mango import create_tcp_container, activate

    async def start_container():
        container = create_tcp_container(addr=('localhost', 5555))

        async with activate(container) as c:
            print("The container is activated now!")
            await asyncio.sleep(0.1) # activate the container for 0.1 seconds, most of the time you want to include e.g. a condition to await
        print("The container is automatically shut down, even on exceptions!")

    asyncio.run(start_container())

.. testoutput::

    The container is activated now!
    The container is automatically shut down, even on exceptions!

At the end of its lifetime, a ``container`` the container will shutdown. This will be done by the context manager, so no need for the
user to worry about it. This will also shutdown all agents that are still running in this container and cancel running tasks.

***************
mango agents
***************
mango agents can be implemented by inheriting from the abstract class ``mango.Agent``.
This class provides basic functionality such as to scheduling convenience methods or to constantly check the inbox for incoming messages.
Every agent can live in exactly one container, to register an agent the method :meth:`mango.Container.register` can be used. This method will assign
the agent a generated agent id (aid) and enables the agent scheduling feature.

However, it is possible to suggest an aid by setting the parameter ``suggested_aid`` of :meth:`mango.Container.register` to your aid wish.
The aid is granted if there is no other agent with this id, and if the aid doesn't interfere with the default aid pattern, otherwise
the generated aid will be used. To check if the aid is available beforehand, you can use ``container.is_aid_available``.

Note that, custom agents that inherit from the ``Agent`` class have to call ``super().__init__()__`` on initialization.

.. testcode::

    from mango import Agent, create_tcp_container

    class MyAgent(Agent):
        pass

    async def create_and_register_agent():
        container = create_tcp_container(addr=('localhost', 5555))

        agent = container.register(MyAgent(), suggested_aid="CustomAgent")
        return agent

    print(asyncio.run(create_and_register_agent()).aid)

.. testoutput::

    CustomAgent

Further there are some important lifecycle methods you often want to implement:

* :meth:`mango.Agent.on_ready`
   * Called when all containers have been activated during the activate call, which started the container the agent is registered in.
   * At this point all relevant containers have been started and the agent is already registered. This is the correct method for starting to send messages, even to other containers.
* :meth:`mango.Agent.on_register`
   * Called when the Agent just has been registered.
   * At this point the scheduler is initialized and the agent address is known, but no communication can happen yet.
* :meth:`mango.Agent.on_start`
   * Called when the container of the agent has been started during activation.
   * At this point internal communication is possible and depending on your setup external communication could be done too.

Besides the lifecycle, one of the main functions implemented in Agents are message exchange function. For this part read :doc:`/message exchange`.

*********************************
Express setup of mango simulation
*********************************

It is not necessary to create the container all by yourself, as you often want to just distribute some agents evenly to a number of containers. This can be done
with an asynchronous context manager created by :meth:`mango.run_with_tcp` (:meth:`mango.run_with_mqtt` for MQTT protocol). This method just expects the number of containers
you want to start and the agents, which shall run in these containers.

With this method sending a message to an agent in another container looks like this:

.. testcode::

    import asyncio
    from mango import PrintingAgent, run_with_tcp

    async def run_with_tcp_example():
        agent_tuple = (PrintingAgent(), dict(aid="MyAgent"))
        single_agent = PrintingAgent()

        async with run_with_tcp(2, agent_tuple, single_agent) as cl:
            # cl is the list of containers, which are created internally
            await agent_tuple[0].send_message("Hello, print me!", single_agent.addr)
            await asyncio.sleep(0.1)

    asyncio.run(run_with_tcp_example())

.. testoutput::

    Received: Hello, print me! with {'sender_id': 'MyAgent', 'sender_addr': ['127.0.0.1', 5555], 'receiver_id': 'agent0', 'network_protocol': 'tcp', 'priority': 0}

***************
agent process
***************
To improve multicore utilization, mango provides a way to distribute agents to processes. For this, it is necessary to create and
register the agent in a slightly different way.

.. code-block:: python3

    process_handle = await main_container.as_agent_process(
        agent_creator=lambda sub_container: sub_container.register(MyAgent(), suggested_aid=f"process_agent1")
    )

The ``process_handle`` is awaitable and will finish exactly when the process is fully set up. Further, it contains the pid ``process_handle.pid``.

Note that after the creation, the agent lives in a mirror container in another process. Therefore, it is not possible to interact
with the agent directly from the main process. If you want to interact with the agent after the creation, it is possible to
dispatch a task in the agent process using `dispatch_to_agent_process`.

.. code-block:: python3

    main_container.dispatch_to_agent_process(
        pid,
        your_function, # will be called with the mirror container + varargs as arguments
        ... # varargs, additional arguments you want to pass to your_function
    )
