import asyncio
from dataclasses import dataclass

import mango.messages.codecs as codecs

# note that our imports changed because we now use the specialized RoleAgent superclass
from mango import Role, RoleAgent, create_container

"""
In example 3, you restructured your code to use codecs for easier handling of typed message objects.
Now we want to expand the functionality of our controller. In addition to setting the maximum feed_in 
of the pv agents, the controller should now also periodically check if the pv agents are still reachable.

To achieve this, the controller should seend a regular "ping" message to each pv agent that is in turn answered
by a corresponding "pong". To properly serparate different responsibilities within agents, mango has a role
system where each role covers the functionalities of a responsibility.

A role is a python object that can be assigned to a RoleAgent. The two main functions each role implements are:
    __init__ - where you do the initial object setup
    setup - which is called when the role is assigned to an agent

This distinction is relevant because only within `setup` the 
RoleContext (i.e. access to the parent agent and container) exist.
Thus, things like message handlers that require container knowledge are introduced there.

This example covers:
    - scheduling and periodic tasks
    - role API basics
"""

PV_CONTAINER_ADDRESS = ("localhost", 5555)
CONTROLLER_CONTAINER_ADDRESS = ("localhost", 5556)
PV_FEED_IN = {
    "PV Agent 0": 2.0,
    "PV Agent 1": 1.0,
}


# To separate the agents functionalities we introduce four roles:
# - a ping role for sending out periodic "are you alive" messages
# - a pong role for replying to ping messages
# - a pvrole for setting and reporting feed_in
# - a controller role for sending out feed_in requests and setting maximum feed_ins
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
        ]  # PV_FEED_IN must be defined at the top
        msg = FeedInReplyMsg(reported_feed_in)

        sender_addr = meta["sender_addr"]
        sender_id = meta["sender_id"]

        self.context.schedule_instant_task(
            self.context.send_acl_message(
                content=msg,
                receiver_addr=sender_addr,
                receiver_id=sender_id,
            )
        )

    def handle_set_feed_in_max(self, content, meta):
        max_feed_in = float(content.max_feed_in)
        self.max_feed_in = max_feed_in
        print(f"{self.context.aid}: Limiting my feed_in to {max_feed_in}")

        msg = MaxFeedInAck()
        sender_addr = meta["sender_addr"]
        sender_id = meta["sender_id"]

        self.context.schedule_instant_task(
            self.context.send_acl_message(
                content=msg,
                receiver_addr=sender_addr,
                receiver_id=sender_id,
            )
        )


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
        for addr, aid in self.known_agents:
            msg = AskFeedInMsg()
            acl_meta = {"sender_addr": self.context.addr, "sender_id": self.context.aid}

            await self.context.send_acl_message(
                content=msg,
                receiver_addr=addr,
                receiver_id=aid,
                acl_metadata=acl_meta,
            )

        # wait for both pv agents to answer
        await self.reports_done

        # limit both pv agents to the smaller ones feed-in
        print(f"Controller received feed_ins: {self.reported_feed_ins}")
        min_feed_in = min(self.reported_feed_ins)

        for addr, aid in self.known_agents:
            msg = SetMaxFeedInMsg(min_feed_in)
            acl_meta = {"sender_addr": self.context.addr, "sender_id": self.context.aid}

            await self.context.send_acl_message(
                content=msg,
                receiver_addr=addr,
                receiver_id=aid,
                acl_metadata=acl_meta,
            )

        # wait for both pv agents to acknowledge the change
        await self.acks_done


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


@codecs.json_serializable
@dataclass
class Ping:
    ping_id: int


@codecs.json_serializable
@dataclass
class Pong:
    pong_id: int


class PVAgent(RoleAgent):
    def __init__(self, container, suggested_aid=None):
        super().__init__(container, suggested_aid=suggested_aid)
        self.add_role(PongRole())
        self.add_role(PVRole())


class ControllerAgent(RoleAgent):
    def __init__(self, container, known_agents, suggested_aid=None):
        super().__init__(container, suggested_aid=suggested_aid)
        self.add_role(PingRole(known_agents, 2))
        self.add_role(ControllerRole(known_agents))


async def main():
    my_codec = codecs.JSON()
    my_codec.add_serializer(*AskFeedInMsg.__serializer__())
    my_codec.add_serializer(*SetMaxFeedInMsg.__serializer__())
    my_codec.add_serializer(*FeedInReplyMsg.__serializer__())
    my_codec.add_serializer(*MaxFeedInAck.__serializer__())

    # dont forget to add our new serializers
    my_codec.add_serializer(*Ping.__serializer__())
    my_codec.add_serializer(*Pong.__serializer__())

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

    # no more run call since everything now happens automatically within the roles
    await asyncio.sleep(5)

    # always properly shut down your containers
    await pv_container.shutdown()
    await controller_container.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
