import asyncio

from mango import Agent, create_container
from mango.messages.message import Performatives

"""
In the previous example, you have learned how to create mango agents and containers and 
how to send basic messages between them.
In this example, you expand upon this. We introduce a controller agent that asks the current feed_in of our PV agents
and subsequently limits the output of both to their minimum.

This example covers:
    - message passing between different containers
    - basic task scheduling
    - setting custom agent ids
    - use of ACL metadata
"""

PV_CONTAINER_ADDRESS = ("localhost", 5555)
CONTROLLER_CONTAINER_ADDRESS = ("localhost", 5556)
PV_FEED_IN = {
    "PV Agent 0": 2.0,
    "PV Agent 1": 1.0,
}


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
            print(
                f"{self.aid}: Received an unexpected message with content {content} and meta {meta}"
            )

    def handle_ask_feed_in(self, sender_addr, sender_id):
        reported_feed_in = PV_FEED_IN[self.aid]  # PV_FEED_IN must be defined at the top
        content = reported_feed_in

        acl_meta = {
            "sender_addr": self.addr,
            "sender_id": self.aid,
            "performative": Performatives.inform,
        }

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
                acl_metadata={"performative": Performatives.accept_proposal},
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
        performative = meta["performative"]
        if performative == Performatives.inform:
            # feed_in_reply message
            self.handle_feed_in_reply(content)
        elif performative == Performatives.accept_proposal:
            # set_max_ack message
            self.handle_set_max_ack()
        else:
            print(
                f"{self.aid}: Received an unexpected message  with content {content} and meta {meta}"
            )

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
        # object with using the send_acl_message method.
        # We distinguish the types of messages we send by adding a type field to our content.

        # ask pv agent feed-ins
        for addr, aid in self.known_agents:
            content = None
            acl_meta = {
                "sender_addr": self.addr,
                "sender_id": self.aid,
                "performative": Performatives.request,
            }
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
            acl_meta = {
                "sender_addr": self.addr,
                "sender_id": self.aid,
                "performative": Performatives.propose,
            }

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


async def main():
    pv_container = await create_container(addr=PV_CONTAINER_ADDRESS)
    controller_container = await create_container(addr=CONTROLLER_CONTAINER_ADDRESS)

    # agents always live inside a container
    pv_agent_0 = PVAgent(pv_container, suggested_aid="PV Agent 0")
    pv_agent_1 = PVAgent(pv_container, suggested_aid="PV Agent 1")

    # We pass info of the pv agents addresses to the controller here directly.
    # In reality, we would use some kind of discovery mechanism for this.
    known_agents = [
        (PV_CONTAINER_ADDRESS, pv_agent_0.aid),
        (PV_CONTAINER_ADDRESS, pv_agent_1.aid),
    ]

    controller_agent = ControllerAgent(
        controller_container, known_agents, suggested_aid="Controller"
    )

    # the only active component in this setup
    await controller_agent.run()

    # always properly shut down your containers
    await pv_container.shutdown()
    await controller_container.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
