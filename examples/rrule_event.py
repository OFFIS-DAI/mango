import asyncio
from mango import create_container
from mango import Agent
from mango.util.clock import ExternalClock
from datetime import datetime
from dateutil import rrule

class Caller(Agent):
    def __init__(self, container, receiver_addr, receiver_id, recurrency):
        super().__init__(container)
        self.receiver_addr = receiver_addr
        self.receiver_id = receiver_id
        self.schedule_recurrent_task(coroutine_func=self.send_hello_world, recurrency=recurrency)

    async def send_hello_world(self):
        time = datetime.fromtimestamp(self._scheduler.clock.time)
        await self.context.send_acl_message(receiver_addr=self.receiver_addr,
                                               receiver_id=self.receiver_id,
                                               content=f'Current time is {time}')

    def handle_message(self, content, meta):
        pass

class Receiver(Agent):
    def __init__(self, container):
        super().__init__(container)
        self.wait_for_reply = asyncio.Future()

    def handle_message(self, content, meta):
        print(f'Received a message with the following content: {content}.')


async def main(start):
    clock = ExternalClock(start_time=start.timestamp())
    addr = ('127.0.0.1', 5555)
    # market acts every 15 minutes
    recurrency = rrule.rrule(rrule.MINUTELY, interval=15, dtstart=start)

    c = await create_container(addr=addr, clock=clock)
    receiver = Receiver(c)
    caller = Caller(c, addr, receiver.aid, recurrency)
    if isinstance(clock, ExternalClock):
        for i in range(100):
            await asyncio.sleep(0.01)  
            clock.set_time(clock.time + 60)
    await c.shutdown()


if __name__ == '__main__':
    from dateutil.parser import parse
    start = parse('202301010000')
    asyncio.run(main(start))