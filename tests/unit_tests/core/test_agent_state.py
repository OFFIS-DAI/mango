from typing import Any

import pytest

from mango import activate, create_ec_container
from mango.agent.core import Agent


class MyAgent(Agent):
    test_counter: int = 0
    ready = False
    started = False
    registered = False

    def handle_message(self, content, meta: dict[str, Any]):
        self.test_counter += 1

    def on_ready(self):
        self.ready = True

    def on_start(self):
        self.started = True

    def on_register(self):
        self.registered = True


def test_register_twice():
    c = create_ec_container()
    agent = MyAgent()
    c.register(agent)

    with pytest.raises(ValueError):
        c.register(agent)


@pytest.mark.asyncio
async def test_ready_twice():
    c = create_ec_container()
    agent = MyAgent()
    c.register(agent)

    await c.start()
    c.on_ready()

    with pytest.raises(RuntimeError):
        c.on_ready()

    await c.shutdown()


@pytest.mark.asyncio
async def test_start_twice():
    c = create_ec_container()
    agent = MyAgent()
    c.register(agent)

    await c.start()
    with pytest.raises(RuntimeError):
        await c.start()

    await c.shutdown()


@pytest.mark.asyncio
async def test_agent_state():
    # GIVEN
    c = create_ec_container()
    agent = MyAgent()
    assert agent.registered is False
    assert agent.started is False
    assert agent.ready is False

    # WHEN
    c.register(agent)

    # THEN
    assert agent.registered is True
    assert agent.started is False
    assert agent.ready is False

    async with activate(c) as c:
        assert agent.started is True
        assert agent.ready is True


@pytest.mark.asyncio
async def test_delayed_agent_state():
    # GIVEN
    c = create_ec_container()

    async with activate(c) as c:
        agent = MyAgent()
        assert agent.registered is False
        c.register(agent)
        assert agent.registered is True
        assert agent.started is True
        assert agent.ready is True
