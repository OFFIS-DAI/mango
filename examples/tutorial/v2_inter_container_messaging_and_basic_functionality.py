import random
import asyncio

from mango.core.agent import Agent
from mango.core.container import Container
from mango.messages.message import ACLMessage as ACLMessage_json, Performatives
import mango.messages.codecs

"""
In the previous example you learned how to create mango agents and containers and how to send basic messages between them.
In this example you expand upon this. We introduce a controller agent that asks the current feed_in of our PV agents and
subsequently limits the output of both to the minimum of the two.

This example covers:
    - message passing between different containers
    - basic task scheduling
    - use of ACL metadata
"""

PV_CONTAINER_ADDRESS = ("localhost", 5555)
CONTROLLER_CONTAINER_ADDRESS = ("localhost", 5556)
random.seed(42)


class PVAgent(Agent):
    def __init__(self, container):
        super().__init__(container)
        self.max_feed_in = -1

    def handle_msg(self, content, meta):
        m_type = content["type"]
        m_content = content["content"]

        sender_addr = meta["sender_addr"]
        sender_id = meta["sender_id"]

        if m_type == "ask_feed_in":
            self.handle_ask_feed_in(sender_addr, sender_id)
        elif m_type == "set_max_feed_in":
            self.handle_set_feed_in_max(m_content, sender_addr, sender_id)
        else:
            print(f"{self._aid}: Received a message of unknown type {m_type}")

    def handle_ask_feed_in(self, sender_addr, sender_id):
        reported_feed_in = random.randint(1, 10)
        msg = {"type": "feed_in_reply", "content": reported_feed_in}

        self.schedule_instant_task(
            self._container.send_message(
                content=msg,
                receiver_addr=sender_addr,
                receiver_id=sender_id,
                create_acl=True,
            )
        )

    def handle_set_feed_in_max(self, max_feed_in, sender_addr, sender_id):
        self.max_feed_in = float(max_feed_in)
        print(f"PV {self._aid}: Limiting my feed_in to {max_feed_in}")

        msg = {"type": "set_max_ack", "content": None}
        self.schedule_instant_task(
            self._container.send_message(
                content=msg,
                receiver_addr=sender_addr,
                receiver_id=sender_id,
                create_acl=True,
            )
        )


class ControllerAgent(Agent):
    def __init__(self, container, known_agents):
        super().__init__(container)
        self.known_agents = known_agents
        self.reported_feed_ins = []
        self.reported_acks = 0
        self.reports_done = None
        self.acks_done = None

    def handle_msg(self, content, meta):
        m_type = content["type"]
        m_content = content["content"]

        if m_type == "feed_in_reply":
            self.handle_feed_in_reply(m_content)
        elif m_type == "set_max_ack":
            self.handle_set_max_ack()
        else:
            print(f"{self._aid}: Received a message of unknown type {m_type}")

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
        # object with the create_acl flag.
        # We distinguish the types of messages we send by adding a type field to our content.

        # ask pv agent feed-ins
        for addr, aid in self.known_agents:
            msg = {"type": "ask_feed_in", "content": None}
            acl_meta = {"sender_addr": self._container.addr, "sender_id": self._aid}

            # alternatively we could call send_message here directly and await it
            self.schedule_instant_task(
                self._container.send_message(
                    content=msg,
                    receiver_addr=addr,
                    receiver_id=aid,
                    create_acl=True,
                    acl_metadata=acl_meta,
                )
            )

        # wait for both pv agents to answer
        await self.reports_done

        # limit both pv agents to the smaller ones feed-in
        print(f"Controller received feed_ins: {self.reported_feed_ins}")
        min_feed_in = min(self.reported_feed_ins)

        for addr, aid in self.known_agents:
            msg = {"type": "set_max_feed_in", "content": min_feed_in}
            acl_meta = {"sender_addr": self._container.addr, "sender_id": self._aid}

            # alternatively we could call send_message here directly and await it
            self.schedule_instant_task(
                self._container.send_message(
                    content=msg,
                    receiver_addr=addr,
                    receiver_id=aid,
                    create_acl=True,
                    acl_metadata=acl_meta,
                )
            )

        # wait for both pv agents to acknowledge the change
        await self.acks_done


async def main():
    pv_container = await Container.factory(addr=PV_CONTAINER_ADDRESS)
    controller_container = await Container.factory(addr=CONTROLLER_CONTAINER_ADDRESS)

    # agents always live inside a container
    pv_agent_1 = PVAgent(pv_container)
    pv_agent_2 = PVAgent(pv_container)

    # We pass info of the pv agents addresses to the controller here directly.
    # In reality, we would use some kind of discovery mechanism for this.
    known_agents = [
        (PV_CONTAINER_ADDRESS, pv_agent_1._aid),
        (PV_CONTAINER_ADDRESS, pv_agent_2._aid),
    ]

    controller_agent = ControllerAgent(controller_container, known_agents)

    # the only active component in this setup
    await controller_agent.run()

    # always properly shut down your containers
    await pv_container.shutdown()
    await controller_container.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
