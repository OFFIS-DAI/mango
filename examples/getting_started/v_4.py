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


class HelloWorldAgent(Agent):
    def __init__(self, container, other_addr, other_id):
        super().__init__(container)
        self.schedule_instant_acl_message(
            receiver_addr=other_addr,
            receiver_id=other_id,
            content="Hello world!",
        )

    def handle_message(self, content, meta):
        print(f"Received a message with the following content: {content}")


async def run_container_and_two_agents(first_addr, second_addr):
    first_container = await create_container(addr=first_addr)
    second_container = await create_container(addr=second_addr)
    first_agent = RepeatingAgent(first_container)
    second_agent = HelloWorldAgent(
        second_container, first_container.addr, first_agent.aid
    )
    await asyncio.sleep(1)
    await first_agent.shutdown()
    await second_agent.shutdown()
    await first_container.shutdown()
    await second_container.shutdown()


if __name__ == "__main__":
    asyncio.run(
        run_container_and_two_agents(
            first_addr=("localhost", 5555), second_addr=("localhost", 5556)
        )
    )
