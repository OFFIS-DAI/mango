

import pytest

from mango.core.container import Container

class LooksLikeAgent():
    async def shutdown(self):
        pass

@pytest.mark.asyncio
async def test_register_aid_pattern_match():
    # GIVEN
    c = await Container.factory(addr=('127.0.0.2', 5555))
    agent = LooksLikeAgent()
    suggested_aid = "agent12"

    # WHEN
    actual_aid = c._register_agent(agent, suggested_aid)

    # THEN
    assert actual_aid == "agent0"
    await c.shutdown()
    

@pytest.mark.asyncio
async def test_register_aid_success():
    # GIVEN
    c = await Container.factory(addr=('127.0.0.2', 5555))
    agent = LooksLikeAgent()
    suggested_aid = "cagent12"

    # WHEN
    actual_aid = c._register_agent(agent, suggested_aid)

    # THEN
    assert actual_aid == suggested_aid
    await c.shutdown()

    
@pytest.mark.asyncio
async def test_register_no_suggested():
    # GIVEN
    c = await Container.factory(addr=('127.0.0.2', 5555))
    agent = LooksLikeAgent()

    # WHEN
    actual_aid = c._register_agent(agent)

    # THEN
    assert actual_aid == "agent0"
    await c.shutdown()
    
@pytest.mark.asyncio
async def test_register_pattern_half_match():
    # GIVEN
    c = await Container.factory(addr=('127.0.0.2', 5555))
    agent = LooksLikeAgent()
    suggested_aid = "agentABC"

    # WHEN
    actual_aid = c._register_agent(agent, suggested_aid)

    # THEN
    assert actual_aid == "agentABC"
    await c.shutdown()

    
@pytest.mark.asyncio
async def test_register_existing():
    # GIVEN
    c = await Container.factory(addr=('127.0.0.2', 5555))
    agent = LooksLikeAgent()
    suggested_aid = "agentABC"

    # WHEN
    actual_aid = c._register_agent(agent, suggested_aid)
    actual_aid2 = c._register_agent(agent, suggested_aid)

    # THEN
    assert actual_aid == "agentABC"
    assert actual_aid2 == "agent0"
    await c.shutdown()

    
@pytest.mark.asyncio
async def test_is_aid_available():
    # GIVEN
    c = await Container.factory(addr=('127.0.0.2', 5555))
    aid_to_check = "agentABC"

    # WHEN
    available = c.is_aid_available(aid_to_check)

    # THEN
    assert available
    await c.shutdown()
    
@pytest.mark.asyncio
async def test_is_aid_available_but_match():
    # GIVEN
    c = await Container.factory(addr=('127.0.0.2', 5555))
    aid_to_check = "agent5"

    # WHEN
    available = c.is_aid_available(aid_to_check)

    # THEN
    assert not available
    await c.shutdown()

@pytest.mark.asyncio
async def test_is_aid_not_available():
    # GIVEN
    c = await Container.factory(addr=('127.0.0.2', 5555))
    c._register_agent(LooksLikeAgent(), "abc")
    aid_to_check = "abc"

    # WHEN
    available = c.is_aid_available(aid_to_check)

    # THEN
    assert not available
    await c.shutdown()

@pytest.mark.asyncio
async def test_is_aid_not_available_and_match():
    # GIVEN
    c = await Container.factory(addr=('127.0.0.2', 5555))
    c._register_agent(LooksLikeAgent())
    aid_to_check = "agent0"

    # WHEN
    available = c.is_aid_available(aid_to_check)

    # THEN
    assert not available
    await c.shutdown()