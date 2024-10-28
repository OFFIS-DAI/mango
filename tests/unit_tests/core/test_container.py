import pytest

from mango import activate, create_acl, create_tcp_container
from mango.agent.core import Agent, AgentAddress


class LooksLikeAgent:
    context = None

    async def shutdown(self):
        pass

    def _do_register(self, container, aid):
        pass


@pytest.mark.asyncio
async def test_register_aid_pattern_match():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    agent = LooksLikeAgent()
    suggested_aid = "agent12"

    # WHEN
    agent_r = c.register(agent, suggested_aid)

    # THEN
    assert c._get_aid(agent_r) == "agent0"
    await c.shutdown()


@pytest.mark.asyncio
async def test_register_aid_success():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    agent = LooksLikeAgent()
    suggested_aid = "cagent12"

    # WHEN
    agent_r = c.register(agent, suggested_aid)

    # THEN
    assert c._get_aid(agent_r) == suggested_aid
    await c.shutdown()


@pytest.mark.asyncio
async def test_register_no_suggested():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    agent = LooksLikeAgent()

    # WHEN
    agent_r = c.register(agent)

    # THEN
    assert c._get_aid(agent_r) == "agent0"
    await c.shutdown()


@pytest.mark.asyncio
async def test_register_pattern_half_match():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    agent = LooksLikeAgent()
    suggested_aid = "agentABC"

    # WHEN
    agent_r = c.register(agent, suggested_aid)

    # THEN
    assert c._get_aid(agent_r) == "agentABC"
    await c.shutdown()


@pytest.mark.asyncio
async def test_register_existing():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    agent = LooksLikeAgent()
    agent2 = LooksLikeAgent()
    suggested_aid = "agentABC"

    # WHEN
    agent_r = c.register(agent, suggested_aid)
    agent_r2 = c.register(agent2, suggested_aid)

    # THEN
    assert c._get_aid(agent_r) == "agentABC"
    assert c._get_aid(agent_r2) == "agent0"
    await c.shutdown()


@pytest.mark.asyncio
async def test_is_aid_available():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    aid_to_check = "agentABC"

    # WHEN
    available = c.is_aid_available(aid_to_check)

    # THEN
    assert available
    await c.shutdown()


@pytest.mark.asyncio
async def test_is_aid_available_but_match():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    aid_to_check = "agent5"

    # WHEN
    available = c.is_aid_available(aid_to_check)

    # THEN
    assert not available
    await c.shutdown()


@pytest.mark.asyncio
async def test_is_aid_not_available():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    c.register(LooksLikeAgent(), "abc")
    aid_to_check = "abc"

    # WHEN
    available = c.is_aid_available(aid_to_check)

    # THEN
    assert not available
    await c.shutdown()


@pytest.mark.asyncio
async def test_is_aid_not_available_and_match():
    # GIVEN
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    c.register(LooksLikeAgent())
    aid_to_check = "agent0"

    # WHEN
    available = c.is_aid_available(aid_to_check)

    # THEN
    assert not available
    await c.shutdown()


@pytest.mark.asyncio
async def test_create_acl_no_modify():
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    common_acl_q = {}
    actual_acl_message = create_acl(
        "",
        receiver_addr=AgentAddress("", ""),
        acl_metadata=common_acl_q,
        sender_addr=AgentAddress(c.addr, ""),
    )

    assert "reeiver_addr" not in common_acl_q
    assert "receiver_id" not in common_acl_q
    assert "sender_addr" not in common_acl_q
    assert actual_acl_message.sender_addr is not None
    await c.shutdown()


@pytest.mark.asyncio
async def test_create_acl_anon():
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    actual_acl_message = create_acl(
        "",
        receiver_addr=AgentAddress("", ""),
        is_anonymous_acl=True,
        sender_addr=AgentAddress(c.addr, ""),
    )

    assert actual_acl_message.sender_addr is None
    await c.shutdown()


@pytest.mark.asyncio
async def test_create_acl_not_anon():
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    actual_acl_message = create_acl(
        "",
        receiver_addr=AgentAddress("", ""),
        is_anonymous_acl=False,
        sender_addr=AgentAddress(c.addr, ""),
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
    c = create_tcp_container(addr=("127.0.0.1", 5555), copy_internal_messages=False)
    agent1 = c.register(ExampleAgent())
    message_to_send = Data()

    async with activate(c):
        await c.send_message(message_to_send, receiver_addr=agent1.addr)

    assert agent1.content is message_to_send


@pytest.mark.asyncio
async def test_send_message_copy():
    c = create_tcp_container(addr=("127.0.0.1", 5555), copy_internal_messages=True)
    agent1 = c.register(ExampleAgent())
    message_to_send = Data()

    async with activate(c):
        await c.send_message(message_to_send, receiver_addr=agent1.addr)

    assert agent1.content is not message_to_send


@pytest.mark.asyncio
async def test_create_acl_diff_receiver():
    c = create_tcp_container(addr=("127.0.0.1", 5555))
    with pytest.warns(UserWarning) as record:
        actual_acl_message = create_acl(
            "",
            receiver_addr=AgentAddress("A", "A"),
            acl_metadata={"receiver_id": "B", "receiver_addr": "B"},
            sender_addr=AgentAddress(c.addr, ""),
            is_anonymous_acl=False,
        )

    assert actual_acl_message.receiver_addr == "A"
    assert actual_acl_message.receiver_id == "A"
    assert len(record) == 2
    await c.shutdown()


@pytest.mark.asyncio
async def test_containers_dont_share_default_codec():
    c1 = create_tcp_container(addr=("127.0.0.1", 5555))
    c2 = create_tcp_container(addr=("127.0.0.1", 5556))

    assert c1.codec is not c2.codec

    await c1.shutdown()
    await c2.shutdown()


@pytest.mark.asyncio
async def test_auto_port_container():
    c1 = create_tcp_container(addr=("127.0.0.1", None), auto_port=True)

    async with activate(c1):
        pass

    assert c1.addr[1] is not None


def test_agent_address_sortable():
    a1 = AgentAddress(("127.0.0.1", 5555), "aid1")
    a2 = AgentAddress(("127.0.0.1", 5555), "aid2")

    # generally allow sorting AgentAddresses
    assert a1 < a2

    # known caveat: same address is not resolved
    a10 = AgentAddress(("127.0.0.1", 5555), "aid10")
    a1_local = AgentAddress(("localhost", 5555), "aid1")
    assert a1 != a1_local

    # container takes precedence over agent_id
    assert a2 < a1_local

    # sorting is alphanumically
    assert a10 < a2
