<p align="center">

![logo](docs/source/_static/Logo_mango_ohne_sub.svg#gh-light-mode-only)
![logo](docs/source/_static/Logo_mango_ohne_sub_white.svg#gh-dark-mode-only)

</p>

[PyPi](https://pypi.org/project/mango-agents/) | [Read the Docs](https://mango-agents.readthedocs.io)
| [Github](https://github.com/OFFIS-DAI/mango) | [mail](mailto:mango@offis.de)

![lifecycle](https://img.shields.io/badge/lifecycle-maturing-blue.svg)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/OFFIS-DAI/mango/blob/development/LICENSE)
[![Test mango-python](https://github.com/OFFIS-DAI/mango/actions/workflows/test-mango.yml/badge.svg)](https://github.com/OFFIS-DAI/mango/actions/workflows/test-mango.yml)
[![codecov](https://codecov.io/gh/OFFIS-DAI/mango/graph/badge.svg?token=6KVKBICGYG)](https://codecov.io/gh/OFFIS-DAI/mango)



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

For installation of mango you may use
[virtualenv](https://virtualenv.pypa.io/en/latest/#) which can create isolated Python environments for different projects.

Once you have created a virtual environment you can just run [pip](https://pip.pypa.io/en/stable/) to install it:

    $ pip install mango-agents

## Getting started

### Creating an agent

In our first example, we create a very simple agent that simply prints the content of
all messages it receives:

```python

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

```
Agents must be a subclass of `mango.Agent`. Agent's are notified when they are registered `mango.Agent.on_register`
and when the container(s) has been activated `mango.Agent.on_ready`. Consequenty, most agent features (like scheduling,
sending internal messages, the agent address) are available after registration, and only after `mango.Agent.on_ready` has
been called, all features are available (sending external messages).


### Creating a container


Agents live in containers, so we need to know how to create a mango container.
The container is responsible for message exchange between agents.

```python

    from mango import create_tcp_container

    # Containers have to be created using a factory method
    # Other container types are available through create_mqtt_container and create_ec_container
    container = create_tcp_container(addr=('127.0.0.1', 5555))
```


This is how a tcp container is created. While container creation, it is possible to set the codec, the address information (depending on the type) and the clock (see read the docs `Scheduling`).

### Running your first agent within a container


The container and the contained agents need `asyncio` (see `asyncio docs <https://docs.python.org/3.10/library/asyncio.html>`_) to work, therefore we need write a coroutine
function and execute it using `asyncio.run`.

The following script will create a RepeatingAgent, register it, and let it run within a container for 50ms and then shutdown the container:


```python

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

    asyncio.run(run_container_and_agent(addr=('127.0.0.1', 5555), duration=0.05))

```
In this example no messages are sent, nor does the Agent do anything, but the call order of the hook-in functions is clearly visible.
The function `mango.activate` will start the container and shut it down after the
code in its scope has been execute (here, after the sleep).

### Creating a proactive Agent

Let's implement another agent that is able to send a hello world message
to another agent:

```python

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

    asyncio.run(run_container_and_agent(addr=('127.0.0.1', 5555), duration=0.05))

```

If you do not want to await sending the message, and just let asyncio/mango schedule it, you can use `mango.Agent.schedule_instant_message` instead of
`mango.Agent.send_message`.

## Support
- Documentation: [mango-agents.readthedocs.io](https://mango-agents.readthedocs.io)
- E-mail: [mango@offis.de](mailto:mango@offis.de)

## License

Distributed under the MIT license.
