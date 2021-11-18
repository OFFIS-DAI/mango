==========
Scheduling
==========

When implementing agents including proactive behavior there are some typical types of tasks you might want to create. For example it might be desired to let the agent check every minute whether some resources are available, or often you just want to execute a task at a specified time. To help achieving this kind of goals mango exposes the scheduling API.

The core of this API is the scheduler, which is part of every agent. To schedule a task its necessary to create a ScheduledTask (resp. an object of a subclass). In mango the following tasks are available:

.. list-table:: Available ScheduledTasks
   :widths: 30 70
   :header-rows: 1

   * - Class
     - Description
   * - InstantScheduledTask
     - Executes the coroutine without delay
   * - DateTimeScheduledTask
     - Executes the coroutine at a specified datetime
   * - PeriodicScheduledTask
     - Executes a coroutine periodically with a static delay between the cycles
   * - ConditionalScheduledTask
     - Executes the coroutine when a specified condition evaluates to True

Furthermore there are convenience methods to get rid of the class imports when using these types of tasks.

.. code-block:: python3

    from mango.core.agent import Agent
    from mango.util.scheduling import InstantScheduledTask

        class ScheduleAgent(Agent):
            def __init__(self, container, other_addr, other_id):
                self.schedule_instant_task(coroutine=self._container.send_message(
                    receiver_addr=other_addr,
                    receiver_id=other_id,
                    content="Hello world!",
                    create_acl=True)
                )
                # equivalent to
                self.schedule_task(InstantScheduledTask(coroutine=self._container.send_message(
                    receiver_addr=other_addr,
                    receiver_id=other_id,
                    content="Hello world!",
                    create_acl=True))
                )

            def handle_msg(self, content, meta: Dict[str, Any]):
                pass


When using the scheduling another feature becomes available: suspendable tasks. This makes it possible to pause and resume all tasks started with the scheduling API. Using this it is necessary to specify an identifier when starting the task (using `src=your_identifier`). To suspend a task you can call `scheduler.suspend(your_identifier)`, to resume them just call the counterpart `scheduler.resume(your_identifier)`. The scheduler is part of the agent and accessible via `self._scheduler`.