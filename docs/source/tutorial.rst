==============
Tutorial
==============

***************
Introduction
***************

This tutorial gives an overview of the basic functions of mango agents and containers. It consists of four
parts building a scenario of two PV plants, operated by their respective agents being directed by a remote
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

For your first mango tutorial, you will learn the fundamentals of creating mango agents and containers as well
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
Every agent must implement the ``handle_message`` method to which incoming messages are forwarded by the container.

.. code-block:: python

    from mango import Agent

    class PVAgent(Agent):
        def __init__(self, container):
            super().__init__(container)
            print(f"Hello I am a PV agent! My id is {self.aid}.")

        def handle_message(self, content, meta):
            print(f"Received message with content: {content} and meta {meta}.")

Now we are ready to instantiate our system. mango is fundamentally built on top of asyncio and a lot of its functions
are provided as coroutines.
This means, practically every mango executable file will implement some variation of this
pattern:

.. code-block:: python

    import asyncio

    async def main():
        # do whatever here

    if __name__ == "__main__":
        asyncio.run(main())

First, we create the container. A container is created via the ``mango.create_container`` coroutine which requires at least
the address of the container as a parameter.

.. code-block:: python

    PV_CONTAINER_ADDRESS = ("localhost", 5555)

    # defaults to tcp connection
    pv_container = await create_container(addr=PV_CONTAINER_ADDRESS)


Now we can create our agents. Agents always live inside a container and this container must be passed to their constructor.

.. code-block:: python

    # agents always live inside a container
    pv_agent_0 = PVAgent(pv_container)
    pv_agent_1 = PVAgent(pv_container)

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

In the previous example, you learned how to create mango agents and containers and how to send basic messages between them.
In this example, you expand upon this. We introduce a controller agent that asks the current feed_in of our PV agents and
subsequently limits the output of both to their minimum.

This example covers:
    - message passing between different containers
    - basic task scheduling
    - setting custom agent ids
    - use of ACL metadata

.. raw:: html

   <details>
   <summary><a>step by step</a></summary>

First, we define our controller Agent. To ensure it can message the pv agents we pass that information
directly to it in the constructor. The control agent will send out messages to each pv agent, await their
replies and act according to that information. To handle this, we also add some control structures to the
constructor that we will later use to keep track of which agents have already answered our messages.
As an additional feature, we will make it possible to manually set the agent of our agents by.


.. code-block:: python

    class ControllerAgent(Agent):
        def __init__(self, container, known_agents, suggested_aid=None):
            super().__init__(container, suggested_aid=suggested_aid)
            self.known_agents = known_agents
            self.reported_feed_ins = []
            self.reported_acks = 0
            self.reports_done = None
            self.acks_done = None

Next, we set up its ``handle_message`` function. The controller needs to distinguish between two message types:
The replies to feed_in requests and later the acknowledgements that a new maximum feed_in was set by a pv agent.
We use the ``performative`` field of the ACL message to do this. We set the ``performative`` field to ``inform``
for feed_in replies and to ``accept_proposal`` for feed_in change acknowledgements. All messages between containers
are expected to be ACL Messages (or implement the split_content_and_meta function). Since we do not want to create
the full ACL object ourselves every time, within this example we always use the convenience method 
``container.send_acl_message``, which also supports setting the acl metadata.

.. code-block:: python

    class ControllerAgent(Agent):
        """..."""

        def handle_message(self, content, meta):
            performative = meta['performative']
            if performative == Performatives.inform:
                # feed_in_reply message
                self.handle_feed_in_reply(content)
            elif performative == Performatives.accept_proposal:
                # set_max_ack message
                self.handle_set_max_ack()
            else:
                print(f"{self.aid}: Received an unexpected message  with content {content} and meta {meta}")

        def handle_feed_in_reply(self, feed_in_value):
            self.reported_feed_ins.append(float(feed_in_value))
            if len(self.reported_feed_ins) == len(self.known_agents):
                if self.reports_done is not None:
                    self.reports_done.set_result(True)

        def handle_set_max_ack(self):
            self.reported_acks += 1
            if self.reported_acks == len(self.known_agents):
                if self.acks_done is not None:
                    self.acks_done.set_result(True)

We do the same for our PV agents. We will also enable user defined agent ids here.

.. code-block:: python

    class PVAgent(Agent):
        def __init__(self, container, suggested_aid=None):
            super().__init__(container, suggested_aid=suggested_aid)
            self.max_feed_in = -1

        def handle_message(self, content, meta):
            performative = meta["performative"]
            sender_addr = meta["sender_addr"]
            sender_id = meta["sender_id"]

            if performative == Performatives.request:
                # ask_feed_in message
                self.handle_ask_feed_in(sender_addr, sender_id)
            elif performative == Performatives.propose:
                # set_max_feed_in message
                self.handle_set_feed_in_max(content, sender_addr, sender_id)
            else:
                print(f"{self.aid}: Received an unexpected message with content {content} and meta {meta}")


When a PV agent receives a request from the controller, it immediately answers. Note two important changes to the first
example here: First, within our message handling methods we can not ``await send_acl_message`` directly
because ``handle_message`` is not a coroutine. Instead, we pass ``send_acl_message`` as a task to the scheduler to be
executed at once via the ``schedule_instant_task`` method.
Second, we set ``acl_meta`` to contain the typing information of our message.

.. code-block:: python

    class PVAgent(Agent):
        """..."""

        def handle_ask_feed_in(self, sender_addr, sender_id):
            reported_feed_in = PV_FEED_IN[self.aid]  # PV_FEED_IN must be defined at the top
            content = reported_feed_in

            acl_meta = {"sender_addr": self.addr, "sender_id": self.aid,
                        "performative": Performatives.inform}

            # Note, could be shortened using self.schedule_instant_acl_message
            self.schedule_instant_task(
                self.send_acl_message(
                    content=content,
                    receiver_addr=sender_addr,
                    receiver_id=sender_id,
                    acl_metadata=acl_meta,
                )
            )

        def handle_set_feed_in_max(self, max_feed_in, sender_addr, sender_id):
            self.max_feed_in = float(max_feed_in)
            print(f"{self.aid}: Limiting my feed_in to {max_feed_in}")
            self.schedule_instant_task(
                self.send_acl_message(
                    content=None,
                    receiver_addr=sender_addr,
                    receiver_id=sender_id,
                    acl_metadata={'performative': Performatives.accept_proposal},
                )
            )

Now both of our agents can handle their respective messages. The last thing to do is make the controller actually
perform its active actions. We do this by implementing a ``run`` function with the following control flow:
- send a feed_in request to each known pv agent
- wait for all pv agents to answer
- find the minimum reported feed_in
- send a maximum feed_in setpoint of this minimum to each pv agent 
- again, wait for all pv agents to reply
- terminate

.. code-block:: python

    class ControllerAgent(Agent):
        """..."""

        async def run(self):
            # we define an asyncio future to await replies from all known pv agents:
            self.reports_done = asyncio.Future()
            self.acks_done = asyncio.Future()

            # Note: For messages passed between different containers (i.e. over the network socket) it is expected
            # that the message is an ACLMessage object. We can let the container wrap our content in such an
            # object with using the send_acl_message method.
            # We distinguish the types of messages we send by adding a type field to our content.

            # ask pv agent feed-ins
            for addr, aid in self.known_agents:
                content = None
                acl_meta = {"sender_addr": self.addr, "sender_id": self.aid,
                            "performative": Performatives.request}
                # alternatively we could call send_acl_message here directly and await it
                self.schedule_instant_task(
                    self.send_acl_message(
                        content=content,
                        receiver_addr=addr,
                        receiver_id=aid,
                        acl_metadata=acl_meta,
                    )
                )

            # wait for both pv agents to answer
            await self.reports_done

            # limit both pv agents to the smaller ones feed-in
            print(f"{self.aid}: received feed_ins: {self.reported_feed_ins}")
            min_feed_in = min(self.reported_feed_ins)

            for addr, aid in self.known_agents:
                content = min_feed_in
                acl_meta = {"sender_addr": self.addr, "sender_id": self.aid,
                            "performative": Performatives.propose}

                # alternatively we could call send_acl_message here directly and await it
                self.schedule_instant_task(
                    self.send_acl_message(
                        content=content,
                        receiver_addr=addr,
                        receiver_id=aid,
                        acl_metadata=acl_meta,
                    )
                )

            # wait for both pv agents to acknowledge the change
            await self.acks_done

Lastly, we call all relevant instantiations and the run function within our main coroutine:

.. code-block:: python

    PV_CONTAINER_ADDRESS = ("localhost", 5555)
    CONTROLLER_CONTAINER_ADDRESS = ("localhost", 5556)
    PV_FEED_IN = {
        'PV Agent 0': 2.0,
        'PV Agent 1': 1.0,
    }

    async def main():
        pv_container = await create_container(addr=PV_CONTAINER_ADDRESS)
        controller_container = await create_container(addr=CONTROLLER_CONTAINER_ADDRESS)

        # agents always live inside a container
        pv_agent_0 = PVAgent(pv_container, suggested_aid='PV Agent 0')
        pv_agent_1 = PVAgent(pv_container, suggested_aid='PV Agent 1')

        # We pass info of the pv agents addresses to the controller here directly.
        # In reality, we would use some kind of discovery mechanism for this.
        known_agents = [
            (PV_CONTAINER_ADDRESS, pv_agent_0.aid),
            (PV_CONTAINER_ADDRESS, pv_agent_1.aid),
        ]

        controller_agent = ControllerAgent(controller_container, known_agents, suggested_aid='Controller')

        # the only active component in this setup
        await controller_agent.run()

        # always properly shut down your containers
        await pv_container.shutdown()
        await controller_container.shutdown()

    if __name__ == "__main__":
        asyncio.run(main())

This concludes the second part of our tutorial. If you run this code you should receive the following output:

    | Controller: received feed_ins: [2.0, 1.0]
    | PV Agent 0: Limiting my feed_in to 1.0
    | PV Agent 1: Limiting my feed_in to 1.0


.. raw:: html

   </details>

*******************************************
3. Using Codecs to simplify Message Types
*******************************************

Corresponding file: `v3_codecs_and_typing.py`

In example 2, you created some basic agent functionality and established inter-container communication.
Message types were distinguished by the performative field of the meta information. This approach is
tedious and prone to error. A better way is to use dedicated message objects and using their types to distinguish
messages.

If instances of custom classes are exchanged over the network (or generally between different containers),
these instances need to be serialized. In mango, objects can be encoded by mango's codecs. To make a new object type
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

We want to use the types of custom message objects as the new mechanism for message typing. We define these
as simple data classes. For simple classes like this, we can use the ``json_serializable`` decorator to 
provide us with the serialization functionality.

.. code-block:: python

    import mango.messages.codecs as codecs
    from dataclasses import dataclass

    @codecs.json_serializable
    @dataclass
    class AskFeedInMsg:
        pass


    @codecs.json_serializable
    @dataclass
    class FeedInReplyMsg:
        feed_in: int


    @codecs.json_serializable
    @dataclass
    class SetMaxFeedInMsg:
        max_feed_in: int


    @codecs.json_serializable
    @dataclass
    class MaxFeedInAck:
        pass

Next, we need to create a codec, make our message objects known to it, and pass it to our containers.

.. code-block:: python

    my_codec = codecs.JSON()
    my_codec.add_serializer(*AskFeedInMsg.__serializer__())
    my_codec.add_serializer(*SetMaxFeedInMsg.__serializer__())
    my_codec.add_serializer(*FeedInReplyMsg.__serializer__())
    my_codec.add_serializer(*MaxFeedInAck.__serializer__())

    pv_container = await create_container(addr=PV_CONTAINER_ADDRESS, codec=my_codec)

    controller_container = await create_container(
        addr=CONTROLLER_CONTAINER_ADDRESS, codec=my_codec
    )

Any time the content of a message matches one of these types now the corresponding serialize and deserialize
functions are called. Of course, you can also create your own serialization and deserialization functions with 
more sophisticated behaviours and pass them to the codec. For more details refer to the ``codecs`` section of
the documentation.

With this, the message handling in our agent classes can be simplified:

.. code-block:: python

    class ControllerAgent(Agent):
        """..."""

        def handle_message(self, content, meta):
            if isinstance(content, FeedInReplyMsg):
                self.handle_feed_in_reply(content.feed_in)
            elif isinstance(content, MaxFeedInAck):
                self.handle_set_max_ack()
            else:
                print(f"{self.aid}: Received a message of unknown type {type(content)}")


    class PVAgent(Agent):
        """..."""

        def handle_message(self, content, meta):
            sender_addr = meta["sender_addr"]
            sender_id = meta["sender_id"]

            if isinstance(content, AskFeedInMsg):
                self.handle_ask_feed_in(sender_addr, sender_id)
            elif isinstance(content, SetMaxFeedInMsg):
                self.handle_set_feed_in_max(content.max_feed_in, sender_addr, sender_id)
            else:
                print(f"{self.aid}: Received a message of unknown type {type(content)}")


This concludes the third part of our tutorial. If you run the code,
you should receive the same output as in part 2:

    | Controller: received feed_ins: [2.0, 1.0]
    | PV Agent 0: Limiting my feed_in to 1.0
    | PV Agent 1: Limiting my feed_in to 1.0

.. raw:: html

   </details>

*************************
4. Scheduling and Roles
*************************

Corresponding file: `v4_scheduling_and_roles.py`

In example 3, you restructured your code to use codecs for easier handling of typed message objects.
Now it is time to expand the functionality of our controller. In addition to setting the maximum feed_in 
of the pv agents, the controller should now also periodically check if the pv agents are still reachable.

To achieve this, the controller should send a regular "ping" message to each pv agent that is in turn answered
by a corresponding "pong". Periodic tasks can be handled for you by mango's scheduling API.

With the introduction of this task, we know have different responsibilities for the agents
(e. g. act as PVAgent and reply to ping requests). In order to facilitate structuring an agent with different
responsibilities we can use the role API.
The idea of using roles is to divide the functionality of an agent by responsibility in a structured way.

A role is a python object that can be assigned to a RoleAgent. The two main functions each role implements are:
    - __init__ - where you do the initial object setup
    - setup - which is called when the role is assigned to an agent

This distinction is relevant because only within `setup` the RoleContext (i.e. access to the parent agent and container) exist.
Thus, things like message handlers that require container knowledge are introduced there.

This example covers:
    - role API basics
    - scheduling and periodic tasks 

.. raw:: html

   <details>
   <summary><a>step by step</a></summary>

The key part of defining roles are their ``__init__`` and ``setup`` methods. The first is called to create the role object.
The second is called when the role is assigned to an agent. In our case, the main change is that the previous distinction
of message types within ``handle_message`` is now done by subscribing to the corresponding message type to tell the agent
it should forward these messages to this role.
The ``subscribe_message`` method expects, besides the role and a handle method, a message condition function.
The idea of the condition function is to allow to define a condition filtering incoming messages.
Another idea is that sending messages from the role is now done via its context with the method:
``self.context.send_acl_message``.

We first create the ``Ping`` role, which has to periodically send out its messages.
We can use mango's scheduling API to handle
this for us via the ``schedule_periodic_tasks`` function. This takes a coroutine to execute and a time
interval. Whenever the time interval runs out the coroutine is triggered. With the scheduling API you can
also run tasks at specific times. For a full overview we refer to the documentation.

.. code-block:: python

    from mango import Role

    class PingRole(Role):
        def __init__(self, ping_recipients, time_between_pings):
            self.ping_recipients = ping_recipients
            self.time_between_pings = time_between_pings
            self.ping_counter = 0
            self.expected_pongs = []

        def setup(self):
            self.context.subscribe_message(
                self, self.handle_pong, lambda content, meta: isinstance(content, Pong)
            )

            # this task is automatically executed every "time_between_pings" seconds
            self.context.schedule_periodic_task(self.send_pings, self.time_between_pings)

        async def send_pings(self):
            for addr, aid in self.ping_recipients:
                ping_id = self.ping_counter
                msg = Ping(ping_id)
                meta = {"sender_addr": self.context.addr, "sender_id": self.context.aid}

                await self.context.send_acl_message(
                    msg,
                    receiver_addr=addr,
                    receiver_id=aid,
                    acl_metadata=meta,
                )
                self.expected_pongs.append(ping_id)
                self.ping_counter += 1

        def handle_pong(self, content, meta):
            if content.pong_id in self.expected_pongs:
                print(
                    f"Pong {self.context.aid}: Received an expected pong with ID: {content.pong_id}"
                )
                self.expected_pongs.remove(content.pong_id)
            else:
                print(
                    f"Pong {self.context.aid}: Received an unexpected pong with ID: {content.pong_id}"
                )


The ControllerRole now covers the former responsibilities of the controller:

.. code-block:: python

    class ControllerRole(Role):
    def __init__(self, known_agents):
        super().__init__()
        self.known_agents = known_agents
        self.reported_feed_ins = []
        self.reported_acks = 0
        self.reports_done = None
        self.acks_done = None

    def setup(self):
        self.context.subscribe_message(
            self,
            self.handle_feed_in_reply,
            lambda content, meta: isinstance(content, FeedInReplyMsg),
        )

        self.context.subscribe_message(
            self,
            self.handle_set_max_ack,
            lambda content, meta: isinstance(content, MaxFeedInAck),
        )

        self.context.schedule_instant_task(self.run())

The methods ``handle_feed_in_reply``, ``handle_set_max_ack`` and ``run`` are also part of this role and
remain unchanged.

The ``Pong`` role is associated with the PV Agents and purely reactive.

.. code-block:: python

    class PongRole(Role):
        def setup(self):
            self.context.subscribe_message(
                self, self.handle_ping, lambda content, meta: isinstance(content, Ping)
            )

        def handle_ping(self, content, meta):
            ping_id = content.ping_id
            sender_addr = meta["sender_addr"]
            sender_id = meta["sender_id"]
            answer = Pong(ping_id)

            print(f"Ping {self.context.aid}: Received a ping with ID: {ping_id}")

            # message sending from roles is done via the RoleContext
            self.context.schedule_instant_task(
                self.context.send_acl_message(
                    answer,
                    receiver_addr=sender_addr,
                    receiver_id=sender_id,
                )
            )


Since the PV Agent is purely reactive, its other functionality stays basically
unchanged and is simply moved to the PVRole.

.. code-block:: python

    class PVRole(Role):
        def __init__(self):
            self.max_feed_in = -1

        def setup(self):
            self.context.subscribe_message(
                self,
                self.handle_ask_feed_in,
                lambda content, meta: isinstance(content, AskFeedInMsg),
            )
            self.context.subscribe_message(
                self,
                self.handle_set_feed_in_max,
                lambda content, meta: isinstance(content, SetMaxFeedInMsg),
            )

        def handle_ask_feed_in(self, content, meta):
            """..."""
        self.context.schedule_instant_task(
            self.context.send_acl_message(
                content=msg,
                receiver_addr=sender_addr,
                receiver_id=sender_id,
            )
        )

        def handle_set_feed_in_max(self, content, meta):
            """..."""
            self.context.schedule_instant_task(
                self.context.send_acl_message(
                    content=msg,
                    receiver_addr=sender_addr,
                    receiver_id=sender_id,
                )
            )


The definition of the agent classes itself now simply boils down to assigning it all the roles it has:

.. code-block:: python

    from mango import RoleAgent

    class PVAgent(RoleAgent):
        def __init__(self, container):
            super().__init__(container)
            self.add_role(PongRole())
            self.add_role(PVRole())

    class ControllerAgent(RoleAgent):
        def __init__(self, container, known_agents):
            super().__init__(container)
            self.add_role(PingRole(known_agents, 2))
            self.add_role(ControllerRole(known_agents))


This concludes the last part of our tutorial.
If you want to run the code, you don't need to await the run method of the controller anymore,
since everything now happens automatically within the roles.
In your ``main``, you can replace the line:

.. code-block:: python

        await controller_agent.run()

with the following line:

.. code-block:: python

        await asyncio.sleep(5)

If you then run this code, you should receive the following output:

    | Ping PV Agent 0: Received a ping with ID: 0
    | Ping PV Agent 1: Received a ping with ID: 1
    | Pong Controller: Received an expected pong with ID: 0
    | Pong Controller: Received an expected pong with ID: 1
    | Controller received feed_ins: [2.0, 1.0]
    | PV Agent 0: Limiting my feed_in to 1.0
    | PV Agent 1: Limiting my feed_in to 1.0
    | Ping PV Agent 0: Received a ping with ID: 2
    | Ping PV Agent 1: Received a ping with ID: 3
    | Pong Controller: Received an expected pong with ID: 2
    | Pong Controller: Received an expected pong with ID: 3
    | Ping PV Agent 0: Received a ping with ID: 4
    | Ping PV Agent 1: Received a ping with ID: 5
    | Pong Controller: Received an expected pong with ID: 4
    | Pong Controller: Received an expected pong with ID: 5

.. raw:: html

   </details>
