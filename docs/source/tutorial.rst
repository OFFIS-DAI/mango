==============
mango tutorial
==============

***************
Introduction
***************

This tutorial gives an overview of the basic functions of mango agents and containers. It consists of four
parts building a scenario of two PV plants, operated by an their respective agents being directed by a remote
controller. 

Each part comes with a standalone executable file. Subsequent parts either extend the functionality or simplify 
some concept in the previous part. 

As a whole, this tutorial covers:
    - container and agent creation
    - message passing within a container
    - message passing between containers
    - codecs
    - scheduling
    - roles


*****************************
1. Setup and Message Passing
*****************************

Corresponding file: `v1_basic_setup_and_message_passing.py`

For your first mango tutorial you will learn the fundamentals of creating mango agents and containers as well
as making them communicate with each other.

This example covers:
    - container
    - agent creation
    - basic message passing
    - clean shutdown of containers

.. raw:: html

   <details>
   <summary><a>step by step</a></summary>

First, we want to create two simple agents and have the container send a message to one of them.
An agent is created by defining a class that inherits from the base Agent class of mango.
Every agent must implement the ``handle_msg`` method to which incoming messages are forwarded by the container.

.. code-block:: python

    from mango.core.agent import Agent

    class PVAgent(Agent):
        def __init__(self, container):
            super().__init__(container)
            print(f"Hello I am a PV agent! My id is {self._aid}.")

        def handle_msg(self, content, meta):
            print(f"Received message with content: {content} and meta {meta}.")

Now we are ready to instantiate our system. Mango is fundamentally built in asyncio and a lot of its functions are 
provided as coroutines. This means, practically every mango executable file will implement some variation of this
pattern:

.. code-block:: python

    import asyncio

    async def main():
        # do whatever here

    if __name__ == "__main__":
        asyncio.run(main())

First, we create the container. A container is created via the ``container.factory`` coroutine which requires at least
the address of the container as a parameter.

.. code-block:: python

    PV_CONTAINER_ADDRESS = ("localhost", 5555)

    # defaults to tcp connection
    pv_container = await Container.factory(addr=PV_CONTAINER_ADDRESS)


Now we can create our agents. Agents always live inside a container and this container must be passed to their constructor.

.. code-block:: python

    # agents always live inside a container
    pv_agent_1 = PVAgent(pv_container)
    pv_agent_2 = PVAgent(pv_container)

For now, our agents are purely passive entities. To make them do something, we need to send them a message. Messages are 
passed by the container via the ``send_message`` function always at least expects some content and a target address.
To send a message directly to an agent, we also need to provide its agent id which is set by the container when the agent
is created. 

.. code-block:: python

     # we can now send a simple message to an agent and observe that it is received:
    # Note that as of now agent IDs are set automatically as agent0, agent1, ... in order of instantiation.
    await pv_container.send_message(
        "Hello, this is a simple message.",
        receiver_addr=PV_CONTAINER_ADDRESS,
        receiver_id="agent0",
    )

Finally, you should always cleanly shut down your containers before your program terminates.

.. code-block:: python

    # don't forget to properly shut down containers at the end of your program
    # otherwise you will get an asyncio.exceptions.CancelledError
    await pv_container.shutdown()

This concludes the first part of our tutorial. If you run this code, you should receive the following output:

    | Hello I am a PV agent! My id is agent0.
    | Hello I am a PV agent! My id is agent1.
    | Received message with content: Hello, this is a simple message. and meta {'network_protocol': 'tcp', 'priority': 0}.
   

.. raw:: html

   </details>

*********************************
2. Messaging between Containers
*********************************

Corresponding file: `v2_inter_container_messaging_and_basic_functionality.py`

In the previous example you learned how to create mango agents and containers and how to send basic messages between them.
In this example you expand upon this. We introduce a controller agent that asks the current feed_in of our PV agents and
subsequently limits the output of both to the minimum of the two.

This example covers:
    - message passing between different containers
    - basic task scheduling
    - use of ACL metadata

*******************************************
1. Using Codecs to simplity Message Types
*******************************************

Corresponding file: `v3_codecs_and_typing.py`

In example 2 you created some basic agent functionality and established inter-container communication.
Message types were distinguished by a corresponding field in the content dictionary. This approach is 
tedious and prone to error. A better way is to use dedicated message objects and using their types to distinguish
messages. Objects can be encoded for messaging between agents by mangos codecs. To make a new object type
known to a codec it needs to provide a serialization and a deserialization method. The object type together
with these methods is then passed to the codec which in turn is passed to a container. The container will then
automatically use these methods when it encounters an object of this type as the content of a message.

This example covers:
    - message classes
    - codec basics
    - the json_serializable decorator

.. raw:: html

   <details>
   <summary><a>step by step</a></summary>

.. raw:: html

   </details>

*************************
1. Scheduling and Roles
*************************

Corresponding file: `v4_scheduling_and_roles.py`

In example 3 you restructured your code to use codecs for easier handling of typed message objects.
Now it is time to expand the functionality of our controller. In addition to setting the maximum feed_in 
of the pv agents, the controller should now also periodically check if the pv agents are still reachable.

To achieve this, the controller should seend a regular "ping" message to each pv agent that is in turn answered
by a corresponding "pong". Periodic tasks can be handled for you by mangos scheduling API.
Additionally, to serparate different responsibilities within agents, mango has a role system where each role 
covers the functionalities of a responsibility.

A role is a python object that can be assigned to a RoleAgent. The two main functions each role implements are:
    - __init__ - where you do the initial object setup
    - setup - which is called when the role is assigned to an agent

This distinction is relevant because only within `setup` the RoleContext (i.e. access to the parent agent and container) exist.
Thus, things like message handlers that require container knowledge are introduced there.

This example covers:
    - scheduling and periodic tasks
    - role API basics

.. raw:: html

   <details>
   <summary><a>step by step</a></summary>

.. raw:: html

   </details>