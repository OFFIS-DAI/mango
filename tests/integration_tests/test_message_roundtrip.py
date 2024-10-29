import asyncio

import pytest

from mango import activate, addr
from mango.agent.core import Agent
from mango.messages.codecs import JSON, PROTOBUF

from ..unit_tests.messages.msg_pb2 import MyMsg
from . import create_test_container

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
    init_addr = ("127.0.0.1", 1555) if connection_type == "tcp" else "c1"
    repl_addr = ("127.0.0.1", 1556) if connection_type == "tcp" else "c2"

    container_1, container_2 = create_test_container(
        connection_type, init_addr, repl_addr, codec
    )

    if connection_type == "mqtt":
        init_target = repl_target = comm_topic
    else:
        init_target = repl_addr
        repl_target = init_addr

    init_agent = container_1.register(InitiatorAgent(container_1))
    repl_agent = container_2.register(ReplierAgent(container_2))
    repl_agent.target = addr(repl_target, init_agent.aid)
    init_agent.target = addr(init_target, repl_agent.aid)

    async with activate(container_1, container_2) as cl:
        await asyncio.gather(repl_agent.start(), init_agent.start())


# InitiatorAgent:
# - send "Hello"
# - awaits reply
# - answers to reply
# - shuts down
class InitiatorAgent(Agent):
    def __init__(self, container):
        super().__init__()
        self.target = None
        self.got_reply = asyncio.Future()
        self.container = container

    def handle_message(self, content, meta):
        if content == M2:
            self.got_reply.set_result(True)

    async def start(self):
        if getattr(self.container, "subscribe_for_agent", None):
            await self.container.subscribe_for_agent(
                aid=self.aid, topic=self.target.protocol_addr
            )

        await asyncio.sleep(0.1)

        # send initial message
        await self.send_message(M1, self.target)

        # await reply
        await self.got_reply

        # answer to reply
        await self.send_message(M3, self.target)


# ReplierAgent:
# - awaits "Hello"
# - sends reply
# - awaits reply
# - shuts down
class ReplierAgent(Agent):
    def __init__(self, container):
        super().__init__()
        self.target = None
        self.other_aid = None

        self.got_first = asyncio.Future()
        self.got_second = asyncio.Future()

        self.container = container

    def handle_message(self, content, meta):
        if content == M1:
            self.got_first.set_result(True)
        elif content == M3:
            self.got_second.set_result(True)

    async def start(self):
        if getattr(self.container, "subscribe_for_agent", None):
            await self.container.subscribe_for_agent(
                aid=self.aid, topic=self.target.protocol_addr
            )

        # await "Hello"
        await self.got_first

        # send reply
        await self.send_message(M2, self.target, receiver_id=self.other_aid)

        # await reply
        await self.got_second


@pytest.mark.asyncio
async def test_tcp_json():
    await setup_and_run_test_case("tcp", JSON_CODEC)


@pytest.mark.asyncio
async def test_tcp_proto():
    await setup_and_run_test_case("tcp", PROTO_CODEC)


"""
@pytest.mark.asyncio
async def test_tcp_fast_json():
    await setup_and_run_test_case("tcp", FAST_JSON_CODEC)


@pytest.mark.asyncio
@pytest.mark.mqtt
async def test_mqtt_fast_json():
    await setup_and_run_test_case("mqtt", FAST_JSON_CODEC)
"""


@pytest.mark.asyncio
@pytest.mark.mqtt
async def test_mqtt_json():
    await setup_and_run_test_case("mqtt", JSON_CODEC)


@pytest.mark.asyncio
@pytest.mark.mqtt
async def test_mqtt_proto():
    await setup_and_run_test_case("mqtt", PROTO_CODEC)


@pytest.mark.asyncio
@pytest.mark.mqtt
async def test_mqtt_minimal_json():
    await asyncio.wait_for(setup_and_run_test_case("mqtt_minimal", JSON_CODEC), 1)


@pytest.mark.asyncio
@pytest.mark.mqtt
async def test_mqtt_minimal_proto():
    await asyncio.wait_for(setup_and_run_test_case("mqtt_minimal", PROTO_CODEC), 1)