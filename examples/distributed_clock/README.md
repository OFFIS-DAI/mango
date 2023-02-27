# Distributed Simulation

To run a distributed simulation, a clock manager is used.
The clock is distributed by an DistributedClockManager Agent on the managing container, which listens to the current time.

1. In the other container a DistributedClockAgent is running, which listens to messages from the other container.
2. The ClockAgent sets the received time on the clock of its container with `set_time` and responds with its `get_next_activity()` after making sure that all tasks which are due at the current timestamp are finished.
3. The ClockManager only acts after all connected Containers have finished and have sent their next timestamp as response.
4. The response is then added as a Future on the manager, which makes sure, that the managers `get_next_activity()` shows the next action needed to run on all containers.

A simple example is added here, which sends messages every 900 seconds from the manager to the agent, while the agent itself sends a message every 300 seconds to its manager.
If the `clock_agent`s routine takes longer, the manager will wait for it to finish before setting the next time.

Caution: it is needed, that all agents are connected before starting the manager

This example is tested with MQTT as well as by using TCP connection