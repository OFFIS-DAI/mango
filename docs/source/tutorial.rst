==============
Tutorial
==============

***************
Introduction
***************

This tutorial gives an overview of the basic functions of mango agents and containers. It consists of four
parts building a scenario of two PV plants, operated by their respective agents being directed by a remote
controller.

Subsequent parts either extend the functionality or simplify some concept in the previous part.

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

For your first mango tutorial, you will learn the fundamentals of creating mango agents and containers as well
as making them communicate with each other.

This example covers:
    - container
    - agent creation
    - basic message passing
    - clean shutdown of containers

First, we want to create two simple agents and have the container send a message to one of them.
An agent is created by defining a class that inherits from the base Agent class of mango.
Every agent must implement the :meth:`mango.Agent.handle_message` method to which incoming messages are forwarded by the container.

.. testcode::

    from mango import Agent

    class PVAgent(Agent):
        def __init__(self):
            super().__init__()
            print("Hello I am a PV agent!")

        def handle_message(self, content, meta):
            print(f"Received message with content: {content} and meta {meta}.")

    PVAgent()

.. testoutput::

    Hello I am a PV agent!

Now we are ready to instantiate our system. mango is fundamentally built on top of asyncio and a lot of its functions
are provided as coroutines.
This means, practically every mango executable file will implement some variation of this
pattern:

.. testcode::

    import asyncio

    async def main():
        print("This will run in the asyncio loop.")
        # do whatever here

    asyncio.run(main())

.. testoutput::

    This will run in the asyncio loop.

First, we create the container. A tcp container is created via the :meth:`mango.create_tcp_container` function which requires at least
the address of the container as a parameter. Other container types available by using :meth:`mango.create_mqtt_container` and :meth:`mango.create_ec_container`.
For this tutorial we will cover the tcp container.

.. testcode::

    from mango import create_tcp_container

    PV_CONTAINER_ADDRESS = ("127.0.0.1", 5555)

    pv_container = create_tcp_container(addr=PV_CONTAINER_ADDRESS)

    print(pv_container.addr)

.. testoutput::

    ('127.0.0.1', 5555)

Now we can create our agents. Agents always live inside a container and therefore need to be registered to the container.

.. testcode::

    # agents always live inside a container
    async def main():
        pv_agent_0 = pv_container.register(PVAgent())
        pv_agent_1 = pv_container.register(PVAgent())

        print(pv_agent_1.addr)

    asyncio.run(main())

.. testoutput::

    Hello I am a PV agent!
    Hello I am a PV agent!
    AgentAddress(protocol_addr=('127.0.0.1', 5555), aid='agent1')

For now, our agents and containers are purely passive entities. First, we need to activate the container to start
the tcp server and its internal asynchronous behavior. In mango this can be done with :meth:`mango.activate` and the `async with` syntax.
Second, we need to send a message from one agent to the other. Messages are passed by the container via the :meth:`mango.Agent.send_message`
function always at least expects some content and a target agent address. To send a message directly to an agent, we also need to provide
its agent id which is set by the container when the agent is created. The address of the container and the aid
is wrapped in the :class:`mango.AgentAddress` class and can be retrieved with :meth:`mango.Agent.addr`.

.. testcode::

    from mango import activate

    # agents always live inside a container
    async def main():
        pv_agent_0 = pv_container.register(PVAgent())
        pv_agent_1 = pv_container.register(PVAgent())

        async with activate(pv_container) as c:
            # we can now send a simple message to an agent and observe that it is received:
            # Note that as of now agent IDs are set automatically as agent0, agent1, ...
            # in order of instantiation.
            await pv_agent_0.send_message(
                "Hello, this is a simple message.",
                receiver_addr=pv_agent_1.addr
            )

    asyncio.run(main())

.. testoutput::

    Hello I am a PV agent!
    Hello I am a PV agent!
    Received message with content: Hello, this is a simple message. and meta {'sender_id': 'agent2', 'sender_addr': ('127.0.0.1', 5555), 'receiver_id': 'agent3', 'network_protocol': 'tcp', 'priority': 0}.


*********************************
2. Messaging between Containers
*********************************

In the previous example, you learned how to create mango agents and containers and how to send basic messages between them.
In this example, you expand upon this. We introduce a controller agent that asks the current feed_in of our PV agents and
subsequently limits the output of both to their minimum.

This example covers:
    - message passing between different containers
    - basic task scheduling
    - setting custom agent ids
    - use of metadata

First, we define our controller Agent. To ensure it can message the pv agents we pass that information
directly to it in the constructor. The control agent will send out messages to each pv agent, await their
replies and act according to that information. To handle this, we also add some control structures to the
constructor that we will later use to keep track of which agents have already answered our messages.
As an additional feature, we will make it possible to manually set the agent of our agents by.


.. testcode::

    from mango import Agent, addr

    class ControllerAgent(Agent):
        def __init__(self, known_agents):
            super().__init__()

            self.known_agents = known_agents
            self.reported_feed_ins = []
            self.reported_acks = 0
            self.reports_done = None
            self.acks_done = None

    print(ControllerAgent([addr("protocol_addr", "aid")]).known_agents)

.. testoutput::

    [AgentAddress(protocol_addr='protocol_addr', aid='aid')]

Next, we set up its :meth:`mango.Agent.handle_message` function. The controller needs to distinguish between two message types:
The replies to feed_in requests and later the acknowledgements that a new maximum feed_in was set by a pv agent.
We assign the key `performative` of the metadata of the message to do this. We set the `performative` entry to `inform`
for feed_in replies and to `accept_proposal` for feed_in change acknowledgements.

.. testcode::

    class ControllerAgent(Agent):
        def __init__(self, known_agents):
            super().__init__()

            self.known_agents = known_agents
            self.reported_feed_ins = []
            self.reported_acks = 0
            self.reports_done = None
            self.acks_done = None

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

We do the same for our PV agents.

.. testcode::

    from mango import sender_addr

    PV_FEED_IN = {
        "PV Agent 0": 2.0,
        "PV Agent 1": 1.0,
    }

    class PVAgent(Agent):
        def __init__(self):
            super().__init__()

            self.max_feed_in = -1

        def handle_message(self, content, meta):
            performative = meta["performative"]
            sender = sender_addr(meta)

            if performative == Performatives.request:
                # ask_feed_in message
                self.handle_ask_feed_in(sender)
            elif performative == Performatives.propose:
                # set_max_feed_in message
                self.handle_set_feed_in_max(content, sender)
            else:
                print(f"{self.aid}: Received an unexpected message with content {content} and meta {meta}")

        def handle_ask_feed_in(self, sender):
            reported_feed_in = PV_FEED_IN[self.aid]  # PV_FEED_IN must be defined at the top
            content = reported_feed_in

            self.schedule_instant_message(
                content=content,
                receiver_addr=sender,
                performative=Performatives.inform
            )

        def handle_set_feed_in_max(self, max_feed_in, sender):
            self.max_feed_in = float(max_feed_in)
            print(f"{self.aid}: Limiting my feed_in to {max_feed_in}")

            self.schedule_instant_message(
                content=None,
                receiver_addr=sender,
                performative=Performatives.accept_proposal,
            )


When a PV agent receives a request from the controller, it immediately answers. Note two important changes to the first
example here: First, within our message handling methods we can not ``await send_message`` directly
because ``handle_message`` is not a coroutine. Instead, we pass ``send_message`` as a task to the scheduler to be
executed at once via the ``schedule_instant_task`` method.
Second, we set ``meta`` to contain the typing information of our message.

Now both of our agents can handle their respective messages. The last thing to do is make the controller actually
perform its active actions. We do this by implementing a ``run`` function with the following control flow:
- send a feed_in request to each known pv agent
- wait for all pv agents to answer
- find the minimum reported feed_in
- send a maximum feed_in setpoint of this minimum to each pv agent
- again, wait for all pv agents to reply
- terminate

.. testcode::

    class ControllerAgent(Agent):
        def __init__(self, known_agents):
            super().__init__()

            self.known_agents = known_agents
            self.reported_feed_ins = []
            self.reported_acks = 0
            self.reports_done = None
            self.acks_done = None

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

        async def run(self):
            # we define an asyncio future to await replies from all known pv agents:
            self.reports_done = asyncio.Future()
            self.acks_done = asyncio.Future()

            # ask pv agent feed-ins
            for addr in self.known_agents:
                self.schedule_instant_message(
                    content=None,
                    receiver_addr=addr,
                    performative=Performatives.request
                )

            # wait for both pv agents to answer
            await self.reports_done

            # deterministic output
            self.reported_feed_ins.sort()

            # limit both pv agents to the smaller ones feed-in
            print(f"{self.aid}: received feed_ins: {self.reported_feed_ins}")
            min_feed_in = min(self.reported_feed_ins)

            for addr in self.known_agents:
                content = min_feed_in

                self.schedule_instant_message(
                    content=content,
                    receiver_addr=addr,
                    performative=Performatives.propose
                )

            # wait for both pv agents to acknowledge the change
            await self.acks_done

Lastly, we call all relevant instantiations and the run function within our main coroutine:

.. testcode::

    from mango import create_tcp_container, activate, Performatives

    PV_CONTAINER_ADDRESS = ("127.0.0.1", 5555)
    CONTROLLER_CONTAINER_ADDRESS = ("127.0.0.1", 5556)
    PV_FEED_IN = {
        'PV Agent 0': 2.0,
        'PV Agent 1': 1.0,
    }

    async def main():
        pv_container = create_tcp_container(addr=PV_CONTAINER_ADDRESS)
        controller_container = create_tcp_container(addr=CONTROLLER_CONTAINER_ADDRESS)

        # agents always live inside a container
        pv_agent_0 = pv_container.register(PVAgent(), suggested_aid='PV Agent 0')
        pv_agent_1 = pv_container.register(PVAgent(), suggested_aid='PV Agent 1')

        # We pass info of the pv agents addresses to the controller here directly.
        # In reality, we would use some kind of discovery mechanism for this.
        known_agents = [
            pv_agent_0.addr,
            pv_agent_1.addr,
        ]

        controller_agent = controller_container.register(ControllerAgent(known_agents), suggested_aid='Controller')

        async with activate(pv_container, controller_container) as cl:
            # the only active component in this setup
            await controller_agent.run()

    asyncio.run(main())

.. testoutput::

    Controller: received feed_ins: [1.0, 2.0]
    PV Agent 0: Limiting my feed_in to 1.0
    PV Agent 1: Limiting my feed_in to 1.0


*******************************************
3. Using Codecs to simplify Message Types
*******************************************

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

We want to use the types of custom message objects as the new mechanism for message typing. We define these
as simple data classes. For simple classes like this, we can use the :meth:`mango.json_serializable`` decorator to
provide us with the serialization functionality.

.. testcode::

    from mango import json_serializable
    from dataclasses import dataclass

    @json_serializable
    @dataclass
    class AskFeedInMsg:
        pass


    @json_serializable
    @dataclass
    class FeedInReplyMsg:
        feed_in: int


    @json_serializable
    @dataclass
    class SetMaxFeedInMsg:
        max_feed_in: int


    @json_serializable
    @dataclass
    class MaxFeedInAck:
        pass

Next, we need to create a codec, make our message objects known to it, and pass it to our containers.

.. testcode::

    from mango import JSON

    PV_CONTAINER_ADDRESS = ("127.0.0.1", 5555)
    CONTROLLER_CONTAINER_ADDRESS = ("127.0.0.1", 5556)

    my_codec = JSON()
    my_codec.add_serializer(*AskFeedInMsg.__serializer__())
    my_codec.add_serializer(*SetMaxFeedInMsg.__serializer__())
    my_codec.add_serializer(*FeedInReplyMsg.__serializer__())
    my_codec.add_serializer(*MaxFeedInAck.__serializer__())

    pv_container = create_tcp_container(addr=PV_CONTAINER_ADDRESS, codec=my_codec)

    controller_container = create_tcp_container(
        addr=CONTROLLER_CONTAINER_ADDRESS, codec=my_codec
    )

Any time the content of a message matches one of these types now the corresponding serialize and deserialize
functions are called. Of course, you can also create your own serialization and deserialization functions with
more sophisticated behaviours and pass them to the codec. For more details refer to the :doc:`codecs` section of
the documentation.

With this, the message handling in our agent classes can be simplified:

.. testcode::

    class ControllerAgent(Agent):
        def __init__(self, known_agents):
            super().__init__()
            self.known_agents = known_agents
            self.reported_feed_ins = []
            self.reported_acks = 0
            self.reports_done = None
            self.acks_done = None

        def handle_message(self, content, meta):
            if isinstance(content, FeedInReplyMsg):
                self.handle_feed_in_reply(content.feed_in)
            elif isinstance(content, MaxFeedInAck):
                self.handle_set_max_ack()
            else:
                print(f"{self.aid}: Received a message of unknown type {type(content)}")

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

        async def run(self):
            # we define an asyncio future to await replies from all known pv agents:
            self.reports_done = asyncio.Future()
            self.acks_done = asyncio.Future()

            # ask pv agent feed-ins
            for addr in self.known_agents:
                msg = AskFeedInMsg()

                # alternatively we could call send_acl_message here directly and await it
                self.schedule_instant_message(
                    content=msg,
                    receiver_addr=addr,
                )

            # wait for both pv agents to answer
            await self.reports_done

            # deterministic output
            self.reported_feed_ins.sort()

            # limit both pv agents to the smaller ones feed-in
            print(f"{self.aid}: received feed_ins: {self.reported_feed_ins}")
            min_feed_in = min(self.reported_feed_ins)

            for addr in self.known_agents:
                msg = SetMaxFeedInMsg(min_feed_in)

                # alternatively we could call send_acl_message here directly and await it
                self.schedule_instant_message(
                    content=msg,
                    receiver_addr=addr
                )

            # wait for both pv agents to acknowledge the change
            await self.acks_done

    class PVAgent(Agent):
        def __init__(self):
            super().__init__()

            self.max_feed_in = -1

        def handle_message(self, content, meta):
            sender = sender_addr(meta)

            if isinstance(content, AskFeedInMsg):
                self.handle_ask_feed_in(sender)
            elif isinstance(content, SetMaxFeedInMsg):
                self.handle_set_feed_in_max(content.max_feed_in, sender)
            else:
                print(f"{self.aid}: Received a message of unknown type {type(content)}")

        def handle_ask_feed_in(self, sender_addr):
            reported_feed_in = PV_FEED_IN[self.aid]  # PV_FEED_IN must be defined at the top
            msg = FeedInReplyMsg(reported_feed_in)

            self.schedule_instant_message(
                content=msg,
                receiver_addr=sender_addr
            )

        def handle_set_feed_in_max(self, max_feed_in, sender_addr):
            self.max_feed_in = float(max_feed_in)
            print(f"{self.aid}: Limiting my feed_in to {max_feed_in}")
            msg = MaxFeedInAck()

            self.schedule_instant_message(
                content=msg,
                receiver_addr=sender_addr,
            )

.. testcode::

    async def main():
        # agents always live inside a container
        pv_agent_0 = pv_container.register(PVAgent(), suggested_aid='PV Agent 0')
        pv_agent_1 = pv_container.register(PVAgent(), suggested_aid='PV Agent 1')

        # We pass info of the pv agents addresses to the controller here directly.
        # In reality, we would use some kind of discovery mechanism for this.
        known_agents = [
            pv_agent_0.addr,
            pv_agent_1.addr,
        ]

        controller_agent = controller_container.register(ControllerAgent(known_agents),
                                                            suggested_aid='Controller')

        async with activate(pv_container, controller_container) as cl:
            # the only active component in this setup
            await controller_agent.run()

    asyncio.run(main())

.. testoutput::

    Controller: received feed_ins: [1.0, 2.0]
    PV Agent 0: Limiting my feed_in to 1.0
    PV Agent 1: Limiting my feed_in to 1.0

This concludes the third part of our tutorial.

*************************
4. Scheduling and Roles
*************************

In example 3, you restructured your code to use codecs for easier handling of typed message objects.
Now it is time to expand the functionality of our controller. In addition to setting the maximum feed_in
of the pv agents, the controller should now also periodically check if the pv agents are still reachable.

To achieve this, the controller should send a regular "ping" message to each pv agent that is in turn answered
by a corresponding "pong". Periodic tasks can be handled for you by mango's scheduling API.

With the introduction of this task, we know have different responsibilities for the agents
(e. g. act as PVAgent and reply to ping requests). In order to facilitate structuring an agent with different
responsibilities we can use the role API.
The idea of using roles is to divide the functionality of an agent by responsibility in a structured way.

A role is a python object that can be assigned to a RoleAgent. There are several lifecycle functions each role may implement:
    - ``__init__`` - where you do the initial object setup
    - :meth:`mango.Role.setup` - which is called when the role is assigned to an agent
    - :meth:`mango.Role.on_start` - which is called when the container is started
    - :meth:`mango.Role.on_ready` - which is called when are activated

This distinction is relevant because not all features exist after construction with ``__init__``. Most of the time
you want to implement :meth:`mango.Role.on_ready` for actions like message sending, or scheduling, because only
since this point you can be sure that all relevant container are started and the agent the role belongs to has been registered.
However, the setup of the role itself should be done in :meth:`mango.Role.setup`.

This example covers:
    - role API basics
    - scheduling and periodic tasks

The key part of defining roles are their ``__init__``, :meth:`mango.Role.setup`, and :meth:`mango.Role.on_ready` methods.
The first is called to create the role object. The second is called when the role is assigned to
an agent. While the third is called when all containers are started using :meth:`mango.activate`.
In our case, the main change is that the previous distinction of message types within `handle_message` is now done
by subscribing to the corresponding message type to tell the agent it should forward these messages
to this role.
The :meth:`mango.RoleContext.subscribe_message` method expects, besides the role and a handle method, a message condition function.
The idea of the condition function is to allow to define a condition filtering incoming messages.
Another idea is that sending messages from the role is now done via its context with the method:
``self.context.send_message```.

We first create the `Ping` role, which has to periodically send out its messages.
We can use mango's scheduling API to handle
this for us via the :meth:`mango.RoleContext.schedule_periodic_task` function. This takes a coroutine to execute and a time
interval. Whenever the time interval runs out the coroutine is triggered. With the scheduling API you can
also run tasks at specific times. For a full overview we refer to the documentation.

.. testcode::

    import asyncio
    from dataclasses import dataclass

    from mango import sender_addr, Role, RoleAgent, JSON, create_tcp_container, json_serializable, agent_composed_of

    PV_CONTAINER_ADDRESS = ("127.0.0.1", 5555)
    CONTROLLER_CONTAINER_ADDRESS = ("127.0.0.1", 5556)
    PV_FEED_IN = {
        "PV Agent 0": 2.0,
        "PV Agent 1": 1.0,
    }

    @json_serializable
    @dataclass
    class Ping:
        ping_id: int

    @json_serializable
    @dataclass
    class Pong:
        pong_id: int

    class PingRole(Role):
        def __init__(self, ping_recipients, time_between_pings):
            super().__init__()
            self.ping_recipients = ping_recipients
            self.time_between_pings = time_between_pings
            self.ping_counter = 0
            self.expected_pongs = []

        def setup(self):
            self.context.subscribe_message(
                self, self.handle_pong, lambda content, meta: isinstance(content, Pong)
            )

        def on_ready(self):
            self.context.schedule_periodic_task(self.send_pings, self.time_between_pings)

        async def send_pings(self):
            for addr in self.ping_recipients:
                ping_id = self.ping_counter
                msg = Ping(ping_id)

                await self.context.send_message(
                    msg,
                    receiver_addr=addr,
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
    print(Ping(1).ping_id)
    print(Pong(1).pong_id)
    print(PingRole(["addr"], 1).ping_recipients)

.. testoutput::

    1
    1
    ['addr']

The ControllerRole now covers the former responsibilities of the controller:

.. testcode::

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

        def on_ready(self):
            self.context.schedule_instant_task(self.run())

        def handle_feed_in_reply(self, content, meta):
            feed_in_value = float(content.feed_in)

            self.reported_feed_ins.append(feed_in_value)
            if len(self.reported_feed_ins) == len(self.known_agents):
                if self.reports_done is not None:
                    self.reports_done.set_result(True)

        def handle_set_max_ack(self, content, meta):
            self.reported_acks += 1
            if self.reported_acks == len(self.known_agents):
                if self.acks_done is not None:
                    self.acks_done.set_result(True)

        async def run(self):
            # we define an asyncio future to await replies from all known pv agents:
            self.reports_done = asyncio.Future()
            self.acks_done = asyncio.Future()

            # ask pv agent feed-ins
            for addr in self.known_agents:
                msg = AskFeedInMsg()

                await self.context.send_message(
                    content=msg,
                    receiver_addr=addr
                )

            # wait for both pv agents to answer
            await self.reports_done

            # limit both pv agents to the smaller ones feed-in
            print(f"Controller received feed_ins: {self.reported_feed_ins}")
            min_feed_in = min(self.reported_feed_ins)

            for addr in self.known_agents:
                msg = SetMaxFeedInMsg(min_feed_in)

                await self.context.send_message(
                    content=msg,
                    receiver_addr=addr,
                )

            # wait for both pv agents to acknowledge the change
            await self.acks_done

    print(ControllerRole([]).known_agents)

.. testoutput::

    []

The ``Pong`` role is associated with the PV Agents and purely reactive.

.. testcode::

    class PongRole(Role):
        def setup(self):
            self.context.subscribe_message(
                self, self.handle_ping, lambda content, meta: isinstance(content, Ping)
            )

        def handle_ping(self, content, meta):
            ping_id = content.ping_id
            answer = Pong(ping_id)

            print(f"Ping {self.context.aid}: Received a ping with ID: {ping_id}")

            # message sending from roles is done via the RoleContext
            self.context.schedule_instant_message(
                    answer,
                    receiver_addr=sender_addr(meta)
            )

    print(type(PongRole()))

.. testoutput::

    <class 'PongRole'>

Since the PV Agent is purely reactive, its other functionality stays basically
unchanged and is simply moved to the PVRole.

.. testcode::

    class PVRole(Role):
        def __init__(self):
            super().__init__()
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
            reported_feed_in = PV_FEED_IN[
                self.context.aid
            ]
            msg = FeedInReplyMsg(reported_feed_in)

            self.context.schedule_instant_message(
                content=msg,
                receiver_addr=sender_addr(meta)
            )

        def handle_set_feed_in_max(self, content, meta):
            max_feed_in = float(content.max_feed_in)
            self.max_feed_in = max_feed_in
            print(f"{self.context.aid}: Limiting my feed_in to {max_feed_in}")

            msg = MaxFeedInAck()

            self.context.schedule_instant_message(
                content=msg,
                receiver_addr=sender_addr(meta),
            )
    print(PVRole().max_feed_in)

.. testoutput::

    -1

The definition of the agent classes itself now simply boils down to using the function :meth:`mango.agent_composed_of`.
The following shows the fully rewriten PV/Controller example featuring the newly introduced Ping function.

.. testcode::

    async def main():
        my_codec = JSON()
        my_codec.add_serializer(*AskFeedInMsg.__serializer__())
        my_codec.add_serializer(*SetMaxFeedInMsg.__serializer__())
        my_codec.add_serializer(*FeedInReplyMsg.__serializer__())
        my_codec.add_serializer(*MaxFeedInAck.__serializer__())

        # dont forget to add our new serializers
        my_codec.add_serializer(*Ping.__serializer__())
        my_codec.add_serializer(*Pong.__serializer__())

        pv_container = create_tcp_container(addr=PV_CONTAINER_ADDRESS, codec=my_codec)

        controller_container = create_tcp_container(
            addr=CONTROLLER_CONTAINER_ADDRESS, codec=my_codec
        )

        pv_agent_0 = agent_composed_of(PongRole(), PVRole(),
                                        register_in=pv_container,
                                        suggested_aid="PV Agent 0")
        pv_agent_1 = agent_composed_of(PongRole(), PVRole(),
                                        register_in=pv_container,
                                        suggested_aid="PV Agent 1")

        known_agents = [
            pv_agent_0.addr,
            pv_agent_1.addr,
        ]

        controller_agent = agent_composed_of(PingRole(known_agents, 2), ControllerRole(known_agents),
                                             register_in=pv_container, suggested_aid="Controller")

        async with activate(controller_container, pv_container) as cl:
            # no more run call since everything now happens automatically within the roles
            await asyncio.sleep(5)

    asyncio.run(main())

.. testoutput::

    Ping PV Agent 0: Received a ping with ID: 0
    Ping PV Agent 1: Received a ping with ID: 1
    Pong Controller: Received an expected pong with ID: 0
    Pong Controller: Received an expected pong with ID: 1
    Controller received feed_ins: [2.0, 1.0]
    PV Agent 0: Limiting my feed_in to 1.0
    PV Agent 1: Limiting my feed_in to 1.0
    Ping PV Agent 0: Received a ping with ID: 2
    Ping PV Agent 1: Received a ping with ID: 3
    Pong Controller: Received an expected pong with ID: 2
    Pong Controller: Received an expected pong with ID: 3
    Ping PV Agent 0: Received a ping with ID: 4
    Ping PV Agent 1: Received a ping with ID: 5
    Pong Controller: Received an expected pong with ID: 4
    Pong Controller: Received an expected pong with ID: 5
