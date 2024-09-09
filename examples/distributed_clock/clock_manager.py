import asyncio
import logging
from datetime import datetime
from typing import TypedDict

import pandas as pd
from serializer import mango_codec_factory
from tqdm import tqdm

from mango import Role, RoleAgent, create_container
from mango.messages.message import Performatives
from mango.util.clock import ExternalClock
from mango.util.distributed_clock import DistributedClockManager
from mango.util.termination_detection import tasks_complete_or_sleeping

logger = logging.getLogger(__name__)


class SimpleBid(TypedDict):
    price: float
    volume: float


class OneSidedMarketRole(Role):
    def __init__(self, demand=1000, receiver_ids=[]):
        super().__init__()
        self.demand = demand
        self.bids = []
        self.receiver_ids = receiver_ids

    def setup(self):
        self.results = []
        self.demands = []

        self.context.subscribe_message(
            self, self.handle_message, lambda content, meta: isinstance(content, dict)
        )

        self.context.subscribe_message(
            self, self.handle_other, lambda content, meta: not isinstance(content, dict)
        )
        self.context.schedule_periodic_task(coroutine_func=self.clear_market, delay=900)
        self.starttime = self.context.current_timestamp

    async def clear_market(self):
        time = datetime.fromtimestamp(self.context.current_timestamp)
        if self.context.current_timestamp > self.starttime:
            i = time.hour + time.minute / 60
            df = pd.DataFrame.from_dict(self.bids)
            self.bids = []
            price = 0
            if df.empty:
                logger.info("Did not receive any bids!")
            else:
                # simple merit order calculation
                df = df.sort_values("price")
                df["cumsum"] = df["volume"].cumsum()
                filtered = df[df["cumsum"] >= self.demand]
                if filtered.empty:
                    # demand could not be matched
                    price = 100
                else:
                    price = filtered["price"].values[0]
            self.results.append(price)
            self.demands.append(self.demand)
        else:
            logger.info("First opening does not have anything to clear")
            price=0
        acl_metadata = {
            "performative": Performatives.inform,
            "sender_id": self.context.aid,
            "sender_addr": self.context.addr,
            "conversation_id": "conversation01",
        }
        resp = []
        for receiver_addr, receiver_id in self.receiver_ids:
            r = self.context.send_acl_message(
                content={"message": f"Current time is {time}", "price": price},
                receiver_addr=receiver_addr,
                receiver_id=receiver_id,
                acl_metadata=acl_metadata,
            )
            resp.append(r)
        for r in resp:
            await r

    def handle_message(self, content, meta):
        # content is SimpleBid
        content["sender_id"] = meta["sender_id"]
        self.bids.append(content)

    def handle_other(self, content, meta):
        # content is other
        print(f'got {content} from {meta.get("sender_id")}')

    async def on_stop(self):
        logger.info(self.results)


async def main(start):
    clock = ExternalClock(start_time=start.timestamp())
    # connection_type = 'mqtt'
    connection_type = "tcp"

    if connection_type == "mqtt":
        addr = "c1"
        other_container_addr = "c2"
    else:
        addr = ("localhost", 5555)
        other_container_addr = ("localhost", 5556)
    container_kwargs = {
        "connection_type": connection_type,
        "addr": addr,
        "clock": clock,
        "mqtt_kwargs": {
            "client_id": "container_1",
            "broker_addr": ("localhost", 1883, 60),
            "transport": "tcp",
        },
    }

    c = await create_container(**container_kwargs)
    clock_agent = DistributedClockManager(
        c, receiver_clock_addresses=[(other_container_addr, "clock_agent")]
    )
    # distribute time here, so that containers already have correct start time
    next_event = await clock_agent.distribute_time()
    market = RoleAgent(c, suggested_aid="market")
    receiver_ids = [(other_container_addr, "a0"), (other_container_addr, "a1")]
    market.add_role(OneSidedMarketRole(demand=1000, receiver_ids=receiver_ids))

    if isinstance(clock, ExternalClock):
        for i in tqdm(range(30)):
            next_event = await clock_agent.distribute_time()
            logger.info("current step: %s", clock.time)
            await tasks_complete_or_sleeping(c)
            # comment next line to see that the first message is not received in correct timings
            # also comment sleep(0) in termination_detection to see other timing problems
            next_event = await clock_agent.distribute_time()
            clock.set_time(next_event)
    await c.shutdown()


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    start = datetime(2023, 1, 1)
    asyncio.run(main(start))
