import asyncio
import logging

from mango import Agent
from mango.container.mqtt import MQTTContainer
from .termination_detection import tasks_complete_or_sleeping

logger = logging.getLogger(__name__)


class ClockAgent(Agent):
    async def wait_all_done(self):
        await tasks_complete_or_sleeping(self._context._container)


class DistributedClockManager(ClockAgent):
    def __init__(self, container, receiver_clock_addresses: list):
        super().__init__(container, "clock")
        self.receiver_clock_addresses = receiver_clock_addresses
        self.schedules = []
        self.futures = {}

    def handle_message(self, content: float, meta):
        if isinstance(meta["sender_addr"], list):
            sender_addr = tuple(meta["sender_addr"])
        else:
            sender_addr = meta["sender_addr"]

        logger.debug("clockmanager: %s from %s", content, sender_addr)
        if content:
            assert isinstance(content, (float, int)), f"{content} was {type(content)}"
            self.schedules.append(content)

        if not self.futures[sender_addr].done():
            self.futures[sender_addr].set_result(True)
        else:
            # with as_agent_process - messages can come from ourselves
            logger.debug("got another message from agent %s - %s", sender_addr, content)

    async def broadcast(self, message, add_futures=True):
        for receiver_addr in self.receiver_clock_addresses:
            logger.debug("clockmanager send: %s - %s", message, receiver_addr)
            send_worked = await self.send_acl_message(
                message,
                receiver_addr,
                "clock_agent",
                acl_metadata={"sender_id": self.aid},
            )
            if send_worked and add_futures:
                self.futures[receiver_addr] = asyncio.Future()

    async def shutdown(self):
        for fut in self.futures.values():
            await fut
        await self.broadcast("stop", add_futures=False)
        await super().shutdown()

    async def distribute_time(self):
        # wait until all jobs in other containers are finished
        for container_id, fut in self.futures.items():
            logger.debug("waiting for %s", container_id)
            # waits forever if manager was started first
            # as answer is never received
            await fut
        # wait for our container too
        await self.wait_all_done()
        next_activity = self._scheduler.clock.get_next_activity()

        if next_activity is not None:
            # logger.error(f"{next_activity}")
            self.schedules.append(next_activity)

        if self.schedules:
            next_event = min(self.schedules)
        else:
            logger.warning("%s: no new events, time stands still", self.aid)
            next_event = self._scheduler.clock.time
        logger.debug("next event at %s", next_event)
        self.schedule_instant_task(coroutine=self.broadcast(next_event))
        self.schedules = []
        return next_event


class DistributedClockAgent(ClockAgent):
    def __init__(self, container):
        super().__init__(container, "clock_agent")
        self.stopped = asyncio.Future()

    def handle_message(self, content: float, meta):
        sender_addr = meta["sender_addr"]
        sender_id = meta["sender_id"]
        logger.info("clockagent: %s from %s", content, sender_addr)
        if content == "stop":
            if not self.stopped.done():
                self.stopped.set_result(True)
        else:
            assert isinstance(content, (int, float)), f"{content} was {type(content)}"
            self._scheduler.clock.set_time(content)

            async def wait_done():
                if isinstance(self._context._container, MQTTContainer):
                    # in MQTT tasks are finished before incoming messages are handled
                    # as we do not wait and directly send back that we finished.
                    # Instead we should leave time so that we start handling incoming messages before.
                    # This should be fixed properly by distributed termination detection
                    await asyncio.sleep(0.05)
                await self.wait_all_done()

            t = asyncio.create_task(wait_done())

            def respond(fut: asyncio.Future = None):
                if self.stopped.done():
                    return

                next_time = self._scheduler.clock.get_next_activity()

                self.schedule_instant_acl_message(
                    next_time,
                    sender_addr,
                    sender_id,
                    acl_metadata={"sender_id": self.aid},
                )

            t.add_done_callback(respond)
