========
Getting started
========
In this section you will step by step create a simple multi-agent system using *mango*

***************
Creating an agent
***************
In our first example, we'll create a very simple agent that simply prints the content of
all messages it receives:

.. code-block:: python3

    from mango.core.agent import Agent

    class RepeatingAgent(Agent):
        def __init__(self, container):
            # We must pass a ref. to the container to "mango.Agent":
            super().__init__(container)
            print(f"Hello world! My id is {self._aid}.")

        def handle_msg(self, content, meta):
            # This method defines what the agent will do with incoming messages.
            print(f"Received a message with the following content: {content}")

Agents must be a subclass of :class:`Agent`. This base class needs
a reference to the container the agents live in, so you must forward
a *container* argument to it if you override ``__init__()``.

***************
Creating a container
***************

Agents live in a container, so we need to know how to create a mango container.
The container is responsible for message exchange between agents.

.. code-block:: python3

    # Containers need to be started via a factory function.
    # This method is a coroutine so it needs to be called using the
    # await statement
    first_container = await Container.factory(addr=('localhost', 5555))

This is how a container is created. Since the method :py:meth:`Container.factory()` is a
coroutine__ we need to await its result.

__ https://docs.python.org/3.9/library/asyncio-task.html

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
    from mango.core.agent import Agent
    from mango.core.container import Container

    class RepeatingAgent(Agent):
            def __init__(self, container):
                # We must pass a ref. to the container to "mango.Agent":
                super().__init__(container)
                print(f"Hello world! My id is {self._aid}.")

            def handle_msg(self, content, meta):
                # This method defines what the agent will do with incoming messages.
                print(f"Received a message with the following content: {content}")

    async def run_container_and_agent(addr, duration):
        first_container = await Container.factory(addr=addr)
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

    from mango.core.agent import Agent

        class HelloWorldAgent(Agent):
            def __init__(self, container, other_addr, other_id):
                super().__init__(container)
                self.schedule_instant_task(coroutine=self._container.send_message(
                    receiver_addr=other_addr,
                    receiver_id=other_id,
                    content="Hello world!",
                    create_acl=True)
                )

            def handle_msg(self, content, meta: Dict[str, Any]):
                print(f"Received a message with the following content: {content}")





