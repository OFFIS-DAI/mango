"""Unit tests for :mod:`mango.agent.decorators`.

These tests do not spin up a container; they exercise
``apply_dispatch`` directly against a stub context that records every
subscribe / schedule call.  That keeps the tests focused on the
decoration semantics (collection order, async-wrap, predicate
threading, ``only_if`` gating) without depending on the rest of the
agent runtime.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from mango import (
    Role,
    RoleAgent,
    activate,
    agent_composed_of,
    create_tcp_container,
    on_event,
    on_message,
    periodic,
)
from mango.agent.decorators import apply_dispatch, collect_dispatch
from mango.util.clock import ExternalClock


@dataclass
class _StubContext:
    """Minimal recording context.  Mirrors only the surface
    ``apply_dispatch`` touches."""

    message_subs: list[dict] = field(default_factory=list)
    event_subs: list[dict] = field(default_factory=list)
    periodic_subs: list[dict] = field(default_factory=list)

    def subscribe_message(self, role, method, condition, priority=0):
        self.message_subs.append(
            {
                "role": role,
                "method": method,
                "condition": condition,
                "priority": priority,
            }
        )

    def subscribe_event(self, role, event_type, method):
        self.event_subs.append(
            {"role": role, "event_type": event_type, "method": method}
        )

    def schedule_periodic_task(self, coro_func, delay, src=None):
        self.periodic_subs.append({"coro": coro_func, "delay": delay, "src": src})

    def schedule_instant_task(self, coro):
        # The async @on_message wrap calls this when a matching message
        # arrives.  Capture and immediately run synchronously for the
        # tests; the coroutine itself shouldn't actually do anything
        # except mutate role state.
        asyncio.get_event_loop().run_until_complete(coro)


class _StubRole:
    """A non-:class:`Role` carrier so we can probe the dispatch
    machinery without inheriting the full Role lifecycle."""

    def __init__(self):
        self.context = _StubContext()
        self.calls: list[tuple[str, Any]] = []


class TestOnMessageDecorator:
    def test_collects_single_subscription(self):
        class R(_StubRole):
            @on_message(str)
            def handle(self, content, meta):
                self.calls.append(("handle", content))

        role = R()
        apply_dispatch(role)
        assert len(role.context.message_subs) == 1
        entry = role.context.message_subs[0]
        assert entry["priority"] == 0
        assert entry["condition"]("hello", {}) is True
        assert entry["condition"](42, {}) is False

    def test_where_predicate_receives_self(self):
        """The ``where`` predicate is bound to the role instance, so it
        can inspect role state (e.g. a sector tag) at filter time."""

        class R(_StubRole):
            sector = "electricity"

            @on_message(dict, where=lambda self, c, m: c.get("sector") == self.sector)
            def handle(self, content, meta):
                pass

        role = R()
        apply_dispatch(role)
        cond = role.context.message_subs[0]["condition"]
        assert cond({"sector": "electricity"}, {}) is True
        assert cond({"sector": "gas"}, {}) is False
        assert cond("not a dict", {}) is False  # type guard still holds

    def test_priority_is_forwarded(self):
        class R(_StubRole):
            @on_message(int, priority=-5)
            def first(self, content, meta):
                pass

            @on_message(int, priority=5)
            def last(self, content, meta):
                pass

        role = R()
        apply_dispatch(role)
        priorities = sorted(s["priority"] for s in role.context.message_subs)
        assert priorities == [-5, 5]

    def test_async_handler_is_scheduled_as_task(self):
        class R(_StubRole):
            @on_message(str)
            async def handle(self, content, meta):
                self.calls.append(("async", content))

        role = R()

        # Replace schedule_instant_task with an awaitable trap so we
        # can verify the wrap behaviour without spinning up a real
        # event loop.
        scheduled = []

        def fake_instant(coro):
            scheduled.append(coro)
            coro.close()  # don't actually run; just confirm it's a coro

        role.context.schedule_instant_task = fake_instant
        apply_dispatch(role)

        # Synthesize a matching delivery.
        method = role.context.message_subs[0]["method"]
        method("hello", {})
        assert len(scheduled) == 1

    def test_sync_handler_is_called_directly(self):
        """A sync handler needs no task at all: the callback invokes it
        inline, which is what keeps ordering deterministic for handlers
        that only mutate role state."""

        class R(_StubRole):
            @on_message(str)
            def handle(self, content, meta):
                self.calls.append(("sync", content, meta["sender_id"]))

        role = R()
        apply_dispatch(role)

        role.context.message_subs[0]["method"]("hello", {"sender_id": "a0"})

        assert role.calls == [("sync", "hello", "a0")]

    def test_rejects_application_to_non_function(self):
        """The decorators write their metadata onto the function object;
        applied to something that cannot carry attributes (a bound
        method, a builtin) they must say so rather than silently drop
        the subscription."""
        with pytest.raises(TypeError, match="plain functions"):
            on_message(str)(len)


class TestOnEventDecorator:
    def test_collects_event_subscription(self):
        @dataclass
        class MyEvent:
            value: int

        class R(_StubRole):
            @on_event(MyEvent)
            def on_event(self, event, src):
                self.calls.append(("event", event))

        role = R()
        apply_dispatch(role)
        assert len(role.context.event_subs) == 1
        assert role.context.event_subs[0]["event_type"] is MyEvent

    def test_rejects_async_handler(self):
        """``emit_event`` dispatches synchronously and never awaits, so an
        ``async def`` handler would be created and dropped unrun."""
        with pytest.raises(TypeError, match="synchronous method"):

            class R(_StubRole):
                @on_event(str)
                async def handle(self, event, source):
                    pass

    def test_multiple_event_types(self):
        @dataclass
        class A: ...

        @dataclass
        class B: ...

        class R(_StubRole):
            @on_event(A)
            @on_event(B)
            def on_either(self, event, src):
                pass

        role = R()
        apply_dispatch(role)
        types = {s["event_type"] for s in role.context.event_subs}
        assert types == {A, B}


class TestPeriodicDecorator:
    def test_collects_periodic_with_literal_delay(self):
        class R(_StubRole):
            @periodic(every=2.5)
            async def tick(self):
                self.calls.append(("tick",))

        role = R()
        apply_dispatch(role)
        assert len(role.context.periodic_subs) == 1
        assert role.context.periodic_subs[0]["delay"] == pytest.approx(2.5)

    def test_periodic_reads_role_attribute_when_string(self):
        class R(_StubRole):
            poll_period_s = 0.75

            @periodic(every="poll_period_s")
            async def tick(self):
                pass

        role = R()
        apply_dispatch(role)
        assert role.context.periodic_subs[0]["delay"] == pytest.approx(0.75)

    def test_periodic_rejects_sync_function(self):
        with pytest.raises(TypeError, match="async coroutine"):

            class R(_StubRole):
                @periodic(every=1.0)
                def tick(self):  # noqa: B903 (sync intentionally for the test)
                    pass

    def test_only_if_gates_the_body(self):
        runs: list[bool] = []

        class R(_StubRole):
            allowed = False

            @periodic(every=0.1, only_if=lambda self: self.allowed)
            async def tick(self):
                runs.append(True)

        role = R()
        apply_dispatch(role)
        wrapped = role.context.periodic_subs[0]["coro"]

        loop = asyncio.new_event_loop()
        try:
            # Gate closed → no run.
            loop.run_until_complete(wrapped())
            assert runs == []
            # Gate open → runs.
            role.allowed = True
            loop.run_until_complete(wrapped())
            assert runs == [True]
        finally:
            loop.close()


class TestCollectDispatch:
    def test_collects_from_base_class(self):
        class Base(_StubRole):
            @on_message(str)
            def base_handler(self, content, meta):
                pass

        class Sub(Base):
            @on_message(int)
            def sub_handler(self, content, meta):
                pass

        meta = collect_dispatch(Sub)
        assert set(meta.keys()) == {"base_handler", "sub_handler"}

    def test_subclass_overrides_keep_metadata(self):
        class Base(_StubRole):
            @on_message(str)
            def handler(self, content, meta):
                pass

        class Sub(Base):
            @on_message(int)
            def handler(self, content, meta):  # noqa: D401 (override)
                pass

        meta = collect_dispatch(Sub)
        # Only the override is kept; the base entry is masked.
        assert "handler" in meta
        assert len(meta["handler"].message_subs) == 1
        assert meta["handler"].message_subs[0]["message_type"] is int


class TestRoleIntegration:
    """Via a real :class:`Role`: verifies what ``_bind`` wires and what
    it leaves to the ``on_ready`` phase."""

    def test_role_without_setup_still_subscribes(self):
        captured = []

        class MyRole(Role):
            @on_message(str)
            def handler(self, content, meta):
                captured.append(content)

        # Stub context that records subscribe_message calls.
        ctx = _StubContext()
        role = MyRole()
        role._context = ctx
        apply_dispatch(role)
        assert len(ctx.message_subs) == 1

    def test_bind_applies_subscriptions_but_defers_periodic(self):
        class MyRole(Role):
            @on_message(str)
            def handler(self, content, meta):
                pass

            @periodic(every=1.0)
            async def tick(self):
                pass

        ctx = _StubContext()
        role = MyRole()
        role._bind(ctx)
        assert len(ctx.message_subs) == 1
        assert ctx.periodic_subs == []

    def test_periodic_is_scheduled_with_role_as_src(self):
        class MyRole(Role):
            @periodic(every=1.0)
            async def tick(self):
                pass

        ctx = _StubContext()
        role = MyRole()
        role._context = ctx
        apply_dispatch(role)
        assert ctx.periodic_subs[0]["src"] is role


class _Tick(Role):
    """Real role with a decorated periodic task and lifecycle counters."""

    def __init__(self):
        super().__init__()
        self.ticks: list[float] = []
        self.started = 0
        self.ready = 0

    def on_start(self):
        self.started += 1

    def on_ready(self):
        self.ready += 1

    @periodic(every=10.0)
    async def tick(self):
        self.ticks.append(self.context.current_timestamp)


class TestPeriodicLifecycle:
    """``@periodic`` against real containers: the task must start at
    ``on_ready`` regardless of whether the role was added before the
    agent was registered, before the container started, or at runtime."""

    @pytest.mark.asyncio
    async def test_agent_composed_of_defers_periodic_until_ready(self):
        container = create_tcp_container(addr=("127.0.0.1", 5681))
        role = _Tick()
        agent_composed_of(role, register_in=container)
        assert role.ticks == []
        async with activate(container):
            await asyncio.sleep(0.05)
            assert len(role.ticks) == 1
        assert (role.started, role.ready) == (1, 1)

    @pytest.mark.asyncio
    async def test_periodic_follows_external_clock(self):
        clock = ExternalClock(start_time=0)
        container = create_tcp_container(addr=("127.0.0.1", 5682), clock=clock)
        role = _Tick()
        agent_composed_of(role, register_in=container)
        async with activate(container):
            await asyncio.sleep(0.05)
            for t in (10, 20, 30):
                clock.set_time(t)
                await asyncio.sleep(0.02)
        assert role.ticks == [0, 10, 20, 30]

    @pytest.mark.asyncio
    async def test_role_added_at_runtime_is_caught_up(self):
        container = create_tcp_container(addr=("127.0.0.1", 5683))
        agent = container.register(RoleAgent())
        async with activate(container):
            role = _Tick()
            agent.add_role(role)
            assert (role.started, role.ready) == (1, 1)
            await asyncio.sleep(0.05)
            assert len(role.ticks) == 1

    @pytest.mark.asyncio
    async def test_role_added_before_start_gets_hooks_once(self):
        container = create_tcp_container(addr=("127.0.0.1", 5684))
        agent = container.register(RoleAgent())
        role = _Tick()
        agent.add_role(role)
        assert (role.started, role.ready) == (0, 0)
        async with activate(container):
            await asyncio.sleep(0.02)
        assert (role.started, role.ready) == (1, 1)

    @pytest.mark.asyncio
    async def test_deactivate_suspends_decorated_periodic(self):
        clock = ExternalClock(start_time=0)
        container = create_tcp_container(addr=("127.0.0.1", 5685), clock=clock)
        role = _Tick()
        agent_composed_of(role, register_in=container)
        async with activate(container):
            await asyncio.sleep(0.05)
            role.context.deactivate(role)
            for t in (10, 20):
                clock.set_time(t)
                await asyncio.sleep(0.03)
            assert role.ticks == [0]
            role.context.activate(role)
            for t in (30, 40):
                clock.set_time(t)
                await asyncio.sleep(0.03)
        assert role.ticks[-2:] == [30, 40]

    @pytest.mark.asyncio
    async def test_runtime_role_does_not_restart_periodic_on_ready(self):
        """A role added after the agent is ready starts its periodic task
        once, in ``catch_up_lifecycle``.  A later ``on_ready`` (the
        container being activated again) must not schedule a second
        copy alongside it."""
        clock = ExternalClock(start_time=0)
        container = create_tcp_container(addr=("127.0.0.1", 5686), clock=clock)
        agent = container.register(RoleAgent())
        role = _Tick()

        async with activate(container):
            agent.add_role(role)
            await asyncio.sleep(0.02)
            assert role.ticks == [0]

            agent._role_handler.on_ready()
            clock.set_time(10)
            await asyncio.sleep(0.03)

        assert role.ticks == [0, 10]
        assert role.ready == 2
