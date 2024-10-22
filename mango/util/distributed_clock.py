import asyncio
import logging

from mango import Agent, sender_addr

from .termination_detection import tasks_complete_or_sleeping

logger = logging.getLogger(__name__)


class ClockAgent(Agent):
    async def wait_all_done(self):
        await tasks_complete_or_sleeping(self.context._container)


class DistributedClockManager(ClockAgent):
    def __init__(self, receiver_clock_addresses: list):
        super().__init__()

        self.receiver_clock_addresses = receiver_clock_addresses
        self.schedules = []
        self.futures = {}

    def on_ready(self):
        self.schedule_instant_task(self.wait_all_online())

    def handle_message(self, content: float, meta):
        sender = sender_addr(meta)
        logger.debug("clockmanager: %s from %s", content, sender)
        if content:
            assert isinstance(content, int | float), f"{content} was {type(content)}"
            self.schedules.append(content)

        if not self.futures[sender].done():
            self.futures[sender].set_result(True)
        else:
            # with as_agent_process - messages can come from ourselves
            logger.debug("got another message from agent %s - %s", sender, content)

    async def broadcast(self, message, add_futures=True):
        """
        Broadcast the given message to all receiver clock addresses.
        If add_futures is set, a future is added which is finished when an answer by the receiving clock agent was received.

        Args:
            message (object): the given message
            add_futures (bool, optional): Adds futures which can be awaited until a response to a message is given. Defaults to True.
        """
        for receiver_addr in self.receiver_clock_addresses:
            logger.debug("clockmanager send: %s - %s", message, receiver_addr)
            # in MQTT we can not be sure if the message was delivered
            # checking the return code here would only help for TCP
            await self.send_message(message, receiver_addr)
            if add_futures:
                self.futures[receiver_addr] = asyncio.Future()

    async def shutdown(self):
        await self.wait_for_futures()
        await self.broadcast("stop", add_futures=False)
        await super().shutdown()

    async def send_current_time(self, time=None):
        """
        Broadcasts the current time to all receiver clock addresses.
        Does not add futures to wait for responses, as no response is expected here.

        Args:
            time (number, optional): The current time which is set. Defaults to None.
        """
        time = time or self.scheduler.clock.time
        await self.broadcast(time, add_futures=False)

    async def wait_for_futures(self):
        """
        Waits for all futures in self.futures

        Gives debug log output to see which agent is waited for.
        """
        for container_id, fut in list(self.futures.items()):
            logger.debug("waiting for %s", container_id)
            # waits forever if manager was started first
            # as answer is never received
            await fut

    async def wait_all_online(self):
        """
        sends a broadcast to ask for the next event to all expected addresses.
        Waits one second and repeats this behavior until a response by all addresses is receivd.
        This effectively waits until all agents are up and running and the manager can start the simulation.

        This is needed, as there is no way in paho mqtt to check whether a message was retrieved,
        except for by sending ping pong messages.
        """
        all_online = False

        while not all_online:
            await self.broadcast("next_event")
            await asyncio.sleep(0)
            try:
                await asyncio.wait_for(self.wait_for_futures(), 1)
            except TimeoutError:
                logger.info("waiting for all to come online")
            else:
                all_online = True

    async def get_next_event(self):
        """Get the next event from the scheduler by requesting all known clock agents"""
        self.schedules = []
        await self.broadcast("next_event")
        await asyncio.sleep(0)
        await self.wait_for_futures()

        # wait for our container too
        await self.wait_all_done()
        next_activity = self.scheduler.clock.get_next_activity()

        if next_activity is not None:
            # logger.error(f"{next_activity}")
            self.schedules.append(next_activity)

        if self.schedules:
            next_event = min(self.schedules)
        else:
            logger.warning("%s: no new events, time stands still", self.aid)
            next_event = self.scheduler.clock.time

        if next_event < self.scheduler.clock.time:
            logger.warning("%s: got old event, time stands still", self.aid)
            next_event = self.scheduler.clock.time
        logger.debug("next event at %s", next_event)
        return next_event

    async def distribute_time(self, time=None):
        """
        Waits until the current container is done.
        Brodcasts the new time to all the other clock agents.
        Thn awaits until the work in the other agents is done and their next event is received.


        Args:
            time (number, optional): The new time which is set. Defaults to None.
        Returns:
            number or None: The time at which the next event happens
        """
        await self.wait_all_done()
        await self.send_current_time(time)
        if not time:
            time = await self.get_next_event()
        return time


class DistributedClockAgent(ClockAgent):
    def __init__(self):
        super().__init__()
        self.stopped = asyncio.Future()

    def handle_message(self, content: float, meta):
        sender = sender_addr(meta)
        logger.info("clockagent: %s from %s", content, sender)
        if content == "stop":
            if not self.stopped.done():
                self.stopped.set_result(True)
        elif content == "next_event":

            async def wait_done():
                await self.wait_all_done()

            t = asyncio.create_task(wait_done())

            def respond(fut: asyncio.Future = None):
                if self.stopped.done():
                    return

                next_time = self.scheduler.clock.get_next_activity()

                self.schedule_instant_message(next_time, sender_addr(meta))

            t.add_done_callback(respond)
        else:
            assert isinstance(content, int | float), f"{content} was {type(content)}"
            self.scheduler.clock.set_time(content)
