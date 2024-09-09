import asyncio
import logging
from typing import TypedDict

import numpy as np
from mango import Role, RoleAgent, create_container
from mango.messages.message import Performatives
from mango.util.clock import ExternalClock
from mango.util.distributed_clock import DistributedClockAgent

logger = logging.getLogger(__name__)


class SimpleBid(TypedDict):
    price: float
    volume: float


class BiddingRole(Role):
    def __init__(self, receiver_addr, receiver_id, volume=100, price=0.05):
        super().__init__()
        self.receiver_addr = receiver_addr
        self.receiver_id = receiver_id
        self.volume = volume
        self.price = price

    def setup(self):
        self.volume = self.volume
        self.price = self.price
        self.context.subscribe_message(
            self, self.handle_message, lambda content, meta: True
        )
        self.context.schedule_periodic_task(coroutine_func=self.say_hello, delay=1200)

    async def say_hello(self):
        await self.context.send_acl_message(
            content="Hello Market",
            receiver_addr=self.receiver_addr,
            receiver_id=self.receiver_id,
            acl_metadata={
                "sender_id": self.context.aid,
                "sender_addr": self.context.addr,
            },
        )

    def handle_message(self, content, meta):
        self.context.schedule_instant_task(coroutine=self.set_bids())
        logger.debug("current_time %s", self.context.current_timestamp)

    async def set_bids(self):
        # await asyncio.sleep(1)
        price = self.price + 0.01 * self.price * np.random.random()
        logger.debug("did set bids at %s", self.context._scheduler.clock.time)

        acl_metadata = {
            "performative": Performatives.inform,
            "sender_id": self.context.aid,
            "sender_addr": self.context.addr,
            "conversation_id": "conversation01",
        }
        await self.context.send_acl_message(
            content={"price": price, "volume": self.volume},
            receiver_addr=self.receiver_addr,
            receiver_id=self.receiver_id,
            acl_metadata=acl_metadata,
        )


async def main():
    clock = ExternalClock(start_time=0)
    # connection_type = 'mqtt'
    connection_type = "tcp"

    if connection_type == "mqtt":
        addr = "c2"
        other_container_addr = "c1"
    else:
        addr = ("localhost", 5556)
        other_container_addr = ("localhost", 5555)
    container_kwargs = {
        "connection_type": connection_type,
        "addr": addr,
        "clock": clock,
        "mqtt_kwargs": {
            "client_id": "container_2",
            "broker_addr": ("localhost", 1883, 60),
            "transport": "tcp",
        },
    }

    c = await create_container(**container_kwargs)

    clock_agent = DistributedClockAgent(c)

    for i in range(2):
        agent = RoleAgent(c, suggested_aid=f"a{i}")
        agent.add_role(
            BiddingRole(other_container_addr, "market", price=0.05 * (i % 9))
        )

    await clock_agent.stopped
    await c.shutdown()


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    asyncio.run(main())
