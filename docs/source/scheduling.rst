====================
Scheduling and Clock
====================

When implementing agents including proactive behavior there are some typical types of tasks you might want to create. For example it might be desired to let the agent check every minute whether some resources are available, or often you just want to execute a task at a specified time. To help achieving this kind of goals mango exposes the scheduling API.

The core of this API is the scheduler, which is part of every agent. To schedule a task its necessary to create a ScheduledTask (resp. an object of a subclass). In mango the following tasks are available:

.. list-table:: Available ScheduledTasks
   :widths: 30 70
   :header-rows: 1

   * - Class
     - Description
   * - InstantScheduledTask
     - Executes the coroutine without delay
   * - TimestampScheduledTask
     - Executes the coroutine at a specified datetime
   * - PeriodicScheduledTask
     - Executes a coroutine periodically with a static delay between the cycles
   * - RecurrentScheduledTask
     - Executes a coroutine according to a dynamic repetition scheme provided by a `rrule`
   * - ConditionalScheduledTask
     - Executes the coroutine when a specified condition evaluates to True
   * - AwaitingTask
     - Execute a given coroutine after another given coroutine has been awaited
   * - RecurrentScheduledTask
     - Will get executed periodically with a specified delay



Furthermore there are convenience methods to get rid of the class imports when using these types of tasks.

.. code-block:: python3

    from mango import Agent
    from mango.util.scheduling import InstantScheduledTask

        class ScheduleAgent(Agent):
            def __init__(self, container, other_addr, other_id):
                self.schedule_instant_acl_message(
                    receiver_addr=other_addr,
                    receiver_id=other_id,
                    content="Hello world!")
                )
                # equivalent to
                self.schedule_instant_acl_message(
                    receiver_addr=other_addr,
                    receiver_id=other_id,
                    content="Hello world!"))
                )

            def handle_message(self, content, meta: Dict[str, Any]):
                pass


When using the scheduling another feature becomes available: suspendable tasks. This makes it possible to pause and resume all tasks started with the scheduling API. Using this it is necessary to specify an identifier when starting the task (using `src=your_identifier`). To suspend a task you can call `scheduler.suspend(your_identifier)`, to resume them just call the counterpart `scheduler.resume(your_identifier)`. The scheduler is part of the agent and accessible via `self._scheduler`.


*******************************
Dispatch Tasks to other Process
*******************************

As asyncio does not provide real parallelism to utilize multiple cores and agents may have tasks, which need a lot computational power, the need to dispatch certain tasks to other processes appear. Handling inter process communication manually is quite exhausting and having multiple process pools across different roles or agents leads to inefficient resource allocations. As a result mango offers a way to dispatch tasks, based on coroutine-functions, to other processes, managed by the framework. 

Analogues to the normal API there are two different ways, first you create a ScheduledProcessTask and call ``schedule_process_task``, second you invoke the convnience methods with "process" in the name. These methods exists on any Agent, the RoleContext and the Scheduler.
In mango the following process tasks are available:

.. list-table:: Available ProcessTasks
   :widths: 30 70
   :header-rows: 1

   * - Class
     - Description
   * - ScheduledProcessTask
     - Marks a ScheduledTask as process compatible
   * - TimestampScheduledProcessTask
     - Timestamp based one-shot task
   * - AwaitingProcessTask
     - Await a coroutine, then execute another
   * - InstantScheduledProcessTask
     - One-shot task, which will get executed instantly
   * - PeriodicScheduledProcessTask
     - Executes a coroutine periodically with a static delay between the cycles
   * - RecurrentScheduledProcessTask
     - Will get executed periodically with a specified delay
   * - ConditionalProcessTask
     - Will get executed as soon as the given condition is fulfilled

.. code-block:: python3

    from mango import Agent
    from mango.util.scheduling import InstantScheduledProcessTask

        class ScheduleAgent(Agent):
            def __init__(self, container, other_addr, other_id):
                self.schedule_instant_process_task(coroutine_creator=lambda: self.send_acl_message(
                    receiver_addr=other_addr,
                    receiver_id=other_id,
                    content="Hello world!")
                )
                # equivalent to
                self.schedule_process_task(InstantScheduledProcessTask(coroutine_creator=lambda: self.send_acl_message(
                    receiver_addr=other_addr,
                    receiver_id=other_id,
                    content="Hello world!"))
                )

            def handle_message(self, content, meta: Dict[str, Any]):
                pass

*******************************
Using an external clock
*******************************
Usually, the scheduler will schedule the tasks of a mango agent based on the real time.
This is the default behaviour of the scheduler.
However, in some contexts it is necessary to schedule the agent based on an external clock,
e. g. in simulations that run faster than real-time.
In mango, this is possible by defining the ``Clock`` of a container, which will be used by the
scheduler of all agents within this container.
The default clock is the ``AsyncioClock``, which works as a real-time clock. An alternative clock
is the ``ExternalClock``. Time of this clock has to be set by an external process. That way you can
control how fast or slow time passes within your agent system:

.. code-block:: python3

    import asyncio
    from mango import create_container
    from mango import Agent
    from mango.util.clock import AsyncioClock, ExternalClock


    class Caller(Agent):
        def __init__(self, container, receiver_addr, receiver_id):
            super().__init__(container)
            self.schedule_timestamp_task(coroutine=self.send_hello_world(receiver_addr, receiver_id),
                                         timestamp=self.current_timestamp + 5)

        async def send_hello_world(self, receiver_addr, receiver_id):
            await self.send_acl_message(receiver_addr=receiver_addr,
                                               receiver_id=receiver_id,
                                               content='Hello World')

        def handle_message(self, content, meta):
            pass


    class Receiver(Agent):
        def __init__(self, container):
            super().__init__(container)
            self.wait_for_reply = asyncio.Future()

        def handle_message(self, content, meta):
            print(f'Received a message with the following content {content}.')
            self.wait_for_reply.set_result(True)


    async def main():
        clock = AsyncioClock()
        # clock = ExternalClock(start_time=1000)
        addr = ('127.0.0.1', 5555)
        c = await create_container(addr=addr, clock=clock)
        receiver = Receiver(c)
        caller = Caller(c, addr, receiver.aid)
        await receiver.wait_for_reply
        await c.shutdown()


    if __name__ == '__main__':
        asyncio.run(main())


This code will terminate after 5 seconds.
If you change the clock to an ``ExternalClock`` by uncommenting the ExternalClock in the example above,
the program won't terminate as the time of the clock is not proceeded by an external process.
If you comment in the ExternalClock and change your main() as follows, the program will terminate after one second:

.. code-block:: python3

    async def main():
        # clock = AsyncioClock()
        clock = ExternalClock(start_time=1000)
        addr = ('127.0.0.1', 5555)

        c = await create_container(addr=addr, clock=clock)
        receiver = Receiver(c)
        caller = Caller(c, addr, receiver.aid)
        if isinstance(clock, ExternalClock):
            await asyncio.sleep(1)
            clock.set_time(clock.time + 5)
        await receiver.wait_for_reply
        await c.shutdown()

