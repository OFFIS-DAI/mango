import asyncio
import logging

from mango import Agent

logger = logging.getLogger(__name__)


class ClockAgent(Agent):
    async def wait_all_done(self):
        await self._context._container.inbox.join()
        for ag in self._context._container._agents.values():  # type: Agent
            await ag.inbox.join()  # make sure inbox of agent is empty and all messages are processed
            await ag._scheduler.tasks_complete_or_sleeping()  # wait until agent is done with all tasks


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

        logger.debug(f"clockmanager: {content} from {sender_addr}")
        if content:
            assert isinstance(content, float), f"{content} was {type(content)}"
            self.schedules.append(content)

        if not self.futures[sender_addr].done():
            self.futures[sender_addr].set_result(True)
        else:
            logger.warning(f"got another message from agent {sender_addr}")

    async def broadcast(self, message, add_futures=True):
        for receiver_addr in self.receiver_clock_addresses:
            logger.debug(f"clockmanager send: {message} - {receiver_addr}")
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
        self.schedules = []
        await asyncio.sleep(0.01)
        # wait until all jobs in other containers are finished
        for container_id, fut in self.futures.items():
            logger.debug(f"waiting for {container_id}")
            # waits forever if manager was started first
            # as answer is never received
            await fut
        # wait for our container too
        await self.wait_all_done()
        if self._scheduler.clock.get_next_activity() is not None:
            self.schedules.append(self._scheduler.clock.get_next_activity())

        if self.schedules:
            next_event = min(self.schedules)
        else:
            logger.warning("no new events, time stands still")
            next_event = self._scheduler.clock.time
        logger.debug(f"next event at {next_event}")
        self.schedule_instant_task(coroutine=self.broadcast(next_event))
        return next_event


class DistributedClockAgent(ClockAgent):
    def __init__(self, container):
        super().__init__(container, "clock_agent")
        self.stopped = asyncio.Future()

    def handle_message(self, content: float, meta):
        sender_addr = meta["sender_addr"]
        sender_id = meta["sender_id"]
        logger.info(f"clockagent: {content} from {sender_addr}")
        if content == "stop":
            if not self.stopped.done():
                self.stopped.set_result(True)
        else:
            assert isinstance(content, (int, float)), f"{content} was {type(content)}"
            self._scheduler.clock.set_time(content)

            t = asyncio.create_task(self.wait_all_done())

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
