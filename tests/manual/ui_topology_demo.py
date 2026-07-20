"""
Manual demo for the topology UI — not part of the automated test suite
(pytest won't collect this; filename doesn't match test_*.py).

Two containers, three agents with declared connections.
Run with: python -m tests.manual.ui_topology_demo
Then open http://localhost:8000 — Ctrl+C to stop.
"""

import asyncio

from mango import Agent, activate, create_tcp_container


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
    sensor = SensorAgent()
    processor = ProcessorAgent()
    monitor = MonitorAgent(visible=True)

    # register agents to containers
    container_a.register(sensor, suggested_aid="sensor")
    container_a.register(processor, suggested_aid="processor")
    container_b.register(monitor, suggested_aid="monitor")

    # ui=True starts the topology UI server and registers every container
    # passed to activate() with it once they're running
    async with activate(container_a, container_b, ui=True) as (
        container_a,
        container_b,
    ):
        # any container's `.registry` is the same shared TopologyRegistry instance
        container_a.registry.add_connection(
            sensor.addr, processor.addr, conn_type="internal", direction="uni"
        )
        container_a.registry.add_connection(
            processor.addr, monitor.addr, conn_type="tcp", direction="uni"
        )
        container_a.registry.add_connection(
            monitor.addr, sensor.addr, conn_type="tcp", direction="bi"
        )

        print("Topology UI: http://localhost:8000")
        print("Press Ctrl+C to stop.\n")

        # run health checks every 3 seconds
        while True:
            await asyncio.sleep(3)
            await container_a.registry.check_health()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
