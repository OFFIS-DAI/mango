import asyncio

import mango.messages.other_proto_msgs_pb2 as other_proto_msg
from examples.simple_agent import SimpleAgent
from mango import create_container


async def one_container_two_agents():
    # ip and port of container
    addr1 = ("127.0.0.1", 5555)
    from mango.messages.codecs import JSON

    codec = JSON()
    container1 = await create_container(connection_type="tcp", codec=codec, addr=addr1)
    agent_a = SimpleAgent(container1, codec=codec)

    agent_id1 = agent_a.aid

    # initialize an agent in container2 and tell him the id of the first agent
    agent_b = SimpleAgent(container1, agent_id1, addr1, codec=codec)

    # let agent_b start a conversation by sending a greeting
    await agent_b.send_greeting()
    try:
        # wait for all agents to be shutdown in the container
        await asyncio.wait_for(asyncio.gather(container1._no_agents_running), timeout=3)
    except KeyboardInterrupt:
        print("KeyboardInterrupt")
    finally:
        print(f"[{addr1}]: Shutting down container")
        await container1.shutdown()


def two_container_two_agents():
    addr1 = (("127.0.0.1", 5555),)
    addr2 = (("127.0.0.1", 5555),)


if __name__ == "__main__":
    import logging

    logging.basicConfig(level="INFO")

    asyncio.run(one_container_two_agents())
