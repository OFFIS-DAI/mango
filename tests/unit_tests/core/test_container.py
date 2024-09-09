import pytest

import mango.container.factory as container_factory
from mango.agent.core import Agent


class LooksLikeAgent:
    async def shutdown(self):
        pass


@pytest.mark.asyncio
async def test_register_aid_pattern_match():
    # GIVEN
    c = await container_factory.create(addr=("127.0.0.2", 5555))
    agent = LooksLikeAgent()
    suggested_aid = "agent12"

    # WHEN
    actual_aid = c.register_agent(agent, suggested_aid)

    # THEN
    assert actual_aid == "agent0"
    await c.shutdown()


@pytest.mark.asyncio
async def test_register_aid_success():
    # GIVEN
    c = await container_factory.create(addr=("127.0.0.2", 5555))
    agent = LooksLikeAgent()
    suggested_aid = "cagent12"

    # WHEN
    actual_aid = c.register_agent(agent, suggested_aid)

    # THEN
    assert actual_aid == suggested_aid
    await c.shutdown()


@pytest.mark.asyncio
async def test_register_no_suggested():
    # GIVEN
    c = await container_factory.create(addr=("127.0.0.2", 5555))
    agent = LooksLikeAgent()

    # WHEN
    actual_aid = c.register_agent(agent)

    # THEN
    assert actual_aid == "agent0"
    await c.shutdown()


@pytest.mark.asyncio
async def test_register_pattern_half_match():
    # GIVEN
    c = await container_factory.create(addr=("127.0.0.2", 5555))
    agent = LooksLikeAgent()
    suggested_aid = "agentABC"

    # WHEN
    actual_aid = c.register_agent(agent, suggested_aid)

    # THEN
    assert actual_aid == "agentABC"
    await c.shutdown()


@pytest.mark.asyncio
async def test_register_existing():
    # GIVEN
    c = await container_factory.create(addr=("127.0.0.2", 5555))
    agent = LooksLikeAgent()
    suggested_aid = "agentABC"

    # WHEN
    actual_aid = c.register_agent(agent, suggested_aid)
    actual_aid2 = c.register_agent(agent, suggested_aid)

    # THEN
    assert actual_aid == "agentABC"
    assert actual_aid2 == "agent0"
    await c.shutdown()


@pytest.mark.asyncio
async def test_is_aid_available():
    # GIVEN
    c = await container_factory.create(addr=("127.0.0.2", 5555))
    aid_to_check = "agentABC"

    # WHEN
    available = c.is_aid_available(aid_to_check)

    # THEN
    assert available
    await c.shutdown()


@pytest.mark.asyncio
async def test_is_aid_available_but_match():
    # GIVEN
    c = await container_factory.create(addr=("127.0.0.2", 5555))
    aid_to_check = "agent5"

    # WHEN
    available = c.is_aid_available(aid_to_check)

    # THEN
    assert not available
    await c.shutdown()


@pytest.mark.asyncio
async def test_is_aid_not_available():
    # GIVEN
    c = await container_factory.create(addr=("127.0.0.2", 5555))
    c.register_agent(LooksLikeAgent(), "abc")
    aid_to_check = "abc"

    # WHEN
    available = c.is_aid_available(aid_to_check)

    # THEN
    assert not available
    await c.shutdown()


@pytest.mark.asyncio
async def test_is_aid_not_available_and_match():
    # GIVEN
    c = await container_factory.create(addr=("127.0.0.2", 5555))
    c.register_agent(LooksLikeAgent())
    aid_to_check = "agent0"

    # WHEN
    available = c.is_aid_available(aid_to_check)

    # THEN
    assert not available
    await c.shutdown()


@pytest.mark.asyncio
async def test_create_acl_no_modify():
    c = await container_factory.create(addr=("127.0.0.2", 5555))
    common_acl_q = {}
    actual_acl_message = c._create_acl(
        "", receiver_addr="", receiver_id="", acl_metadata=common_acl_q
    )

    assert "reeiver_addr" not in common_acl_q
    assert "receiver_id" not in common_acl_q
    assert "sender_addr" not in common_acl_q
    assert actual_acl_message.sender_addr is not None
    await c.shutdown()


@pytest.mark.asyncio
async def test_create_acl_anon():
    c = await container_factory.create(addr=("127.0.0.2", 5555))
    actual_acl_message = c._create_acl(
        "", receiver_addr="", receiver_id="", is_anonymous_acl=True
    )

    assert actual_acl_message.sender_addr is None
    await c.shutdown()


@pytest.mark.asyncio
async def test_create_acl_not_anon():
    c = await container_factory.create(addr=("127.0.0.2", 5555))
    actual_acl_message = c._create_acl(
        "", receiver_addr="", receiver_id="", is_anonymous_acl=False
    )

    assert actual_acl_message.sender_addr is not None
    await c.shutdown()


class ExampleAgent(Agent):
    def handle_message(self, content, meta):
        self.content = content


class Data:
    i = 0


@pytest.mark.asyncio
async def test_send_message_no_copy():
    c = await container_factory.create(
        addr=("127.0.0.2", 5555), copy_internal_messages=False
    )
    agent1 = ExampleAgent(c)
    message_to_send = Data()

    await c.send_acl_message(
        message_to_send, receiver_addr=c.addr, receiver_id=agent1.aid
    )
    await c.shutdown()

    assert agent1.content is message_to_send


@pytest.mark.asyncio
async def test_send_message_copy():
    c = await container_factory.create(
        addr=("127.0.0.2", 5555), copy_internal_messages=True
    )
    agent1 = ExampleAgent(c)
    message_to_send = Data()

    await c.send_acl_message(
        message_to_send, receiver_addr=c.addr, receiver_id=agent1.aid
    )
    await c.shutdown()

    assert agent1.content is not message_to_send


@pytest.mark.asyncio
async def test_create_acl_diff_receiver():
    c = await container_factory.create(addr=("127.0.0.2", 5555))
    with pytest.warns(UserWarning) as record:
        actual_acl_message = c._create_acl(
            "",
            receiver_addr="A",
            receiver_id="A",
            acl_metadata={"receiver_id": "B", "receiver_addr": "B"},
            is_anonymous_acl=False,
        )

    assert actual_acl_message.receiver_addr == "A"
    assert actual_acl_message.receiver_id == "A"
    assert len(record) == 2
    await c.shutdown()


@pytest.mark.asyncio
async def test_containers_dont_share_default_codec():
    c1 = await container_factory.create(addr=("127.0.0.2", 5555))
    c2 = await container_factory.create(addr=("127.0.0.2", 5556))

    assert c1.codec is not c2.codec

    await c1.shutdown()
    await c2.shutdown()
