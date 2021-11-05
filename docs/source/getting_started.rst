========
Getting started
========
In this section you will
***************
Creating your first agent
***************

The following code will instantiate a container
and a simple agent, which prints the string representation of the content of all incoming matches:

.. code-block:: python3

    from mango.core.agent import Agent


    class RepeatingAgent(Agent):
        def __init__(self, container):
            super().__init__(container)

        def handle_msg(self, content, meta):
            print(f"Received a message with the following content: {content}")

***************
Creating your first container
***************

In order to let an agent run, it has to be located within a container.
The following code will create a simple container that is running on 'localhost' and listening on port 5555.

.. code-block:: python3

    from mango.core.container import Container


    first_container = asyncio.run(await Container.factory(addr=('localhost', 5555))

***************
Running your first agent within a container
***************

The following script will create a RepeatingAgent and let it run within a container for three seconds and
then shutdown the container:

.. code-block:: python3

    from mango.core.agent import Agent
    from mango.core.container import Container

        class RepeatingAgent(Agent):
            def __init__(self, container):
                super().__init__(container)

            def handle_msg(self, content, meta: Dict[str, Any]):
                print(f"Received a message with the following content: {content}")

        async def init_and_run_container_and_agent():
            first_container = await Container.factory(addr=('localhost', 5555))
            first_agent = RepeatingAgent(first_container)
            await asyncio.sleep(3)
            await first_container.shutdown()

        asyncio.run(init_and_run_container_and_agent())

There should be no outputs as there are no messages that your fist agent has received.

***************
Exchanging messages between agents
***************

Let's implement another agent that sends a hello world message to another agent:

.. code-block:: python3

    from mango.core.agent import Agent, Container
        class HelloWorldAgent(Agent):
            def __init__(self, container, other_addr, other_id):
                super().__init__(container)
                asyncio.create_task(self._container.send_message(
                    receiver_addr=other_addr,
                    receiver_id=other_id,
                    content="Hello world!",
                    create_acl=True)
                )

            def handle_msg(self, content, meta: Dict[str, Any]):
                print(f"Received a message with the following content: {content}")



