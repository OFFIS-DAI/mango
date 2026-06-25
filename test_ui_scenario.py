"""
Simple scenario to test the TopologyRegistry UI.

Two containers, three agents with declared connections.
Open http://localhost:8000 after running this script.
Press Ctrl+C to stop.
"""

import asyncio
import sys
sys.path.insert(0, "/home/b2k/Desktop/Season2/mango")

from mango import Agent, create_tcp_container, activate, addr
from mango.ui import TopologyRegistry


class SensorAgent(Agent):
    def handle_message(self, content, meta):
        print(f"[SensorAgent] received: {content}")

    async def health_check(self):
        return "active"


class ProcessorAgent(Agent):
    def handle_message(self, content, meta):
        print(f"[ProcessorAgent] received: {content}")

    async def health_check(self):
        return "active"


class MonitorAgent(Agent):
    def handle_message(self, content, meta):
        print(f"[MonitorAgent] received: {content}")

    async def health_check(self):
        return "idle"


async def main():
    # two containers on different ports
    container_a = create_tcp_container(addr=("127.0.0.1", 5555))
    container_b = create_tcp_container(addr=("127.0.0.1", 5556))

    # create agents
    sensor    = SensorAgent()
    processor = ProcessorAgent()
    monitor   = MonitorAgent(visible=True)

    # register agents to containers
    container_a.register(sensor,    suggested_aid="sensor")
    container_a.register(processor, suggested_aid="processor")
    container_b.register(monitor,   suggested_aid="monitor")

    # set up registry — must happen after agents are registered
    # so the backfill in register() picks them up
    registry = TopologyRegistry()
    registry.register(container_a)
    registry.register(container_b)

    # declare connections
    registry.add_connection(sensor.addr,    processor.addr, conn_type="internal", direction="uni")
    registry.add_connection(processor.addr, monitor.addr,   conn_type="tcp",      direction="uni")
    registry.add_connection(monitor.addr,   sensor.addr,    conn_type="tcp",      direction="bi")

    async with activate(container_a, container_b):
        # start UI server inside the running loop
        await registry.start_server(host="localhost", port=8000)
        print("Topology UI: http://localhost:8000")
        print("Press Ctrl+C to stop.\n")

        # run health checks every 3 seconds
        while True:
            await asyncio.sleep(3)
            await registry.check_health()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
