import pytest
from mango.core.agent import Agent
from mango.core.container import Container
from mango.messages.codecs import JSON, PROTOBUF
from msg_pb2 import MyMsg
import asyncio

M1 = "Hello"
M2 = "Hello2"
M3 = "Goodbye"


def str_to_proto(my_str):
    msg = MyMsg()
    msg.content = bytes(my_str, "utf-8")
    return msg


def proto_to_str(data):
    msg = MyMsg()
    msg.ParseFromString(data)
    return msg.content.decode("utf-8")


def string_serializer():
    return (str, str_to_proto, proto_to_str)


JSON_CODEC = JSON()
PROTO_CODEC = PROTOBUF()
PROTO_CODEC.add_serializer(*string_serializer())


async def setup_and_run_test_case(connection_type, codec):
    comm_topic = "test_topic"
    init_addr = ("localhost", 1555) if connection_type == "tcp" else None
    repl_addr = ("localhost", 1556) if connection_type == "tcp" else None

    broker = ("localhost", 1883, 60)
    mqtt_kwargs_1 = {
        "client_id": "container_1",
        "broker_addr": broker,
        "transport": "tcp",
    }

    mqtt_kwargs_2 = {
        "client_id": "container_2",
        "broker_addr": broker,
        "transport": "tcp",
    }

    container_1 = await Container.factory(
        connection_type=connection_type,
        codec=codec,
        addr=init_addr,
        mqtt_kwargs=mqtt_kwargs_1,
    )
    container_2 = await Container.factory(
        connection_type=connection_type,
        codec=codec,
        addr=repl_addr,
        mqtt_kwargs=mqtt_kwargs_2,
    )

    if connection_type == "mqtt":
        init_target = repl_target = comm_topic
    else:
        init_target = repl_addr
        repl_target = init_addr

    init_agent = InitiatorAgent(container_1, init_target)
    repl_agent = ReplierAgent(container_2, repl_target)

    repl_agent.other_aid = init_agent._aid
    init_agent.other_aid = repl_agent._aid

    await asyncio.gather(repl_agent.start(), init_agent.start())
    await asyncio.gather(
        container_1.shutdown(),
        container_2.shutdown(),
    )


# InitiatorAgent:
# - send "Hello"
# - awaits reply
# - answers to reply
# - shuts down
class InitiatorAgent(Agent):
    def __init__(self, container, target):
        super().__init__(container)
        self.target = target
        self.other_aid = None

        self.got_reply = asyncio.Future()

    def handle_msg(self, content, meta):
        if content == M2:
            self.got_reply.set_result(True)

    async def start(self):
        if getattr(self._container, "subscribe_for_agent", None):
            await self._container.subscribe_for_agent(aid=self.aid, topic=self.target)

        await asyncio.sleep(0.1)

        # send initial message
        await self._container.send_message(
            M1,
            self.target,
            receiver_id=self.other_aid,
            create_acl=True,
        )

        # await reply
        await self.got_reply

        # answer to reply
        await self._container.send_message(
            M3,
            self.target,
            receiver_id=self.other_aid,
            create_acl=True,
        )

        # shut down
        pass


# ReplierAgent:
# - awaits "Hello"
# - sends reply
# - awaits reply
# - shuts down
class ReplierAgent(Agent):
    def __init__(self, container, target):
        super().__init__(container)
        self.target = target
        self.other_aid = None

        self.got_first = asyncio.Future()
        self.got_second = asyncio.Future()

    def handle_msg(self, content, meta):
        if content == M1:
            self.got_first.set_result(True)
        elif content == M3:
            self.got_second.set_result(True)

    async def start(self):
        if getattr(self._container, "subscribe_for_agent", None):
            await self._container.subscribe_for_agent(aid=self.aid, topic=self.target)

        # await "Hello"
        await self.got_first

        # send reply
        await self._container.send_message(
            M2,
            self.target,
            receiver_id=self.other_aid,
            create_acl=True,
        )

        # await reply
        await self.got_second

        # shut down
        pass


@pytest.mark.asyncio
async def test_tcp_json():
    await setup_and_run_test_case("tcp", JSON_CODEC)


@pytest.mark.asyncio
async def test_tcp_proto():
    await setup_and_run_test_case("tcp", PROTO_CODEC)


@pytest.mark.asyncio
async def test_mqtt_json():
    await setup_and_run_test_case("mqtt", JSON_CODEC)


@pytest.mark.asyncio
async def test_mqtt_proto():
    await setup_and_run_test_case("mqtt", PROTO_CODEC)
