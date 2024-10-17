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

.. code-block:: python

  from mango import Agent

  class ScheduleAgent(Agent):
      def schedule(self, other_addr):
          self.schedule_instant_message(
              "Hello world!"
              other_addr
          )


When using the scheduling another feature becomes available: suspendable tasks.
This makes it possible to pause and resume all tasks started with the scheduling API.
Using this it is necessary to specify an identifier when starting the task (using ``src=your_identifier``).
To suspend a task you can call ``scheduler.suspend(your_identifier)``, to resume them just call the
counterpart ``scheduler.resume(your_identifier)``. The scheduler is part of the agent and accessible
via ``self._scheduler``.


*******************************
Dispatch Tasks to other Process
*******************************

As asyncio does not provide real parallelism to utilize multiple cores and agents may have tasks,
which need a lot computational power, the need to dispatch certain tasks to other processes appear.
Handling inter process communication manually is quite exhausting and having multiple process pools
across different roles or agents leads to inefficient resource allocations. As a result mango offers
a way to dispatch tasks, based on coroutine-functions, to other processes, managed by the framework.

Analogues to the normal API there are two different ways, first you create a ScheduledProcessTask
and call ``schedule_process_task``, second you invoke the convnience methods with "process" in the name.
These methods exists on any Agent, the RoleContext and the Scheduler.
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

.. testcode::

    import asyncio
    from mango import create_tcp_container, Agent, AsyncioClock, ExternalClock, activate

    class Caller(Agent):
        def __init__(self, receiver_addr):
            super().__init__()
            self.receiver_addr = receiver_addr

        def on_ready(self):
          self.schedule_timestamp_task(coroutine=self.send_hello_world(self.receiver_addr),
                                      timestamp=self.current_timestamp + 0.5)

        async def send_hello_world(self, receiver_addr):
            await self.send_message(receiver_addr=self.receiver_addr,
                                               content='Hello World')


    class Receiver(Agent):
        def __init__(self):
            super().__init__()
            self.wait_for_reply = asyncio.Future()

        def handle_message(self, content, meta):
            print(f'Received a message with the following content {content}.')
            self.wait_for_reply.set_result(True)


    async def main():
        clock = AsyncioClock()
        addr = ('127.0.0.1', 5555)
        c = create_tcp_container(addr=addr, clock=clock)
        receiver = c.register(Receiver())
        caller = c.register(Caller(receiver.addr))

        async with activate(c):
          await receiver.wait_for_reply

    asyncio.run(main())

.. testoutput::

  Received a message with the following content Hello World.

This code will terminate after 0.5 seconds.
If you change the clock to an ``ExternalClock`` in the example above,
the program won't terminate as the time of the clock is not proceeded by an external process.
If you comment in the ExternalClock and change your main() as follows, the program will terminate after one second:

.. testcode::

    async def main():

        clock = ExternalClock(start_time=1000)
        addr = ('127.0.0.1', 5555)
        c = create_tcp_container(addr=addr, clock=clock)
        receiver = c.register(Receiver())
        caller = c.register(Caller(receiver.addr))

        async with activate(c):
          await asyncio.sleep(1)
          clock.set_time(clock.time + 0.5)
          await receiver.wait_for_reply

    asyncio.run(main())

.. testoutput::

    Received a message with the following content Hello World.

*******************************
Using a distributed clock
*******************************
To distribute simulations, mango provides a distributed clock, which is implemented with by two Agents:
1. DistributedClockAgent: this agent needs to be present in every participating container
2. DistributedClockManager: this agent shall exist exactly once

The clock is distributed by an DistributedClockManager Agent on the managing container, which listens to the current time.

1. In the other container DistributedClockAgent's are running, which listen to messages from the ClockManager.
2. The ClockAgent sets the received time on the clock of its container with `set_time` and responds with its `get_next_activity()` after making sure that all tasks which are due at the current timestamp are finished.
3. The ClockManager only acts after all connected Containers have finished and have sent their next timestamp as response.
4. The response is then added as a Future on the manager, which makes sure, that the managers `get_next_activity()` shows the next action needed to run on all containers.

Caution: it is needed, that all agents are connected before starting the manager

In the following a simple example is shown.

.. testcode::

  import asyncio
  from mango import DistributedClockAgent, DistributedClockManager, create_tcp_container, activate, ExternalClock

  async def main():
    container_man = create_tcp_container(("localhost", 1555), clock=ExternalClock())
    container_ag = create_tcp_container(("localhost", 1556), clock=ExternalClock())

    clock_agent = container_ag.register(DistributedClockAgent())
    clock_manager = container_man.register(DistributedClockManager(
      receiver_clock_addresses=[clock_agent.addr]
    ))

    async with activate(container_man, container_ag) as cl:
        # increasing the time
        container_man.clock.set_time(100)
        # first distribute the time - then wait for the agent to finish
        next_event = await clock_manager.distribute_time()
        # here no second distribute to wait for retrieval is needed
        # the clock_manager distributed the time to the other container
        assert container_ag.clock.time == 100
        print("Time has been distributed!")

  asyncio.run(main())
.. testoutput::

    Time has been distributed!
