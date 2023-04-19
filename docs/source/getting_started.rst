========
Getting started
========
In this section you will get to know the necessary steps to create a simple multi-agent system
using *mango*. For an introduction to the different features that mango offers, we refer to the
:doc:`tutorial`.

***************
Creating an agent
***************
In our first example, we create a very simple agent that simply prints the content of
all messages it receives:

.. code-block:: python3

    from mango import Agent


    class RepeatingAgent(Agent):
        def __init__(self, container):
            # We must pass a reference of the container to "mango.Agent":
            super().__init__(container)
            print(f"Hello world! My id is {self.aid}.")

        def handle_message(self, content, meta):
            # This method defines what the agent will do with incoming messages.
            print(f"Received a message with the following content: {content}")

Agents must be a subclass of :class:`Agent`. This base class needs
a reference to the container that the agents live in, so you must forward
a *container* argument to it if you override ``__init__()``.

***************
Creating a container
***************

Agents live in a container, so we need to know how to create a mango container.
The container is responsible for message exchange between agents. More information about container and agents can be
found in :doc:`Agents and container<agents-container>`

.. code-block:: python3

    from mango import create_container
    # Containers need to be started via a factory function.
    # This method is a coroutine so it needs to be called from a coroutine using the
    # await statement
    async def get_container():
        return await create_container(addr=('localhost', 5555))

This is how a container is created. Since the method :py:meth:`create_container()` is a
coroutine__ we need to await its result.

__ https://docs.python.org/3.10/library/asyncio-task.html

***************
Running your first agent within a container
***************
To put it all together we will wrap the creation of a container and the agent into a coroutine
and execute it using :py:meth:`asyncio.run()`.
The following script will create a RepeatingAgent
and let it run within a container for three seconds and
then shutdown the container:

.. code-block:: python3

    import asyncio
    from mango import Agent
    from mango import create_container


    class RepeatingAgent(Agent):
        def __init__(self, container):
            # We must pass a ref. to the container to "mango.Agent":
            super().__init__(container)
            print(f"Hello world! My id is {self.aid}.")

        def handle_message(self, content, meta):
            # This method defines what the agent will do with incoming messages.
            print(f"Received a message with the following content: {content}")


    async def run_container_and_agent(addr, duration):
        first_container = await create_container(addr=addr)
        first_agent = RepeatingAgent(first_container)
        await asyncio.sleep(duration)
        await first_container.shutdown()

    asyncio.run(run_container_and_agent(addr=('localhost', 5555), duration=3))


The only output you should see is "Hello world! My id is agent0.", because
the agent does not receive any other messages.

***************
Creating a proactive Agent
***************

Let's implement another agent that is able to send a hello world message
to another agent:

.. code-block:: python3

    from mango import Agent

        class HelloWorldAgent(Agent):
            def __init__(self, container, other_addr, other_id):
                super().__init__(container)
                self.schedule_instant_task(coroutine=self.context.send_acl_message(
                    receiver_addr=other_addr,
                    receiver_id=other_id,
                    content="Hello world!")
                )

            def handle_message(self, content, meta):
                print(f"Received a message with the following content: {content}")

We are using the scheduling API, which is explained in further detail in the section :doc:`scheduling`.

***************
Connecting two agents
***************
We can now connect an instance of a HelloWorldAgent with an instance of
a RepeatingAgent and let them run.

.. code-block:: python3

    import asyncio
    from mango import Agent
    from mango import create_container


    class RepeatingAgent(Agent):
        def __init__(self, container):
            # We must pass a ref. to the container to "mango.Agent":
            super().__init__(container)
            print(f"Hello world! My id is {self.aid}.")

        def handle_message(self, content, meta):
            # This method defines what the agent will do with incoming messages.
            print(f"Received a message with the following content: {content}")


    class HelloWorldAgent(Agent):
        def __init__(self, container, other_addr, other_id):
            super().__init__(container)
            self.schedule_instant_acl_message(
                receiver_addr=other_addr,
                receiver_id=other_id,
                content="Hello world!"
            )

        def handle_message(self, content, meta):
            print(f"Received a message with the following content: {content}")


    async def run_container_and_two_agents(first_addr, second_addr):
        first_container = await create_container(addr=first_addr)
        second_container = await create_container(addr=second_addr)
        first_agent = RepeatingAgent(first_container)
        second_agent = HelloWorldAgent(second_container, first_container.addr, first_agent.aid)
        await asyncio.sleep(1)
        await first_agent.shutdown()
        await second_agent.shutdown()
        await first_container.shutdown()
        await second_container.shutdown()


    if __name__ == '__main__':
        asyncio.run(run_container_and_two_agents(
            first_addr=('localhost', 5555), second_addr=('localhost', 5556)))

You should now see the following output:

`Hello world! My id is agent0.`
`Received a message with the following content: Hello world!`

You have now successfully created two agents and connected them.
