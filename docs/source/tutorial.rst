========
First tutorial
========
This lesson follows the getting started and gives you a more detailed introduction into *mango*
by creating a more complex scenario.

***************
Introduction and scenario overview
***************

In this section you will learn step by step to create a simple multi-agent system using *mango*.
This scenario consists of 2 agents each connected to a PV system. Thereby electrical energy is
delivered to a power gird. However, the maximum feed-in to the grid is limited. Thus the total feed-in of the
two PV systems have to be adjusted constantly.
Therefore, a controller monitors the feed in and regulates it as needed.

Next the example introduces a load plan to be fulfilled. The two plants should now always provide as much electrical
energy that this load plan is fulfilled.
The fulfillment ist done by our adapted controller.

Finally, in the case that the two PV systems do not provide enough electricity, a CHP unit is introduced. This also
provides electricity to the net, if necessary.

The following step by step guide introduces all components of mango and gives an overview about the message exchange.
However this tutorial does not implement fancy simulation models for the PV and CHP units, these are only
simulated using random numbers,
since the main focus of this tutorial is on the multi-agent framework *mango* and the message exchange.

***************
1. Creating two PV agents
***************

As already mentioned this scenario consists of 2 agents connected to PV systems.
The electrical energy is delivered to a power grid which connects all entities in theis scenario.
A simulation of the power grid is not part of this scenario, this would go beyond the scope here,
we just cover the maximum fed-in in the next step.

The PV Agent is defined as follows:

.. code-block:: python3

    class PVAgent(Agent):
        def __init__(self, container):
            # We must pass a reference of the container to "mango.Agent":
            super().__init__(container)
            self.conversations = []
            print(f"Hello I am a PV agent! My id is {self._aid}.")


        def handle_msg(self, content, meta):
            """
            decide which actions shall be performed as reaction to message
            :param content: the content of the message
            :param meta: meta information
            """
            print(f"Received message: {content} with meta {meta}")

            t = asyncio.create_task(self.react_to_get_feed_in(content, meta))
            t.add_done_callback(self.raise_exceptions)

TODO rest of the code

Now we have two PV agents generated. At the moment, these stand on their own.
To connect them we introduce a controller.

***************
2. Creating a controller agent
***************
The maximum feed-in to the grid is limited. Thus the total feed-in of the two PV systems have to be adjusted constantly.
Therefore, a controller monitors the feed in and regulates it as needed.
For this purpose, the controller monitors the output of the PV systems and regulates
them if necessary to remain within the maximum.



.. code-block:: python3

    class ControllerAgent(Agent):
        def __init__(self, container, other_aid, other_addr):
            # We must pass a reference of the container to "mango.Agent":
            super().__init__(container)
            self.other_aid = other_aid
            self.other_addr = other_addr
            self.conversations = []
            print(f"Hello I am a controller agent! My id is {self.aid}.")
            self.schedule_instant_task(coroutine=self._container.send_message(
                receiver_addr=other_addr,
                receiver_id=other_aid,
                content="Hello I am a controller agent!",
                create_acl=True)
            )

***************
Creating the responding container
***************
The container is responsible for the message exchange between agents.
As we already know from the getting started agents live in container, so we create a *mango*
container for the two PV agents and the container agent.


.. code-block:: python3
    from mango.core.container import Container

    async def run_container_and_controller():
        # As we already know Containers need to be started via a factory function.
        # This method is a coroutine, so it needs to be called from a coroutine using the await statement
        # create one container + the controller

        pv_and_controller_container = await Container.factory(addr=('localhost', 5555))

        first_pv_agent = PVAgent(pv_and_controller_container)
        second_pv_agent = PVAgent(pv_and_controller_container)
        controller_agent = ControllerAgent(pv_and_controller_container, other_aid=first_pv_agent.aid, other_addr=pv_and_controller_container.addr)

        await controller_agent.get_feed_in()
        # await controller_agent.get_feed_in(pv_and_controller_container.addr, second_pv_agent.aid)

        await asyncio.sleep(1)
        if len(controller_agent.conversations) == 0:
            print("starting shutdowns")
            await first_pv_agent.shutdown()
            await second_pv_agent.shutdown()
            await controller_agent.shutdown()
            await pv_and_controller_container.shutdown()


This is how a container is created. Since the method :py:meth:`Container.factory()` is a
coroutine__ we need to await its result.

This is the first time we can start our little simulation.
After running, you should see the following output in the console.

.. line-block::
    `Hello I am a PV agent! My id is agent0.`
    `Hello I am a PV agent! My id is agent1.`
    `Hello I am a controller agent! My id is agent2.`


You have now successfully created three agents and connected them.

***************
3. Creating a load plan agent
***************
To make the message exchange a bit more sophisticated, we now want to introduce a load plan.
This consists of a current supply that should always be fulfilled.

For this purpose, a feed-in is specified that should be fulfilled by the PV systems.
Which plant contributes how much is not relevant for us here.
We are only interested in the fulfillment, as long as the maximum feed-in from the second step is considered.



.. code-block:: python3

    class LoadPlanAgent(Agent):
        def __init__(self, container):
            # We must pass a reference of the container to "mango.Agent":
            super().__init__(container)
            self.conversations = []
            print(f"Hello I am a LoadPlanAgent agent! My id is {self.aid}.")

        async def react_to_get_loadplan(self, msg_in, meta_in):
            conversation_id = meta_in['conversation_id']
            reply_by = meta_in['reply_by']
            sender_id = meta_in['sender_id']
            sender_addr = meta_in['sender_addr']
            loadplan = np.random.uniform(2.0, 20.0)

            # prepare a reply
            if conversation_id not in self.conversations:
                self.conversations.append(conversation_id)
            if reply_by == 'get_loadplan':
                # answer
                message_out_content = f"My loadplan is: {loadplan}"
            elif reply_by == 'end':
                # end received, send good bye
                message_out_content = 'Good Bye'
                # end conversation
                reply_key = None
                self.conversations.remove(conversation_id)
            else:
                assert False, f'got strange reply_by: {reply_by}'
            message = ACLMessage_json(
                sender_id=self.aid,
                sender_addr=self.addr_str,
                receiver_id=sender_id,
                receiver_addr=sender_addr,
                content=message_out_content,
                in_reply_to=reply_by,
                reply_by=reply_key,
                conversation_id=conversation_id,
                performative=Performatives.inform)
            self.agent_logger.debug(f'Going to send {message}')
            await self._container.send_message(message, sender_addr)

            if len(self.conversations) == 0:
                await self.shutdown()

        def handle_msg(self, content, meta):
            """
            decide which actions shall be performed as reaction to message
            :param content: the content of the message
            :param meta: meta information
            """
            print(f'Received message: {content} with meta {meta}')

            t = asyncio.create_task(self.send_loadplan())
            t.add_done_callback(self.raise_exceptions)




***************
4. Creating a CHP-agent
***************
In this step we introduce a CHP as an additional component..

The CHP is located in a second container. Due to the second container, data is exchanged between the two containers.
For this reason we introduce codecs in this step.
Codecs enable the container to encode and decode known data types to send them as messages.
The *mango* json serializer that can recursively handle any json serializable object.

.. code-block:: python3

    class CHPAgent(Agent):
        def __init__(self, container):
            # We must pass a reference of the container to "mango.Agent":
            super().__init__(container)
            print(f"Hello I am a CHP agent! My id is {self._aid}.")

        def handle_msg(self, content, meta):
            # This method defines what the agent will do with incoming messages.
            print(f"Received a message with the following content: {content}")



***************
5. Adapting our controller
***************
In the last step, we introduced a load plan to be fulfilled and a CHP. For this we now have to adapt our controller.
This again takes over the control logic.

In this step we introduce the feature roles and scheduling.