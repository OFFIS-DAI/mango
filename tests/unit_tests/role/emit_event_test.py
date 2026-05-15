"""Tests for the relaxed :meth:`RoleContext.emit_event` semantics.

Before: emitting an event with no subscribed listener raised
``KeyError``, forcing every call site to wrap the emit in
``try/except KeyError``.  Now: missing listeners are silently dropped
by default; the legacy strict behaviour is opt-in via ``strict=True``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from mango import RoleAgent
from mango.agent.role import RoleContext, RoleHandler


@dataclass
class _SomeEvent:
    value: int


def _make_handler() -> RoleHandler:
    """Build a bare RoleHandler suitable for emit_event tests.  No
    real container is needed because emit_event is purely role-local."""
    h = RoleHandler(scheduler=None)
    return h


class TestEmitEventNoListener:
    def test_default_is_silent(self):
        """Emitting an event nobody subscribed to is a no-op."""
        handler = _make_handler()
        # Must not raise.
        handler.emit_event(_SomeEvent(value=1))

    def test_strict_raises_keyerror(self):
        """Opting back into the legacy strict behaviour."""
        handler = _make_handler()
        with pytest.raises(KeyError):
            handler.emit_event(_SomeEvent(value=1), strict=True)

    def test_listener_receives_event_silent_mode(self):
        """The relaxed default still delivers events to actual listeners."""
        handler = _make_handler()
        received: list[_SomeEvent] = []
        # Subscribe with the legacy API (role is irrelevant for this test).
        handler.subscribe_event(
            role=object(),
            event_type=_SomeEvent,
            method=lambda ev, src: received.append(ev),
        )
        handler.emit_event(_SomeEvent(value=42))
        assert received and received[0].value == 42

    def test_listener_receives_event_strict_mode(self):
        """Strict mode is purely about the no-listener case — when a
        listener exists, the event is delivered normally."""
        handler = _make_handler()
        received: list[_SomeEvent] = []
        handler.subscribe_event(
            role=object(),
            event_type=_SomeEvent,
            method=lambda ev, src: received.append(ev),
        )
        handler.emit_event(_SomeEvent(value=7), strict=True)
        assert received and received[0].value == 7


class TestRoleContextEmitEvent:
    """The same semantics must reach callers via :class:`RoleContext`,
    which is the public surface most roles use."""

    def test_role_context_silent_no_listener(self):
        # Use a RoleAgent so the role-context wiring is realistic; we
        # don't need a container because emit_event is role-local.
        agent = RoleAgent()
        # The context is reachable via the private handle on the agent.
        ctx: RoleContext = agent._role_context
        # No listener subscribed — must not raise.
        ctx.emit_event(_SomeEvent(value=1))

    def test_role_context_strict_raises(self):
        agent = RoleAgent()
        ctx: RoleContext = agent._role_context
        with pytest.raises(KeyError):
            ctx.emit_event(_SomeEvent(value=1), strict=True)
