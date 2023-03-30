import asyncio

from mango import Agent, create_container

"""
For your first mango tutorial you will learn the fundamentals of creating mango agents and containers as well
as making them communicate with each other.

This example covers:
    - container
    - agent creation
    - basic message passing
    - clean shutdown of containers
"""

PV_CONTAINER_ADDRESS = ("localhost", 5555)


class PVAgent(Agent):
    def __init__(self, container):
        super().__init__(container)
        print(f"Hello I am a PV agent! My id is {self.aid}.")

    def handle_message(self, content, meta):
        print(f"Received message with content: {content} and meta {meta}.")


async def main():
    # defaults to tcp connection
    pv_container = await create_container(addr=PV_CONTAINER_ADDRESS)

    # agents always live inside a container
    pv_agent_0 = PVAgent(pv_container)
    pv_agent_1 = PVAgent(pv_container)

    # we can now send a simple message to an agent and observe that it is received:
    # Note that as of now agent IDs are set automatically as agent0, agent1, ... in order of instantiation.
    await pv_container.send_message(
        "Hello, this is a simple message.",
        receiver_addr=PV_CONTAINER_ADDRESS,
        receiver_id="agent0",
    )

    # don't forget to properly shut down containers at the end of your program
    # otherwise you will get an asyncio.exceptions.CancelledError
    await pv_container.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
