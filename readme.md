# mango

[PyPi](https://pypi.org/project/mango-agents/) | [Read the Docs](https://mango-agents.readthedocs.io)
| [Github](https://github.com/OFFIS-DAI/mango) | [mail](mailto:mango@offis.de)

**Note:** _This project is still in an early development stage. 
We appreciate constructive feedback and suggestions for improvement._

mango (**m**odul**a**r pytho**n** a**g**ent framew**o**rk) is a python library for multi-agent systems (MAS).
It is written on top of asyncio and is released under the MIT license.

mango allows the user to create simple agents with little effort and in the same time offers options 
to structure agents with complex behaviour.

The main features of mango are:
 - Container mechanism to speedup local message exchange
 - Message definition based on the FIPA ACL standard
 - Structuring complex agents with loose coupling and agent roles
 - Built-in codecs: JSON and protobuf
 - Supports communication between agents directly via TCP or via an external MQTT broker

A detailed documentation for this project can be found at [mango-agents.readthedocs.io](https://mango-agents.readthedocs.io)

## Installation

*mango* requires Python >= 3.8 and runs on Linux, OSX and Windows.

For installation of mango you should use
[virtualenv](https://virtualenv.pypa.io/en/latest/#) which can create isolated Python environments for different projects.

It is also recommended to install
[virtualenvwrapper](https://virtualenvwrapper.readthedocs.io/en/latest/index.html)
which makes it easier to manage different virtual environments.

Once you have created a virtual environment you can just run [pip](https://pip.pypa.io/en/stable/) to install it:

    $ pip install mango-agents

## Getting started

####Creating an agent

In our first example, we'll create a very simple agent that simply prints the content of
all messages it receives:

```python

    from mango import Agent

    class RepeatingAgent(Agent):
        def __init__(self, container):
            # We must pass a ref of the container to "mango.Agent":
            super().__init__(container)
            print(f"Hello world! My id is {self.aid}.")

        def handle_message(self, content, meta):
            # This method defines what the agent will do with incoming messages.
            print(f"Received a message with the following content: {content}")
```
Agents must be a subclass of `Agent`. This base class needs
a reference to the container the agents live in, so you must forward
a *container* argument to it if you override `__init__()`.

####Creating a container

Agents live in a container, so we need to know how to create a mango container.
The container is responsible for message exchange between agents.

```python3

    # Containers need to be started via a factory function.
    # This method is a coroutine so it needs to be called using the
    # await statement
    first_container = await create_container(addr=('localhost', 5555))
```

This is how a container is created. Since the method `create_container()` is a
[coroutine](https://docs.python.org/3.9/library/asyncio-task.html) we need to await its result. 

#### Running your first agent within a container

To put it all together we will wrap the creation of a container and the agent into a coroutine
and execute it using `asyncio.run()`.
The following script will create a RepeatingAgent
and let it run within a container for three seconds and
then shutdown the container:

```python

    import asyncio
    from mango import Agent, create_container

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
        await first_agent.shutdown()
        await first_container.shutdown()

    asyncio.run(run_container_and_agent(addr=('localhost', 5555), duration=3))
```
The only output you should see is `Hello world! My id is agent0.`, because
the agent does yet not receive any messages.

#### Creating a proactive Agent

Let's implement another agent that is able to send a hello world message
to another agent:

```python3

    from mango import Agent
    from mango.util.scheduling import InstantScheduledTask

        class HelloWorldAgent(Agent):
            def __init__(self, container, other_addr, other_id):
                super().__init__(container)
                self.schedule_instant_acl_message(
                    receiver_addr=other_addr,
                    receiver_id=other_id,
                    content="Hello world!")
                )

            def handle_message(self, content, meta):
                print(f"Received a message with the following content: {content}")
```
#### Connecting two agents
We can now connect an instance of a HelloWorldAgent with an instance of a RepeatingAgent and run them.
```python
import asyncio
from mango import Agent, create_container
from mango.util.scheduling import InstantScheduledTask


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
            content="Hello world!")
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


def test_second_example():
    asyncio.run(run_container_and_two_agents(
        first_addr=('localhost', 5555), second_addr=('localhost', 5556))
    )
    
```

You should now see the following output:

`Hello world! My id is agent0.`

`Received a message with the following content: Hello world!`

You have now successfully created two agents and connected them.

## Support
- Documentation: [mango-agents.readthedocs.io](https://mango-agents.readthedocs.io)
- E-mail: [mango@offis.de](mailto:mango@offis.de)

## License

Distributed under the MIT license. 

[comment]: <> (##TODO  Release History * 0.0.1 First TCPContainer with json)
[comment]: <> (* 0.0.2 * Added MQTTContainer and protobuf support  )

