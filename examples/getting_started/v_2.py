import asyncio

from mango import Agent, create_container


class RepeatingAgent(Agent):
    def __init__(self, container):
        # We must pass a ref. to the container to "mango.Agent":
        super().__init__(container)
        print(f"Hello world! My id is {self.aid}.")

    def handle_message(self, content, meta):
        # This method defines what the agent will do with incoming messages.
        print(f"Received a message with the following content: {content}")


async def run_container_and_agent(addr, duration):
    first_container = await create_container(addr=addr)
    RepeatingAgent(first_container)
    await asyncio.sleep(duration)
    await first_container.shutdown()


asyncio.run(run_container_and_agent(addr=("localhost", 5555), duration=3))
