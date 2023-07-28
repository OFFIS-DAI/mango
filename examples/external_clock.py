import asyncio

from mango import Agent, create_container
from mango.util.clock import ExternalClock


class Caller(Agent):
    def __init__(self, container, receiver_addr, receiver_id):
        super().__init__(container)
        self.schedule_timestamp_task(
            coroutine=self.send_hello_world(receiver_addr, receiver_id),
            timestamp=self.current_timestamp + 5,
        )

    async def send_hello_world(self, receiver_addr, receiver_id):
        await self.send_acl_message(
            receiver_addr=receiver_addr, receiver_id=receiver_id, content="Hello World"
        )

    def handle_message(self, content, meta):
        pass


class Receiver(Agent):
    def __init__(self, container):
        super().__init__(container)
        self.wait_for_reply = asyncio.Future()

    def handle_message(self, content, meta):
        print(f"Received a message with the following content {content}.")
        self.wait_for_reply.set_result(True)


async def main():
    # clock = AsyncioClock()
    clock = ExternalClock(start_time=1000)
    addr = ("127.0.0.1", 5555)

    c = await create_container(addr=addr, clock=clock)
    receiver = Receiver(c)
    caller = Caller(c, addr, receiver.aid)
    if isinstance(clock, ExternalClock):
        await asyncio.sleep(1)
        clock.set_time(clock.time + 5)
    await receiver.wait_for_reply
    await c.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
