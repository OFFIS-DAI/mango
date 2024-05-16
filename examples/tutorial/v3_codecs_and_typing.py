import asyncio
from dataclasses import dataclass

import mango.messages.codecs as codecs
from mango import Agent, create_container

"""
In example 2 we created some basic agent functionality and established inter-container communication.
To distinguish message types we used a corresponding field in our content dictionary. This approach is 
tedious and prone to error. A better way is to use dedicated message objects and using their types to distinguish
messages. Arbitrary objects can be encoded for messaging between agents by mangos codecs.

This example covers:
    - message classes
    - codec basics
    - the json_serializable decorator
"""

PV_CONTAINER_ADDRESS = ("localhost", 5555)
CONTROLLER_CONTAINER_ADDRESS = ("localhost", 5556)
PV_FEED_IN = {
    "PV Agent 0": 2.0,
    "PV Agent 1": 1.0,
}


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


class PVAgent(Agent):
    def __init__(self, container, suggested_aid=None):
        super().__init__(container, suggested_aid=suggested_aid)
        self.max_feed_in = -1

    def handle_message(self, content, meta):
        sender_addr = meta["sender_addr"]
        sender_id = meta["sender_id"]

        if isinstance(content, AskFeedInMsg):
            self.handle_ask_feed_in(sender_addr, sender_id)
        elif isinstance(content, SetMaxFeedInMsg):
            self.handle_set_feed_in_max(content.max_feed_in, sender_addr, sender_id)
        else:
            print(f"{self.aid}: Received a message of unknown type {type(content)}")

    def handle_ask_feed_in(self, sender_addr, sender_id):
        reported_feed_in = PV_FEED_IN[self.aid]  # PV_FEED_IN must be defined at the top
        msg = FeedInReplyMsg(reported_feed_in)

        self.schedule_instant_task(
            self.send_acl_message(
                content=msg,
                receiver_addr=sender_addr,
                receiver_id=sender_id,
            )
        )

    def handle_set_feed_in_max(self, max_feed_in, sender_addr, sender_id):
        self.max_feed_in = float(max_feed_in)
        print(f"{self.aid}: Limiting my feed_in to {max_feed_in}")
        msg = MaxFeedInAck()

        self.schedule_instant_task(
            self.send_acl_message(
                content=msg,
                receiver_addr=sender_addr,
                receiver_id=sender_id,
            )
        )


class ControllerAgent(Agent):
    def __init__(self, container, known_agents, suggested_aid=None):
        super().__init__(container, suggested_aid=suggested_aid)
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

        # Note: For messages passed between different containers (i.e. over the network socket) it is expected
        # that the message is an ACLMessage object. We can let the container wrap our content in such an
        # object using the send_acl_message method.
        # We distinguish the types of messages we send by adding a type field to our content.

        # ask pv agent feed-ins
        for addr, aid in self.known_agents:
            msg = AskFeedInMsg()
            acl_meta = {"sender_addr": self.addr, "sender_id": self.aid}

            # alternatively we could call send_acl_message here directly and await it
            self.schedule_instant_task(
                self.send_acl_message(
                    content=msg,
                    receiver_addr=addr,
                    receiver_id=aid,
                    acl_metadata=acl_meta,
                )
            )

        # wait for both pv agents to answer
        await self.reports_done

        # limit both pv agents to the smaller ones feed-in
        print(f"{self.aid} received feed_ins: {self.reported_feed_ins}")
        min_feed_in = min(self.reported_feed_ins)

        for addr, aid in self.known_agents:
            msg = SetMaxFeedInMsg(min_feed_in)
            acl_meta = {"sender_addr": self.addr, "sender_id": self.aid}

            # alternatively we could call send_acl_message here directly and await it
            self.schedule_instant_task(
                self.send_acl_message(
                    content=msg,
                    receiver_addr=addr,
                    receiver_id=aid,
                    acl_metadata=acl_meta,
                )
            )

        # wait for both pv agents to acknowledge the change
        await self.acks_done


async def main():
    # If no codec is given, every container automatically creates a new JSON codec.
    # Now, we set up the codecs explicitely and pass them the neccessary extra serializers.

    # Both types of agents need to be able to handle the same message types (either for serialization
    # or deserializaion). In general, a serializer is passed to the codec by three values:
    # (type, serialize_method, deserialize_method)
    #
    # the @json_serializable decorater creates these automatically for simple classes and
    # provides them as a tuple via the __serializer__ method on the class.
    my_codec = codecs.JSON()
    my_codec.add_serializer(*AskFeedInMsg.__serializer__())
    my_codec.add_serializer(*SetMaxFeedInMsg.__serializer__())
    my_codec.add_serializer(*FeedInReplyMsg.__serializer__())
    my_codec.add_serializer(*MaxFeedInAck.__serializer__())

    pv_container = await create_container(addr=PV_CONTAINER_ADDRESS, codec=my_codec)

    controller_container = await create_container(
        addr=CONTROLLER_CONTAINER_ADDRESS, codec=my_codec
    )

    pv_agent_0 = PVAgent(pv_container, suggested_aid="PV Agent 0")
    pv_agent_1 = PVAgent(pv_container, suggested_aid="PV Agent 1")

    known_agents = [
        (PV_CONTAINER_ADDRESS, pv_agent_0.aid),
        (PV_CONTAINER_ADDRESS, pv_agent_1.aid),
    ]

    controller_agent = ControllerAgent(
        controller_container, known_agents, suggested_aid="Controller"
    )
    await controller_agent.run()

    # always properly shut down your containers
    await pv_container.shutdown()
    await controller_container.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
