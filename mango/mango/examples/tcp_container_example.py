import asyncio

import mango.messages.other_proto_msgs_pb2 as  other_proto_msg
from mango.core.container import Container
from mango.examples.simple_agent import SimpleAgent


async def one_container_two_agents():
    # ip and port of container
    addr1 = ('127.0.0.1', 5555)
    container1 = await Container.factory(connection_type='tcp',
                                         codec='json', addr=addr1)
    agent_a = SimpleAgent(container1)

    agent_id1 = agent_a._aid

    # initialize an agent in container2 and tell him the id of the first agent
    agent_b = SimpleAgent(container1, agent_id1, addr1)

    # let agent_b start a conversation by sending a greeting
    await agent_b.send_greeting()
    try:
        # wait for all agents to be shutdown in the container
        await asyncio.wait_for(asyncio.gather(container1._no_agents_running),
            timeout=3)
    except KeyboardInterrupt:
        print('KeyboardInterrupt')
    finally:
        print(f'[{addr1}]: Shutting down container')
        await container1.shutdown()



def two_container_two_agents():
    addr1 = ('127.0.0.1', 5555),
    addr2 = ('127.0.0.1', 5555),
    codec = 'protobuf',
    proto_msgs_module = other_proto_msg
    pass


if __name__ == '__main__':
    asyncio.run(one_container_two_agents())
