====================
Scheduling and Clock
====================

Agents in mango can be *reactive* (responding to messages) or *proactive*
(initiating actions on a schedule).  The scheduling API lets you express
both simple one-shot tasks and complex recurrence patterns.

Every agent owns a *scheduler*.  To schedule work you call one of the
convenience methods on the agent (e.g. :meth:`~mango.Agent.schedule_periodic_task`)
or create a :class:`~mango.util.scheduling.ScheduledTask` subclass directly.

Available task types
--------------------

.. list-table::
   :widths: 35 65
   :header-rows: 1

   * - Class / convenience method
     - Description
   * - ``InstantScheduledTask`` / ``schedule_instant_task``
     - Runs the coroutine on the next event-loop iteration (no delay).
   * - ``TimestampScheduledTask`` / ``schedule_timestamp_task``
     - Runs the coroutine at the specified clock timestamp.
   * - ``PeriodicScheduledTask`` / ``schedule_periodic_task``
     - Runs a coroutine function repeatedly with a fixed delay between cycles.
   * - ``RecurrentScheduledTask`` / ``schedule_recurrent_task``
     - Runs a coroutine according to a dynamic schedule provided by a
       `dateutil rrule <https://dateutil.readthedocs.io/en/stable/rrule.html>`_.
   * - ``ConditionalScheduledTask`` / ``schedule_conditional_task``
     - Runs the coroutine as soon as a condition function returns ``True``.
   * - ``AwaitingTask`` / ``schedule_awaiting_task``
     - Awaits one coroutine, then runs another.

For every regular task type there is a matching *process* variant (e.g.
``PeriodicScheduledProcessTask``) that dispatches work to a subprocess — see
`Dispatching tasks to other processes`_ below.

Basic example
-------------

.. testcode::

    import asyncio
    from mango import Agent, run_with_tcp

    class ScheduleAgent(Agent):
        def on_ready(self):
            self.schedule_periodic_task(self.say_hello, delay=0.05)

        async def say_hello(self):
            print("Hello!")

    async def run():
        async with run_with_tcp(1, ScheduleAgent()) as container:
            await asyncio.sleep(0.12)  # let three cycles run

    asyncio.run(run())

.. testoutput::

    Hello!
    Hello!
    Hello!

Suspendable tasks
-----------------

Every task can be *suspended* and *resumed* by passing a ``src`` identifier
when scheduling it.  This is particularly useful for the role system
(see :doc:`role-api`) where an entire role can be put on hold.

.. code-block:: python

    class MyAgent(Agent):
        def on_ready(self):
            self.schedule_periodic_task(
                self.do_work, delay=1.0, src="worker"
            )

        def pause(self):
            self.scheduler.suspend("worker")

        def resume(self):
            self.scheduler.resume("worker")

        async def do_work(self):
            ...


Dispatching tasks to other processes
-------------------------------------

asyncio provides concurrency but not parallelism — CPU-bound work blocks the
event loop.  mango lets you offload heavy computation to a managed worker
process pool.

Use the ``_process_`` variants of the scheduling convenience methods:

.. code-block:: python

    class HeavyAgent(Agent):
        def on_ready(self):
            self.schedule_periodic_process_task(
                self.crunch_numbers, delay=1.0
            )

        async def crunch_numbers(self):
            # runs in a worker process
            return sum(range(10_000_000))

The available process task types mirror the regular task types:

.. list-table::
   :widths: 35 65
   :header-rows: 1

   * - Class
     - Description
   * - ``InstantScheduledProcessTask``
     - One-shot task run immediately in a subprocess.
   * - ``TimestampScheduledProcessTask``
     - One-shot task run at a given timestamp in a subprocess.
   * - ``PeriodicScheduledProcessTask``
     - Periodic task run in a subprocess.
   * - ``RecurrentScheduledProcessTask``
     - Recurrent task run in a subprocess.
   * - ``ConditionalProcessTask``
     - Condition-based task run in a subprocess.
   * - ``AwaitingProcessTask``
     - Await a coroutine, then run another in a subprocess.


.. _ClockDocs:

Using an external clock
------------------------

By default the scheduler uses :class:`~mango.AsyncioClock`, which ties
simulation time to wall-clock time.  Switch to :class:`~mango.ExternalClock`
when you need to control time externally — for example in a simulation that
runs faster (or slower) than real time.

.. testcode::

    import asyncio
    from mango import create_tcp_container, Agent, AsyncioClock, ExternalClock, activate

    class Caller(Agent):
        def __init__(self, receiver_addr):
            super().__init__()
            self.receiver_addr = receiver_addr

        def on_ready(self):
            self.schedule_timestamp_task(
                coroutine=self.send_hello_world(self.receiver_addr),
                timestamp=self.current_timestamp + 0.5,
            )

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

This terminates after roughly 0.5 seconds.  If you switch to
``ExternalClock`` and never call ``set_time`` the program would hang —
the task is waiting for a timestamp that never arrives:

.. testcode::

    async def main():
        clock = ExternalClock(start_time=1000)
        addr = ('127.0.0.1', 5555)
        c = create_tcp_container(addr=addr, clock=clock)
        receiver = c.register(Receiver())
        caller = c.register(Caller(receiver.addr))

        async with activate(c):
            await asyncio.sleep(1)
            clock.set_time(clock.time + 0.5)  # advance the clock manually
            await receiver.wait_for_reply

    asyncio.run(main())

.. testoutput::

    Received a message with the following content Hello World.

.. note::
    When using :class:`~mango.SimulationWorld` the clock is managed
    automatically by :func:`~mango.step_simulation`.  You do not need to
    call ``set_time`` yourself.  See :doc:`simulation` for details.


Using a distributed clock
--------------------------

For simulations that span *multiple* containers mango provides a distributed
clock, implemented as two agents:

* :class:`~mango.DistributedClockManager` — runs once on the managing
  container; decides when to advance time.
* :class:`~mango.DistributedClockAgent` — runs in every participating
  container; synchronises the local :class:`~mango.ExternalClock` with the
  manager.

The protocol works as follows:

1. The manager calls ``distribute_time()`` to broadcast a new timestamp.
2. Each ``DistributedClockAgent`` calls ``set_time`` on its container's clock
   and waits for all local tasks that are due to finish.
3. Each agent replies with its ``get_next_activity()`` timestamp.
4. The manager collects all replies and only proceeds after every container
   has responded, ensuring a consistent global time step.

.. warning::
    All agents must be connected to the manager **before** the first call to
    ``distribute_time()``.

.. testcode::

    import asyncio
    from mango import (
        DistributedClockAgent, DistributedClockManager,
        create_tcp_container, activate, ExternalClock,
    )

    async def main():
        container_man = create_tcp_container(("127.0.0.1", 1555), clock=ExternalClock())
        container_ag  = create_tcp_container(("127.0.0.1", 1556), clock=ExternalClock())

        clock_agent   = container_ag.register(DistributedClockAgent())
        clock_manager = container_man.register(DistributedClockManager(
            receiver_clock_addresses=[clock_agent.addr]
        ))

        async with activate(container_man, container_ag):
            container_man.clock.set_time(100)
            await clock_manager.distribute_time()
            assert container_ag.clock.time == 100
            print("Time has been distributed!")

    asyncio.run(main())

.. testoutput::

    Time has been distributed!

.. seealso::

    :doc:`simulation` — ``SimulationWorld`` manages time automatically for
    single-process simulations.
