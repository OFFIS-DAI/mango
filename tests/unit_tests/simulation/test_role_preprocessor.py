import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from mango import (
    Agent,
    MessagePreprocessor,
    RoleAgent,
    WaitingMessagePreprocessor,
    run_with_simulation,
    step_simulation,
)
from mango.agent.role import Role


class SimpleAgent(Agent):
    def __init__(self):
        super().__init__()
        self.messages: list[tuple[Any, dict]] = []

    def handle_message(self, content, meta):
        self.messages.append((content, meta))


class CapturingPreprocessor(MessagePreprocessor):
    def __init__(self):
        self.calls: list[tuple] = []
        self.inited = False

    def init(self, role_or_agent):
        self.inited = True

    def handle(self, role_or_agent, handler, content, meta):
        self.calls.append((content, meta))
        handler(content, meta)


class TransformingPreprocessor(MessagePreprocessor):
    def handle(self, role_or_agent, handler, content, meta):
        content, meta = self.process(content, meta)
        handler(content, meta)

    def process(self, content, meta):
        return content.upper() if isinstance(content, str) else content, meta


def test_init_called_on_subscribe():
    from mango.agent.role import RoleHandler

    class DummyRole(Role):
        pass

    handler = RoleHandler(MagicMock())
    role = DummyRole()
    preprocessor = CapturingPreprocessor()
    handler.subscribe_message(
        role, lambda c, m: None, lambda c, m: True, preprocessor=preprocessor
    )
    assert preprocessor.inited is True


def test_handle_called_on_message():
    from mango.agent.role import RoleHandler

    received = []

    class DummyRole(Role):
        def on_msg(self, content, meta):
            received.append(content)

    handler = RoleHandler(MagicMock())
    role = DummyRole()
    preprocessor = CapturingPreprocessor()
    handler.subscribe_message(
        role, role.on_msg, lambda c, m: True, preprocessor=preprocessor
    )
    handler.handle_message("hello", {})
    assert len(preprocessor.calls) == 1
    assert preprocessor.calls[0][0] == "hello"
    assert received == ["hello"]


def test_can_transform_content():
    from mango.agent.role import RoleHandler

    received = []

    class DummyRole(Role):
        def on_msg(self, content, meta):
            received.append(content)

    handler = RoleHandler(MagicMock())
    role = DummyRole()
    handler.subscribe_message(
        role, role.on_msg, lambda c, m: True, preprocessor=TransformingPreprocessor()
    )
    handler.handle_message("hello", {})
    assert received == ["HELLO"]


def test_none_preprocessor_is_passthrough():
    from mango.agent.role import RoleHandler

    received = []

    class DummyRole(Role):
        def on_msg(self, content, meta):
            received.append(content)

    handler = RoleHandler(MagicMock())
    role = DummyRole()
    handler.subscribe_message(role, role.on_msg, lambda c, m: True)
    handler.handle_message("direct", {})
    assert received == ["direct"]


def test_condition_filters_before_preprocessor():
    from mango.agent.role import RoleHandler

    received = []

    class DummyRole(Role):
        def on_msg(self, content, meta):
            received.append(content)

    handler = RoleHandler(MagicMock())
    role = DummyRole()
    preprocessor = CapturingPreprocessor()
    handler.subscribe_message(
        role, role.on_msg, lambda c, m: c == "yes", preprocessor=preprocessor
    )
    handler.handle_message("no", {})
    assert received == []
    assert preprocessor.calls == []

    handler.handle_message("yes", {})
    assert received == ["yes"]


@pytest.mark.asyncio
async def test_waiting_preprocessor_serializes_delivery():
    order = []

    class SerialRole(Role):
        def setup(self):
            self.context.subscribe_message(
                self,
                self.on_msg,
                lambda c, m: True,
                preprocessor=WaitingMessagePreprocessor(),
            )

        def on_msg(self, content, meta):
            order.append(content)

    agent = RoleAgent()
    async with run_with_simulation(agent) as world:
        role = SerialRole()
        agent.add_role(role)
        sender = world.register(SimpleAgent())

        for i in range(3):
            await world.send_message(i, receiver_addr=agent.addr, sender_id=sender.aid)

        await step_simulation(world, step_size_s=1.0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert order == [0, 1, 2]
