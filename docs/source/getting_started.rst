
Getting started
===============
In this section you will get to know the necessary steps to create a simple multi-agent system
using *mango*. For an introduction to the different features that mango offers, we refer to the
:doc:`tutorial`.

*****************
Creating an agent
*****************
In our first example, we create a very simple agent that simply prints the content of
all messages it receives:

.. testcode::

    from mango import Agent

    class RepeatingAgent(Agent):

        def __init__(self):
            super().__init__()
            print(f"Creating a RepeatingAgent. At this point self.addr={self.addr}")

        def handle_message(self, content, meta):
            # This method defines what the agent will do with incoming messages.
            print(f"Received a message with the following content: {content}!")

        def on_register(self):
            print(f"The agent has been registered to a container: {self.addr}!")

        def on_ready(self):
            print("All containers have been activated!")

    RepeatingAgent()

.. testoutput::

    Creating a RepeatingAgent. At this point self.addr=None

Agents must be a subclass of :class:`mango.Agent`. Agent's are notified when they are registered :meth:`mango.Agent.on_register`
and when the container(s) has been activated :meth:`mango.Agent.on_ready`. Consequenty, most agent features (like scheduling,
sending internal messages, the agent address) are available after registration, and only after :meth:`mango.Agent.on_ready` has
been called, all features are available (sending external messages).

********************
Creating a container
********************

Agents live in containers, so we need to know how to create a mango container.
The container is responsible for message exchange between agents. More information about container and agents can be
found in :doc:`Agents and container <agents-container>`

.. testcode::

    from mango import create_tcp_container

    # Containers have to be created using a factory method
    # Other container types are available through create_mqtt_container and create_ec_container
    container = create_tcp_container(addr=('localhost', 5555))
    print(container.addr)

.. testoutput::

    ('localhost', 5555)


This is how a tcp container is created. While container creation, it is possible to set the codec, the address information (depending on the type)
and the clock (see :ref:`ClockDocs`).

*******************************************
Running your first agent within a container
*******************************************

The container and the contained agents need `asyncio` (see `asyncio docs <https://docs.python.org/3.10/library/asyncio.html>`_) to work, therefore we need write a coroutine
function and execute it using `asyncio.run`.

The following script will create a RepeatingAgent, register it, and let it run within a container for 50ms and then shutdown the container:

.. testcode::

    import asyncio
    from mango import create_tcp_container, Agent, activate

    class RepeatingAgent(Agent):
        def __init__(self):
            super().__init__()
            print(f"Creating a RepeatingAgent. At this point self.addr={self.addr}")

        def handle_message(self, content, meta):
            print(f"Received a message with the following content: {content}!")

        def on_register(self):
            print(f"The agent has been registered to a container: {self.addr}!")

        def on_ready(self):
            print("All containers have been activated!")


    async def run_container_and_agent(addr, duration):
        first_container = create_tcp_container(addr=addr)
        first_agent = first_container.register(RepeatingAgent())

        async with activate(first_container) as container:
            await asyncio.sleep(duration)

    asyncio.run(run_container_and_agent(addr=('localhost', 5555), duration=0.05))

.. testoutput::

    Creating a RepeatingAgent. At this point self.addr=None
    The agent has been registered to a container: AgentAddress(protocol_addr=('localhost', 5555), aid='agent0')!
    All containers have been activated!

In this example no messages are sent, nor does the Agent do anything, but the call order of the hook-in functions is clearly visible.
The function :py:meth:`mango.activate` will start the container and shut it down after the
code in its scope has been execute (here, after the sleep).

**************************
Creating a proactive Agent
**************************

Let's implement another agent that is able to send a hello world message
to another agent:

.. testcode::

    import asyncio
    from mango import Agent

    class HelloWorldAgent(Agent):
        async def greet(self, other_addr):
            await self.send_message("Hello world!", other_addr)

        def handle_message(self, content, meta):
            print(f"Received a message with the following content: {content}")

    async def run_container_and_agent(addr, duration):
        first_container = create_tcp_container(addr=addr)
        first_hello_agent = first_container.register(HelloWorldAgent())
        second_hello_agent = first_container.register(HelloWorldAgent())

        async with activate(first_container) as container:
            await first_hello_agent.greet(second_hello_agent.addr)

    asyncio.run(run_container_and_agent(addr=('localhost', 5555), duration=0.05))

.. testoutput::

    Received a message with the following content: Hello world!


If you do not want to await sending the message, and just let asyncio/mango schedule it, you can use :meth:`mango.Agent.schedule_instant_message` instead of
:meth:`mango.Agent.send_message`.

*********************
Connecting two agents
*********************
We can now connect an instance of a HelloWorldAgent with an instance of
a RepeatingAgent and let them run.

.. code-block:: python

    import asyncio
    from mango import Agent, create_tcp_container, activate


    class RepeatingAgent(Agent):
        def __init__(self, container):
            # We must pass a ref. to the container to "mango.Agent":
            super().__init__(container)
            print(f"Hello world! My id is {self.aid}.")

        def handle_message(self, content, meta):
            # This method defines what the agent will do with incoming messages.
            print(f"Received a message with the following content: {content}")

    class HelloWorldAgent(Agent):
        async def greet(self, other_addr):
            await self.send_message("Hello world!", other_addr)

        def handle_message(self, content, meta):
            print(f"Received a message with the following content: {content}")


    async def run_container_and_two_agents(first_addr, second_addr):
        first_container = create_tcp_container(addr=first_addr)
        second_container = create_tcp_container(addr=second_addr)

        first_agent = first_container.register(RepeatingAgent())
        second_agent = second_container.register(HelloWorldAgent())

        async with activate(first_container, second_container) as cl:
            await second_agent.greet(first_agent.addr)


    def test_second_example():
        asyncio.run(run_container_and_two_agents(
            first_addr=('localhost', 5555), second_addr=('localhost', 5556))
        )

You should now see the following output:

`Hello world! My id is agent0.`
`Received a message with the following content: Hello world!`

You have now successfully created two agents and connected them.
